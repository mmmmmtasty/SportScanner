from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from sportscanner.db.models import Competition, CompetitionSeason, Event, EventOrigin, Segment
from sportscanner.provider.items import episode_display_title


def effective_episode_thumb(segment: Segment, event: Event | None) -> str | None:
    return segment.thumb_url or (event.thumb_url if event is not None else None)


def clear_segment_metadata_snapshot(segment: Segment) -> None:
    segment.metadata_source = None
    segment.metadata_record = None
    segment.metadata_images = None
    segment.metadata_refreshed_at = None


def _images_for_segment(competition: Competition, segment: Segment, event: Event | None) -> list[dict[str, str]]:
    title = episode_display_title(segment, event, competition.name)
    images: list[dict[str, str]] = []
    thumb = effective_episode_thumb(segment, event)
    if thumb:
        images.append({"type": "snapshot", "url": thumb, "alt": title})
    if competition.poster_url:
        images.append({"type": "coverPoster", "url": competition.poster_url, "alt": competition.name})
    if competition.fanart_url:
        images.append({"type": "background", "url": competition.fanart_url, "alt": competition.name})
    return images


def _record_for_segment(
    competition: Competition,
    season: CompetitionSeason,
    segment: Segment,
    event: Event,
) -> dict[str, Any]:
    aired_at = event.date or segment.air_date
    return {
        "type": "episode",
        "title": episode_display_title(segment, event, competition.name),
        "summary": segment.summary or event.description,
        "duration": segment.duration_ms,
        "index": segment.episode_number,
        "originallyAvailableAt": aired_at.isoformat() if aired_at is not None else None,
        "thumb": effective_episode_thumb(segment, event),
        "matchMethod": segment.match_method,
        "competition": {
            "id": competition.id,
            "title": competition.name,
            "poster": competition.poster_url,
            "fanart": competition.fanart_url,
        },
        "season": {
            "id": season.id,
            "title": season.label,
            "number": season.season_number,
        },
        "event": {
            "id": event.id,
            "tsdbId": event.tsdb_id,
            "title": event.name,
            "date": event.date.isoformat() if event.date is not None else None,
        },
    }


def sync_segment_metadata_snapshot(
    session: Session,
    *,
    segment: Segment,
    metadata_source_name: str | None,
) -> bool:
    if segment.event_id is None:
        clear_segment_metadata_snapshot(segment)
        return False
    event = session.get(Event, segment.event_id)
    season = session.get(CompetitionSeason, segment.competition_season_id)
    competition = session.get(Competition, season.competition_id) if season is not None else None
    if event is None or season is None or competition is None:
        clear_segment_metadata_snapshot(segment)
        return False

    if event.origin == EventOrigin.UPSTREAM.value:
        segment.metadata_source = metadata_source_name or EventOrigin.UPSTREAM.value
    else:
        segment.metadata_source = event.origin
    segment.metadata_record = _record_for_segment(competition, season, segment, event)
    segment.metadata_images = _images_for_segment(competition, segment, event)
    segment.metadata_refreshed_at = datetime.now(UTC)
    return True
