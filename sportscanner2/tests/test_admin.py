from __future__ import annotations

import errno
import logging
from datetime import date
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

import sportscanner.organizer.placer as placer
from sportscanner.db.models import AppSetting, Competition, CompetitionSeason, Event, ReviewTask, Recording
from sportscanner.log_buffer import LogBuffer
from sportscanner.plex import PlexRegistrationResult
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


def test_settings_page_explains_plex_fields(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/settings")

    assert response.status_code == 200
    assert "Plex Server URL" in response.text
    assert "Plex Token (X-Plex-Token)" in response.text
    assert "Provider Identifier In Plex" in response.text
    assert "Incoming Directory" in response.text
    assert "Library Directory" in response.text
    assert "Save Settings" in response.text
    assert "Register Provider And Group" in response.text


def test_settings_page_falls_back_when_directory_settings_are_blank(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        session.add(AppSetting(key="incoming_dir", value="   "))
        session.add(AppSetting(key="library_dir", value=""))
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/admin/settings")

    assert response.status_code == 200
    assert f'value="{provider_app.state.services.settings.incoming_dir}"' in response.text
    assert f'value="{provider_app.state.services.settings.library_dir}"' in response.text


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


def test_save_settings_persists_directory_overrides_and_updates_runtime(provider_app, tmp_path: Path) -> None:
    client = TestClient(provider_app)
    incoming_dir = tmp_path / "docker-incoming"
    library_dir = tmp_path / "docker-library"
    incoming_dir.mkdir()

    response = client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata",
            "plex_provider_group_name": "SportScanner 2",
            "incoming_dir": str(incoming_dir),
            "library_dir": str(library_dir),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert provider_app.state.services.settings.incoming_dir == incoming_dir
    assert provider_app.state.services.settings.library_dir == library_dir
    with provider_app.state.services.session_factory() as session:
        assert session.get(AppSetting, "incoming_dir").value == str(incoming_dir)
        assert session.get(AppSetting, "library_dir").value == str(library_dir)


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


def test_save_settings_rejects_relative_directory_override(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata",
            "plex_provider_group_name": "SportScanner 2",
            "incoming_dir": "incoming",
        },
    )

    assert response.status_code == 400
    assert "Incoming Directory must be an absolute path." in response.text


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
    assert f'value="{provider_app.state.services.settings.library_dir}"' in response.text


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
    assert "Inbox-first flow" not in response.text
    assert "Inbox" in response.text
    assert "Plex connection" in response.text
    assert "Connected" in response.text
    assert "Review Queue" in response.text


def test_review_queue_explains_resolution_flow(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        session.add(ReviewTask(recording_id="seg_primary", task_type="match_review"))
        session.commit()

    client = TestClient(provider_app)
    response = client.get("/admin/review")

    assert response.status_code == 200
    assert "How To Work The Queue" in response.text
    assert "Choose The Right Outcome" in response.text
    assert "Potential Matches" in response.text


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
        event = session.get(Event, "tsdb_1001")
        assert event is not None
        event.description = (
            "Race recap " * 20
            + "withaverylongunbrokenstretchoftextthatshouldwrapinsidethecardwithoutblowingupthelayout"
        )
        session.add_all(
            [
                ReviewTask(
                    recording_id="seg_primary",
                    task_type="match_review",
                    candidates=[{"event_id": "tsdb_1001", "name": event.name, "confidence": 0.84}],
                ),
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
    assert "Potential Matches" in response.text
    assert "Automatic Candidates" not in response.text
    assert "match-description" in response.text
    assert "withaverylongunbrokenstretchoftextthatshouldwrapinsidethecardwithoutblowingupthelayout" in response.text


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
                    description="Extended session notes " * 8,
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
    assert "Extended session notes" in response.text
    assert "match-description" in response.text


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
    with provider_app.state.services.session_factory() as session:
        segment = session.get(Recording, "seg_primary")
        segment.match_method = "auto_high_confidence"
        session.commit()

    client = TestClient(provider_app)

    response = client.get("/admin/recordings/seg_primary")

    assert response.status_code == 200
    assert "File Details" in response.text
    assert "Best Match" in response.text
    assert "Current Mapping" in response.text
    assert "Manual Override" in response.text
    assert "Matched event" in response.text
    assert "Match method" in response.text
    assert "Automatic" in response.text
    assert "Austrian Grand Prix Race" in response.text
    assert "Refresh Event Metadata" in response.text
    assert "Refresh File Metadata" in response.text
    assert "Event" in response.text
    assert "Season 2025" in response.text


def test_segment_detail_persists_manual_overrides(provider_app) -> None:
    with provider_app.state.services.session_factory() as session:
        session.add(
            CompetitionSeason(
                id="season_tsdb_4370_2026",
                competition_id="tsdb_4370",
                season_number=2026,
                label="2026",
                is_complete=True,
            )
        )
        session.commit()

    client = TestClient(provider_app)
    response = client.post(
        "/admin/recordings/seg_primary",
        data={
            "title": "Austria GP Main Event",
            "kind": "race",
            "summary": "Manual summary override",
            "competition_season_id": "season_tsdb_4370_2026",
            "air_date": "2025-07-01",
            "episode_number": "1301",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with provider_app.state.services.session_factory() as session:
        segment = session.get(Recording, "seg_primary")
        assert segment is not None
        assert segment.title == "Austria GP Main Event"
        assert segment.summary == "Manual summary override"
        assert segment.competition_season_id == "season_tsdb_4370_2026"
        assert segment.air_date == date(2025, 7, 1)
        assert segment.episode_number == 1301
        expected_path = (
            provider_app.state.services.settings.library_dir
            / "Formula 1"
            / "Season 2026"
            / "Formula 1 - 2025-07-01 - Austria GP Main Event.mkv"
        )
        assert segment.managed_path == str(expected_path)
        assert segment.metadata_record is not None
        assert segment.metadata_record["title"] == "Austria GP Main Event"
        assert segment.metadata_record["summary"] == "Manual summary override"
        assert segment.metadata_record["originallyAvailableAt"] == "2025-07-01"
        assert segment.metadata_record["season"]["title"] == "2026"


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
        assert segment.metadata_record["competition"]["sport"] == "Motorsport"
        assert segment.metadata_record["event"]["summary"] == "Upstream summary for the cached record"
        assert segment.metadata_record["event"]["tsdbId"] == 1001
        assert any(image["url"] == "https://example.com/event-thumb.jpg" for image in segment.metadata_images or [])


def test_competitions_page_shows_row_refresh_actions(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/competitions")

    assert response.status_code == 200
    assert "Refresh Competition" in response.text
    assert "Library" in response.text
    assert "/admin/competitions/tsdb_4370/refresh-metadata" in response.text


def test_inbox_uses_column_header_filters_and_links_no_match_to_review_queue(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/inbox")

    assert response.status_code == 200
    assert 'id="inbox-filter-form"' in response.text
    assert 'aria-label="Filter inbox by status"' in response.text
    assert 'aria-label="Filter inbox by competition"' in response.text
    assert 'aria-label="Filter inbox by confidence"' in response.text
    assert "Date from" not in response.text
    assert 'href="http://testserver/admin/review"' in response.text


def test_season_page_removes_refresh_competition_button_and_hides_breadcrumb_panel(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/library/tsdb_4370/seasons/season_tsdb_4370_2025")

    assert response.status_code == 200
    assert 'aria-label="Breadcrumb"' not in response.text
    assert "Formula 1" in response.text
    assert "2025" in response.text
    assert "Refresh Season" in response.text
    assert "Confidence" in response.text
    assert "season-event-row-high" in response.text
    assert "Refreshing Competition" not in response.text


def test_competition_refresh_route_updates_competition_and_segment_metadata(provider_app) -> None:
    metadata = provider_app.state.services.metadata_source
    metadata._f1.poster_url = "https://example.com/f1-updated-poster.jpg"
    metadata._f1.fanart_url = "https://example.com/f1-updated-fanart.jpg"
    metadata._f1.source_payload = {"idLeague": "4370", "strLeague": "Formula 1", "strPoster": metadata._f1.poster_url}
    metadata._f1_event = UpstreamEvent(
        id="tsdb_1001",
        tsdb_id=1001,
        name="Austrian Grand Prix Updated",
        competition_name="Formula 1",
        date=date(2025, 6, 30),
        source_payload={"idEvent": "1001", "strEvent": "Austrian Grand Prix Updated"},
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
        assert competition.upstream_metadata == {"idLeague": "4370", "strLeague": "Formula 1", "strPoster": metadata._f1.poster_url}
        assert event.name == "Austrian Grand Prix Updated"
        assert event.date == date(2025, 6, 30)
        assert event.upstream_metadata == {"idEvent": "1001", "strEvent": "Austrian Grand Prix Updated"}
        assert segment.metadata_record is not None
        assert segment.metadata_record["event"]["date"] == "2025-06-30"
        assert segment.metadata_record["event"]["upstreamMetadata"] == {"idEvent": "1001", "strEvent": "Austrian Grand Prix Updated"}
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


def test_refresh_route_redirects_with_queue_flash_when_refresh_raises(provider_app, monkeypatch) -> None:
    def fail_refresh(recording_id: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(provider_app.state.services.organizer, "refresh_recording_metadata", fail_refresh)
    client = TestClient(provider_app)

    response = client.post("/admin/recordings/seg_primary/refresh-metadata", follow_redirects=False)

    assert response.status_code == 303
    assert "flash=File+metadata+refresh+queued." in response.headers["location"]


def test_refresh_route_supports_htmx_queue_feedback(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.post(
        "/admin/recordings/seg_primary/refresh-metadata",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 204
    assert "showToast" in response.headers["HX-Trigger"]


def test_season_refresh_route_updates_only_the_requested_season(provider_app) -> None:
    metadata = provider_app.state.services.metadata_source
    metadata._f1_event = UpstreamEvent(
        id="tsdb_1001",
        tsdb_id=1001,
        name="Austrian Grand Prix Season Refresh",
        competition_name="Formula 1",
        date=date(2025, 6, 27),
    )
    client = TestClient(provider_app)

    response = client.post(
        "/admin/library/tsdb_4370/seasons/season_tsdb_4370_2025/refresh-metadata",
        follow_redirects=False,
    )

    assert response.status_code == 303
    with provider_app.state.services.session_factory() as session:
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        assert event is not None
        assert event.name == "Austrian Grand Prix Season Refresh"
        assert event.date == date(2025, 6, 27)


def test_event_refresh_route_updates_attached_recording_metadata(provider_app) -> None:
    metadata = provider_app.state.services.metadata_source
    metadata._f1_event = UpstreamEvent(
        id="tsdb_1001",
        tsdb_id=1001,
        name="Austrian Grand Prix Event Refresh",
        competition_name="Formula 1",
        date=date(2025, 7, 2),
    )
    client = TestClient(provider_app)

    response = client.post("/admin/events/tsdb_1001/refresh-metadata", follow_redirects=False)

    assert response.status_code == 303
    with provider_app.state.services.session_factory() as session:
        event = session.scalar(select(Event).where(Event.id == "tsdb_1001"))
        segment = session.get(Recording, "seg_primary")
        assert event is not None
        assert segment is not None
        assert event.name == "Austrian Grand Prix Event Refresh"
        assert event.date == date(2025, 7, 2)
        assert segment.metadata_record is not None
        assert segment.metadata_record["event"]["date"] == "2025-07-02"


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
        assert 'id="log-filter-controls"' in response.text
        assert 'id="log-filter-reset"' in response.text
        assert 'aria-label="Filter by level"' in response.text
        assert "refreshIntervalMs = 3000" in response.text
        assert "Current <strong>INFO</strong>" in response.text
        assert "persisted log entry" in response.text
    finally:
        logger.removeHandler(buf)


def test_advanced_page_redirects_to_logs(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/advanced", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("/admin/logs")


def test_configure_logs_persists_level(provider_app) -> None:
    client = TestClient(provider_app)
    sport_logger = logging.getLogger("sportscanner")
    original_level = sport_logger.level

    try:
        response = client.post("/admin/logs/configure", data={"level": "DEBUG"}, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"].endswith("/admin/logs")
        assert sport_logger.getEffectiveLevel() == logging.DEBUG
        with provider_app.state.services.session_factory() as session:
            setting = session.get(AppSetting, "log_level")
            assert setting is not None
            assert setting.value == "DEBUG"
    finally:
        sport_logger.setLevel(original_level)


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
        assert "hello from debug" not in response.text

        response = client.get("/admin/logs/entries?level=DEBUG")
        assert "hello from debug" in response.text
        assert "hello from info" not in response.text
        assert "something went wrong" not in response.text

        response = client.get("/admin/logs/entries?keyword=wrong")
        assert "something went wrong" in response.text
        assert "hello from info" not in response.text
    finally:
        logger.removeHandler(buf)


def test_logs_page_renders_structured_payload(provider_app) -> None:
    buf = LogBuffer(session_factory=provider_app.state.services.session_factory)
    buf.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("sportscanner.test_logs_payload")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(buf)
    provider_app.state.log_buffer = buf
    try:
        logger.debug(
            "received upstream payload",
            extra={"structured_data": {"source": "live", "payload": {"events": [{"idEvent": "1234"}]}}},
        )
        client = TestClient(provider_app)

        response = client.get("/admin/logs")

        assert response.status_code == 200
        assert "View JSON payload" in response.text
        assert "expandedPayloadRows" in response.text
        assert "stopRefreshLoop()" in response.text
        assert 'closest(".log-payload > summary")' in response.text
        assert "idEvent" in response.text
        assert "1234" in response.text
    finally:
        logger.removeHandler(buf)
