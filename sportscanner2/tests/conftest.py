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
from sportscanner.db.models import Base, Competition, CompetitionSeason, Event, Segment, SegmentStatus
from sportscanner.organizer.service import OrganizerService
from sportscanner.plex import PlexPmsClient
from sportscanner.provider.router import router as provider_router
from sportscanner.services import SportScannerServices
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


class FakeMetadataSource:
    name = "fake"

    def __init__(self) -> None:
        self._competition = UpstreamCompetition(id="tsdb_4370", tsdb_id=4370, name="Formula 1")
        self._event = UpstreamEvent(
            id="tsdb_1001",
            tsdb_id=1001,
            name="Austrian Grand Prix",
            competition_name="Formula 1",
            date=date(2025, 6, 29),
        )

    def probe(self) -> str:
        return "v1"

    def all_competitions(self) -> list[UpstreamCompetition]:
        return [self._competition]

    def search_filename(self, query: str) -> list[UpstreamEvent]:
        if "Austrian" in query:
            return [self._event]
        return []

    def events_on_day(self, competition_name: str, event_date: date) -> list[UpstreamEvent]:
        if competition_name == "Formula 1" and event_date == self._event.date:
            return [self._event]
        return []

    def season_events(self, competition: UpstreamCompetition, season_label: str) -> tuple[list[UpstreamEvent], bool]:
        if competition.tsdb_id == 4370 and season_label == "2025":
            return ([self._event], True)
        return ([], False)

    def lookup_event(self, tsdb_event_id: int) -> UpstreamEvent | None:
        if tsdb_event_id == 1001:
            return self._event
        return None


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
    )


@pytest.fixture()
def session_factory(settings: Settings):
    engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    factory.engine = engine  # type: ignore[attr-defined]
    return factory


@pytest.fixture()
def metadata_source() -> FakeMetadataSource:
    return FakeMetadataSource()


@pytest.fixture()
def organizer(settings: Settings, session_factory, metadata_source: FakeMetadataSource) -> OrganizerService:
    return OrganizerService(settings, session_factory, metadata_source=metadata_source)


@pytest.fixture()
def seeded_db(session_factory):
    with session_factory() as session:
        competition = Competition(id="tsdb_4370", tsdb_id=4370, name="Formula 1")
        season = CompetitionSeason(
            id="season_tsdb_4370_2025",
            competition_id=competition.id,
            season_number=2025,
            label="2025",
            is_complete=True,
        )
        event = Event(
            id="tsdb_1001",
            tsdb_id=1001,
            competition_season_id=season.id,
            name="Austrian Grand Prix Race",
            date=date(2025, 6, 29),
            event_sequence=11,
        )
        segment = Segment(
            id="seg_primary",
            event_id=event.id,
            competition_season_id=season.id,
            kind="race",
            title="Austrian Grand Prix Race",
            episode_number=1150,
            segment_code=50,
            air_date=date(2025, 6, 29),
            source_path="/tmp/Austrian.mkv",
            managed_path="/library/Formula 1/Season 2025/Formula 1 - 2025-06-29 - Austrian Grand Prix Race.mkv",
            status=SegmentStatus.PUBLISHED.value,
        )
        session.add_all([competition, season, event, segment])
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
    templates_dir = Path(__file__).resolve().parents[1] / "src" / "sportscanner" / "templates"
    static_dir = Path(__file__).resolve().parents[1] / "src" / "sportscanner" / "static"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(provider_router)
    app.include_router(admin_router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/admin/", status_code=307)

    return app
