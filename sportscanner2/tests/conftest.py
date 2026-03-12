from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sportscanner.admin.router import router as admin_router
from sportscanner.config import Settings
from sportscanner.db.models import Base, Competition, CompetitionSeason, Event, Recording, RecordingStatus
from sportscanner.log_buffer import LogBuffer
from sportscanner.organizer.service import OrganizerService
from sportscanner.plex import PlexPmsClient
from sportscanner.provider.router import router as provider_router
from sportscanner.services import SportScannerServices
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


TEST_MODE_BY_MARKER = {
    "deterministic_integration": "deterministic",
    "plex_integration": "plex",
}


class FakeMetadataSource:
    name = "fake"

    def __init__(self) -> None:
        self._f1 = UpstreamCompetition(
            id="tsdb_4370", tsdb_id=4370, name="Formula 1",
            poster_url="https://example.com/f1_poster.jpg",
            fanart_url="https://example.com/f1_fanart.jpg",
        )
        self._epl = UpstreamCompetition(
            id="tsdb_4328", tsdb_id=4328, name="English Premier League",
            poster_url="https://example.com/epl_poster.jpg",
            fanart_url="https://example.com/epl_fanart.jpg",
        )
        self._nba = UpstreamCompetition(
            id="tsdb_4387", tsdb_id=4387, name="NBA",
            poster_url="https://example.com/nba_poster.jpg",
        )
        self._ufc = UpstreamCompetition(
            id="tsdb_4443", tsdb_id=4443, name="UFC",
            poster_url="https://example.com/ufc_poster.jpg",
        )

        self._f1_event = UpstreamEvent(
            id="tsdb_1001",
            tsdb_id=1001,
            name="Austrian Grand Prix",
            competition_name="Formula 1",
            date=date(2025, 6, 29),
        )
        self._epl_event = UpstreamEvent(
            id="tsdb_2001",
            tsdb_id=2001,
            name="Arsenal vs Tottenham",
            competition_name="English Premier League",
            date=date(2025, 4, 12),
        )
        self._nba_event = UpstreamEvent(
            id="tsdb_3001",
            tsdb_id=3001,
            name="Lakers vs Celtics",
            competition_name="NBA",
            date=date(2025, 6, 10),
        )
        self._ufc_event = UpstreamEvent(
            id="tsdb_4001",
            tsdb_id=4001,
            name="UFC 310 Main Card",
            competition_name="UFC",
            date=date(2025, 3, 8),
        )

    def probe(self) -> str:
        return "v1"

    def all_competitions(self) -> list[UpstreamCompetition]:
        return [self._f1, self._epl, self._nba, self._ufc]

    def search_filename(self, query: str) -> list[UpstreamEvent]:
        if "Austrian" in query:
            return [self._f1_event]
        if "Arsenal" in query or "Tottenham" in query:
            return [self._epl_event]
        if "Lakers" in query or "Celtics" in query:
            return [self._nba_event]
        if "UFC" in query:
            return [self._ufc_event]
        return []

    def events_on_day(self, competition_name: str, event_date: date) -> list[UpstreamEvent]:
        mapping = {
            ("Formula 1", self._f1_event.date): self._f1_event,
            ("English Premier League", self._epl_event.date): self._epl_event,
            ("NBA", self._nba_event.date): self._nba_event,
            ("UFC", self._ufc_event.date): self._ufc_event,
        }
        result = mapping.get((competition_name, event_date))
        return [result] if result else []

    def season_events(self, competition: UpstreamCompetition, season_label: str) -> tuple[list[UpstreamEvent], bool]:
        if competition.tsdb_id == 4370 and season_label == "2025":
            return ([self._f1_event], True)
        if competition.tsdb_id == 4328 and season_label == "2025":
            return ([self._epl_event], True)
        if competition.tsdb_id == 4387 and season_label == "2025":
            return ([self._nba_event], True)
        if competition.tsdb_id == 4443 and season_label == "2025":
            return ([self._ufc_event], True)
        return ([], False)

    def lookup_competition(self, tsdb_id: int) -> UpstreamCompetition | None:
        lookup = {
            4370: self._f1,
            4328: self._epl,
            4387: self._nba,
            4443: self._ufc,
        }
        return lookup.get(tsdb_id)

    def lookup_event(self, tsdb_event_id: int) -> UpstreamEvent | None:
        lookup = {
            1001: self._f1_event,
            2001: self._epl_event,
            3001: self._nba_event,
            4001: self._ufc_event,
        }
        return lookup.get(tsdb_event_id)


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--test-mode",
        action="store",
        default="unit",
        choices=("unit", "deterministic", "plex", "all"),
        help="Select which test slice to run.",
    )


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "unit: isolated tests using local fixtures, mocks, or stubs")
    config.addinivalue_line(
        "markers",
        "deterministic_integration: end-to-end workflow tests against the fake Plex harness",
    )
    config.addinivalue_line(
        "markers",
        "plex_integration: live Plex integration tests against the local runtime and Plex server",
    )


def pytest_collection_modifyitems(config, items) -> None:
    selected_mode = config.getoption("--test-mode")
    selected = []
    deselected = []
    for item in items:
        item_mode = "unit"
        for marker_name, mode_name in TEST_MODE_BY_MARKER.items():
            if item.get_closest_marker(marker_name):
                item_mode = mode_name
                break
        if item_mode == "unit":
            item.add_marker(pytest.mark.unit)
        if selected_mode == "all" or item_mode == selected_mode:
            selected.append(item)
        else:
            deselected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    cache = tmp_path / "cache"
    incoming.mkdir()
    library.mkdir()
    cache.mkdir()
    return Settings(
        db_path=tmp_path / "sportscanner.db",
        incoming_dir=incoming,
        library_dir=library,
        asset_cache_dir=cache,
        tsdb_api_key="123",
        tsdb_api_mode="v1",
        plex_provider_identifier="tv.plex.agents.custom.sportscanner.metadata",
        plex_provider_group_name="SportScanner 2",
    )


@pytest.fixture()
def session_factory(settings: Settings):
    engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    factory.engine = engine  # type: ignore[attr-defined]
    yield factory
    engine.dispose()


@pytest.fixture()
def metadata_source() -> FakeMetadataSource:
    return FakeMetadataSource()


@pytest.fixture()
def organizer(settings: Settings, session_factory, metadata_source: FakeMetadataSource) -> OrganizerService:
    return OrganizerService(settings, session_factory, metadata_source=metadata_source)


@pytest.fixture()
def seeded_db(session_factory):
    with session_factory() as session:
        # --- Formula 1 (Motorsport) ---
        f1 = Competition(id="tsdb_4370", tsdb_id=4370, name="Formula 1", sport="Motorsport")
        f1_season = CompetitionSeason(
            id="season_tsdb_4370_2025",
            competition_id=f1.id,
            season_number=2025,
            label="2025",
            is_complete=True,
        )
        f1_event = Event(
            id="tsdb_1001",
            tsdb_id=1001,
            competition_season_id=f1_season.id,
            name="Austrian Grand Prix Race",
            date=date(2025, 6, 29),
            event_sequence=11,
        )
        f1_segment = Recording(
            id="seg_primary",
            event_id=f1_event.id,
            competition_season_id=f1_season.id,
            kind="race",
            title="Austrian Grand Prix Race",
            episode_number=1150,
            recording_code=50,
            air_date=date(2025, 6, 29),
            source_path="/tmp/Austrian.mkv",
            managed_path="/library/Formula 1/Season 2025/Formula 1 - 2025-06-29 - Austrian Grand Prix Race.mkv",
            status=RecordingStatus.PUBLISHED.value,
        )

        # --- English Premier League (Soccer) ---
        epl = Competition(id="tsdb_4328", tsdb_id=4328, name="English Premier League", sport="Soccer")
        epl_season = CompetitionSeason(
            id="season_tsdb_4328_2025",
            competition_id=epl.id,
            season_number=2025,
            label="2025",
            is_complete=False,
        )
        epl_event = Event(
            id="tsdb_2001",
            tsdb_id=2001,
            competition_season_id=epl_season.id,
            name="Arsenal vs Tottenham",
            date=date(2025, 4, 12),
            home_team="Arsenal",
            away_team="Tottenham",
            event_sequence=30,
        )
        epl_segment = Recording(
            id="seg_epl_001",
            event_id=epl_event.id,
            competition_season_id=epl_season.id,
            kind="match",
            title="Arsenal vs Tottenham",
            episode_number=290,
            recording_code=10,
            air_date=date(2025, 4, 12),
            source_path="/tmp/Arsenal.mkv",
            managed_path="/library/English Premier League/Season 2025/English Premier League - 2025-04-12 - Arsenal vs Tottenham.mkv",
            status=RecordingStatus.PUBLISHED.value,
        )

        # --- NBA (Basketball) ---
        nba = Competition(id="tsdb_4387", tsdb_id=4387, name="NBA", sport="Basketball")
        nba_season = CompetitionSeason(
            id="season_tsdb_4387_2025",
            competition_id=nba.id,
            season_number=2025,
            label="2025",
            is_complete=False,
        )
        nba_event = Event(
            id="tsdb_3001",
            tsdb_id=3001,
            competition_season_id=nba_season.id,
            name="Lakers vs Celtics",
            date=date(2025, 6, 10),
            home_team="Lakers",
            away_team="Celtics",
            event_sequence=7,
        )
        nba_segment = Recording(
            id="seg_nba_001",
            event_id=nba_event.id,
            competition_season_id=nba_season.id,
            kind="game",
            title="Lakers vs Celtics",
            episode_number=407,
            recording_code=10,
            air_date=date(2025, 6, 10),
            source_path="/tmp/Lakers.mkv",
            managed_path="/library/NBA/Season 2025/NBA - 2025-06-10 - Lakers vs Celtics.mkv",
            status=RecordingStatus.PUBLISHED.value,
        )

        # --- UFC (MMA / Combat Sports) ---
        ufc = Competition(id="tsdb_4443", tsdb_id=4443, name="UFC", sport="MMA")
        ufc_season = CompetitionSeason(
            id="season_tsdb_4443_2025",
            competition_id=ufc.id,
            season_number=2025,
            label="2025",
            is_complete=False,
        )
        ufc_event = Event(
            id="tsdb_4001",
            tsdb_id=4001,
            competition_season_id=ufc_season.id,
            name="UFC 310 Main Card",
            date=date(2025, 3, 8),
            event_sequence=4,
        )
        ufc_segment = Recording(
            id="seg_ufc_001",
            event_id=ufc_event.id,
            competition_season_id=ufc_season.id,
            kind="match",
            title="UFC 310 Main Card",
            episode_number=310,
            recording_code=10,
            air_date=date(2025, 3, 8),
            source_path="/tmp/UFC310.mkv",
            managed_path="/library/UFC/Season 2025/UFC - 2025-03-08 - UFC 310 Main Card.mkv",
            status=RecordingStatus.PUBLISHED.value,
        )

        session.add_all([
            f1, f1_season, f1_event, f1_segment,
            epl, epl_season, epl_event, epl_segment,
            nba, nba_season, nba_event, nba_segment,
            ufc, ufc_season, ufc_event, ufc_segment,
        ])
        session.commit()
    return session_factory


@pytest.fixture()
def provider_app(settings: Settings, seeded_db, metadata_source: FakeMetadataSource) -> FastAPI:
    app = FastAPI()
    services = SportScannerServices(
        settings=settings,
        engine=seeded_db.engine,  # type: ignore[attr-defined]
        session_factory=seeded_db,
        metadata_source=metadata_source,
        organizer=OrganizerService(settings, seeded_db, metadata_source=metadata_source),
        plex=PlexPmsClient(),
        watcher=None,
    )
    app.state.services = services
    app.state.log_buffer = LogBuffer(session_factory=seeded_db)
    templates_dir = Path(__file__).resolve().parents[1] / "src" / "sportscanner" / "templates"
    static_dir = Path(__file__).resolve().parents[1] / "src" / "sportscanner" / "static"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(provider_router)
    app.include_router(admin_router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/admin/", status_code=307)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> RedirectResponse:
        return RedirectResponse(url="/static/favicon.svg", status_code=307)

    return app
