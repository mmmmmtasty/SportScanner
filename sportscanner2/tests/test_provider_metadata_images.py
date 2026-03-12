from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from sportscanner.db.models import Competition, Event


def test_provider_episode_images_fall_back_to_event_thumb(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        assert event is not None
        segment = event.recordings[0]
        event.thumb_url = "https://example.com/upstream-event.jpg"
        segment.thumb_url = None
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary/images")

    assert response.status_code == 200
    image = response.json()["MediaContainer"]["Image"][0]
    assert image["url"] == "https://example.com/upstream-event.jpg"


def test_provider_episode_images_includes_competition_art(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        comp = session.get(Competition, "tsdb_4370")
        comp.poster_url = "https://example.com/f1-poster.jpg"
        comp.fanart_url = "https://example.com/f1-fanart.jpg"
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        event.thumb_url = "https://example.com/event-thumb.jpg"
        event.recordings[0].thumb_url = None
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary/images")

    assert response.status_code == 200
    images = response.json()["MediaContainer"]["Image"]
    types = {img["type"]: img["url"] for img in images}
    assert types["snapshot"] == "https://example.com/event-thumb.jpg"
    assert types["coverPoster"] == "https://example.com/f1-poster.jpg"
    assert types["background"] == "https://example.com/f1-fanart.jpg"


def test_provider_season_images_includes_poster_and_fanart(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        comp = session.get(Competition, "tsdb_4370")
        comp.poster_url = "https://example.com/f1-poster.jpg"
        comp.fanart_url = "https://example.com/f1-fanart.jpg"
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/season_tsdb_4370_2025/images")

    assert response.status_code == 200
    images = response.json()["MediaContainer"]["Image"]
    types = {img["type"]: img["url"] for img in images}
    assert types["coverPoster"] == "https://example.com/f1-poster.jpg"
    assert types["background"] == "https://example.com/f1-fanart.jpg"


def test_provider_season_images_omits_missing_fanart(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        comp = session.get(Competition, "tsdb_4370")
        comp.poster_url = "https://example.com/f1-poster.jpg"
        comp.fanart_url = None
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/season_tsdb_4370_2025/images")

    assert response.status_code == 200
    images = response.json()["MediaContainer"]["Image"]
    assert len(images) == 1
    assert images[0]["type"] == "coverPoster"


def test_provider_episode_images_omits_missing_thumb(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        comp = session.get(Competition, "tsdb_4370")
        comp.poster_url = "https://example.com/f1-poster.jpg"
        comp.fanart_url = "https://example.com/f1-fanart.jpg"
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        event.thumb_url = None
        event.recordings[0].thumb_url = None
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary/images")

    assert response.status_code == 200
    images = response.json()["MediaContainer"]["Image"]
    types = {img["type"] for img in images}
    assert "snapshot" not in types
    assert "coverPoster" in types
    assert "background" in types
