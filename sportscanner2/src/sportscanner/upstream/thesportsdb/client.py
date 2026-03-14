from __future__ import annotations

import csv
from html import unescape
import io
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from sportscanner.config import Settings
from sportscanner.db.models import ApiCache
from sportscanner.upstream.base import MetadataSource, UpstreamCompetition, UpstreamEvent
from sportscanner.upstream.thesportsdb.adapter import adapt_competition, adapt_event, adapt_event_csv


CACHE_TTLS = {
    "all_leagues.php": timedelta(hours=24),
    "lookupleague.php": timedelta(hours=12),
    "searchfilename.php": timedelta(hours=6),
    "eventsday.php": timedelta(hours=1),
    "eventsseason.php": timedelta(hours=4),
    "lookupevent.php": timedelta(hours=12),
    "season_csv": timedelta(hours=4),
}

logger = logging.getLogger("sportscanner.upstream.thesportsdb")


class TheSportsDbClient(MetadataSource):
    name = "thesportsdb"

    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]) -> None:
        self.settings = settings
        self.session_factory = session_factory
        api_key = settings.tsdb_api_key or "123"
        self.base_v1_url = f"https://www.thesportsdb.com/api/v1/json/{api_key}"
        self._mode: str | None = None

    def probe(self) -> str:
        if self._mode is not None:
            return self._mode
        self._mode = "v1"
        return self._mode

    def all_competitions(self, *, force_refresh: bool = False) -> list[UpstreamCompetition]:
        payload = self._get_json("all_leagues.php", cache=not force_refresh)
        return [adapt_competition(item) for item in payload.get("leagues", [])]

    def search_filename(self, query: str, *, force_refresh: bool = False) -> list[UpstreamEvent]:
        payload = self._get_json("searchfilename.php", params={"e": query}, cache=not force_refresh)
        return [adapt_event(item) for item in payload.get("event") or []]

    def events_on_day(
        self,
        competition_name: str,
        event_date: date,
        *,
        force_refresh: bool = False,
    ) -> list[UpstreamEvent]:
        payload = self._get_json(
            "eventsday.php",
            params={"d": event_date.isoformat(), "l": competition_name},
            cache=not force_refresh,
        )
        return [adapt_event(item, competition_name=competition_name) for item in payload.get("events", []) or []]

    def season_events(
        self,
        competition: UpstreamCompetition,
        season_label: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[UpstreamEvent], bool]:
        if competition.tsdb_id is None:
            return ([], False)
        csv_events = self._fetch_season_csv(
            competition.tsdb_id,
            season_label,
            competition.name,
            force_refresh=force_refresh,
        )
        if csv_events:
            return (csv_events, True)
        payload = self._get_json(
            "eventsseason.php",
            params={"id": competition.tsdb_id, "s": season_label},
            cache=not force_refresh,
        )
        events = [adapt_event(item, competition_name=competition.name) for item in payload.get("events", []) or []]
        return (events, bool(events))

    def lookup_competition(self, tsdb_id: int, *, force_refresh: bool = False) -> UpstreamCompetition | None:
        payload = self._get_json("lookupleague.php", params={"id": tsdb_id}, cache=not force_refresh)
        leagues = payload.get("leagues", []) or []
        if not leagues:
            return None
        return adapt_competition(leagues[0])

    def lookup_event(self, tsdb_event_id: int, *, force_refresh: bool = False) -> UpstreamEvent | None:
        payload = self._get_json("lookupevent.php", params={"id": tsdb_event_id}, cache=not force_refresh)
        events = payload.get("events", []) or []
        if not events:
            return None
        return adapt_event(events[0])

    def _fetch_season_csv(
        self,
        tsdb_id: int,
        season_label: str,
        competition_name: str,
        *,
        force_refresh: bool = False,
    ) -> list[UpstreamEvent]:
        """Fetch all season events via the TheSportsDB website CSV export.

        This bypasses the free-tier API cap (15 events) by downloading the full
        season schedule from the website's CSV endpoint.
        """
        cache_key = f"season_csv:{tsdb_id}:{season_label}"
        now = datetime.now(UTC)
        if not force_refresh:
            try:
                with self.session_factory() as session:
                    cached = session.get(ApiCache, cache_key)
                    expires_at = self._as_utc(cached.expires_at) if cached is not None else None
                    if cached is not None and expires_at is not None and expires_at >= now:
                        return self._parse_csv_events(cached.response_body, competition_name, tsdb_id)
            except OperationalError:
                pass

        slug = re.sub(r"[^a-z0-9]+", "-", competition_name.lower()).strip("-")
        url = f"https://www.thesportsdb.com/season/{tsdb_id}-{slug}/{season_label}?csv=1&all=1"
        try:
            response = httpx.get(url, timeout=20.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError:
            return []

        match = re.search(r"<textarea[^>]*>(.*?)</textarea>", response.text, re.DOTALL | re.IGNORECASE)
        if not match:
            return []
        csv_content = unescape(match.group(1)).strip()
        if not csv_content:
            return []

        try:
            ttl = CACHE_TTLS["season_csv"]
            with self.session_factory() as session:
                entry = ApiCache(
                    cache_key=cache_key,
                    response_body=csv_content,
                    fetched_at=now,
                    expires_at=now + ttl,
                )
                existing = session.get(ApiCache, cache_key)
                if existing is None:
                    session.add(entry)
                else:
                    existing.response_body = entry.response_body
                    existing.fetched_at = entry.fetched_at
                    existing.expires_at = entry.expires_at
                session.commit()
        except OperationalError:
            pass

        return self._parse_csv_events(csv_content, competition_name, tsdb_id)

    def _parse_csv_events(self, csv_content: str, competition_name: str, competition_tsdb_id: int) -> list[UpstreamEvent]:
        events = []
        for row in csv.DictReader(io.StringIO(csv_content)):
            event = adapt_event_csv(row, competition_name=competition_name, competition_tsdb_id=competition_tsdb_id)
            if event is not None:
                events.append(event)
        return events

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
            try:
                with self.session_factory() as session:
                    cached = session.get(ApiCache, cache_key)
                    expires_at = self._as_utc(cached.expires_at) if cached is not None else None
                    if cached is not None and expires_at is not None and expires_at >= now:
                        payload = json.loads(cached.response_body)
                        logger.debug(
                            "thesportsdb_cache_hit endpoint=%s version=%s",
                            endpoint,
                            version,
                            extra={
                                "structured_data": {
                                    "source": "cache",
                                    "endpoint": endpoint,
                                    "version": version,
                                    "params": params,
                                    "cache_key": cache_key,
                                    "payload": payload,
                                }
                            },
                        )
                        return payload
            except OperationalError:
                # SQLite write contention should not break live metadata fetches.
                pass

        url = f"{self.base_v1_url}/{endpoint.lstrip('/')}"
        logger.info(
            "thesportsdb_request endpoint=%s version=%s cache=%s",
            endpoint,
            version,
            "enabled" if cache else "disabled",
        )
        try:
            response = httpx.get(url, params=params, timeout=20.0)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "thesportsdb_http_error endpoint=%s version=%s status_code=%s",
                endpoint,
                version,
                exc.response.status_code,
                extra={
                    "structured_data": {
                        "source": "live",
                        "endpoint": endpoint,
                        "version": version,
                        "params": params,
                        "status_code": exc.response.status_code,
                        "response_text": exc.response.text,
                    }
                },
            )
            raise
        except httpx.HTTPError as exc:
            logger.warning(
                "thesportsdb_request_failed endpoint=%s version=%s error=%s",
                endpoint,
                version,
                exc,
                extra={
                    "structured_data": {
                        "source": "live",
                        "endpoint": endpoint,
                        "version": version,
                        "params": params,
                        "error": str(exc),
                    }
                },
            )
            raise

        logger.debug(
            "thesportsdb_response endpoint=%s version=%s status_code=%s",
            endpoint,
            version,
            response.status_code,
            extra={
                "structured_data": {
                    "source": "live",
                    "endpoint": endpoint,
                    "version": version,
                    "params": params,
                    "status_code": response.status_code,
                    "payload": payload,
                }
            },
        )

        if cache:
            ttl = CACHE_TTLS.get(endpoint, timedelta(hours=1))
            try:
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
            except OperationalError:
                # Cache misses are acceptable when ingest already owns the SQLite write lock.
                pass
        return payload

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
