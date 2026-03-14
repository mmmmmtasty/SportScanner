from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx

from sportscanner.config import Settings
from sportscanner.db.models import ApiCache
from sportscanner.log_buffer import LogBuffer
from sportscanner.upstream.base import UpstreamCompetition
from sportscanner.upstream.thesportsdb.client import TheSportsDbClient


def test_get_json_accepts_naive_cache_timestamps(settings, session_factory, monkeypatch) -> None:
    cache_key = "v1:all_leagues.php?"
    naive_now = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as session:
        session.add(
            ApiCache(
                cache_key=cache_key,
                response_body=json.dumps({"leagues": []}),
                fetched_at=naive_now,
                expires_at=naive_now + timedelta(hours=1),
            )
        )
        session.commit()

    def fail_http_get(*args, **kwargs):
        raise AssertionError("cache hit should not make an HTTP request")

    monkeypatch.setattr("sportscanner.upstream.thesportsdb.client.httpx.get", fail_http_get)

    client = TheSportsDbClient(settings, session_factory)

    assert client.all_competitions() == []


def test_get_json_logs_structured_response_payload(settings, session_factory, monkeypatch) -> None:
    request = None

    def fake_http_get(url, *, params=None, timeout=None):
        nonlocal request
        request = {"url": url, "params": params, "timeout": timeout}
        return httpx.Response(
            200,
            request=httpx.Request("GET", url, params=params),
            json={"event": [{"idEvent": "1001", "strEvent": "Austrian Grand Prix"}]},
        )

    monkeypatch.setattr("sportscanner.upstream.thesportsdb.client.httpx.get", fake_http_get)

    buf = LogBuffer(session_factory=session_factory)
    buf.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("sportscanner.upstream.thesportsdb")
    original_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(buf)
    try:
        client = TheSportsDbClient(settings, session_factory)

        events = client.search_filename("Austrian")

        assert request is not None
        assert len(events) == 1
        entries = buf.entries(component="sportscanner.upstream.thesportsdb")
        response_entry = next(entry for entry in entries if entry.message.startswith("thesportsdb_response"))
        assert response_entry.payload_json is not None
        assert '"idEvent": "1001"' in response_entry.payload_json
        assert '"endpoint": "searchfilename.php"' in response_entry.payload_json
    finally:
        logger.removeHandler(buf)
        logger.setLevel(original_level)


def test_probe_defaults_to_v1_without_hitting_v2(settings, session_factory, monkeypatch) -> None:
    def fail_http_get(*args, **kwargs):
        raise AssertionError("probe should not issue a v2 network request in auto mode")

    monkeypatch.setattr("sportscanner.upstream.thesportsdb.client.httpx.get", fail_http_get)

    client = TheSportsDbClient(settings, session_factory)

    assert client.probe() == "v1"


def test_settings_normalize_legacy_v2_mode(tmp_path) -> None:
    settings = Settings(
        db_path=tmp_path / "sportscanner.db",
        incoming_dir=tmp_path / "incoming",
        library_dir=tmp_path / "library",
        asset_cache_dir=tmp_path / "cache",
        tsdb_api_mode="v2",
    )

    assert settings.tsdb_api_mode == "v1"


def test_lookup_event_force_refresh_bypasses_cached_response(settings, session_factory, monkeypatch) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add(
            ApiCache(
                cache_key="v1:lookupevent.php?id=1001",
                response_body=json.dumps({"events": [{"idEvent": "1001", "strEvent": "Cached Grand Prix"}]}),
                fetched_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        session.commit()

    calls = 0

    def fake_http_get(url, *, params=None, timeout=None, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            request=httpx.Request("GET", url, params=params),
            json={"events": [{"idEvent": "1001", "strEvent": "Fresh Grand Prix"}]},
        )

    monkeypatch.setattr("sportscanner.upstream.thesportsdb.client.httpx.get", fake_http_get)
    client = TheSportsDbClient(settings, session_factory)

    cached = client.lookup_event(1001)
    refreshed = client.lookup_event(1001, force_refresh=True)

    assert cached is not None
    assert refreshed is not None
    assert cached.name == "Cached Grand Prix"
    assert refreshed.name == "Fresh Grand Prix"
    assert calls == 1


def test_season_events_force_refresh_bypasses_cached_csv(settings, session_factory, monkeypatch) -> None:
    now = datetime.now(UTC)
    cached_csv = "idEvent,Event,dateEvent\n1001,Cached Grand Prix,2025-06-29\n"
    with session_factory() as session:
        session.add(
            ApiCache(
                cache_key="season_csv:4370:2025",
                response_body=cached_csv,
                fetched_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        session.commit()

    calls = 0

    def fake_http_get(url, *, timeout=None, follow_redirects=None, **kwargs):
        nonlocal calls
        calls += 1
        html = (
            "<html><body><textarea>"
            "idEvent,Event,dateEvent\n1001,Fresh Grand &amp; Prix,2025-06-30\n"
            "</textarea></body></html>"
        )
        return httpx.Response(200, request=httpx.Request("GET", url), text=html)

    monkeypatch.setattr("sportscanner.upstream.thesportsdb.client.httpx.get", fake_http_get)
    client = TheSportsDbClient(settings, session_factory)
    competition = UpstreamCompetition(id="tsdb_4370", tsdb_id=4370, name="Formula 1")

    cached_events, cached_complete = client.season_events(competition, "2025")
    refreshed_events, refreshed_complete = client.season_events(competition, "2025", force_refresh=True)

    assert cached_complete is True
    assert refreshed_complete is True
    assert cached_events[0].name == "Cached Grand Prix"
    assert refreshed_events[0].name == "Fresh Grand & Prix"
    assert refreshed_events[0].date.isoformat() == "2025-06-30"
    assert calls == 1


def test_parse_csv_events_accepts_null_score_values(settings, session_factory) -> None:
    client = TheSportsDbClient(settings, session_factory)

    events = client._parse_csv_events(
        "idEvent,Home Team,Away Team,Round,Home Score,Away Score,dateEvent,Thumb\n"
        "1001,Arsenal,Tottenham,Round 4,,,2025-04-12,\n",
        "English Premier League",
        4328,
    )

    assert len(events) == 1
    assert events[0].home_score is None
    assert events[0].away_score is None
