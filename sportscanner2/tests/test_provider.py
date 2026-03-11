from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from sportscanner.db.models import Competition, Event


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
    match_feature = next(item for item in provider["Feature"] if item["type"] == "match")
    assert match_feature["method"] == "POST"
    assert match_feature["mediaTypes"] == [2]


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
    response = client.post("/provider/tv/library/metadata/matches", json={"type": 2, "title": "Formula 1"})

    assert response.status_code == 200
    container = response.json()["MediaContainer"]
    assert container["identifier"] == "tv.plex.agents.custom.sportscanner.metadata"
    results = container["Metadata"]
    assert len(results) == 1
    assert results[0]["title"] == "Formula 1"
    assert results[0]["type"] == "show"


def test_provider_matches_show_via_query_params(provider_app) -> None:
    """Plex sends match requests as query parameters, not a JSON body."""
    client = TestClient(provider_app)
    response = client.post("/provider/tv/library/metadata/matches?title=Formula+1&type=2")

    assert response.status_code == 200
    results = response.json()["MediaContainer"]["Metadata"]
    assert results[0]["title"] == "Formula 1"


def test_provider_match_requires_type(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.post("/provider/tv/library/metadata/matches", json={"title": "Formula 1"})

    assert response.status_code == 400
    assert response.json()["detail"] == "type is required"


def test_provider_manual_match_returns_ranked_metadata(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        session.add(Competition(id="tsdb_4400", tsdb_id=4400, name="Formula E"))
        session.commit()

    client = TestClient(provider_app)
    response = client.post(
        "/provider/tv/library/metadata/matches",
        json={"type": 2, "title": "Formula", "manual": 1},
    )

    assert response.status_code == 200
    results = response.json()["MediaContainer"]["Metadata"]
    assert [item["title"] for item in results] == ["Formula 1", "Formula E"]
    assert results[0]["score"] >= results[1]["score"]


def test_provider_matches_with_include_children_returns_episodes(provider_app) -> None:
    """Plex expects includeChildren to return the matched parent plus direct child metadata."""
    client = TestClient(provider_app)
    response = client.post(
        "/provider/tv/library/metadata/matches",
        json={"type": 2, "title": "Formula 1", "includeChildren": 1},
    )

    assert response.status_code == 200
    matched = response.json()["MediaContainer"]["Metadata"][0]
    assert matched["type"] == "show"
    assert matched["title"] == "Formula 1"
    assert matched["Children"]["size"] == 1
    child = matched["Children"]["Metadata"][0]
    assert child["type"] == "season"
    assert child["index"] == 2025
    assert child["parentTitle"] == "Formula 1"


def test_provider_show_children(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/show_tsdb_4370/children")

    assert response.status_code == 200
    container = response.json()["MediaContainer"]
    assert container["identifier"] == "tv.plex.agents.custom.sportscanner.metadata"
    seasons = container["Metadata"]
    assert seasons[0]["type"] == "season"


def test_provider_show_metadata_uses_children_key_and_include_children(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/show_tsdb_4370?includeChildren=1")

    assert response.status_code == 200
    metadata = response.json()["MediaContainer"]["Metadata"][0]
    assert metadata["key"] == "/library/metadata/show_tsdb_4370/children"
    assert metadata["Children"]["size"] == 1
    assert metadata["Children"]["Metadata"][0]["type"] == "season"


def test_provider_episode_metadata(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary")

    assert response.status_code == 200
    metadata = response.json()["MediaContainer"]["Metadata"][0]
    assert metadata["index"] == 1150
    assert metadata["grandparentTitle"] == "Formula 1"


def test_provider_invalid_rating_key_returns_404(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/not_a_real_key")

    assert response.status_code == 404


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
