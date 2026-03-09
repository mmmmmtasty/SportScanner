from __future__ import annotations

from fastapi.testclient import TestClient


def test_provider_root(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv")

    assert response.status_code == 200
    body = response.json()
    assert body["MediaProvider"]["identifier"] == "tv.plex.agents.custom.sportscanner.metadata"


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

