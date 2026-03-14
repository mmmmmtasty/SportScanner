from __future__ import annotations

from datetime import date
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from sportscanner.config import Settings, validate_plex_provider_identifier
from sportscanner.db.models import (
    AppSetting,
    Competition,
    CompetitionSeason,
    Event,
    EventOrder,
    EventOrigin,
    MetadataRefreshJob,
    MetadataRefreshJobSource,
    MetadataRefreshJobStatus,
    MetadataRefreshJobTarget,
    Override,
    Recording,
    RecordingStatus,
    ReviewTask,
    ReviewTaskStatus,
)
from sportscanner.db.queries import (
    complete_metadata_refresh_job,
    create_alias,
    create_metadata_refresh_job,
    get_alias_for_text,
    get_or_create_competition_season,
    get_recording_by_fingerprint,
    get_recording_by_source_path,
)
from sportscanner.metadata_snapshot import clear_recording_metadata_snapshot, sync_recording_metadata_snapshot
from sportscanner.organizer.fingerprint import compute_fingerprint
from sportscanner.organizer.matcher import (
    best_db_competition_match,
    best_upstream_competition_match,
    build_match_explanation,
    match_event,
    season_for_date,
)
from sportscanner.organizer.numbering import EventSequenceInput, RecordingCodeInput, compute_event_sequences, compute_recording_codes, derive_weekend_group, episode_number
from sportscanner.organizer.parser import ParsedFile, is_media_file, parse_filename
from sportscanner.organizer.placer import build_managed_filename, move_managed_file, place_file, season_directory_name
from sportscanner.organizer.plexmatch import render_season_plexmatch, render_show_plexmatch, write_atomic_if_changed
from sportscanner.text import sanitize_event_name
from sportscanner.upstream.base import MetadataSource, UpstreamCompetition, UpstreamEvent

try:
    import yaml
except ImportError:  # pragma: no cover - optional fallback for minimal environments
    yaml = None

logger = logging.getLogger("sportscanner.organizer.service")


def _stringify_override_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


@dataclass(slots=True)
class SidecarMetadata:
    competition: str | None = None
    season: int | None = None
    event: str | None = None
    kind: str | None = None
    title_suffix: str | None = None
    tsdb_event_id: int | None = None


def slugify(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return lowered or "unknown"


def parse_simple_yaml(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        result[key.strip()] = raw_value.strip().strip('"').strip("'")
    return result


def read_sidecar(path: Path) -> SidecarMetadata:
    sidecar_path = path.with_suffix(".sportscanner.yml")
    if not sidecar_path.exists():
        return SidecarMetadata()
    try:
        text = sidecar_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return SidecarMetadata()
    data = yaml.safe_load(text) if yaml is not None else parse_simple_yaml(text)
    if not isinstance(data, dict):
        return SidecarMetadata()
    return SidecarMetadata(
        competition=data.get("competition"),
        season=int(data["season"]) if data.get("season") else None,
        event=data.get("event"),
        kind=data.get("kind") or data.get("segment_kind"),
        title_suffix=data.get("title_suffix"),
        tsdb_event_id=int(data["tsdb_event_id"]) if data.get("tsdb_event_id") else None,
    )


def read_competition_config(path: Path) -> dict[str, str]:
    current = path.parent
    while True:
        candidate = current / "competition.sportscanner.yml"
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
            data = yaml.safe_load(text) if yaml is not None else parse_simple_yaml(text)
            if isinstance(data, dict):
                return {str(key): str(value) for key, value in data.items() if value is not None}
            return {}
        if current == current.parent:
            break
        current = current.parent
    return {}


class OrganizerService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        metadata_source: MetadataSource | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.metadata_source = metadata_source
        self._ingest_lock = threading.RLock()
        self._upstream_competitions_cache: list[UpstreamCompetition] | None = None

    def _effective_plex_provider_identifier(self, session: Session) -> str:
        configured = session.get(AppSetting, "plex_provider_identifier")
        value = configured.value if configured is not None else self.settings.plex_provider_identifier
        try:
            return validate_plex_provider_identifier(value)
        except ValueError:
            return self.settings.plex_provider_identifier

    def _effective_directory(self, session: Session, key: str, fallback: Path) -> Path:
        configured = session.get(AppSetting, key)
        if configured is None or not configured.value.strip():
            return fallback
        return Path(configured.value.strip())

    def _all_upstream_competitions(self) -> list[UpstreamCompetition]:
        if self.metadata_source is None:
            return []
        if self._upstream_competitions_cache is None:
            self._upstream_competitions_cache = self.metadata_source.all_competitions()
        return self._upstream_competitions_cache

    def rescan_incoming(self) -> list[str]:
        with self._ingest_lock:
            processed: list[str] = []
            auto_refresh_targets: list[tuple[str, str]] = []
            with self.session_factory() as session:
                incoming = self._effective_directory(session, "incoming_dir", self.settings.incoming_dir)
                if not incoming.exists():
                    logger.info("rescan_skipped incoming_dir_missing=%s", incoming)
                    return processed
                logger.info("rescan_started incoming_dir=%s", incoming)
                for path in sorted(incoming.rglob("*")):
                    if path.is_file() and is_media_file(path):
                        if self.ingest_path_if_parseable(path) is not None:
                            processed.append(str(path))
                refreshed_missing_sources = 0
                published = list(
                    session.scalars(
                        select(Recording)
                        .where(Recording.status == RecordingStatus.PUBLISHED.value)
                        .order_by(Recording.updated_at.asc(), Recording.id.asc())
                    )
                )
                for recording in published:
                    if Path(recording.source_path).exists():
                        continue
                    event = session.get(Event, recording.event_id) if recording.event_id else None
                    if event is None or event.tsdb_id is None:
                        continue
                    auto_refresh_targets.append((recording.id, recording.title))
                session.commit()
            for recording_id, recording_title in auto_refresh_targets:
                try:
                    self._run_tracked_metadata_refresh_job(
                        target_type=MetadataRefreshJobTarget.RECORDING,
                        target_id=recording_id,
                        target_label=recording_title,
                        source=MetadataRefreshJobSource.AUTOMATIC,
                        task=lambda recording_id=recording_id: self.refresh_recording_metadata(recording_id),
                    )
                except Exception as exc:
                    logger.warning(
                        "rescan_missing_source_refresh_failed recording_id=%s error=%s",
                        recording_id,
                        exc,
                    )
                else:
                    refreshed_missing_sources += 1
            logger.info(
                "rescan_reconciled_missing_sources refreshed_count=%s",
                refreshed_missing_sources,
            )
            logger.info("rescan_completed processed_count=%s", len(processed))
            return processed

    def refresh_competition_metadata(self, competition_id: str) -> Competition:
        with self._ingest_lock:
            with self.session_factory() as session:
                competition = session.get(Competition, competition_id)
                if competition is None:
                    raise KeyError(f"Unknown competition: {competition_id}")
                self._refresh_competition_metadata(session, competition)
                session.commit()
                refreshed = session.get(Competition, competition_id)
                assert refreshed is not None
                return refreshed

    def refresh_season_metadata(self, season_id: str) -> CompetitionSeason:
        with self._ingest_lock:
            with self.session_factory() as session:
                season = session.get(CompetitionSeason, season_id)
                if season is None:
                    raise KeyError(f"Unknown season: {season_id}")
                self._refresh_season_metadata(session, season)
                session.commit()
                refreshed = session.get(CompetitionSeason, season_id)
                assert refreshed is not None
                return refreshed

    def refresh_event_metadata(self, event_id: str) -> Event:
        with self._ingest_lock:
            with self.session_factory() as session:
                event = session.get(Event, event_id)
                if event is None:
                    raise KeyError(f"Unknown event: {event_id}")
                refreshed_id = self._refresh_event_metadata(session, event).id
                session.commit()
                refreshed = session.get(Event, refreshed_id)
                assert refreshed is not None
                return refreshed

    def refresh_recording_metadata(self, recording_id: str) -> Recording:
        with self._ingest_lock:
            with self.session_factory() as session:
                recording = session.get(Recording, recording_id)
                if recording is None:
                    raise KeyError(f"Unknown recording: {recording_id}")
                self._refresh_recording_metadata(session, recording)
                session.commit()
                refreshed = session.get(Recording, recording_id)
                assert refreshed is not None
                return refreshed

    def _run_tracked_metadata_refresh_job(
        self,
        *,
        target_type: MetadataRefreshJobTarget,
        target_id: str,
        target_label: str,
        source: MetadataRefreshJobSource,
        task,
    ):
        with self.session_factory() as session:
            job = create_metadata_refresh_job(
                session,
                target_type=target_type,
                target_id=target_id,
                target_label=target_label,
                source=source,
            )
            session.commit()
            job_id = job.id

        try:
            result = task()
        except Exception as exc:
            with self.session_factory() as session:
                job = session.get(MetadataRefreshJob, job_id)
                if job is not None:
                    complete_metadata_refresh_job(
                        session,
                        job,
                        status=MetadataRefreshJobStatus.ERROR,
                        error_message=str(exc),
                    )
                    session.commit()
            raise

        with self.session_factory() as session:
            job = session.get(MetadataRefreshJob, job_id)
            if job is not None:
                complete_metadata_refresh_job(
                    session,
                    job,
                    status=MetadataRefreshJobStatus.SUCCESS,
                )
                session.commit()
        return result

    def update_recording_details(
        self,
        recording_id: str,
        *,
        title: str,
        kind: str,
        summary: str | None,
        air_date: date | None,
        episode_number: int | None,
        competition_season_id: str,
    ) -> Recording:
        with self._ingest_lock:
            with self.session_factory() as session:
                recording = session.get(Recording, recording_id)
                if recording is None:
                    raise KeyError(f"Unknown recording: {recording_id}")
                season = session.get(CompetitionSeason, competition_season_id)
                if season is None:
                    raise KeyError(f"Unknown season: {competition_season_id}")

                previous_season_id = recording.competition_season_id
                previous_values = {
                    "title": recording.title,
                    "kind": recording.kind,
                    "summary": recording.summary,
                    "air_date": recording.air_date,
                    "episode_number": recording.episode_number,
                    "competition_season_id": recording.competition_season_id,
                }
                recording.title = title
                recording.kind = kind
                recording.summary = summary
                recording.air_date = air_date
                recording.episode_number = episode_number
                recording.competition_season_id = competition_season_id
                session.flush()

                event = session.get(Event, recording.event_id) if recording.event_id else None
                override_baselines = {
                    "air_date": event.date if event is not None else None,
                    "summary": event.description if event is not None else None,
                    "competition_season_id": event.competition_season_id if event is not None else previous_season_id,
                }
                new_values = {
                    "title": title,
                    "kind": kind,
                    "summary": summary,
                    "air_date": air_date,
                    "episode_number": episode_number,
                    "competition_season_id": competition_season_id,
                }
                existing_overrides = {
                    override.field: override
                    for override in session.scalars(select(Override).where(Override.recording_id == recording.id))
                }
                for field, new_value in new_values.items():
                    baseline = override_baselines.get(field)
                    override = existing_overrides.get(field)
                    if field in override_baselines and new_value == baseline:
                        if override is not None:
                            session.delete(override)
                        continue
                    if new_value == previous_values[field] and override is None:
                        continue
                    if override is None:
                        override = Override(
                            recording_id=recording.id,
                            field=field,
                            old_value=_stringify_override_value(previous_values[field]),
                        )
                        session.add(override)
                    override.new_value = _stringify_override_value(new_value)
                    override.reason = "manual_override"
                session.flush()
                session.expire(recording, ["overrides"])

                affected_seasons = {previous_season_id, recording.competition_season_id}
                for season_id in sorted(affected_seasons):
                    self._refresh_published_recordings_for_season(session, season_id)

                if recording.event_id is not None:
                    sync_recording_metadata_snapshot(
                        session,
                        recording=recording,
                        metadata_source_name=getattr(self.metadata_source, "name", None),
                    )
                session.commit()
                refreshed = session.get(Recording, recording_id)
                assert refreshed is not None
                return refreshed

    def ingest_path_if_parseable(self, source_path: str | Path) -> Recording | None:
        try:
            return self.ingest_path(source_path)
        except ValueError as exc:
            logger.warning("ingest_skipped_unparsable source_path=%s error=%s", source_path, exc)
            return None

    def ingest_path(self, source_path: str | Path) -> Recording:
        with self._ingest_lock:
            logger.info("ingest_started source_path=%s", source_path)
            parsed = parse_filename(source_path)
            sidecar = read_sidecar(parsed.source_path)
            with self.session_factory() as session:
                recording = self._upsert_recording_from_path(session, parsed, sidecar)
                session.commit()
                recording_id = recording.id
            with self.session_factory() as session:
                result = session.get(Recording, recording_id)
                assert result is not None
                logger.info(
                    "ingest_completed source_path=%s status=%s match_method=%s event_id=%s episode_number=%s",
                    source_path,
                    result.status,
                    result.match_method,
                    result.event_id,
                    result.episode_number,
                )
                return result

    def resolve_review_task(
        self,
        task_id: int,
        *,
        event_id: str | None = None,
        tsdb_event_id: int | None = None,
        publish_as_special: bool = False,
    ) -> Recording:
        with self.session_factory() as session:
            task = session.get(ReviewTask, task_id)
            if task is None:
                raise KeyError(f"Unknown review task: {task_id}")
            recording = session.get(Recording, task.recording_id)
            if recording is None:
                raise KeyError(f"Unknown recording for review task: {task.recording_id}")

            if publish_as_special:
                recording = self._publish_as_special(session, recording)
                task.resolution = {"type": "special", "event_id": recording.event_id}
            elif tsdb_event_id is not None:
                event = self._resolve_review_lookup_event(session, recording, tsdb_event_id)
                recording = self._assign_recording_to_event(
                    session,
                    recording,
                    event,
                    match_method="manual_lookup_event_id",
                )
                task.resolution = {"type": "tsdb_event", "event_id": event.id, "tsdb_event_id": tsdb_event_id}
            elif event_id:
                event = session.get(Event, event_id)
                if event is None:
                    raise KeyError(f"Unknown event: {event_id}")
                recording = self._assign_recording_to_event(
                    session,
                    recording,
                    event,
                    match_method="manual_resolution",
                )
                task.resolution = {"type": "event", "event_id": event.id}
            else:
                recording.status = RecordingStatus.IGNORED.value
                clear_recording_metadata_snapshot(recording)
                task.resolution = {"type": "ignored"}

            task.status = ReviewTaskStatus.RESOLVED.value
            session.commit()
            refreshed = session.get(Recording, recording.id)
            assert refreshed is not None
            logger.info(
                "review_task_resolved task_id=%s resolution_type=%s recording_id=%s event_id=%s",
                task_id,
                task.resolution.get("type"),
                recording.id,
                recording.event_id,
            )
            return refreshed

    def rematch_recording(
        self,
        recording_id: str,
        *,
        event_id: str | None = None,
        tsdb_event_id: int | None = None,
    ) -> Recording:
        """Re-match an already-published (or review) recording to a different event.

        Works the same as resolving a review task but does not require an open
        ReviewTask to exist — useful for correcting a previously auto-published file.
        Any open review tasks for this recording are closed as a side-effect.
        """
        with self._ingest_lock:
            with self.session_factory() as session:
                recording = session.get(Recording, recording_id)
                if recording is None:
                    raise KeyError(f"Unknown recording: {recording_id}")
                old_event_id = recording.event_id

                if tsdb_event_id is not None:
                    event = self._resolve_review_lookup_event(session, recording, tsdb_event_id)
                elif event_id is not None:
                    event = session.get(Event, event_id)
                    if event is None:
                        raise KeyError(f"Unknown event: {event_id}")
                else:
                    raise ValueError("event_id or tsdb_event_id is required")

                # Stash old season so _assign_recording_to_event can recompute it
                old_explanation = recording.match_explanation or {}
                old_explanation["_old_season_id"] = recording.competition_season_id

                recording.match_explanation = old_explanation
                self._assign_recording_to_event(
                    session,
                    recording,
                    event,
                    match_method="manual_resolution",
                    old_event_id=old_event_id,
                )

                # Close any open review tasks
                open_tasks = list(
                    session.scalars(
                        select(ReviewTask).where(
                            ReviewTask.recording_id == recording_id,
                            ReviewTask.status == ReviewTaskStatus.OPEN.value,
                        )
                    )
                )
                for task in open_tasks:
                    task.status = ReviewTaskStatus.RESOLVED.value
                    task.resolution = {"type": "rematch", "event_id": event.id}

                session.commit()
                result = session.get(Recording, recording_id)
                assert result is not None
                logger.info(
                    "recording_rematched recording_id=%s old_event_id=%s new_event_id=%s",
                    recording_id,
                    old_event_id,
                    event.id,
                )
                return result

    def resolve_high_confidence_recordings(self, *, threshold: float = 0.70) -> list[str]:
        """Auto-publish REVIEW recordings that have a matched event_id and confidence >= threshold.

        Returns the list of recording IDs that were published.
        """
        with self._ingest_lock:
            with self.session_factory() as session:
                candidates = list(
                    session.scalars(
                        select(Recording).where(
                            Recording.status == RecordingStatus.REVIEW.value,
                            Recording.event_id.is_not(None),
                            Recording.match_confidence >= threshold,
                        )
                    )
                )
                resolved_ids: list[str] = []
                for recording in candidates:
                    event = session.get(Event, recording.event_id)
                    if event is None:
                        continue
                    old_event_id = recording.event_id
                    old_explanation = recording.match_explanation or {}
                    old_explanation["_old_season_id"] = recording.competition_season_id
                    recording.match_explanation = old_explanation
                    self._assign_recording_to_event(
                        session,
                        recording,
                        event,
                        match_method="auto_high_confidence",
                        old_event_id=old_event_id,
                    )
                    open_tasks = list(
                        session.scalars(
                            select(ReviewTask).where(
                                ReviewTask.recording_id == recording.id,
                                ReviewTask.status == ReviewTaskStatus.OPEN.value,
                            )
                        )
                    )
                    for task in open_tasks:
                        task.status = ReviewTaskStatus.RESOLVED.value
                        task.resolution = {"type": "auto_high_confidence", "event_id": event.id}
                    resolved_ids.append(recording.id)
                session.commit()
                logger.info(
                    "resolve_high_confidence_completed threshold=%s resolved_count=%s",
                    threshold,
                    len(resolved_ids),
                )
                return resolved_ids

    def _assign_recording_to_event(
        self,
        session: Session,
        recording: Recording,
        event: Event,
        *,
        match_method: str,
        old_event_id: str | None = None,
    ) -> Recording:
        season = session.get(CompetitionSeason, event.competition_season_id)
        competition = session.get(Competition, season.competition_id) if season else None

        # Preserve audit trail of previous matches
        existing_explanation = recording.match_explanation or {}
        history = list(existing_explanation.get("history", []))
        if old_event_id and old_event_id != event.id:
            history.append({"event_id": old_event_id, "method": recording.match_method})

        recording.event_id = event.id
        recording.competition_season_id = event.competition_season_id
        recording.status = RecordingStatus.PUBLISHED.value
        recording.match_confidence = 1.0
        recording.match_method = match_method
        explanation = build_match_explanation(
            method=match_method,
            confidence=1.0,
            event_name=event.name,
            competition_name=competition.name if competition else None,
        )
        explanation["history"] = history
        recording.match_explanation = explanation

        # Create alias for manual assignments so future files auto-match
        if match_method in ("manual_resolution", "manual_lookup_event_id", "rematch") and competition is not None:
            try:
                parsed = parse_filename(recording.source_path)
                if parsed and parsed.show:
                    create_alias(session, competition.id, parsed.show, source="auto")
            except Exception:
                pass

        session.flush()
        old_season_id = existing_explanation.get("_old_season_id")
        if old_season_id and old_season_id != event.competition_season_id:
            self._recompute_sequences_for_season(session, old_season_id)
        self._recompute_sequences_for_season(session, event.competition_season_id)
        self._recompute_recordings_for_event(session, event.id)
        self._publish_recording(session, recording)
        return recording

    def _resolve_review_lookup_event(self, session: Session, recording: Recording, tsdb_event_id: int) -> Event:
        if self.metadata_source is None:
            raise ValueError("Metadata source is not configured")
        upstream = self.metadata_source.lookup_event(tsdb_event_id)
        if upstream is None:
            raise KeyError(f"Unknown upstream event: {tsdb_event_id}")

        current_season = session.get(CompetitionSeason, recording.competition_season_id)
        current_competition = session.get(Competition, current_season.competition_id) if current_season else None
        if current_competition is not None and (
            not upstream.competition_name
            or best_db_competition_match(
                upstream.competition_name,
                [current_competition],
                threshold=0.6,
            )
            is not None
        ):
            competition = current_competition
        else:
            competition_name = upstream.competition_name or (current_competition.name if current_competition else "")
            competition = self._resolve_competition(session, competition_name)

        if upstream.date is not None:
            season_number, season_label = season_for_date(upstream.date, competition)
            season = get_or_create_competition_season(
                session,
                competition=competition,
                season_number=season_number,
                label=season_label,
                is_complete=True,
            )
            session.flush()
        elif current_season is not None and current_season.competition_id == competition.id:
            season = current_season
        else:
            raise KeyError(f"Could not resolve season for upstream event: {tsdb_event_id}")
        return self._upsert_event(session, season.id, upstream)

    def _upsert_recording_from_path(self, session: Session, parsed: ParsedFile, sidecar: SidecarMetadata) -> Recording:
        # Pre-fetch upstream search results before any session.flush() to avoid a
        # write-lock deadlock: _get_json opens its own session to write api_cache,
        # but that session would be blocked by the write lock held by this one.
        prefetched_search_results = (
            self.metadata_source.search_filename(parsed.search_query())
            if self.metadata_source is not None
            else []
        )
        competition = self._resolve_competition(
            session,
            sidecar.competition or parsed.show,
            search_hints=prefetched_search_results,
        )
        self._apply_competition_config(competition, parsed.source_path)
        season_number, season_label = self._resolve_season(parsed, competition, sidecar)

        if season_number == 0:
            season_complete = True
        else:
            season_complete = self._populate_season_events(session, competition, season_label)

        season = get_or_create_competition_season(
            session,
            competition=competition,
            season_number=season_number,
            label=season_label,
            is_complete=season_complete,
        )
        session.flush()

        # Fingerprint deduplication: if we've seen this file content before under a
        # different path AND the old path no longer exists (rename), update the path
        # rather than create a duplicate.
        fingerprint = compute_fingerprint(parsed.source_path)
        if fingerprint:
            fp_match = get_recording_by_fingerprint(session, fingerprint)
            # Only consider it a rename if the old recording isn't published (published files
            # are moved to the library, so their incoming source_path naturally doesn't exist).
            if (
                fp_match is not None
                and fp_match.source_path != str(parsed.source_path)
                and fp_match.status != RecordingStatus.PUBLISHED.value
                and not Path(fp_match.source_path).exists()
            ):
                logger.info(
                    "recording_fingerprint_match_rename old_path=%s new_path=%s recording_id=%s",
                    fp_match.source_path,
                    parsed.source_path,
                    fp_match.id,
                )
                fp_match.source_path = str(parsed.source_path)
                return fp_match

        existing = get_recording_by_source_path(session, str(parsed.source_path))
        title = parsed.title
        if sidecar.title_suffix:
            title = f"{title} {sidecar.title_suffix}".strip()
        kind = sidecar.kind or parsed.kind
        recording = existing or Recording(id=f"rec_{uuid4().hex}")
        recording.competition_season_id = season.id
        recording.kind = kind
        recording.title = title
        recording.air_date = parsed.event_date
        recording.source_path = str(parsed.source_path)
        recording.file_fingerprint = fingerprint
        recording.status = RecordingStatus.STAGED.value
        clear_recording_metadata_snapshot(recording)
        if existing is None:
            session.add(recording)
        session.flush()

        direct_match = self._match_via_sidecar(session, competition, season.id, sidecar)
        if direct_match is not None:
            recording.event_id = direct_match.id
            recording.status = RecordingStatus.PUBLISHED.value
            recording.match_confidence = 1.0
            recording.match_method = "sidecar_event_id"
            recording.match_explanation = build_match_explanation(
                method="sidecar_event_id",
                confidence=1.0,
                event_name=direct_match.name,
            )
            session.flush()
            self._recompute_sequences_for_season(session, season.id)
            self._recompute_recordings_for_event(session, direct_match.id)
            self._publish_recording(session, recording)
            return recording

        if season_number == 0:
            self._ensure_review_task(session, recording, [])
            recording.status = RecordingStatus.REVIEW.value
            recording.match_method = "special_requires_review"
            logger.info("recording_requires_special_review recording_id=%s source_path=%s", recording.id, recording.source_path)
            return recording

        if not season.is_complete:
            self._ensure_review_task(session, recording, [])
            recording.status = RecordingStatus.STAGED.value
            recording.match_method = "season_incomplete"
            logger.info(
                "recording_staged_for_incomplete_season recording_id=%s season_id=%s source_path=%s",
                recording.id,
                season.id,
                recording.source_path,
            )
            return recording

        season_events = list(session.scalars(select(Event).where(Event.competition_season_id == season.id)))
        upstream_events = [self._event_to_upstream(event, competition.name) for event in season_events]
        event_match = match_event(parsed, search_results=prefetched_search_results, season_events=upstream_events)

        if event_match.event is not None:
            matched_event = self._upsert_event(session, season.id, event_match.event)
            recording.event_id = matched_event.id
            recording.match_confidence = event_match.confidence
            recording.match_method = event_match.method
            recording.match_explanation = build_match_explanation(
                method=event_match.method,
                confidence=event_match.confidence,
                event_name=matched_event.name,
                competition_name=competition.name,
            )
            if event_match.confidence >= 0.8:
                recording.status = RecordingStatus.PUBLISHED.value
                session.flush()
                self._recompute_sequences_for_season(session, season.id)
                self._recompute_recordings_for_event(session, matched_event.id)
                self._publish_recording(session, recording)
            else:
                recording.status = RecordingStatus.REVIEW.value
                self._ensure_review_task(session, recording, event_match.candidates)
                logger.info(
                    "recording_sent_to_review recording_id=%s method=%s confidence=%s",
                    recording.id,
                    event_match.method,
                    event_match.confidence,
                )
            return recording

        recording.match_confidence = event_match.confidence
        recording.match_method = event_match.method
        recording.match_explanation = build_match_explanation(
            method=event_match.method,
            confidence=event_match.confidence,
            competition_name=competition.name,
        )
        recording.status = RecordingStatus.REVIEW.value
        self._ensure_review_task(session, recording, event_match.candidates)
        logger.info(
            "recording_sent_to_review recording_id=%s method=%s confidence=%s",
            recording.id,
            event_match.method,
            event_match.confidence,
        )
        return recording

    def _resolve_competition(
        self,
        session: Session,
        show_name: str,
        *,
        search_hints: list[UpstreamEvent] | None = None,
    ) -> Competition:
        # Check learned aliases first — exact (case-insensitive) match wins immediately.
        alias = get_alias_for_text(session, show_name)
        if alias is not None:
            competition = session.get(Competition, alias.competition_id)
            if competition is not None:
                logger.info(
                    "competition_resolved_via_alias show_name=%r alias=%r competition=%s",
                    show_name,
                    alias.alias_text,
                    competition.name,
                )
                return competition

        competitions = list(session.scalars(select(Competition)))
        matched = best_db_competition_match(show_name, competitions)
        if matched is not None:
            if matched.tsdb_id is None and search_hints and self.metadata_source is not None:
                self._try_upgrade_competition(session, matched, search_hints)
            else:
                self._refresh_competition_details(matched)
            return matched

        if self.metadata_source is not None:
            upstream_match = best_upstream_competition_match(show_name, self._all_upstream_competitions())
            if upstream_match is not None:
                rich = None
                if upstream_match.tsdb_id is not None:
                    try:
                        rich = self.metadata_source.lookup_competition(upstream_match.tsdb_id)
                    except Exception:
                        pass
                competition = Competition(
                    id=upstream_match.competition_id or f"manual_{slugify(show_name)}",
                    tsdb_id=upstream_match.tsdb_id,
                    name=upstream_match.name,
                    alternate_names=(rich.alternate_names if rich else upstream_match.alternate_names),
                )
                self._apply_competition_metadata(competition, rich or upstream_match, overwrite_description=True)
                session.add(competition)
                session.flush()
                return competition

            # all_competitions() didn't cover this sport (e.g. free API tier limited to
            # soccer leagues).  Fall back to the competition_tsdb_id carried by search
            # results — each event row from searchfilename.php includes idLeague.
            if search_hints:
                hint_ids = {h.competition_tsdb_id for h in search_hints if h.competition_tsdb_id is not None}
                if len(hint_ids) == 1:
                    tsdb_id = next(iter(hint_ids))
                    existing = session.scalar(select(Competition).where(Competition.tsdb_id == tsdb_id))
                    if existing is not None:
                        self._refresh_competition_details(existing)
                        return existing
                    rich = None
                    try:
                        rich = self.metadata_source.lookup_competition(tsdb_id)
                    except Exception:
                        pass
                    if rich is not None:
                        competition = Competition(
                            id=f"tsdb_{tsdb_id}",
                            tsdb_id=tsdb_id,
                            name=rich.name,
                            alternate_names=rich.alternate_names,
                        )
                        self._apply_competition_metadata(competition, rich, overwrite_description=True)
                        session.add(competition)
                        session.flush()
                        return competition

        manual_id = f"manual_{slugify(show_name)}"
        competition = session.get(Competition, manual_id)
        if competition is None:
            competition = Competition(id=manual_id, name=show_name)
            session.add(competition)
            session.flush()
        return competition

    def _try_upgrade_competition(
        self,
        session: Session,
        competition: Competition,
        search_hints: list[UpstreamEvent],
    ) -> None:
        """Upgrade a manual (no-tsdb_id) competition in-place using idLeague from search results."""
        if self.metadata_source is None:
            return
        hint_ids = {h.competition_tsdb_id for h in search_hints if h.competition_tsdb_id is not None}
        if len(hint_ids) != 1:
            return
        tsdb_id = next(iter(hint_ids))
        # Don't clobber another competition that already owns this tsdb_id.
        conflict = session.scalar(select(Competition).where(Competition.tsdb_id == tsdb_id))
        if conflict is not None and conflict.id != competition.id:
            return
        rich = None
        try:
            rich = self.metadata_source.lookup_competition(tsdb_id)
        except Exception:
            return
        if rich is None:
            return
        competition.tsdb_id = tsdb_id
        self._apply_competition_metadata(competition, rich, overwrite_description=False)
        logger.info(
            "competition_upgraded competition=%s tsdb_id=%s",
            competition.name,
            tsdb_id,
        )

    def _apply_competition_metadata(
        self,
        competition: Competition,
        rich: UpstreamCompetition,
        *,
        overwrite_description: bool,
    ) -> None:
        if rich.alternate_names:
            competition.alternate_names = rich.alternate_names
        if rich.sport:
            competition.sport = rich.sport
        if rich.country:
            competition.country = rich.country
        if rich.formed_year is not None:
            competition.formed_year = rich.formed_year
        if rich.poster_url:
            competition.poster_url = rich.poster_url
        if rich.banner_url:
            competition.banner_url = rich.banner_url
        if rich.fanart_url:
            competition.fanart_url = rich.fanart_url
        if rich.description and (overwrite_description or not competition.description):
            competition.description = rich.description
        if rich.source_payload:
            competition.upstream_metadata = rich.source_payload

    def _refresh_competition_details(self, competition: Competition, *, overwrite: bool = False) -> bool:
        if self.metadata_source is None:
            return False
        if competition.tsdb_id is None:
            return False
        if not overwrite and (competition.poster_url or competition.fanart_url):
            return False
        try:
            rich = self.metadata_source.lookup_competition(competition.tsdb_id)
        except Exception as exc:
            logger.warning(
                "competition_refresh_lookup_failed competition=%s tsdb_id=%s error=%s",
                competition.name,
                competition.tsdb_id,
                exc,
            )
            return False
        if rich is None:
            return False
        self._apply_competition_metadata(competition, rich, overwrite_description=overwrite)
        return True

    def _refresh_competition_metadata(self, session: Session, competition: Competition) -> None:
        self._refresh_competition_details(competition, overwrite=True)
        seasons = list(
            session.scalars(
                select(CompetitionSeason)
                .where(CompetitionSeason.competition_id == competition.id)
                .order_by(CompetitionSeason.season_number.asc())
            )
        )
        if self.metadata_source is not None and competition.tsdb_id is not None:
            upstream_competition = UpstreamCompetition(
                id=competition.id,
                name=competition.name,
                tsdb_id=competition.tsdb_id,
                alternate_names=competition.alternate_names,
            )
            for season in seasons:
                if season.season_number == 0:
                    continue
                try:
                    events, is_complete = self.metadata_source.season_events(upstream_competition, season.label)
                except Exception as exc:
                    logger.warning(
                        "competition_refresh_season_failed competition=%s season=%s error=%s",
                        competition.name,
                        season.label,
                        exc,
                    )
                    continue
                season.is_complete = is_complete
                for event in events:
                    self._upsert_event(session, season.id, event)
                self._recompute_sequences_for_season(session, season.id)
                self._refresh_published_recordings_for_season(session, season.id)

        retry_recordings = list(
            session.scalars(
                select(Recording)
                .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
                .where(
                    CompetitionSeason.competition_id == competition.id,
                    or_(
                        Recording.event_id.is_(None),
                        Recording.status != RecordingStatus.PUBLISHED.value,
                    ),
                )
                .order_by(Recording.created_at.asc(), Recording.id.asc())
            )
        )
        for recording in retry_recordings:
            self._retry_recording_match(session, recording)

        published_recordings = list(
            session.scalars(
                select(Recording)
                .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
                .where(
                    CompetitionSeason.competition_id == competition.id,
                    Recording.status == RecordingStatus.PUBLISHED.value,
                )
            )
        )
        for recording in published_recordings:
            sync_recording_metadata_snapshot(
                session,
                recording=recording,
                metadata_source_name=getattr(self.metadata_source, "name", None),
            )
        logger.info(
            "competition_metadata_refreshed competition=%s season_count=%s retried_recordings=%s",
            competition.name,
            len(seasons),
            len(retry_recordings),
        )

    def _refresh_season_metadata(self, session: Session, season: CompetitionSeason) -> None:
        competition = session.get(Competition, season.competition_id)
        if competition is None:
            raise KeyError(f"Unknown competition: {season.competition_id}")

        self._refresh_competition_details(competition, overwrite=True)
        if self.metadata_source is not None and competition.tsdb_id is not None and season.season_number != 0:
            upstream_competition = UpstreamCompetition(
                id=competition.id,
                name=competition.name,
                tsdb_id=competition.tsdb_id,
                alternate_names=competition.alternate_names,
            )
            try:
                events, is_complete = self.metadata_source.season_events(upstream_competition, season.label)
            except Exception as exc:
                logger.warning(
                    "season_refresh_failed competition=%s season=%s error=%s",
                    competition.name,
                    season.label,
                    exc,
                )
            else:
                season.is_complete = is_complete
                for event in events:
                    self._upsert_event(session, season.id, event)

        self._recompute_sequences_for_season(session, season.id)
        self._refresh_published_recordings_for_season(session, season.id)

        retry_recordings = list(
            session.scalars(
                select(Recording)
                .where(
                    Recording.competition_season_id == season.id,
                    or_(
                        Recording.event_id.is_(None),
                        Recording.status != RecordingStatus.PUBLISHED.value,
                    ),
                )
                .order_by(Recording.created_at.asc(), Recording.id.asc())
            )
        )
        for recording in retry_recordings:
            self._retry_recording_match(session, recording)

        published_recordings = list(
            session.scalars(
                select(Recording).where(
                    Recording.competition_season_id == season.id,
                    Recording.status == RecordingStatus.PUBLISHED.value,
                )
            )
        )
        for recording in published_recordings:
            sync_recording_metadata_snapshot(
                session,
                recording=recording,
                metadata_source_name=getattr(self.metadata_source, "name", None),
            )

        logger.info(
            "season_metadata_refreshed competition=%s season=%s retried_recordings=%s",
            competition.name,
            season.label,
            len(retry_recordings),
        )

    def _refresh_event_metadata(self, session: Session, event: Event) -> Event:
        current_season = session.get(CompetitionSeason, event.competition_season_id)
        if current_season is None:
            raise KeyError(f"Unknown season: {event.competition_season_id}")
        current_competition = session.get(Competition, current_season.competition_id)
        if current_competition is None:
            raise KeyError(f"Unknown competition: {current_season.competition_id}")

        original_season_id = event.competition_season_id
        if self.metadata_source is not None and event.tsdb_id is not None:
            upstream = self.metadata_source.lookup_event(event.tsdb_id)
            if upstream is not None:
                competition = current_competition
                if upstream.competition_name and upstream.competition_name != current_competition.name:
                    competition = self._resolve_competition(session, upstream.competition_name)
                if upstream.date is not None:
                    season_number, season_label = season_for_date(upstream.date, competition)
                    season = get_or_create_competition_season(
                        session,
                        competition=competition,
                        season_number=season_number,
                        label=season_label,
                        is_complete=True,
                    )
                    session.flush()
                else:
                    season = current_season
                event = self._upsert_event(session, season.id, upstream)
                current_competition = competition

        attached_recordings = list(session.scalars(select(Recording).where(Recording.event_id == event.id)))
        for recording in attached_recordings:
            if recording.competition_season_id == original_season_id:
                recording.competition_season_id = event.competition_season_id

        affected_seasons = {original_season_id, event.competition_season_id}
        for season_id in sorted(affected_seasons):
            self._recompute_sequences_for_season(session, season_id)
            self._refresh_published_recordings_for_season(session, season_id)

        self._refresh_competition_details(current_competition, overwrite=True)
        for recording in attached_recordings:
            sync_recording_metadata_snapshot(
                session,
                recording=recording,
                metadata_source_name=getattr(self.metadata_source, "name", None),
            )

        logger.info(
            "event_metadata_refreshed competition=%s event_id=%s season_id=%s recording_count=%s",
            current_competition.name,
            event.id,
            event.competition_season_id,
            len(attached_recordings),
        )
        return event

    def _refresh_recording_metadata(self, session: Session, recording: Recording) -> Recording:
        event = session.get(Event, recording.event_id) if recording.event_id else None
        current_season_id = recording.competition_season_id
        if self.metadata_source is not None and event is not None and event.tsdb_id is not None:
            try:
                refreshed_event = self._resolve_review_lookup_event(session, recording, event.tsdb_id)
            except Exception as exc:
                logger.warning(
                    "recording_refresh_lookup_failed recording_id=%s tsdb_id=%s error=%s",
                    recording.id,
                    event.tsdb_id,
                    exc,
                )
            else:
                recording.event_id = refreshed_event.id
                recording.competition_season_id = refreshed_event.competition_season_id
                recording.status = RecordingStatus.PUBLISHED.value
                session.flush()

                affected_seasons = {current_season_id, refreshed_event.competition_season_id}
                for season_id in sorted(affected_seasons):
                    self._recompute_sequences_for_season(session, season_id)
                    self._refresh_published_recordings_for_season(session, season_id)

                refreshed_season = session.get(CompetitionSeason, recording.competition_season_id)
                refreshed_competition = (
                    session.get(Competition, refreshed_season.competition_id) if refreshed_season is not None else None
                )
                if refreshed_competition is not None:
                    self._refresh_competition_details(refreshed_competition, overwrite=True)
                sync_recording_metadata_snapshot(
                    session,
                    recording=recording,
                    metadata_source_name=getattr(self.metadata_source, "name", None),
                )
                logger.info(
                    "recording_metadata_refreshed recording_id=%s method=lookup_event event_id=%s",
                    recording.id,
                    recording.event_id,
                )
                return recording

        refreshed = self._retry_recording_match(session, recording)
        logger.info(
            "recording_metadata_refreshed recording_id=%s method=reingest event_id=%s status=%s",
            refreshed.id,
            refreshed.event_id,
            refreshed.status,
        )
        return refreshed

    def _retry_recording_match(self, session: Session, recording: Recording) -> Recording:
        parsed = parse_filename(recording.source_path)
        sidecar = read_sidecar(parsed.source_path)
        refreshed = self._upsert_recording_from_path(session, parsed, sidecar)
        competition_season = session.get(CompetitionSeason, refreshed.competition_season_id)
        competition = session.get(Competition, competition_season.competition_id) if competition_season else None
        if competition is not None:
            self._refresh_competition_details(competition, overwrite=True)
            if refreshed.status == RecordingStatus.PUBLISHED.value:
                sync_recording_metadata_snapshot(
                    session,
                    recording=refreshed,
                    metadata_source_name=getattr(self.metadata_source, "name", None),
                )
        return refreshed

    def _apply_competition_config(self, competition: Competition, source_path: Path) -> None:
        config = read_competition_config(source_path)
        if not config:
            return
        if config.get("season_pattern"):
            competition.season_pattern = config["season_pattern"]
        if config.get("season_split_month"):
            competition.season_split_month = int(config["season_split_month"])
        if config.get("season_split_day"):
            competition.season_split_day = int(config["season_split_day"])
        if config.get("event_order"):
            raw = config["event_order"].lower()
            valid_orders = {item.value for item in EventOrder}
            if raw in valid_orders:
                competition.event_order = raw
            else:
                logger.warning(
                    "competition_config_invalid_event_order competition=%s value=%s valid=%s",
                    competition.name,
                    raw,
                    sorted(valid_orders),
                )

    def _resolve_season(self, parsed: ParsedFile, competition: Competition, sidecar: SidecarMetadata) -> tuple[int, str]:
        if sidecar.season is not None:
            return (sidecar.season, "0" if sidecar.season == 0 else str(sidecar.season))
        if parsed.event_date is None:
            return (0, "0")
        return season_for_date(parsed.event_date, competition)

    def _match_via_sidecar(self, session: Session, competition: Competition, season_id: str, sidecar: SidecarMetadata) -> Event | None:
        if sidecar.tsdb_event_id is None:
            return None
        existing = session.scalar(select(Event).where(Event.tsdb_id == sidecar.tsdb_event_id))
        if existing is not None:
            return existing
        if self.metadata_source is None:
            return None
        upstream = self.metadata_source.lookup_event(sidecar.tsdb_event_id)
        if upstream is None:
            return None
        return self._upsert_event(session, season_id, upstream)

    def _populate_season_events(self, session: Session, competition: Competition, season_label: str) -> bool:
        if self.metadata_source is None or competition.tsdb_id is None:
            return False
        upstream_competition = UpstreamCompetition(
            id=competition.id,
            name=competition.name,
            tsdb_id=competition.tsdb_id,
            alternate_names=competition.alternate_names,
        )
        events, is_complete = self.metadata_source.season_events(upstream_competition, season_label)
        if not is_complete:
            logger.info("season_events_incomplete competition=%s season_label=%s", competition.name, season_label)
            return False
        season_number = int(season_label.split("-")[0]) if "-" in season_label else int(season_label)
        season = get_or_create_competition_season(
            session,
            competition=competition,
            season_number=season_number,
            label=season_label,
            is_complete=True,
        )
        session.flush()
        for event in events:
            self._upsert_event(session, season.id, event)
        self._recompute_sequences_for_season(session, season.id)
        self._refresh_published_recordings_for_season(session, season.id)
        logger.info(
            "season_events_synced competition=%s season_id=%s event_count=%s",
            competition.name,
            season.id,
            len(events),
        )
        return True

    def _upsert_event(self, session: Session, competition_season_id: str, upstream: UpstreamEvent) -> Event:
        event = None
        if upstream.tsdb_id is not None:
            event = session.scalar(select(Event).where(Event.tsdb_id == upstream.tsdb_id))
        if event is None:
            event = Event(id=upstream.id if upstream.id.startswith("tsdb_") else f"manual_{uuid4().hex}")
            session.add(event)
        event_name = sanitize_event_name(upstream.name) or "Unknown Event"
        event.tsdb_id = upstream.tsdb_id
        event.competition_season_id = competition_season_id
        event.origin = EventOrigin.UPSTREAM.value
        event.name = event_name
        event.date = upstream.date
        event.time = upstream.time
        event.round = upstream.round
        event.venue = upstream.venue
        event.city = upstream.city
        event.country = upstream.country
        event.home_team = upstream.home_team
        event.away_team = upstream.away_team
        event.home_score = upstream.home_score
        event.away_score = upstream.away_score
        event.description = upstream.description
        event.thumb_url = upstream.thumb_url
        if upstream.source_payload:
            event.upstream_metadata = upstream.source_payload
        event.weekend_group = upstream.weekend_group or derive_weekend_group(event_name)
        session.flush()
        return event

    def _event_to_upstream(self, event: Event, competition_name: str) -> UpstreamEvent:
        return UpstreamEvent(
            id=event.id,
            tsdb_id=event.tsdb_id,
            name=event.name,
            competition_name=competition_name,
            date=event.date,
            time=event.time,
            round=event.round,
            venue=event.venue,
            city=event.city,
            country=event.country,
            home_team=event.home_team,
            away_team=event.away_team,
            home_score=event.home_score,
            away_score=event.away_score,
            description=event.description,
            thumb_url=event.thumb_url,
            weekend_group=event.weekend_group,
        )

    def _recompute_sequences_for_season(self, session: Session, competition_season_id: str) -> None:
        season = session.get(CompetitionSeason, competition_season_id)
        if season is None:
            return
        competition = session.get(Competition, season.competition_id)
        if competition is None:
            return
        events = list(session.scalars(select(Event).where(Event.competition_season_id == competition_season_id)))
        sequence_map = compute_event_sequences(
            [
                EventSequenceInput(
                    event_id=event.id,
                    name=event.name,
                    event_date=event.date,
                    event_time=event.time,
                    round_number=event.round,
                    weekend_group=event.weekend_group,
                )
                for event in events
            ],
            competition.event_order,
        )
        for event in events:
            event.event_sequence = sequence_map.get(event.id)

    def _recompute_recordings_for_event(self, session: Session, event_id: str) -> None:
        event = session.get(Event, event_id)
        if event is None or event.event_sequence is None:
            return
        recordings = list(session.scalars(select(Recording).where(Recording.event_id == event_id)))
        code_map = compute_recording_codes(
            [
                RecordingCodeInput(
                    recording_id=recording.id,
                    kind=recording.kind,
                    title=recording.title,
                    source_path=recording.source_path,
                )
                for recording in recordings
            ]
        )
        for recording in recordings:
            if recording.status == RecordingStatus.PUBLISHED.value and recording.episode_number is not None:
                continue  # freeze episode numbers for published recordings
            recording.recording_code = code_map[recording.id]
            recording.episode_number = episode_number(event.event_sequence, recording.recording_code)

    def _ensure_review_task(self, session: Session, recording: Recording, candidates: list[dict]) -> None:
        task = session.scalar(
            select(ReviewTask).where(
                ReviewTask.recording_id == recording.id,
                ReviewTask.status == ReviewTaskStatus.OPEN.value,
            )
        )
        if task is None:
            task = ReviewTask(recording_id=recording.id, task_type="match_review")
            session.add(task)
        task.candidates = candidates
        task.status = ReviewTaskStatus.OPEN.value
        logger.info("review_task_open recording_id=%s candidate_count=%s", recording.id, len(candidates))

    def _refresh_published_recordings_for_season(self, session: Session, competition_season_id: str) -> None:
        season = session.get(CompetitionSeason, competition_season_id)
        if season is None:
            return
        competition = session.get(Competition, season.competition_id)
        if competition is None:
            return
        published_recordings = list(
            session.scalars(
                select(Recording).where(
                    Recording.competition_season_id == competition_season_id,
                    Recording.status == RecordingStatus.PUBLISHED.value,
                )
            )
        )
        event_ids = sorted({recording.event_id for recording in published_recordings if recording.event_id})
        for event_id in event_ids:
            self._recompute_recordings_for_event(session, event_id)
        for recording in published_recordings:
            self._publish_recording(session, recording)
        guid_prefix = self._effective_plex_provider_identifier(session)
        show_dir = self._effective_directory(session, "library_dir", self.settings.library_dir) / competition.name
        season_dir = show_dir / season_directory_name(season.season_number)
        write_atomic_if_changed(
            season_dir / ".plexmatch",
            render_season_plexmatch(competition, season, published_recordings, guid_prefix),
        )
        logger.info(
            "season_publish_reconciled season_id=%s published_recording_count=%s",
            competition_season_id,
            len(published_recordings),
        )

    def _publish_recording(self, session: Session, recording: Recording) -> None:
        if recording.event_id is None:
            clear_recording_metadata_snapshot(recording)
            return
        event = session.get(Event, recording.event_id)
        season = session.get(CompetitionSeason, recording.competition_season_id)
        if event is None or season is None:
            clear_recording_metadata_snapshot(recording)
            return
        competition = session.get(Competition, season.competition_id)
        if competition is None:
            clear_recording_metadata_snapshot(recording)
            return

        show_dir = self._effective_directory(session, "library_dir", self.settings.library_dir) / competition.name
        season_dir = show_dir / season_directory_name(season.season_number)
        destination = season_dir / build_managed_filename(
            competition_name=competition.name,
            air_date=recording.air_date,
            title=recording.title,
            source_path=recording.source_path,
        )
        existing_managed = Path(recording.managed_path) if recording.managed_path else None
        source_path = Path(recording.source_path)
        if destination.exists():
            recording.managed_path = str(destination)
        elif existing_managed is not None and existing_managed.exists():
            if existing_managed != destination:
                recording.managed_path = move_managed_file(existing_managed, destination)
            else:
                recording.managed_path = str(destination)
        elif source_path.exists():
            recording.managed_path = place_file(recording.source_path, destination)
        else:
            logger.warning(
                "recording_publish_source_missing recording_id=%s source_path=%s managed_path=%s",
                recording.id,
                recording.source_path,
                recording.managed_path,
            )
            recording.managed_path = str(destination)
        sync_recording_metadata_snapshot(
            session,
            recording=recording,
            metadata_source_name=getattr(self.metadata_source, "name", None),
        )
        published_recordings = list(
            session.scalars(
                select(Recording).where(
                    Recording.competition_season_id == season.id,
                    Recording.status == RecordingStatus.PUBLISHED.value,
                )
            )
        )
        guid_prefix = self._effective_plex_provider_identifier(session)
        write_atomic_if_changed(show_dir / ".plexmatch", render_show_plexmatch(competition, guid_prefix))
        write_atomic_if_changed(
            season_dir / ".plexmatch",
            render_season_plexmatch(competition, season, published_recordings, guid_prefix),
        )
        logger.info(
            "recording_published recording_id=%s season_id=%s event_id=%s managed_path=%s episode_number=%s",
            recording.id,
            season.id,
            event.id,
            recording.managed_path,
            recording.episode_number,
        )

    def _publish_as_special(self, session: Session, recording: Recording) -> Recording:
        season = session.get(CompetitionSeason, recording.competition_season_id)
        if season is None:
            raise KeyError(f"Unknown season: {recording.competition_season_id}")
        competition = session.get(Competition, season.competition_id)
        if competition is None:
            raise KeyError(f"Unknown competition: {season.competition_id}")
        season0 = get_or_create_competition_season(
            session,
            competition=competition,
            season_number=0,
            label="0",
            is_complete=True,
        )
        session.flush()
        event = Event(
            id=f"manual_{uuid4().hex}",
            competition_season_id=season0.id,
            origin=EventOrigin.MANUAL.value,
            name=recording.title,
            date=recording.air_date,
            time=recording.air_time,
        )
        session.add(event)
        session.flush()
        recording.competition_season_id = season0.id
        recording.event_id = event.id
        recording.status = RecordingStatus.PUBLISHED.value
        recording.match_confidence = 1.0
        recording.match_method = "manual_special"
        session.flush()
        self._recompute_sequences_for_season(session, season0.id)
        self._recompute_recordings_for_event(session, event.id)
        self._publish_recording(session, recording)
        return recording
