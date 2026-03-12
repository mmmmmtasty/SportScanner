from __future__ import annotations

import re
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


PRIMARY_EVENT_KINDS = {
    "match",
    "race",
    "practice",
    "qualifying",
    "sprint",
    "sprint_qualifying",
}
_YEAR_PREFIX_RE = re.compile(r"^\d{4}(?:/\d{2,4})?\s+")


def normalize_title(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else " " for char in value).split()


def _strip_event_name_prefixes(event_name: str, competition_name: str) -> str:
    text = _YEAR_PREFIX_RE.sub("", event_name).strip()
    competition = competition_name.strip()
    if competition and text.lower().startswith(competition.lower()):
        text = text[len(competition):].lstrip(" -–").strip()
    return text or event_name


def episode_display_title(segment: Segment, event: Event | None, competition_name: str = "") -> str:
    if event is None or not event.name:
        return segment.title
    stripped_event = _strip_event_name_prefixes(event.name, competition_name)
    if segment.kind in PRIMARY_EVENT_KINDS:
        return stripped_event
    event_tokens = normalize_title(stripped_event)
    segment_tokens = normalize_title(segment.title)
    if segment_tokens[: len(event_tokens)] == event_tokens:
        return segment.title
    return f"{stripped_event} {segment.title}".strip()


def first_event_date(session, competition_id: str, season_number: int | None = None) -> date | None:
    query = (
        select(func.min(Event.date))
        .join(CompetitionSeason, CompetitionSeason.id == Event.competition_season_id)
        .where(CompetitionSeason.competition_id == competition_id)
    )
    if season_number is not None:
        query = query.where(CompetitionSeason.season_number == season_number)
    return session.scalar(query)


def episode_metadata(
    competition: Competition,
    season: CompetitionSeason,
    event: Event | None,
    segment: Segment,
    provider_identifier: str,
) -> MetadataItemModel:
    rating_key = make_episode_rating_key(segment.id)
    season_rating_key = make_season_rating_key(competition.id, season.season_number)
    show_rating_key = make_show_rating_key(competition.id)
    aired_at = event.date if event is not None else segment.air_date
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_episode_guid(segment.id, provider_identifier),
        key=f"/library/metadata/{rating_key}",
        type="episode",
        title=episode_display_title(segment, event, competition.name),
        summary=segment.summary or (event.description if event is not None else None),
        year=aired_at.year if aired_at is not None else None,
        index=segment.episode_number,
        parentKey=f"/library/metadata/{season_rating_key}",
        parentGuid=make_season_guid(competition.id, season.season_number, provider_identifier),
        parentIndex=season.season_number,
        parentRatingKey=season_rating_key,
        parentTitle=season.label,
        parentType="season",
        parentThumb=competition.poster_url,
        grandparentKey=f"/library/metadata/{show_rating_key}",
        grandparentGuid=make_show_guid(competition.id, provider_identifier),
        grandparentRatingKey=show_rating_key,
        grandparentTitle=competition.name,
        grandparentType="show",
        grandparentThumb=competition.poster_url,
        originallyAvailableAt=aired_at,
        thumb=segment.thumb_url or (event.thumb_url if event is not None else None),
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
    return [
        episode_metadata(
            competition,
            season,
            session.get(Event, segment.event_id) if segment.event_id else None,
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
    season_aired_at = first_event_date(session, competition.id, season.season_number)
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
        year=season_aired_at.year if season_aired_at is not None else season.season_number,
        index=season.season_number,
        parentKey=f"/library/metadata/{show_rating_key}",
        parentGuid=make_show_guid(competition.id, provider_identifier),
        parentRatingKey=show_rating_key,
        parentTitle=competition.name,
        parentType="show",
        parentThumb=competition.poster_url,
        originallyAvailableAt=season_aired_at,
        thumb=competition.poster_url,
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
    return [
        episode_metadata(
            competition,
            seasons[segment.competition_season_id],
            session.get(Event, segment.event_id) if segment.event_id else None,
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
    show_aired_at = None
    if competition.formed_year is not None:
        show_aired_at = date(competition.formed_year, 1, 1)
    if show_aired_at is None:
        show_aired_at = first_event_date(session, competition.id)
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
        originallyAvailableAt=show_aired_at,
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
