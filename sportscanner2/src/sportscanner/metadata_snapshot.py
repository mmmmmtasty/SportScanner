from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from sportscanner.db.models import Competition, CompetitionSeason, Event, EventOrigin, Recording
from sportscanner.provider.items import episode_display_title, has_field_override


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
    poster = event.poster_url if event is not None and event.poster_url else competition.poster_url
    if poster:
        images.append({"type": "coverPoster", "url": poster, "alt": title})
    background = event.fanart_url if event is not None and event.fanart_url else competition.fanart_url
    if background:
        images.append({"type": "background", "url": background, "alt": competition.name})
    return images


def _record_for_recording(
    competition: Competition,
    season: CompetitionSeason,
    recording: Recording,
    event: Event,
) -> dict[str, Any]:
    aired_at = (
        recording.air_date
        if recording.air_date is not None and has_field_override(recording, "air_date")
        else (event.date or recording.air_date)
    )
    return {
        "type": "episode",
        "title": episode_display_title(recording, event, competition.name),
        "summary": recording.summary or event.description,
        "kind": recording.kind,
        "duration": recording.duration_ms,
        "index": recording.episode_number,
        "recordingCode": recording.recording_code,
        "originallyAvailableAt": aired_at.isoformat() if aired_at is not None else None,
        "sourcePath": recording.source_path,
        "managedPath": recording.managed_path,
        "thumb": effective_episode_thumb(recording, event),
        "matchMethod": recording.match_method,
        "matchConfidence": recording.match_confidence,
        "competition": {
            "id": competition.id,
            "title": competition.name,
            "alternateTitles": competition.alternate_names,
            "sport": competition.sport,
            "country": competition.country,
            "formedYear": competition.formed_year,
            "summary": competition.description,
            "poster": competition.poster_url,
            "banner": competition.banner_url,
            "fanart": competition.fanart_url,
            "upstreamMetadata": competition.upstream_metadata,
        },
        "season": {
            "id": season.id,
            "title": season.label,
            "number": season.season_number,
            "isComplete": season.is_complete,
        },
        "event": {
            "id": event.id,
            "tsdbId": event.tsdb_id,
            "title": event.name,
            "date": event.date.isoformat() if event.date is not None else None,
            "time": event.time.isoformat() if event.time is not None else None,
            "round": event.round,
            "venue": event.venue,
            "city": event.city,
            "country": event.country,
            "homeTeam": event.home_team,
            "awayTeam": event.away_team,
            "homeScore": event.home_score,
            "awayScore": event.away_score,
            "summary": event.description,
            "thumb": event.thumb_url,
            "poster": event.poster_url,
            "banner": event.banner_url,
            "fanart": event.fanart_url,
            "weekendGroup": event.weekend_group,
            "upstreamMetadata": event.upstream_metadata,
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
