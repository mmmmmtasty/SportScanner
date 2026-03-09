from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sportscanner.config import Settings
from sportscanner.db.models import ApiCache
from sportscanner.upstream.base import MetadataSource, UpstreamCompetition, UpstreamEvent
from sportscanner.upstream.thesportsdb.adapter import adapt_competition, adapt_event


CACHE_TTLS = {
    "all_leagues.php": timedelta(hours=24),
    "lookupleague.php": timedelta(hours=12),
    "searchfilename.php": timedelta(hours=6),
    "eventsday.php": timedelta(hours=1),
    "eventsseason.php": timedelta(hours=4),
    "lookupevent.php": timedelta(hours=12),
}


class TheSportsDbClient(MetadataSource):
    name = "thesportsdb"

    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]) -> None:
        self.settings = settings
        self.session_factory = session_factory
        api_key = settings.tsdb_api_key or "123"
        self.base_v1_url = f"https://www.thesportsdb.com/api/v1/json/{api_key}"
        self.base_v2_url = f"https://www.thesportsdb.com/api/v2/json/{api_key}"
        self._mode: str | None = None

    def probe(self) -> str:
        if self._mode is not None:
            return self._mode
        if self.settings.tsdb_api_mode in {"v1", "v2"}:
            self._mode = self.settings.tsdb_api_mode
            return self._mode
        try:
            self._get_json("/all/leagues", version="v2", cache=False)
            self._mode = "v2"
        except httpx.HTTPError:
            self._mode = "v1"
        return self._mode

    def all_competitions(self) -> list[UpstreamCompetition]:
        payload = self._get_json("all_leagues.php")
        return [adapt_competition(item) for item in payload.get("leagues", [])]

    def search_filename(self, query: str) -> list[UpstreamEvent]:
        payload = self._get_json("searchfilename.php", params={"e": query})
        return [adapt_event(item) for item in payload.get("event", [])]

    def events_on_day(self, competition_name: str, event_date: date) -> list[UpstreamEvent]:
        payload = self._get_json(
            "eventsday.php",
            params={"d": event_date.isoformat(), "l": competition_name},
        )
        return [adapt_event(item, competition_name=competition_name) for item in payload.get("events", []) or []]

    def season_events(self, competition: UpstreamCompetition, season_label: str) -> tuple[list[UpstreamEvent], bool]:
        if competition.tsdb_id is None:
            return ([], False)
        payload = self._get_json(
            "eventsseason.php",
            params={"id": competition.tsdb_id, "s": season_label},
        )
        events = [adapt_event(item, competition_name=competition.name) for item in payload.get("events", []) or []]
        return (events, bool(events))

    def lookup_event(self, tsdb_event_id: int) -> UpstreamEvent | None:
        payload = self._get_json("lookupevent.php", params={"id": tsdb_event_id})
        events = payload.get("events", []) or []
        if not events:
            return None
        return adapt_event(events[0])

    def _get_json(
        self,
        endpoint: str,
        *,
        params: dict | None = None,
        version: str = "v1",
        cache: bool = True,
    ) -> dict:
        params = params or {}
        cache_key = f"{version}:{endpoint}?{urlencode(sorted(params.items()))}"
        now = datetime.now(UTC)
        if cache:
            with self.session_factory() as session:
                cached = session.get(ApiCache, cache_key)
                if cached is not None and cached.expires_at >= now:
                    return json.loads(cached.response_body)

        base_url = self.base_v2_url if version == "v2" else self.base_v1_url
        url = f"{base_url}/{endpoint.lstrip('/')}"
        response = httpx.get(url, params=params, timeout=20.0)
        response.raise_for_status()
        payload = response.json()

        if cache:
            ttl = CACHE_TTLS.get(endpoint, timedelta(hours=1))
            with self.session_factory() as session:
                cached = ApiCache(
                    cache_key=cache_key,
                    response_body=json.dumps(payload),
                    fetched_at=now,
                    expires_at=now + ttl,
                )
                existing = session.get(ApiCache, cache_key)
                if existing is None:
                    session.add(cached)
                else:
                    existing.response_body = cached.response_body
                    existing.fetched_at = cached.fetched_at
                    existing.expires_at = cached.expires_at
                session.commit()
        return payload
