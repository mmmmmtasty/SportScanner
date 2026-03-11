from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher

from sqlalchemy import func, select

from sportscanner.db.models import Competition, CompetitionSeason, Event, Segment, SegmentStatus
from sportscanner.provider.rating_keys import (
    make_episode_guid,
    make_episode_rating_key,
    make_season_guid,
    make_season_rating_key,
    make_show_guid,
    make_show_rating_key,
)
from sportscanner.provider.schemas import ChildrenModel, MetadataItemModel


def sequence_score(left: str, right: str) -> int:
    return round(SequenceMatcher(None, left.lower(), right.lower()).ratio() * 100)


def show_leaf_count(session, competition_id: str) -> int:
    return session.scalar(
        select(func.count(Segment.id))
        .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
        .where(
            CompetitionSeason.competition_id == competition_id,
            Segment.status == SegmentStatus.PUBLISHED.value,
        )
    ) or 0


def segment_event_dates(session, segments: list[Segment]) -> dict[str, date | None]:
    event_ids = sorted({segment.event_id for segment in segments if segment.event_id})
    if not event_ids:
        return {}
    rows = session.execute(select(Event.id, Event.date).where(Event.id.in_(event_ids))).all()
    return {str(event_id): event_date for event_id, event_date in rows}


def episode_metadata(
    competition: Competition,
    season: CompetitionSeason,
    event_date: date | None,
    segment: Segment,
    provider_identifier: str,
) -> MetadataItemModel:
    rating_key = make_episode_rating_key(segment.id)
    season_rating_key = make_season_rating_key(competition.id, season.season_number)
    show_rating_key = make_show_rating_key(competition.id)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_episode_guid(segment.id, provider_identifier),
        key=f"/library/metadata/{rating_key}",
        type="episode",
        title=segment.title,
        summary=segment.summary,
        index=segment.episode_number,
        parentKey=f"/library/metadata/{season_rating_key}",
        parentGuid=make_season_guid(competition.id, season.season_number, provider_identifier),
        parentIndex=season.season_number,
        parentRatingKey=season_rating_key,
        parentTitle=season.label,
        parentType="season",
        grandparentKey=f"/library/metadata/{show_rating_key}",
        grandparentGuid=make_show_guid(competition.id, provider_identifier),
        grandparentRatingKey=show_rating_key,
        grandparentTitle=competition.name,
        originallyAvailableAt=event_date or segment.air_date,
        thumb=segment.thumb_url,
    )


def season_episode_items(
    session,
    competition: Competition,
    season: CompetitionSeason,
    provider_identifier: str,
) -> list[MetadataItemModel]:
    segments = list(
        session.scalars(
            select(Segment)
            .where(
                Segment.competition_season_id == season.id,
                Segment.status == SegmentStatus.PUBLISHED.value,
            )
            .order_by(Segment.episode_number.asc(), Segment.id.asc())
        )
    )
    event_dates = segment_event_dates(session, segments)
    return [
        episode_metadata(
            competition,
            season,
            event_dates.get(segment.event_id or ""),
            segment,
            provider_identifier,
        )
        for segment in segments
    ]


def season_metadata(
    session,
    competition: Competition,
    season: CompetitionSeason,
    provider_identifier: str,
    *,
    include_children: bool = False,
) -> MetadataItemModel:
    rating_key = make_season_rating_key(competition.id, season.season_number)
    show_rating_key = make_show_rating_key(competition.id)
    children = None
    if include_children:
        episode_items = season_episode_items(session, competition, season, provider_identifier)
        children = ChildrenModel(size=len(episode_items), Metadata=episode_items)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_season_guid(competition.id, season.season_number, provider_identifier),
        key=f"/library/metadata/{rating_key}/children",
        type="season",
        title=season.label,
        index=season.season_number,
        parentKey=f"/library/metadata/{show_rating_key}",
        parentGuid=make_show_guid(competition.id, provider_identifier),
        parentRatingKey=show_rating_key,
        parentTitle=competition.name,
        parentType="show",
        Children=children,
    )


def show_season_items(session, competition: Competition, provider_identifier: str) -> list[MetadataItemModel]:
    seasons = list(
        session.scalars(
            select(CompetitionSeason)
            .where(CompetitionSeason.competition_id == competition.id)
            .order_by(CompetitionSeason.season_number.asc())
        )
    )
    return [season_metadata(session, competition, season, provider_identifier) for season in seasons]


def show_episode_items(session, competition: Competition, provider_identifier: str) -> list[MetadataItemModel]:
    seasons = {
        season.id: season
        for season in session.scalars(
            select(CompetitionSeason)
            .where(CompetitionSeason.competition_id == competition.id)
            .order_by(CompetitionSeason.season_number.asc())
        )
    }
    segments = list(
        session.scalars(
            select(Segment)
            .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
            .where(
                CompetitionSeason.competition_id == competition.id,
                Segment.status == SegmentStatus.PUBLISHED.value,
            )
            .order_by(CompetitionSeason.season_number.asc(), Segment.episode_number.asc(), Segment.id.asc())
        )
    )
    event_dates = segment_event_dates(session, segments)
    return [
        episode_metadata(
            competition,
            seasons[segment.competition_season_id],
            event_dates.get(segment.event_id or ""),
            segment,
            provider_identifier,
        )
        for segment in segments
    ]


def show_metadata(
    session,
    competition: Competition,
    provider_identifier: str,
    *,
    include_children: bool = False,
) -> MetadataItemModel:
    rating_key = make_show_rating_key(competition.id)
    children = None
    if include_children:
        season_items = show_season_items(session, competition, provider_identifier)
        children = ChildrenModel(size=len(season_items), Metadata=season_items)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_show_guid(competition.id, provider_identifier),
        key=f"/library/metadata/{rating_key}/children",
        type="show",
        title=competition.name,
        summary=competition.description,
        year=competition.formed_year,
        leafCount=show_leaf_count(session, competition.id),
        thumb=competition.poster_url,
        art=competition.fanart_url,
        Children=children,
    )


def with_score(item: MetadataItemModel, score: int) -> MetadataItemModel:
    return item.model_copy(update={"score": score})


def dedupe_metadata(items: list[MetadataItemModel]) -> list[MetadataItemModel]:
    seen: set[str] = set()
    deduped: list[MetadataItemModel] = []
    for item in items:
        if item.ratingKey in seen:
            continue
        seen.add(item.ratingKey)
        deduped.append(item)
    return deduped
