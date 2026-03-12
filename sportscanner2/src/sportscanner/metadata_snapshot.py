from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from sportscanner.db.models import Competition, CompetitionSeason, Event, EventOrigin, Recording
from sportscanner.provider.items import episode_display_title


def effective_episode_thumb(recording: Recording, event: Event | None) -> str | None:
    return recording.thumb_url or (event.thumb_url if event is not None else None)


def clear_recording_metadata_snapshot(recording: Recording) -> None:
    recording.metadata_source = None
    recording.metadata_record = None
    recording.metadata_images = None
    recording.metadata_refreshed_at = None


def _images_for_recording(competition: Competition, recording: Recording, event: Event | None) -> list[dict[str, str]]:
    title = episode_display_title(recording, event, competition.name)
    images: list[dict[str, str]] = []
    thumb = effective_episode_thumb(recording, event)
    if thumb:
        images.append({"type": "snapshot", "url": thumb, "alt": title})
    if competition.poster_url:
        images.append({"type": "coverPoster", "url": competition.poster_url, "alt": competition.name})
    if competition.fanart_url:
        images.append({"type": "background", "url": competition.fanart_url, "alt": competition.name})
    return images


def _record_for_recording(
    competition: Competition,
    season: CompetitionSeason,
    recording: Recording,
    event: Event,
) -> dict[str, Any]:
    aired_at = event.date or recording.air_date
    return {
        "type": "episode",
        "title": episode_display_title(recording, event, competition.name),
        "summary": recording.summary or event.description,
        "duration": recording.duration_ms,
        "index": recording.episode_number,
        "originallyAvailableAt": aired_at.isoformat() if aired_at is not None else None,
        "thumb": effective_episode_thumb(recording, event),
        "matchMethod": recording.match_method,
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


def sync_recording_metadata_snapshot(
    session: Session,
    *,
    recording: Recording,
    metadata_source_name: str | None,
) -> bool:
    if recording.event_id is None:
        clear_recording_metadata_snapshot(recording)
        return False
    event = session.get(Event, recording.event_id)
    season = session.get(CompetitionSeason, recording.competition_season_id)
    competition = session.get(Competition, season.competition_id) if season is not None else None
    if event is None or season is None or competition is None:
        clear_recording_metadata_snapshot(recording)
        return False

    if event.origin == EventOrigin.UPSTREAM.value:
        recording.metadata_source = metadata_source_name or EventOrigin.UPSTREAM.value
    else:
        recording.metadata_source = event.origin
    recording.metadata_record = _record_for_recording(competition, season, recording, event)
    recording.metadata_images = _images_for_recording(competition, recording, event)
    recording.metadata_refreshed_at = datetime.now(UTC)
    return True
