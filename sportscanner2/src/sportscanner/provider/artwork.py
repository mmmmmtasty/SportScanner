from __future__ import annotations

from collections.abc import Callable

from sportscanner.db.models import Competition, Event, Recording


AssetUrlBuilder = Callable[[str, str, str, str], str]


def proxy_asset_url(
    asset_url_builder: AssetUrlBuilder | None,
    *,
    entity_type: str,
    entity_id: str,
    asset_type: str,
    source_url: str | None,
) -> str | None:
    if not source_url:
        return None
    if asset_url_builder is None:
        return source_url
    return asset_url_builder(entity_type, entity_id, asset_type, source_url)


def competition_poster_url(competition: Competition, asset_url_builder: AssetUrlBuilder | None) -> str | None:
    return proxy_asset_url(
        asset_url_builder,
        entity_type="competition",
        entity_id=competition.id,
        asset_type="poster",
        source_url=competition.poster_url,
    )


def competition_fanart_url(competition: Competition, asset_url_builder: AssetUrlBuilder | None) -> str | None:
    return proxy_asset_url(
        asset_url_builder,
        entity_type="competition",
        entity_id=competition.id,
        asset_type="fanart",
        source_url=competition.fanart_url,
    )


def event_thumb_url(
    recording: Recording,
    event: Event | None,
    asset_url_builder: AssetUrlBuilder | None,
) -> str | None:
    if recording.thumb_url:
        return proxy_asset_url(
            asset_url_builder,
            entity_type="recording",
            entity_id=recording.id,
            asset_type="thumb",
            source_url=recording.thumb_url,
        )
    if event is None:
        return None
    return proxy_asset_url(
        asset_url_builder,
        entity_type="event",
        entity_id=event.id,
        asset_type="thumb",
        source_url=event.thumb_url,
    )


def episode_cover_poster_url(
    competition: Competition,
    event: Event | None,
    asset_url_builder: AssetUrlBuilder | None,
) -> str | None:
    if event is not None and event.poster_url:
        return proxy_asset_url(
            asset_url_builder,
            entity_type="event",
            entity_id=event.id,
            asset_type="poster",
            source_url=event.poster_url,
        )
    return competition_poster_url(competition, asset_url_builder)


def episode_background_url(
    competition: Competition,
    event: Event | None,
    asset_url_builder: AssetUrlBuilder | None,
) -> str | None:
    if event is not None and event.fanart_url:
        return proxy_asset_url(
            asset_url_builder,
            entity_type="event",
            entity_id=event.id,
            asset_type="fanart",
            source_url=event.fanart_url,
        )
    return competition_fanart_url(competition, asset_url_builder)
