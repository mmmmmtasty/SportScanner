from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher

from sqlalchemy import func, select

from sportscanner.db.models import Competition, CompetitionSeason, Event, Recording, RecordingStatus
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
        select(func.count(Recording.id))
        .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
        .where(
            CompetitionSeason.competition_id == competition_id,
            Recording.status == RecordingStatus.PUBLISHED.value,
        )
    ) or 0


def recording_event_dates(session, recordings: list[Recording]) -> dict[str, date | None]:
    event_ids = sorted({recording.event_id for recording in recordings if recording.event_id})
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


def episode_display_title(recording: Recording, event: Event | None, competition_name: str = "") -> str:
    if event is None or not event.name:
        return recording.title
    stripped_event = _strip_event_name_prefixes(event.name, competition_name)
    if recording.kind in PRIMARY_EVENT_KINDS:
        return stripped_event
    event_tokens = normalize_title(stripped_event)
    recording_tokens = normalize_title(recording.title)
    if recording_tokens[: len(event_tokens)] == event_tokens:
        return recording.title
    return f"{stripped_event} {recording.title}".strip()


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
    recording: Recording,
    provider_identifier: str,
) -> MetadataItemModel:
    rating_key = make_episode_rating_key(recording.id)
    season_rating_key = make_season_rating_key(competition.id, season.season_number)
    show_rating_key = make_show_rating_key(competition.id)
    aired_at = event.date if event is not None else recording.air_date
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_episode_guid(recording.id, provider_identifier),
        key=f"/library/metadata/{rating_key}",
        type="episode",
        title=episode_display_title(recording, event, competition.name),
        summary=recording.summary or (event.description if event is not None else None),
        year=aired_at.year if aired_at is not None else None,
        duration=recording.duration_ms,
        index=recording.episode_number,
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
        thumb=recording.thumb_url or (event.thumb_url if event is not None else None),
    )


def season_episode_items(
    session,
    competition: Competition,
    season: CompetitionSeason,
    provider_identifier: str,
) -> list[MetadataItemModel]:
    recordings = list(
        session.scalars(
            select(Recording)
            .where(
                Recording.competition_season_id == season.id,
                Recording.status == RecordingStatus.PUBLISHED.value,
            )
            .order_by(Recording.episode_number.asc(), Recording.id.asc())
        )
    )
    return [
        episode_metadata(
            competition,
            season,
            session.get(Event, recording.event_id) if recording.event_id else None,
            recording,
            provider_identifier,
        )
        for recording in recordings
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
    recordings = list(
        session.scalars(
            select(Recording)
            .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
            .where(
                CompetitionSeason.competition_id == competition.id,
                Recording.status == RecordingStatus.PUBLISHED.value,
            )
            .order_by(CompetitionSeason.season_number.asc(), Recording.episode_number.asc(), Recording.id.asc())
        )
    )
    return [
        episode_metadata(
            competition,
            seasons[recording.competition_season_id],
            session.get(Event, recording.event_id) if recording.event_id else None,
            recording,
            provider_identifier,
        )
        for recording in recordings
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
