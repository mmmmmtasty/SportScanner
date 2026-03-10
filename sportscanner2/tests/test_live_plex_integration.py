from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from pathlib import PurePosixPath

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sportscanner.db.models import Competition, CompetitionSeason, Event
from sportscanner.organizer.placer import build_managed_filename, season_directory_name


pytestmark = pytest.mark.plex_integration


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    pytest.skip(f"{name} is required for live Plex integration tests")


def _db_path() -> Path:
    configured = os.getenv("SPORTSCANNER_DB_PATH")
    if configured:
        return Path(configured)
    local_default = Path(__file__).resolve().parents[1] / "data" / "sportscanner.db"
    if local_default.exists():
        return local_default
    pytest.skip("SPORTSCANNER_DB_PATH is required for the live ingest integration test")


def _seed_live_event(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with factory() as session:
            competition = session.scalar(select(Competition).where(Competition.name == "Formula 1"))
            if competition is None:
                competition = Competition(id="tsdb_4370", tsdb_id=4370, name="Formula 1")
                session.add(competition)
                session.flush()
            elif competition.tsdb_id is None:
                competition.tsdb_id = 4370

            season = session.scalar(
                select(CompetitionSeason).where(
                    CompetitionSeason.competition_id == competition.id,
                    CompetitionSeason.season_number == 2025,
                )
            )
            if season is None:
                season = CompetitionSeason(
                    id=f"season_{competition.id}_2025",
                    competition_id=competition.id,
                    season_number=2025,
                    label="2025",
                    is_complete=True,
                )
                session.add(season)
                session.flush()
            else:
                season.is_complete = True

            event = session.scalar(select(Event).where(Event.tsdb_id == 1001))
            if event is None:
                event = Event(
                    id="tsdb_1001",
                    tsdb_id=1001,
                    competition_season_id=season.id,
                    name="Austrian Grand Prix",
                    date=date(2025, 6, 29),
                )
                session.add(event)
            else:
                event.competition_season_id = season.id
                event.name = "Austrian Grand Prix"
                event.date = date(2025, 6, 29)
            session.commit()
    finally:
        engine.dispose()


def _segment_id_for_source_path(db_path: Path, source_path: Path) -> str:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with factory() as session:
            row = session.execute(
                text("SELECT id FROM segment WHERE source_path = :source_path"),
                {"source_path": str(source_path)},
            ).first()
            assert row is not None, f"missing segment row for {source_path}"
            return str(row[0])
    finally:
        engine.dispose()


def _plex_sections(client: httpx.Client, plex_base_url: str, plex_token: str) -> list[ET.Element]:
    response = client.get(
        f"{plex_base_url}/library/sections",
        params={"X-Plex-Token": plex_token},
    )
    assert response.status_code == 200, f"Plex sections query failed: {response.text}"
    return ET.fromstring(response.text).findall(".//Directory")


def _find_library_section_id(client: httpx.Client, plex_base_url: str, plex_token: str, library_name: str) -> int:
    return int(_find_library_section(client, plex_base_url, plex_token, library_name).attrib["key"])


def _find_library_section(
    client: httpx.Client,
    plex_base_url: str,
    plex_token: str,
    library_name: str,
) -> ET.Element:
    directories = _plex_sections(client, plex_base_url, plex_token)
    for directory in directories:
        if directory.attrib.get("title") == library_name and directory.attrib.get("type") == "show":
            return directory
    pytest.fail(f"Could not find Plex library named {library_name!r}")


def _poll_until(deadline: float, callback, message: str):
    last_value = None
    while time.monotonic() < deadline:
        last_value = callback()
        if last_value is not None:
            return last_value
        time.sleep(1)
    pytest.fail(message if last_value is None else f"{message}: {last_value}")


def _episode_videos(client: httpx.Client, plex_base_url: str, plex_token: str, section_id: int) -> list[ET.Element]:
    response = client.get(
        f"{plex_base_url}/library/sections/{section_id}/all",
        params={"type": 4, "X-Plex-Token": plex_token},
        headers={"X-Plex-Container-Size": "200"},
    )
    assert response.status_code == 200, f"Plex episode listing failed: {response.text}"
    return ET.fromstring(response.text).findall(".//Video")


def _show_directories(client: httpx.Client, plex_base_url: str, plex_token: str, section_id: int) -> list[ET.Element]:
    response = client.get(
        f"{plex_base_url}/library/sections/{section_id}/all",
        params={"X-Plex-Token": plex_token},
        headers={"X-Plex-Container-Size": "200"},
    )
    assert response.status_code == 200, f"Plex show listing failed: {response.text}"
    return ET.fromstring(response.text).findall(".//Directory")


def _metadata_children(client: httpx.Client, plex_base_url: str, plex_token: str, key: str) -> ET.Element:
    response = client.get(f"{plex_base_url}{key}", params={"X-Plex-Token": plex_token})
    assert response.status_code == 200, f"Plex metadata children failed for {key}: {response.text}"
    return ET.fromstring(response.text)


def test_live_plex_registration_and_library() -> None:
    provider_base_url = os.getenv("SPORTSCANNER_PROVIDER_URL", "http://127.0.0.1:32699").rstrip("/")
    plex_base_url = os.getenv("SPORTSCANNER_PMS_URL", "http://192.168.0.127:32400").rstrip("/")
    plex_token = _required_env("SPORTSCANNER_PMS_TOKEN")
    provider_identifier = os.getenv(
        "SPORTSCANNER_PROVIDER_IDENTIFIER",
        "tv.plex.agents.custom.sportscanner.metadata.local",
    )
    provider_group_name = os.getenv("SPORTSCANNER_PROVIDER_GROUP_NAME", "SportScanner 2 Local")
    library_name = os.getenv("SPORTSCANNER_PLEX_LIBRARY_NAME", "Sport_Test")

    with httpx.Client(timeout=20.0) as client:
        provider_health = client.get(f"{provider_base_url}/health")
        assert provider_health.status_code == 200

        provider_root = client.get(f"{provider_base_url}/provider/tv")
        assert provider_root.status_code == 200
        provider_payload = provider_root.json()["MediaProvider"]
        assert provider_payload["identifier"] == provider_identifier
        assert provider_payload["title"] == provider_group_name

        groups_response = client.get(
            f"{plex_base_url}/media/providers/metadata/group",
            params={"X-Plex-Token": plex_token},
            headers={"Accept": "application/json"},
        )
        assert groups_response.status_code == 200
        groups = groups_response.json()["MediaContainer"].get("MetadataAgentProviderGroup", [])
        if isinstance(groups, dict):
            groups = [groups]
        assert any(
            group.get("primaryIdentifier") == provider_identifier and group.get("title") == provider_group_name
            for group in groups
        )

        directories = _plex_sections(client, plex_base_url, plex_token)
        assert any(
            directory.attrib.get("title") == library_name and directory.attrib.get("type") == "show"
            for directory in directories
        )


def test_live_ingest_and_metadata_resolution() -> None:
    """Create a live fixture, force Plex to scan it, and assert Plex resolved metadata."""
    provider_base_url = os.getenv("SPORTSCANNER_PROVIDER_URL", "http://127.0.0.1:32699").rstrip("/")
    plex_base_url = os.getenv("SPORTSCANNER_PMS_URL", "http://192.168.0.127:32400").rstrip("/")
    plex_token = _required_env("SPORTSCANNER_PMS_TOKEN")
    provider_identifier = os.getenv(
        "SPORTSCANNER_PROVIDER_IDENTIFIER",
        "tv.plex.agents.custom.sportscanner.metadata.local",
    )
    library_name = os.getenv("SPORTSCANNER_PLEX_LIBRARY_NAME", "Sport_Test")
    incoming_dir_env = os.getenv("SPORTSCANNER_INCOMING_DIR")
    if not incoming_dir_env:
        pytest.skip("SPORTSCANNER_INCOMING_DIR is required for the ingest integration test")

    incoming_dir = Path(incoming_dir_env)
    if not incoming_dir.exists():
        pytest.skip(f"SPORTSCANNER_INCOMING_DIR={incoming_dir} does not exist (Unraid share not mounted?)")

    run_id = str(int(time.time()))
    fixture_dir = incoming_dir / f"live-plex-{run_id}"
    fixture_filename = "Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"
    fixture_path = fixture_dir / fixture_filename
    fixture_sidecar = fixture_path.with_suffix(".sportscanner.yml")
    expected_title = f"Austrian Grand Prix Live Test {run_id}"
    db_path = _db_path()
    _seed_live_event(db_path)

    with httpx.Client(base_url=provider_base_url, timeout=30.0) as client, httpx.Client(timeout=30.0) as plex_client:
        health = client.get("/health")
        assert health.status_code == 200, f"SportScanner health check failed: {health.text}"
        health_payload = health.json()
        library_dir = Path(health_payload["library_dir"])
        section = _find_library_section(
            client=plex_client,
            plex_base_url=plex_base_url,
            plex_token=plex_token,
            library_name=library_name,
        )
        section_id = int(section.attrib["key"])
        location = section.find("./Location")
        assert location is not None and location.attrib.get("path"), "Plex library is missing a Location path"
        plex_library_root = PurePosixPath(location.attrib["path"])
        expected_managed_path = (
            library_dir
            / "Formula 1"
            / season_directory_name(2025)
            / build_managed_filename(
                competition_name="Formula 1",
                air_date=date(2025, 6, 29),
                title=expected_title,
                source_path=fixture_path,
            )
        )
        expected_plexmatch = expected_managed_path.parent / ".plexmatch"
        relative_managed_path = expected_managed_path.relative_to(library_dir)
        plex_managed_path = str(plex_library_root.joinpath(*relative_managed_path.parts))
        plex_refresh_path = str(plex_library_root.joinpath(*relative_managed_path.parent.parts))

        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text("live plex integration", encoding="utf-8")
        fixture_sidecar.write_text(
            "\n".join(
                [
                    "tsdb_event_id: 1001",
                    f'title_suffix: "Live Test {run_id}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        stats_before = client.get("/admin/stats.json").raise_for_status().json()
        published_before = stats_before["published_segments"]
        try:
            rescan_response = client.post("/admin/rescan")
            assert rescan_response.status_code in (200, 303), (
                f"Rescan returned unexpected status {rescan_response.status_code}"
            )

            deadline = time.monotonic() + 30
            published_after = published_before
            while time.monotonic() < deadline:
                stats = client.get("/admin/stats.json").raise_for_status().json()
                published_after = stats["published_segments"]
                if published_after > published_before:
                    break
                time.sleep(1)

            assert published_after > published_before, (
                f"No new published segments after rescan (still {published_after})"
            )

            _poll_until(
                time.monotonic() + 30,
                lambda: expected_managed_path if expected_managed_path.exists() else None,
                f"managed file was not created at {expected_managed_path}",
            )
            assert expected_plexmatch.exists(), f"expected .plexmatch at {expected_plexmatch}"
            assert expected_managed_path.name in expected_plexmatch.read_text(encoding="utf-8")

            refresh_response = plex_client.get(
                f"{plex_base_url}/library/sections/{section_id}/refresh",
                params={
                    "force": 1,
                    "X-Plex-Token": plex_token,
                },
            )
            assert refresh_response.status_code == 200, (
                f"Plex refresh failed with {refresh_response.status_code}: {refresh_response.text}"
            )

            show = _poll_until(
                time.monotonic() + 90,
                lambda: next(
                    (
                        directory
                        for directory in _show_directories(plex_client, plex_base_url, plex_token, section_id)
                        if directory.attrib.get("title") == "Formula 1"
                    ),
                    None,
                ),
                "Plex never scanned the Formula 1 show into Sport_Test",
            )
            season = _poll_until(
                time.monotonic() + 90,
                lambda: next(
                    (
                        directory
                        for directory in _metadata_children(
                            plex_client,
                            plex_base_url,
                            plex_token,
                            show.attrib["key"],
                        ).findall(".//Directory")
                        if directory.attrib.get("title") == "Season 2025"
                    ),
                    None,
                ),
                "Plex never exposed the 2025 season under the Formula 1 show",
            )
            episode = _poll_until(
                time.monotonic() + 90,
                lambda: next(
                    (
                        video
                        for video in _metadata_children(
                            plex_client,
                            plex_base_url,
                            plex_token,
                            season.attrib["key"],
                        ).findall(".//Video")
                        if video.find(".//Part") is not None
                        and video.find(".//Part").attrib.get("file") == plex_managed_path
                    ),
                    None,
                ),
                f"Plex never scanned the episode file for {expected_title}",
            )
            assert episode.attrib.get("grandparentTitle") == "Formula 1"
            assert episode.find(".//Part") is not None
            assert episode.find(".//Part").attrib.get("file") == plex_managed_path

            segment_id = _segment_id_for_source_path(db_path, fixture_path)
            provider_response = client.get(f"/provider/tv/library/metadata/episode_{segment_id}")
            assert provider_response.status_code == 200, provider_response.text
            provider_metadata = provider_response.json()["MediaContainer"]["Metadata"][0]
            assert provider_metadata["title"] == expected_title
            assert provider_metadata["grandparentTitle"] == "Formula 1"
            assert provider_metadata["originallyAvailableAt"] == "2025-06-29"
            assert provider_metadata["guid"].startswith(f"{provider_identifier}://episode/")

        finally:
            if fixture_sidecar.exists():
                fixture_sidecar.unlink()
            if fixture_path.exists():
                fixture_path.unlink()
            if fixture_dir.exists():
                fixture_dir.rmdir()
