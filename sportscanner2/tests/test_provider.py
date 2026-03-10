from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from sportscanner.db.models import Event


def test_root_redirects_to_admin(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/admin/"


def test_favicon_redirects_to_static_asset(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/favicon.ico", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/static/favicon.svg"


def test_provider_root(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv")

    assert response.status_code == 200
    provider = response.json()["MediaProvider"]
    assert provider["identifier"] == "tv.plex.agents.custom.sportscanner.metadata"
    assert provider["title"] == "SportScanner 2"
    assert any(item["type"] == 4 for item in provider["Types"])
    assert any(item["type"] == "metadata" for item in provider["Feature"])
    assert any(item["type"] == "match" for item in provider["Feature"])


def test_provider_root_uses_configured_identifier(provider_app) -> None:
    provider_app.state.services.settings.plex_provider_identifier = "tv.plex.agents.custom.sportscanner.metadata.local"
    provider_app.state.services.settings.plex_provider_group_name = "SportScanner 2 Local"
    client = TestClient(provider_app)

    response = client.get("/provider/tv")

    assert response.status_code == 200
    provider = response.json()["MediaProvider"]
    assert provider["identifier"] == "tv.plex.agents.custom.sportscanner.metadata.local"
    assert provider["title"] == "SportScanner 2 Local"


def test_provider_matches_show(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.post("/provider/tv/library/metadata/matches", json={"title": "Formula 1"})

    assert response.status_code == 200
    results = response.json()["MediaContainer"]["SearchResult"]
    assert results[0]["name"] == "Formula 1"


def test_provider_show_children(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/show_tsdb_4370/children")

    assert response.status_code == 200
    seasons = response.json()["MediaContainer"]["Metadata"]
    assert seasons[0]["type"] == "season"


def test_provider_episode_metadata(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary")

    assert response.status_code == 200
    metadata = response.json()["MediaContainer"]["Metadata"][0]
    assert metadata["index"] == 1150
    assert metadata["grandparentTitle"] == "Formula 1"


def test_provider_episode_metadata_prefers_event_date(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        assert event is not None
        event.date = event.date.replace(day=30)
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary")

    assert response.status_code == 200
    metadata = response.json()["MediaContainer"]["Metadata"][0]
    assert metadata["originallyAvailableAt"] == "2025-06-30"
