from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from sportscanner.db.models import Event


def test_provider_episode_images_fall_back_to_event_thumb(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        assert event is not None
        segment = event.segments[0]
        event.thumb_url = "https://example.com/upstream-event.jpg"
        segment.thumb_url = None
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/provider/tv/library/metadata/episode_seg_primary/images")

    assert response.status_code == 200
    image = response.json()["MediaContainer"]["Image"][0]
    assert image["url"] == "https://example.com/upstream-event.jpg"
