from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from sportscanner.db.models import (
    Competition,
    CompetitionAlias,
    CompetitionAliasSource,
    CompetitionSeason,
    Event,
    PlexRefreshJob,
    PlexRefreshJobStatus,
    Recording,
    RecordingStatus,
    ReviewTask,
)


def get_or_create_competition_season(
    session: Session,
    *,
    competition: Competition,
    season_number: int,
    label: str,
    is_complete: bool = True,
) -> CompetitionSeason:
    existing = session.scalar(
        select(CompetitionSeason).where(
            CompetitionSeason.competition_id == competition.id,
            CompetitionSeason.season_number == season_number,
        )
    )
    if existing is not None:
        existing.label = label
        existing.is_complete = is_complete
        return existing

    season = CompetitionSeason(
        id=f"season_{competition.id}_{season_number}",
        competition_id=competition.id,
        season_number=season_number,
        label=label,
        is_complete=is_complete,
    )
    session.add(season)
    return season


def get_published_show(session: Session, competition_id: str) -> Competition | None:
    return session.scalar(
        select(Competition)
        .where(Competition.id == competition_id)
        .options(selectinload(Competition.seasons))
    )


def get_published_season(session: Session, competition_id: str, season_number: int) -> CompetitionSeason | None:
    return session.scalar(
        select(CompetitionSeason)
        .where(
            CompetitionSeason.competition_id == competition_id,
            CompetitionSeason.season_number == season_number,
        )
        .options(selectinload(CompetitionSeason.competition))
    )


def get_published_recording(session: Session, recording_id: str) -> Recording | None:
    return session.scalar(
        select(Recording)
        .where(Recording.id == recording_id, Recording.status == RecordingStatus.PUBLISHED.value)
        .options(selectinload(Recording.event), selectinload(Recording.competition_season))
    )


def list_published_seasons(session: Session, competition_id: str) -> list[CompetitionSeason]:
    return list(
        session.scalars(
            select(CompetitionSeason)
            .where(CompetitionSeason.competition_id == competition_id)
            .order_by(CompetitionSeason.season_number.asc())
        )
    )


def list_published_recordings_for_season(session: Session, competition_season_id: str) -> list[Recording]:
    return list(
        session.scalars(
            select(Recording)
            .where(
                Recording.competition_season_id == competition_season_id,
                Recording.status == RecordingStatus.PUBLISHED.value,
            )
            .order_by(Recording.episode_number.asc(), Recording.title.asc())
        )
    )


def list_published_recordings_for_competition(session: Session, competition_id: str) -> list[Recording]:
    stmt: Select[tuple[Recording]] = (
        select(Recording)
        .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
        .where(
            CompetitionSeason.competition_id == competition_id,
            Recording.status == RecordingStatus.PUBLISHED.value,
        )
        .order_by(CompetitionSeason.season_number.asc(), Recording.episode_number.asc())
    )
    return list(session.scalars(stmt))


def list_open_review_tasks(session: Session) -> list[ReviewTask]:
    return list(
        session.scalars(
            select(ReviewTask)
            .where(ReviewTask.status == "open")
            .options(
                selectinload(ReviewTask.recording).selectinload(Recording.event),
                selectinload(ReviewTask.recording).selectinload(Recording.competition_season),
            )
            .order_by(ReviewTask.created_at.asc())
        )
    )


def get_event_by_tsdb_id(session: Session, tsdb_id: int) -> Event | None:
    return session.scalar(select(Event).where(Event.tsdb_id == tsdb_id))


def get_recording_by_source_path(session: Session, source_path: str) -> Recording | None:
    return session.scalar(select(Recording).where(Recording.source_path == source_path))


def get_recording_by_fingerprint(session: Session, fingerprint: str) -> Recording | None:
    return session.scalar(select(Recording).where(Recording.file_fingerprint == fingerprint))


def get_alias_for_text(session: Session, alias_text: str) -> CompetitionAlias | None:
    return session.scalar(
        select(CompetitionAlias).where(
            CompetitionAlias.alias_text == alias_text.lower().strip()
        ).limit(1)
    )


def create_alias(session: Session, competition_id: str, alias_text: str, source: str = "manual") -> CompetitionAlias | None:
    normalised = alias_text.lower().strip()
    if not normalised:
        return None
    existing = session.scalar(
        select(CompetitionAlias).where(
            CompetitionAlias.competition_id == competition_id,
            CompetitionAlias.alias_text == normalised,
        )
    )
    if existing is not None:
        return existing
    alias = CompetitionAlias(
        competition_id=competition_id,
        alias_text=normalised,
        source=source,
    )
    session.add(alias)
    session.flush()
    return alias


def list_competition_aliases(session: Session, competition_id: str) -> list[CompetitionAlias]:
    return list(
        session.scalars(
            select(CompetitionAlias)
            .where(CompetitionAlias.competition_id == competition_id)
            .order_by(CompetitionAlias.created_at.asc())
        )
    )


def get_season_with_events_and_recordings(
    session: Session,
    season_id: str,
) -> tuple[CompetitionSeason | None, list[Event], dict[str, list[Recording]]]:
    """Return (season, events_ordered, recordings_by_event_id)."""
    season = session.scalar(
        select(CompetitionSeason)
        .where(CompetitionSeason.id == season_id)
        .options(selectinload(CompetitionSeason.competition))
    )
    if season is None:
        return None, [], {}
    events = list(
        session.scalars(
            select(Event)
            .where(Event.competition_season_id == season_id)
            .order_by(
                Event.event_sequence.asc().nulls_last(),
                Event.date.asc().nulls_last(),
                Event.round.asc().nulls_last(),
                Event.name.asc(),
            )
        )
    )
    recordings = list(
        session.scalars(
            select(Recording).where(Recording.competition_season_id == season_id)
        )
    )
    recordings_by_event: dict[str, list[Recording]] = {}
    for r in recordings:
        if r.event_id:
            recordings_by_event.setdefault(r.event_id, []).append(r)
    return season, events, recordings_by_event


def create_refresh_job(
    session: Session,
    *,
    section_id: int,
    recording_id: str | None = None,
    debounce_seconds: int = 60,
) -> PlexRefreshJob | None:
    """Create a PlexRefreshJob, debounced: returns None if a pending job for the same section already exists within debounce_seconds."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=debounce_seconds)
    existing = session.scalar(
        select(PlexRefreshJob).where(
            PlexRefreshJob.section_id == section_id,
            PlexRefreshJob.status == PlexRefreshJobStatus.PENDING.value,
            PlexRefreshJob.created_at >= cutoff,
        ).limit(1)
    )
    if existing is not None:
        return None
    job = PlexRefreshJob(
        recording_id=recording_id,
        section_id=section_id,
        status=PlexRefreshJobStatus.PENDING.value,
    )
    session.add(job)
    session.flush()
    return job
