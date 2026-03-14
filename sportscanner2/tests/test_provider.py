from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from sportscanner.db.models import Competition, Event, Recording


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
    assert all(item["Scheme"][0]["scheme"] == provider["identifier"] for item in provider["Types"])
    assert any(item["type"] == "metadata" for item in provider["Feature"])
    match_feature = next(item for item in provider["Feature"] if item["type"] == "match")
    assert match_feature == {"type": "match", "key": "/library/metadata/matches"}


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


def test_provider_matches_show_from_episode_guid(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.post(
        "/provider/tv/library/metadata/matches",
        json={
            "type": 2,
            "guid": "tv.plex.agents.custom.sportscanner.metadata://episode/episode_seg_primary",
            "title": "Austrian Grand Prix Race",
        },
    )

    assert response.status_code == 200
    results = response.json()["MediaContainer"]["Metadata"]
    assert len(results) == 1
    assert results[0]["type"] == "show"
    assert results[0]["title"] == "Formula 1"


def test_provider_matches_season_from_episode_guid(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.post(
        "/provider/tv/library/metadata/matches",
        json={
            "type": 3,
            "guid": "tv.plex.agents.custom.sportscanner.metadata://episode/episode_seg_primary",
            "title": "Season 2025",
            "parentTitle": "Austrian Grand Prix Race",
            "index": 2025,
        },
    )

    assert response.status_code == 200
    results = response.json()["MediaContainer"]["Metadata"]
    assert len(results) == 1
    assert results[0]["type"] == "season"
    assert results[0]["title"] == "2025"
    assert results[0]["parentTitle"] == "Formula 1"


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


def test_provider_show_children_supports_plex_paging_headers(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get(
        "/provider/tv/library/metadata/show_tsdb_4370/children",
        headers={"X-Plex-Container-Start": "0", "X-Plex-Container-Size": "1"},
    )

    assert response.status_code == 200
    container = response.json()["MediaContainer"]
    assert container["offset"] == 0
    assert container["size"] == 1
    assert container["totalSize"] == 1


def test_provider_show_children_supports_plex_paging_query_params(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get(
        "/provider/tv/library/metadata/show_tsdb_4370/children?X-Plex-Container-Start=0&X-Plex-Container-Size=1"
    )

    assert response.status_code == 200
    container = response.json()["MediaContainer"]
    assert container["offset"] == 0
    assert container["size"] == 1
    assert container["totalSize"] == 1


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


def test_provider_episode_duration_is_included_when_set(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        segment = session.get(Recording, "seg_primary")
        segment.duration_ms = 5_400_000  # 90 minutes
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary")

    assert response.status_code == 200
    metadata = response.json()["MediaContainer"]["Metadata"][0]
    assert metadata["duration"] == 5_400_000


def test_provider_episode_duration_absent_when_unset(provider_app) -> None:
    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary")

    assert response.status_code == 200
    metadata = response.json()["MediaContainer"]["Metadata"][0]
    assert "duration" not in metadata


def test_provider_episode_metadata_prefers_upstream_event_fields(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        competition = session.scalar(select(Competition).where(Competition.id == "tsdb_4370"))
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        assert competition is not None
        assert event is not None
        segment = event.recordings[0]
        competition.poster_url = "https://example.com/show.jpg"
        competition.formed_year = 1950
        event.name = "2025 Formula 1 Australian Grand Prix - Qualifying"
        event.description = "Upstream summary"
        event.thumb_url = "https://example.com/event.jpg"
        segment.kind = "qualifying"
        segment.title = "Qualifying"
        segment.summary = None
        segment.thumb_url = None
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary")

    assert response.status_code == 200
    metadata = response.json()["MediaContainer"]["Metadata"][0]
    assert metadata["title"] == "Australian Grand Prix - Qualifying"
    assert metadata["summary"] == "Upstream summary"
    assert metadata["thumb"].startswith("http://testserver/provider/tv/assets/event/tsdb_1001/thumb")
    assert metadata["parentThumb"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/poster")
    assert metadata["grandparentType"] == "show"
    assert metadata["grandparentThumb"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/poster")
    assert metadata["year"] == 2025


def test_provider_episode_match_uses_display_title_similarity(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        assert event is not None
        segment = event.recordings[0]
        event.name = "2025 Formula 1 Australian Grand Prix - Qualifying"
        segment.kind = "qualifying"
        segment.title = "Qualifying"
        session.commit()

    client = TestClient(provider_app)
    response = client.post(
        "/provider/tv/library/metadata/matches",
        json={
            "type": 4,
            "title": "Australian Grand Prix - Qualifying",
            "grandparentTitle": "Formula 1",
        },
    )

    assert response.status_code == 200
    results = response.json()["MediaContainer"]["Metadata"]
    assert len(results) == 1
    assert results[0]["title"] == "Australian Grand Prix - Qualifying"


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


def test_provider_episode_images_use_snapshot_type(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        assert event is not None
        segment = event.recordings[0]
        segment.thumb_url = "https://example.com/episode.jpg"
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary/images")

    assert response.status_code == 200
    image = response.json()["MediaContainer"]["Image"][0]
    assert image["type"] == "snapshot"


def test_provider_all_four_sports_in_library(provider_app) -> None:
    """Verify all 4 sports (F1, EPL, NBA, UFC) are accessible via the provider API."""
    client = TestClient(provider_app)

    sports = [
        ("Formula 1", "seg_primary", "Austrian Grand Prix Race"),
        ("English Premier League", "seg_epl_001", "Arsenal vs Tottenham"),
        ("NBA", "seg_nba_001", "Lakers vs Celtics"),
        ("UFC", "seg_ufc_001", "310 Main Card"),
    ]

    for show_title, segment_id, episode_title in sports:
        # Show is matchable by title
        match_response = client.post(
            "/provider/tv/library/metadata/matches",
            json={"type": 2, "title": show_title},
        )
        assert match_response.status_code == 200, f"{show_title}: match failed"
        results = match_response.json()["MediaContainer"]["Metadata"]
        assert any(r["title"] == show_title for r in results), f"{show_title}: not found in matches"

        # Episode is retrievable by segment id
        ep_response = client.get(f"/provider/tv/library/metadata/episode_{segment_id}")
        assert ep_response.status_code == 200, f"{show_title}: episode fetch failed"
        metadata = ep_response.json()["MediaContainer"]["Metadata"][0]
        assert metadata["grandparentTitle"] == show_title, f"{show_title}: wrong grandparentTitle"
        assert metadata["title"] == episode_title, f"{show_title}: wrong episode title"


def test_provider_show_and_season_metadata_include_original_dates(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        competition = session.scalar(select(Competition).where(Competition.id == "tsdb_4370"))
        assert competition is not None
        competition.formed_year = 1950
        competition.poster_url = "https://example.com/show.jpg"
        session.commit()

    client = TestClient(provider_app)

    show_response = client.get("/provider/tv/library/metadata/show_tsdb_4370")
    season_response = client.get("/provider/tv/library/metadata/season_tsdb_4370_2025")

    assert show_response.status_code == 200
    assert season_response.status_code == 200
    show_metadata = show_response.json()["MediaContainer"]["Metadata"][0]
    season_metadata = season_response.json()["MediaContainer"]["Metadata"][0]
    assert show_metadata["originallyAvailableAt"] == "1950-01-01"
    assert season_metadata["originallyAvailableAt"] == "2025-06-29"
    assert season_metadata["parentThumb"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/poster")
    assert season_metadata["thumb"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/poster")
