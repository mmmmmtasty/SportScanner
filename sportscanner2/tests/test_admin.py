from __future__ import annotations

import errno
import logging
from datetime import date
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

import sportscanner.organizer.placer as placer
from sportscanner.db.models import Competition, Event, ReviewTask, Recording
from sportscanner.log_buffer import LogBuffer
from sportscanner.plex import PlexRegistrationResult
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


def test_settings_page_explains_plex_fields(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/settings")

    assert response.status_code == 200
    assert "Normal Setup Order" in response.text
    assert "Plex Server URL" in response.text
    assert "Plex Token (X-Plex-Token)" in response.text
    assert "Provider Identifier In Plex" in response.text
    assert "Save Settings" in response.text
    assert "Register Provider And Group" in response.text


def test_save_settings_redirects_back_to_settings(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/admin/settings")


def test_save_settings_rejects_invalid_provider_identifier(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "sportscanner.metadata",
            "plex_provider_group_name": "SportScanner 2",
        },
    )

    assert response.status_code == 400
    assert "Action failed" in response.text
    assert "must start with tv.plex.agents." in response.text


def test_register_plex_redirects_to_get_result_page(provider_app) -> None:
    class FakePlex:
        def with_credentials(self, base_url, token):
            return self

        def register_provider_and_group(self, *, provider_uri, provider_identifier, provider_group_name):
            return PlexRegistrationResult(
                provider_identifier=provider_identifier,
                provider_uri=provider_uri,
                provider_group_id=42,
            )

    provider_app.state.services.plex = FakePlex()
    client = TestClient(provider_app)

    client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata.local",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.post("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("http://testserver/admin/register-plex?")


def test_register_plex_page_renders_create_library_form(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 200
    assert "Create Plex Test Library" in response.text


def test_register_plex_failure_renders_html_error(provider_app) -> None:
    class FakePlex:
        def with_credentials(self, base_url, token):
            return self

        def register_provider_and_group(self, *, provider_uri, provider_identifier, provider_group_name):
            request = httpx.Request("GET", "http://plex:32400/media/providers/metadata/group")
            response = httpx.Response(400, request=request, text="Bad Request")
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    provider_app.state.services.plex = FakePlex()
    client = TestClient(provider_app)

    client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata.local",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.post("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 200
    assert "Action Failed" in response.text
    assert "Plex returned 400 Bad Request." in response.text


def test_dashboard_shows_connected_plex_state(provider_app) -> None:
    class FakePlex:
        def with_credentials(self, base_url, token):
            return self

        def list_library_sections(self):
            return [
                {"key": 1, "title": "Sport_Test", "type": "show", "agent": "tv.plex.agents.series", "scanner": "Plex TV Series"},
                {"key": 2, "title": "Movies", "type": "movie", "agent": "", "scanner": ""},
            ]

    provider_app.state.services.plex = FakePlex()
    client = TestClient(provider_app)
    client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata.local",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.get("/admin/")

    assert response.status_code == 200
    assert "Inbox-first flow" in response.text
    assert "Inbox" in response.text
    assert "Plex connection" in response.text
    assert "Connected" in response.text


def test_review_queue_explains_resolution_flow(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        session.add(ReviewTask(recording_id="seg_primary", task_type="match_review"))
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/admin/review")

    assert response.status_code == 200
    assert "How To Work The Queue" in response.text
    assert "Choose The Right Outcome" in response.text


def test_create_plex_library_registers_provider_group_before_creation(provider_app) -> None:
    class FakePlex:
        def __init__(self) -> None:
            self.created_with = None
            self.registered_with = None

        def with_credentials(self, base_url, token):
            return self

        def register_provider_and_group(self, *, provider_uri, provider_identifier, provider_group_name):
            self.registered_with = (provider_uri, provider_identifier, provider_group_name)
            return PlexRegistrationResult(
                provider_identifier=provider_identifier,
                provider_uri=provider_uri,
                provider_group_id=42,
            )

        def create_tv_shows_library(self, *, name, location, provider_group_id, agent=None):
            self.created_with = (name, location, provider_group_id)
            return 17

    fake = FakePlex()
    provider_app.state.services.plex = fake
    client = TestClient(provider_app)
    client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata.local",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.post(
        "/admin/create-plex-library",
        data={"library_name": "Sport_Test", "library_location": "/sport/sportscanner2-dev"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert fake.registered_with is not None
    assert fake.created_with == ("Sport_Test", "/sport/sportscanner2-dev", 42)


def test_review_task_detail_shows_queue_position_and_ignore_action(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        session.add_all(
            [
                ReviewTask(recording_id="seg_primary", task_type="match_review"),
                ReviewTask(recording_id="seg_primary", task_type="match_review"),
            ]
        )
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/admin/review/1")

    assert response.status_code == 200
    assert "Queue item 1 of 2" in response.text
    assert "Ignore File" in response.text
    assert "Plex-facing title, date, summary," in response.text


def test_review_search_includes_upstream_lookup_action(provider_app) -> None:
    class SearchMetadataSource:
        name = "fake"

        def probe(self) -> str:
            return "v1"

        def all_competitions(self) -> list[UpstreamCompetition]:
            return []

        def search_filename(self, query: str) -> list[UpstreamEvent]:
            if "Australian" not in query:
                return []
            return [
                UpstreamEvent(
                    id="tsdb_9901",
                    tsdb_id=9901,
                    name="Australian Grand Prix Qualifying",
                    competition_name="Formula 1",
                    date=date(2025, 6, 28),
                )
            ]

        def events_on_day(self, competition_name: str, event_date: date) -> list[UpstreamEvent]:
            return []

        def season_events(self, competition: UpstreamCompetition, season_label: str) -> tuple[list[UpstreamEvent], bool]:
            return ([], False)

        def lookup_event(self, tsdb_event_id: int) -> UpstreamEvent | None:
            return None

    provider_app.state.services.metadata_source = SearchMetadataSource()
    with provider_app.state.services.session_factory() as session:
        session.add(ReviewTask(recording_id="seg_primary", task_type="match_review"))
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/admin/review/1/search?q=Australian")

    assert response.status_code == 200
    assert "Load From TheSportsDB" in response.text
    assert "TheSportsDB" in response.text


def test_plex_libraries_page_explains_refresh_vs_scan(provider_app) -> None:
    class FakePlex:
        def with_credentials(self, base_url, token):
            return self

        def list_library_sections(self):
            return [
                {"key": 1, "title": "Sport_Test", "type": "show", "agent": "tv.plex.agents.series", "scanner": "Plex TV Series"},
            ]

    provider_app.state.services.plex = FakePlex()
    client = TestClient(provider_app)
    client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata.local",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.get("/admin/plex-libraries")

    assert response.status_code == 200
    assert "Plex" in response.text
    assert "Refresh Queue" in response.text
    assert "Libraries" in response.text
    assert "Force Refresh" in response.text


def test_segment_detail_shows_matched_event_context(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/recordings/seg_primary")

    assert response.status_code == 200
    assert "What We Found" in response.text
    assert "Best Match" in response.text
    assert "Current Mapping" in response.text
    assert "Matched Event" in response.text
    assert "Austrian Grand Prix Race" in response.text
    assert "Refresh Item Metadata" in response.text


def test_segment_detail_persists_and_shows_cached_item_metadata(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        competition = session.scalar(select(Competition).where(Competition.id == "tsdb_4370"))
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        segment = session.get(Recording, "seg_primary")
        assert competition is not None
        assert event is not None
        assert segment is not None
        competition.poster_url = "https://example.com/show-poster.jpg"
        competition.fanart_url = "https://example.com/show-fanart.jpg"
        event.description = "Upstream summary for the cached record"
        event.thumb_url = "https://example.com/event-thumb.jpg"
        segment.duration_ms = 5_400_000
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/admin/recordings/seg_primary")

    assert response.status_code == 200
    assert "Cached Item Metadata" in response.text
    assert "Upstream summary for the cached record" in response.text
    assert "https://example.com/event-thumb.jpg" in response.text
    assert "https://example.com/show-poster.jpg" in response.text

    with provider_app.state.services.session_factory() as session:
        segment = session.get(Recording, "seg_primary")
        assert segment is not None
        assert segment.metadata_source == "fake"
        assert segment.metadata_record is not None
        assert segment.metadata_record["event"]["tsdbId"] == 1001
        assert any(image["url"] == "https://example.com/event-thumb.jpg" for image in segment.metadata_images or [])


def test_competitions_page_shows_row_refresh_actions(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/competitions")

    assert response.status_code == 200
    assert "Refresh Schedule" in response.text
    assert "Library" in response.text
    assert "/admin/competitions/tsdb_4370/refresh-metadata" in response.text


def test_competition_refresh_route_updates_competition_and_segment_metadata(provider_app) -> None:
    metadata = provider_app.state.services.metadata_source
    metadata._f1.poster_url = "https://example.com/f1-updated-poster.jpg"
    metadata._f1.fanart_url = "https://example.com/f1-updated-fanart.jpg"
    metadata._f1_event = UpstreamEvent(
        id="tsdb_1001",
        tsdb_id=1001,
        name="Austrian Grand Prix Updated",
        competition_name="Formula 1",
        date=date(2025, 6, 30),
    )
    client = TestClient(provider_app)

    response = client.post("/admin/competitions/tsdb_4370/refresh-metadata", follow_redirects=False)

    assert response.status_code == 303
    with provider_app.state.services.session_factory() as session:
        competition = session.scalar(select(Competition).where(Competition.id == "tsdb_4370"))
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        segment = session.get(Recording, "seg_primary")
        assert competition is not None
        assert event is not None
        assert segment is not None
        assert competition.poster_url == "https://example.com/f1-updated-poster.jpg"
        assert event.name == "Austrian Grand Prix Updated"
        assert event.date == date(2025, 6, 30)
        assert segment.metadata_record is not None
        assert segment.metadata_record["event"]["date"] == "2025-06-30"
        assert any(
            image["url"] == "https://example.com/f1-updated-poster.jpg"
            for image in segment.metadata_images or []
        )


def test_competition_refresh_route_handles_cross_device_managed_file_moves(provider_app, monkeypatch) -> None:
    settings = provider_app.state.services.settings
    old_managed = settings.db_path.parent / "old-library" / "Formula 1.mkv"
    old_managed.parent.mkdir(parents=True, exist_ok=True)
    old_managed.write_text("managed file", encoding="utf-8")

    with provider_app.state.services.session_factory() as session:
        segment = session.get(Recording, "seg_primary")
        assert segment is not None
        segment.managed_path = str(old_managed)
        session.commit()

    original_replace = placer.os.replace

    def flaky_replace(source: str | Path, target: str | Path) -> None:
        if Path(source) == old_managed:
            raise OSError(errno.EXDEV, "Cross-device link")
        original_replace(source, target)

    monkeypatch.setattr(placer.os, "replace", flaky_replace)

    client = TestClient(provider_app)
    response = client.post("/admin/competitions/tsdb_4370/refresh-metadata", follow_redirects=False)

    assert response.status_code == 303
    with provider_app.state.services.session_factory() as session:
        segment = session.get(Recording, "seg_primary")
        assert segment is not None
        assert segment.managed_path is not None
        destination = Path(segment.managed_path)
        assert destination.exists()
        assert destination.read_text(encoding="utf-8") == "managed file"
    assert not old_managed.exists()


def test_segment_refresh_route_retries_item_metadata(provider_app) -> None:
    metadata = provider_app.state.services.metadata_source
    metadata._f1_event = UpstreamEvent(
        id="tsdb_1001",
        tsdb_id=1001,
        name="Austrian Grand Prix Revised",
        competition_name="Formula 1",
        date=date(2025, 6, 28),
    )
    client = TestClient(provider_app)

    response = client.post("/admin/recordings/seg_primary/refresh-metadata", follow_redirects=False)

    assert response.status_code == 303
    with provider_app.state.services.session_factory() as session:
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        segment = session.get(Recording, "seg_primary")
        assert event is not None
        assert segment is not None
        assert event.name == "Austrian Grand Prix Revised"
        assert event.date == date(2025, 6, 28)
        assert segment.metadata_record is not None
        assert segment.metadata_record["event"]["date"] == "2025-06-28"


def test_stats_json_returns_counts(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/stats.json")

    assert response.status_code == 200
    data = response.json()
    assert "competitions" in data
    assert "published_recordings" in data
    assert "open_review_tasks" in data


def test_logs_page_renders(provider_app) -> None:
    buf = LogBuffer(session_factory=provider_app.state.services.session_factory)
    buf.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("sportscanner.test_logs_page")
    logger.setLevel(logging.INFO)
    logger.addHandler(buf)
    provider_app.state.log_buffer = buf
    try:
        logger.info("persisted log entry")
        client = TestClient(provider_app)

        response = client.get("/admin/logs")

        assert response.status_code == 200
        assert "Logs" in response.text
        assert "persisted log entry" in response.text
    finally:
        logger.removeHandler(buf)


def test_logs_entries_returns_filtered_rows(provider_app) -> None:
    buf = LogBuffer(session_factory=provider_app.state.services.session_factory)
    buf.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("sportscanner.test_logs")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(buf)
    provider_app.state.log_buffer = buf
    try:
        logger.info("hello from info")
        logger.debug("hello from debug")
        logger.error("something went wrong")

        client = TestClient(provider_app)

        response = client.get("/admin/logs/entries")
        assert response.status_code == 200
        assert "hello from info" in response.text

        response = client.get("/admin/logs/entries?level=ERROR")
        assert "something went wrong" in response.text
        assert "hello from info" not in response.text

        response = client.get("/admin/logs/entries?keyword=wrong")
        assert "something went wrong" in response.text
        assert "hello from info" not in response.text
    finally:
        logger.removeHandler(buf)
