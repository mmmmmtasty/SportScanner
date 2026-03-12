from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from sportscanner.db.models import Competition, CompetitionSeason, Event, Recording, RecordingStatus, ReviewTask


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
