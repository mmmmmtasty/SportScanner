from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from sportscanner.config import Settings
from sportscanner.db.models import Competition, CompetitionSeason, Event, EventOrder, EventOrigin, ReviewTask, ReviewTaskStatus, Segment, SegmentStatus
from sportscanner.db.queries import get_or_create_competition_season, get_segment_by_source_path
from sportscanner.metadata_snapshot import clear_segment_metadata_snapshot, sync_segment_metadata_snapshot
from sportscanner.organizer.matcher import best_db_competition_match, best_upstream_competition_match, match_event, season_for_date
from sportscanner.organizer.numbering import EventSequenceInput, SegmentCodeInput, compute_event_sequences, compute_segment_codes, derive_weekend_group, episode_number
from sportscanner.organizer.parser import ParsedFile, is_media_file, parse_filename
from sportscanner.organizer.placer import build_managed_filename, place_file, season_directory_name
from sportscanner.organizer.plexmatch import render_season_plexmatch, render_show_plexmatch, write_atomic_if_changed
from sportscanner.upstream.base import MetadataSource, UpstreamCompetition, UpstreamEvent

try:
    import yaml
except ImportError:  # pragma: no cover - optional fallback for minimal environments
    yaml = None

logger = logging.getLogger("sportscanner.organizer.service")


@dataclass(slots=True)
class SidecarMetadata:
    competition: str | None = None
    season: int | None = None
    event: str | None = None
    segment_kind: str | None = None
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
        segment_kind=data.get("segment_kind"),
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

    def rescan_incoming(self) -> list[str]:
        with self._ingest_lock:
            processed: list[str] = []
            incoming = self.settings.incoming_dir
            if not incoming.exists():
                logger.info("rescan_skipped incoming_dir_missing=%s", incoming)
                return processed
            logger.info("rescan_started incoming_dir=%s", incoming)
            for path in sorted(incoming.rglob("*")):
                if path.is_file() and is_media_file(path):
                    if self.ingest_path_if_parseable(path) is not None:
                        processed.append(str(path))
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

    def refresh_segment_metadata(self, segment_id: str) -> Segment:
        with self._ingest_lock:
            with self.session_factory() as session:
                segment = session.get(Segment, segment_id)
                if segment is None:
                    raise KeyError(f"Unknown segment: {segment_id}")
                self._refresh_segment_metadata(session, segment)
                session.commit()
                refreshed = session.get(Segment, segment_id)
                assert refreshed is not None
                return refreshed

    def ingest_path_if_parseable(self, source_path: str | Path) -> Segment | None:
        try:
            return self.ingest_path(source_path)
        except ValueError as exc:
            logger.warning("ingest_skipped_unparsable source_path=%s error=%s", source_path, exc)
            return None

    def ingest_path(self, source_path: str | Path) -> Segment:
        with self._ingest_lock:
            logger.info("ingest_started source_path=%s", source_path)
            parsed = parse_filename(source_path)
            sidecar = read_sidecar(parsed.source_path)
            with self.session_factory() as session:
                segment = self._upsert_segment_from_path(session, parsed, sidecar)
                session.commit()
                segment_id = segment.id
            with self.session_factory() as session:
                result = session.get(Segment, segment_id)
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
    ) -> Segment:
        with self.session_factory() as session:
            task = session.get(ReviewTask, task_id)
            if task is None:
                raise KeyError(f"Unknown review task: {task_id}")
            segment = session.get(Segment, task.segment_id)
            if segment is None:
                raise KeyError(f"Unknown segment for review task: {task.segment_id}")

            if publish_as_special:
                segment = self._publish_as_special(session, segment)
                task.resolution = {"type": "special", "event_id": segment.event_id}
            elif tsdb_event_id is not None:
                event = self._resolve_review_lookup_event(session, segment, tsdb_event_id)
                segment = self._assign_segment_to_event(
                    session,
                    segment,
                    event,
                    match_method="manual_lookup_event_id",
                )
                task.resolution = {"type": "tsdb_event", "event_id": event.id, "tsdb_event_id": tsdb_event_id}
            elif event_id:
                event = session.get(Event, event_id)
                if event is None:
                    raise KeyError(f"Unknown event: {event_id}")
                segment = self._assign_segment_to_event(
                    session,
                    segment,
                    event,
                    match_method="manual_resolution",
                )
                task.resolution = {"type": "event", "event_id": event.id}
            else:
                segment.status = SegmentStatus.IGNORED.value
                clear_segment_metadata_snapshot(segment)
                task.resolution = {"type": "ignored"}

            task.status = ReviewTaskStatus.RESOLVED.value
            session.commit()
            refreshed = session.get(Segment, segment.id)
            assert refreshed is not None
            logger.info(
                "review_task_resolved task_id=%s resolution_type=%s segment_id=%s event_id=%s",
                task_id,
                task.resolution.get("type"),
                segment.id,
                segment.event_id,
            )
            return refreshed

    def _assign_segment_to_event(
        self,
        session: Session,
        segment: Segment,
        event: Event,
        *,
        match_method: str,
    ) -> Segment:
        segment.event_id = event.id
        segment.competition_season_id = event.competition_season_id
        segment.status = SegmentStatus.PUBLISHED.value
        segment.match_confidence = 1.0
        segment.match_method = match_method
        session.flush()
        self._recompute_sequences_for_season(session, event.competition_season_id)
        self._recompute_segments_for_event(session, event.id)
        self._publish_segment(session, segment)
        return segment

    def _resolve_review_lookup_event(self, session: Session, segment: Segment, tsdb_event_id: int) -> Event:
        if self.metadata_source is None:
            raise ValueError("Metadata source is not configured")
        upstream = self.metadata_source.lookup_event(tsdb_event_id)
        if upstream is None:
            raise KeyError(f"Unknown upstream event: {tsdb_event_id}")

        current_season = session.get(CompetitionSeason, segment.competition_season_id)
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

    def _upsert_segment_from_path(self, session: Session, parsed: ParsedFile, sidecar: SidecarMetadata) -> Segment:
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

        existing = get_segment_by_source_path(session, str(parsed.source_path))
        title = parsed.title
        if sidecar.title_suffix:
            title = f"{title} {sidecar.title_suffix}".strip()
        kind = sidecar.segment_kind or parsed.segment_kind
        segment = existing or Segment(id=f"seg_{uuid4().hex}")
        segment.competition_season_id = season.id
        segment.kind = kind
        segment.title = title
        segment.air_date = parsed.event_date
        segment.source_path = str(parsed.source_path)
        segment.status = SegmentStatus.STAGED.value
        clear_segment_metadata_snapshot(segment)
        if existing is None:
            session.add(segment)
        session.flush()

        direct_match = self._match_via_sidecar(session, competition, season.id, sidecar)
        if direct_match is not None:
            segment.event_id = direct_match.id
            segment.status = SegmentStatus.PUBLISHED.value
            segment.match_confidence = 1.0
            segment.match_method = "sidecar_event_id"
            session.flush()
            self._recompute_sequences_for_season(session, season.id)
            self._recompute_segments_for_event(session, direct_match.id)
            self._publish_segment(session, segment)
            return segment

        if season_number == 0:
            self._ensure_review_task(session, segment, [])
            segment.status = SegmentStatus.REVIEW.value
            segment.match_method = "special_requires_review"
            logger.info("segment_requires_special_review segment_id=%s source_path=%s", segment.id, segment.source_path)
            return segment

        if not season.is_complete:
            self._ensure_review_task(session, segment, [])
            segment.status = SegmentStatus.STAGED.value
            segment.match_method = "season_incomplete"
            logger.info(
                "segment_staged_for_incomplete_season segment_id=%s season_id=%s source_path=%s",
                segment.id,
                season.id,
                segment.source_path,
            )
            return segment

        season_events = list(session.scalars(select(Event).where(Event.competition_season_id == season.id)))
        upstream_events = [self._event_to_upstream(event, competition.name) for event in season_events]
        event_match = match_event(parsed, search_results=prefetched_search_results, season_events=upstream_events)

        if event_match.event is not None:
            matched_event = self._upsert_event(session, season.id, event_match.event)
            segment.event_id = matched_event.id
            segment.match_confidence = event_match.confidence
            segment.match_method = event_match.method
            if event_match.confidence >= 0.8:
                segment.status = SegmentStatus.PUBLISHED.value
                session.flush()
                self._recompute_sequences_for_season(session, season.id)
                self._recompute_segments_for_event(session, matched_event.id)
                self._publish_segment(session, segment)
            else:
                segment.status = SegmentStatus.REVIEW.value
                self._ensure_review_task(session, segment, event_match.candidates)
                logger.info(
                    "segment_sent_to_review segment_id=%s method=%s confidence=%s",
                    segment.id,
                    event_match.method,
                    event_match.confidence,
                )
            return segment

        segment.match_confidence = event_match.confidence
        segment.match_method = event_match.method
        segment.status = SegmentStatus.REVIEW.value
        self._ensure_review_task(session, segment, event_match.candidates)
        logger.info(
            "segment_sent_to_review segment_id=%s method=%s confidence=%s",
            segment.id,
            event_match.method,
            event_match.confidence,
        )
        return segment

    def _resolve_competition(
        self,
        session: Session,
        show_name: str,
        *,
        search_hints: list[UpstreamEvent] | None = None,
    ) -> Competition:
        competitions = list(session.scalars(select(Competition)))
        matched = best_db_competition_match(show_name, competitions)
        if matched is not None:
            if matched.tsdb_id is None and search_hints and self.metadata_source is not None:
                self._try_upgrade_competition(session, matched, search_hints)
            else:
                self._refresh_competition_details(matched)
            return matched

        if self.metadata_source is not None:
            upstream_match = best_upstream_competition_match(show_name, self.metadata_source.all_competitions())
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
                    description=rich.description if rich else None,
                    poster_url=rich.poster_url if rich else None,
                    banner_url=rich.banner_url if rich else None,
                    fanart_url=rich.fanart_url if rich else None,
                )
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
                            description=rich.description,
                            poster_url=rich.poster_url,
                            banner_url=rich.banner_url,
                            fanart_url=rich.fanart_url,
                        )
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
        if not competition.poster_url and rich.poster_url:
            competition.poster_url = rich.poster_url
        if not competition.banner_url and rich.banner_url:
            competition.banner_url = rich.banner_url
        if not competition.fanart_url and rich.fanart_url:
            competition.fanart_url = rich.fanart_url
        if not competition.description and rich.description:
            competition.description = rich.description
        logger.info(
            "competition_upgraded competition=%s tsdb_id=%s",
            competition.name,
            tsdb_id,
        )

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
        if rich.description and (overwrite or not competition.description):
            competition.description = rich.description
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
                self._refresh_published_segments_for_season(session, season.id)

        retry_segments = list(
            session.scalars(
                select(Segment)
                .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
                .where(
                    CompetitionSeason.competition_id == competition.id,
                    or_(
                        Segment.event_id.is_(None),
                        Segment.status != SegmentStatus.PUBLISHED.value,
                    ),
                )
                .order_by(Segment.created_at.asc(), Segment.id.asc())
            )
        )
        for segment in retry_segments:
            self._retry_segment_match(session, segment)

        published_segments = list(
            session.scalars(
                select(Segment)
                .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
                .where(
                    CompetitionSeason.competition_id == competition.id,
                    Segment.status == SegmentStatus.PUBLISHED.value,
                )
            )
        )
        for segment in published_segments:
            sync_segment_metadata_snapshot(
                session,
                segment=segment,
                metadata_source_name=getattr(self.metadata_source, "name", None),
            )
        logger.info(
            "competition_metadata_refreshed competition=%s season_count=%s retried_segments=%s",
            competition.name,
            len(seasons),
            len(retry_segments),
        )

    def _refresh_segment_metadata(self, session: Session, segment: Segment) -> Segment:
        event = session.get(Event, segment.event_id) if segment.event_id else None
        current_season_id = segment.competition_season_id
        if self.metadata_source is not None and event is not None and event.tsdb_id is not None:
            try:
                refreshed_event = self._resolve_review_lookup_event(session, segment, event.tsdb_id)
            except Exception as exc:
                logger.warning(
                    "segment_refresh_lookup_failed segment_id=%s tsdb_id=%s error=%s",
                    segment.id,
                    event.tsdb_id,
                    exc,
                )
            else:
                segment.event_id = refreshed_event.id
                segment.competition_season_id = refreshed_event.competition_season_id
                segment.status = SegmentStatus.PUBLISHED.value
                session.flush()

                affected_seasons = {current_season_id, refreshed_event.competition_season_id}
                for season_id in sorted(affected_seasons):
                    self._recompute_sequences_for_season(session, season_id)
                    self._refresh_published_segments_for_season(session, season_id)

                refreshed_season = session.get(CompetitionSeason, segment.competition_season_id)
                refreshed_competition = (
                    session.get(Competition, refreshed_season.competition_id) if refreshed_season is not None else None
                )
                if refreshed_competition is not None:
                    self._refresh_competition_details(refreshed_competition, overwrite=True)
                sync_segment_metadata_snapshot(
                    session,
                    segment=segment,
                    metadata_source_name=getattr(self.metadata_source, "name", None),
                )
                logger.info(
                    "segment_metadata_refreshed segment_id=%s method=lookup_event event_id=%s",
                    segment.id,
                    segment.event_id,
                )
                return segment

        refreshed = self._retry_segment_match(session, segment)
        logger.info(
            "segment_metadata_refreshed segment_id=%s method=reingest event_id=%s status=%s",
            refreshed.id,
            refreshed.event_id,
            refreshed.status,
        )
        return refreshed

    def _retry_segment_match(self, session: Session, segment: Segment) -> Segment:
        parsed = parse_filename(segment.source_path)
        sidecar = read_sidecar(parsed.source_path)
        refreshed = self._upsert_segment_from_path(session, parsed, sidecar)
        competition_season = session.get(CompetitionSeason, refreshed.competition_season_id)
        competition = session.get(Competition, competition_season.competition_id) if competition_season else None
        if competition is not None:
            self._refresh_competition_details(competition, overwrite=True)
            if refreshed.status == SegmentStatus.PUBLISHED.value:
                sync_segment_metadata_snapshot(
                    session,
                    segment=refreshed,
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
        self._refresh_published_segments_for_season(session, season.id)
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
        event.tsdb_id = upstream.tsdb_id
        event.competition_season_id = competition_season_id
        event.origin = EventOrigin.UPSTREAM.value
        event.name = upstream.name
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
        event.weekend_group = upstream.weekend_group or derive_weekend_group(upstream.name)
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

    def _recompute_segments_for_event(self, session: Session, event_id: str) -> None:
        event = session.get(Event, event_id)
        if event is None or event.event_sequence is None:
            return
        segments = list(session.scalars(select(Segment).where(Segment.event_id == event_id)))
        code_map = compute_segment_codes(
            [
                SegmentCodeInput(
                    segment_id=segment.id,
                    kind=segment.kind,
                    title=segment.title,
                    source_path=segment.source_path,
                )
                for segment in segments
            ]
        )
        for segment in segments:
            segment.segment_code = code_map[segment.id]
            segment.episode_number = episode_number(event.event_sequence, segment.segment_code)

    def _ensure_review_task(self, session: Session, segment: Segment, candidates: list[dict]) -> None:
        task = session.scalar(
            select(ReviewTask).where(
                ReviewTask.segment_id == segment.id,
                ReviewTask.status == ReviewTaskStatus.OPEN.value,
            )
        )
        if task is None:
            task = ReviewTask(segment_id=segment.id, task_type="match_review")
            session.add(task)
        task.candidates = candidates
        task.status = ReviewTaskStatus.OPEN.value
        logger.info("review_task_open segment_id=%s candidate_count=%s", segment.id, len(candidates))

    def _refresh_published_segments_for_season(self, session: Session, competition_season_id: str) -> None:
        published_segments = list(
            session.scalars(
                select(Segment).where(
                    Segment.competition_season_id == competition_season_id,
                    Segment.status == SegmentStatus.PUBLISHED.value,
                )
            )
        )
        if not published_segments:
            return
        event_ids = sorted({segment.event_id for segment in published_segments if segment.event_id})
        for event_id in event_ids:
            self._recompute_segments_for_event(session, event_id)
        for segment in published_segments:
            self._publish_segment(session, segment)
        logger.info(
            "season_publish_reconciled season_id=%s published_segment_count=%s",
            competition_season_id,
            len(published_segments),
        )

    def _publish_segment(self, session: Session, segment: Segment) -> None:
        if segment.event_id is None:
            clear_segment_metadata_snapshot(segment)
            return
        event = session.get(Event, segment.event_id)
        season = session.get(CompetitionSeason, segment.competition_season_id)
        if event is None or season is None:
            clear_segment_metadata_snapshot(segment)
            return
        competition = session.get(Competition, season.competition_id)
        if competition is None:
            clear_segment_metadata_snapshot(segment)
            return

        show_dir = self.settings.library_dir / competition.name
        season_dir = show_dir / season_directory_name(season.season_number)
        destination = season_dir / build_managed_filename(
            competition_name=competition.name,
            air_date=segment.air_date,
            title=segment.title,
            source_path=segment.source_path,
        )
        existing_managed = Path(segment.managed_path) if segment.managed_path else None
        source_path = Path(segment.source_path)
        if destination.exists():
            segment.managed_path = str(destination)
        elif existing_managed is not None and existing_managed.exists():
            if existing_managed != destination:
                destination.parent.mkdir(parents=True, exist_ok=True)
                existing_managed.replace(destination)
            segment.managed_path = str(destination)
        elif source_path.exists():
            segment.managed_path = place_file(segment.source_path, destination)
        else:
            logger.warning(
                "segment_publish_source_missing segment_id=%s source_path=%s managed_path=%s",
                segment.id,
                segment.source_path,
                segment.managed_path,
            )
            segment.managed_path = segment.managed_path or str(destination)
        sync_segment_metadata_snapshot(
            session,
            segment=segment,
            metadata_source_name=getattr(self.metadata_source, "name", None),
        )
        published_segments = list(
            session.scalars(
                select(Segment).where(
                    Segment.competition_season_id == season.id,
                    Segment.status == SegmentStatus.PUBLISHED.value,
                )
            )
        )
        guid_prefix = self.settings.plex_provider_identifier
        write_atomic_if_changed(show_dir / ".plexmatch", render_show_plexmatch(competition, guid_prefix))
        write_atomic_if_changed(
            season_dir / ".plexmatch",
            render_season_plexmatch(competition, season, published_segments, guid_prefix),
        )
        logger.info(
            "segment_published segment_id=%s season_id=%s event_id=%s managed_path=%s episode_number=%s",
            segment.id,
            season.id,
            event.id,
            segment.managed_path,
            segment.episode_number,
        )

    def _publish_as_special(self, session: Session, segment: Segment) -> Segment:
        season = session.get(CompetitionSeason, segment.competition_season_id)
        if season is None:
            raise KeyError(f"Unknown season: {segment.competition_season_id}")
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
            name=segment.title,
            date=segment.air_date,
            time=segment.air_time,
        )
        session.add(event)
        session.flush()
        segment.competition_season_id = season0.id
        segment.event_id = event.id
        segment.status = SegmentStatus.PUBLISHED.value
        segment.match_confidence = 1.0
        segment.match_method = "manual_special"
        session.flush()
        self._recompute_sequences_for_season(session, season0.id)
        self._recompute_segments_for_event(session, event.id)
        self._publish_segment(session, segment)
        return segment
