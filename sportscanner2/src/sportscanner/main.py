from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text

from sportscanner.admin.router import router as admin_router
from sportscanner.config import get_settings
from sportscanner.db.engine import create_session_factory, create_sqlite_engine, init_db
from sportscanner.db.models import AppSetting
from sportscanner.log_buffer import LogBuffer
from sportscanner.organizer.service import OrganizerService
from sportscanner.organizer.watcher import OrganizerWatcher
from sportscanner.plex import PlexPmsClient
from sportscanner.provider.router import router as provider_router
from sportscanner.services import SportScannerServices
from sportscanner.upstream.thesportsdb.client import TheSportsDbClient


async def _plex_refresh_worker(services: "SportScannerServices") -> None:  # type: ignore[name-defined]
    """Background task: process pending PlexRefreshJob rows every 30 seconds."""
    from sportscanner.db.models import AppSetting, PlexRefreshJob, PlexRefreshJobStatus, Recording

    _log = logging.getLogger("sportscanner.plex_refresh")
    while True:
        await asyncio.sleep(30)
        try:
            with services.session_factory() as session:
                pms_url_row = session.get(AppSetting, "pms_url")
                pms_token_row = session.get(AppSetting, "pms_token")
                pms_url = (pms_url_row.value if pms_url_row else None) or services.settings.pms_url
                pms_token = (pms_token_row.value if pms_token_row else None) or services.settings.pms_token
                if not pms_url or not pms_token:
                    continue

                pending = list(
                    session.scalars(
                        select(PlexRefreshJob)
                        .where(PlexRefreshJob.status == PlexRefreshJobStatus.PENDING.value)
                        .order_by(PlexRefreshJob.created_at.asc())
                        .limit(10)
                    )
                )
                if not pending:
                    continue

                plex = services.plex.with_credentials(pms_url, pms_token)
                for job in pending:
                    now = datetime.now(tz=timezone.utc)
                    try:
                        plex.refresh_library_section(job.section_id)
                        job.status = PlexRefreshJobStatus.SUCCESS.value
                        _log.info("plex_refresh_job_success section_id=%s job_id=%s", job.section_id, job.id)
                    except Exception as exc:
                        job.status = PlexRefreshJobStatus.ERROR.value
                        job.error_message = str(exc)[:500]
                        _log.warning("plex_refresh_job_failed section_id=%s job_id=%s error=%s", job.section_id, job.id, exc)
                    job.completed_at = now
                    if job.recording_id:
                        recording = session.get(Recording, job.recording_id)
                        if recording is not None:
                            recording.plex_refresh_status = job.status
                            recording.plex_refreshed_at = now
                session.commit()
        except Exception as exc:
            logging.getLogger("sportscanner.plex_refresh").warning("plex_refresh_worker_error error=%s", exc)


def _load_log_level(session_factory, fallback: str) -> str:
    try:
        with session_factory() as session:
            setting = session.get(AppSetting, "log_level")
            if setting is not None and setting.value.strip():
                return setting.value.strip().upper()
    except Exception:
        return fallback.upper()
    return fallback.upper()


def create_app() -> FastAPI:
    settings = get_settings()
    engine = create_sqlite_engine(settings)
    init_db(engine)
    session_factory = create_session_factory(engine)
    sport_logger = logging.getLogger("sportscanner")
    sport_logger.setLevel(_load_log_level(session_factory, settings.log_level))
    for handler in list(sport_logger.handlers):
        if isinstance(handler, LogBuffer):
            sport_logger.removeHandler(handler)
            handler.close()
    log_buffer = LogBuffer(session_factory=session_factory)
    log_buffer.setFormatter(logging.Formatter("%(message)s"))
    sport_logger.addHandler(log_buffer)
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
        app.state.log_buffer = log_buffer
        templates_dir = Path(__file__).resolve().parent / "templates"
        app.state.templates = Jinja2Templates(directory=str(templates_dir))
        settings.asset_cache_dir.mkdir(parents=True, exist_ok=True)
        settings.library_dir.mkdir(parents=True, exist_ok=True)
        if settings.incoming_dir.exists():
            watcher.start()
        refresh_task = asyncio.create_task(_plex_refresh_worker(services))
        yield
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
        watcher.stop()
        sport_logger.removeHandler(log_buffer)
        log_buffer.close()
        engine.dispose()

    app = FastAPI(title="SportScanner 2", version="0.1.0", lifespan=lifespan)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(provider_router)
    app.include_router(admin_router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/admin/", status_code=307)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> RedirectResponse:
        return RedirectResponse(url="/static/favicon.svg", status_code=307)

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
