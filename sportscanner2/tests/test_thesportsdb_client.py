from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx

from sportscanner.db.models import ApiCache
from sportscanner.log_buffer import LogBuffer
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
