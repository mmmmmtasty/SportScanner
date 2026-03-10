from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sportscanner.db.models import ApiCache
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
