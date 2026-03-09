from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from sportscanner.db.models import Competition, CompetitionSeason, Event, ReviewTask, Segment, SegmentStatus


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


def get_published_segment(session: Session, segment_id: str) -> Segment | None:
    return session.scalar(
        select(Segment)
        .where(Segment.id == segment_id, Segment.status == SegmentStatus.PUBLISHED.value)
        .options(selectinload(Segment.event), selectinload(Segment.competition_season))
    )


def list_published_seasons(session: Session, competition_id: str) -> list[CompetitionSeason]:
    return list(
        session.scalars(
            select(CompetitionSeason)
            .where(CompetitionSeason.competition_id == competition_id)
            .order_by(CompetitionSeason.season_number.asc())
        )
    )


def list_published_segments_for_season(session: Session, competition_season_id: str) -> list[Segment]:
    return list(
        session.scalars(
            select(Segment)
            .where(
                Segment.competition_season_id == competition_season_id,
                Segment.status == SegmentStatus.PUBLISHED.value,
            )
            .order_by(Segment.episode_number.asc(), Segment.title.asc())
        )
    )


def list_published_segments_for_competition(session: Session, competition_id: str) -> list[Segment]:
    stmt: Select[tuple[Segment]] = (
        select(Segment)
        .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
        .where(
            CompetitionSeason.competition_id == competition_id,
            Segment.status == SegmentStatus.PUBLISHED.value,
        )
        .order_by(CompetitionSeason.season_number.asc(), Segment.episode_number.asc())
    )
    return list(session.scalars(stmt))


def list_open_review_tasks(session: Session) -> list[ReviewTask]:
    return list(
        session.scalars(
            select(ReviewTask)
            .where(ReviewTask.status == "open")
            .options(
                selectinload(ReviewTask.segment).selectinload(Segment.event),
                selectinload(ReviewTask.segment).selectinload(Segment.competition_season),
            )
            .order_by(ReviewTask.created_at.asc())
        )
    )


def get_event_by_tsdb_id(session: Session, tsdb_id: int) -> Event | None:
    return session.scalar(select(Event).where(Event.tsdb_id == tsdb_id))


def get_segment_by_source_path(session: Session, source_path: str) -> Segment | None:
    return session.scalar(select(Segment).where(Segment.source_path == source_path))

