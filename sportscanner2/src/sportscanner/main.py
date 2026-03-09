from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from sportscanner.admin.router import router as admin_router
from sportscanner.config import get_settings
from sportscanner.db.engine import create_session_factory, create_sqlite_engine, init_db
from sportscanner.organizer.service import OrganizerService
from sportscanner.organizer.watcher import OrganizerWatcher
from sportscanner.plex import PlexPmsClient
from sportscanner.provider.router import router as provider_router
from sportscanner.services import SportScannerServices
from sportscanner.upstream.thesportsdb.client import TheSportsDbClient


def create_app() -> FastAPI:
    settings = get_settings()
    engine = create_sqlite_engine(settings)
    init_db(engine)
    session_factory = create_session_factory(engine)
    metadata_source = TheSportsDbClient(settings, session_factory)
    organizer = OrganizerService(settings, session_factory, metadata_source=metadata_source)
    watcher = OrganizerWatcher(organizer, settings.incoming_dir, settings.watcher_debounce_seconds)
    plex = PlexPmsClient(base_url=settings.pms_url, token=settings.pms_token)
    services = SportScannerServices(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        metadata_source=metadata_source,
        organizer=organizer,
        plex=plex,
        watcher=watcher,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services
        templates_dir = Path(__file__).resolve().parent / "templates"
        app.state.templates = Jinja2Templates(directory=str(templates_dir))
        settings.asset_cache_dir.mkdir(parents=True, exist_ok=True)
        settings.library_dir.mkdir(parents=True, exist_ok=True)
        if settings.incoming_dir.exists():
            watcher.start()
        yield
        watcher.stop()

    app = FastAPI(title="SportScanner 2", version="0.1.0", lifespan=lifespan)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(provider_router)
    app.include_router(admin_router)

    @app.get("/health")
    def health() -> dict[str, object]:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "db": "ok",
            "incoming_dir": str(settings.incoming_dir),
            "library_dir": str(settings.library_dir),
            "tsdb_api_mode": metadata_source.probe(),
            "pms_configured": plex.configured(),
        }

    return app

