from __future__ import annotations

import httpx

from fastapi.testclient import TestClient
from sqlalchemy import select

from sportscanner.db.models import Asset, Competition, Event


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
    assert image["url"].startswith("http://testserver/provider/tv/assets/event/tsdb_1001/thumb")


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
    assert types["snapshot"].startswith("http://testserver/provider/tv/assets/event/tsdb_1001/thumb")
    assert types["coverPoster"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/poster")
    assert types["background"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/fanart")


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
    assert types["coverPoster"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/poster")
    assert types["background"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/fanart")


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
    assert images[0]["url"].startswith("http://testserver/provider/tv/assets/competition/tsdb_4370/poster")


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


def test_provider_asset_route_downloads_and_caches_artwork(provider_app, monkeypatch) -> None:
    def fake_get(url: str, timeout: float, follow_redirects: bool) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            content=b"jpeg-data",
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    client = TestClient(provider_app)

    response = client.get(
        "/provider/tv/assets/event/tsdb_1001/thumb",
        params={"source_url": "https://images.example.com/event-thumb.jpg"},
    )

    assert response.status_code == 200
    assert response.content == b"jpeg-data"
    assert response.headers["content-type"].startswith("image/jpeg")

    with provider_app.state.services.session_factory() as session:
        asset = session.scalar(
            select(Asset).where(
                Asset.entity_type == "event",
                Asset.entity_id == "tsdb_1001",
                Asset.asset_type == "thumb",
            )
        )
        assert asset is not None
        assert asset.cached_path is not None
