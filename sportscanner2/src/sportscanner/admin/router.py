from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from sportscanner.db.models import AppSetting, Competition, CompetitionSeason, Event, ReviewTask, Segment, SegmentStatus

router = APIRouter(prefix="/admin", tags=["admin"])


def _render(request: Request, template_name: str, context: dict) -> HTMLResponse:
    templates = request.app.state.templates
    merged = {"request": request, **context}
    return templates.TemplateResponse(template_name, merged)


def _session_factory(request: Request):
    return request.app.state.services.session_factory


def _setting(session, key: str, fallback: str | None = None) -> str | None:
    setting = session.get(AppSetting, key)
    return setting.value if setting is not None else fallback


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        stats = {
            "competitions": session.scalar(select(func.count(Competition.id))) or 0,
            "seasons": session.scalar(select(func.count(CompetitionSeason.id))) or 0,
            "segments": session.scalar(select(func.count(Segment.id))) or 0,
            "published_segments": session.scalar(
                select(func.count(Segment.id)).where(Segment.status == SegmentStatus.PUBLISHED.value)
            ) or 0,
            "review_tasks": session.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")) or 0,
        }
    api_mode = services.metadata_source.probe() if services.metadata_source is not None else "disabled"
    return _render(request, "dashboard.html", {"stats": stats, "api_mode": api_mode})


@router.get("/review", response_class=HTMLResponse)
def review_queue(request: Request) -> HTMLResponse:
    with _session_factory(request)() as session:
        tasks = list(
            session.scalars(
                select(ReviewTask)
                .order_by(ReviewTask.created_at.asc())
            )
        )
    return _render(request, "review_queue.html", {"tasks": tasks})


@router.get("/review/{task_id}", response_class=HTMLResponse)
def review_task_detail(request: Request, task_id: int) -> HTMLResponse:
    with _session_factory(request)() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Unknown review task")
        segment = session.get(Segment, task.segment_id)
        season = session.get(CompetitionSeason, segment.competition_season_id) if segment else None
        competition = session.get(Competition, season.competition_id) if season else None
    return _render(
        request,
        "review_detail.html",
        {"task": task, "segment": segment, "season": season, "competition": competition},
    )


@router.post("/review/{task_id}/resolve")
def resolve_review_task(
    request: Request,
    task_id: int,
    event_id: str | None = Form(default=None),
    publish_as_special: str | None = Form(default=None),
) -> RedirectResponse:
    organizer = request.app.state.services.organizer
    organizer.resolve_review_task(
        task_id,
        event_id=event_id or None,
        publish_as_special=bool(publish_as_special),
    )
    return RedirectResponse(url=f"{request.url_for('review_queue')}", status_code=303)


@router.get("/competitions", response_class=HTMLResponse)
def competitions(request: Request) -> HTMLResponse:
    with _session_factory(request)() as session:
        rows = list(session.scalars(select(Competition).order_by(Competition.name.asc())))
    return _render(request, "competitions.html", {"competitions": rows})


@router.get("/competitions/{competition_id}", response_class=HTMLResponse)
def competition_detail(request: Request, competition_id: str) -> HTMLResponse:
    with _session_factory(request)() as session:
        competition = session.get(Competition, competition_id)
        if competition is None:
            raise HTTPException(status_code=404, detail="Unknown competition")
        seasons = list(
            session.scalars(
                select(CompetitionSeason)
                .where(CompetitionSeason.competition_id == competition_id)
                .order_by(CompetitionSeason.season_number.asc())
            )
        )
        segments = list(
            session.scalars(
                select(Segment)
                .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
                .where(CompetitionSeason.competition_id == competition_id)
                .order_by(CompetitionSeason.season_number.asc(), Segment.episode_number.asc())
            )
        )
    return _render(
        request,
        "competition_detail.html",
        {"competition": competition, "seasons": seasons, "segments": segments},
    )


@router.get("/segments/{segment_id}", response_class=HTMLResponse)
def segment_detail(request: Request, segment_id: str) -> HTMLResponse:
    with _session_factory(request)() as session:
        segment = session.get(Segment, segment_id)
        if segment is None:
            raise HTTPException(status_code=404, detail="Unknown segment")
    return _render(request, "segment_detail.html", {"segment": segment})


@router.post("/segments/{segment_id}")
def update_segment(
    request: Request,
    segment_id: str,
    title: str = Form(...),
    kind: str = Form(...),
) -> RedirectResponse:
    with _session_factory(request)() as session:
        segment = session.get(Segment, segment_id)
        if segment is None:
            raise HTTPException(status_code=404, detail="Unknown segment")
        segment.title = title
        segment.kind = kind
        session.commit()
    return RedirectResponse(url=f"{request.url_for('segment_detail', segment_id=segment_id)}", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        values = {
            "pms_url": _setting(session, "pms_url", services.settings.pms_url),
            "pms_token": _setting(session, "pms_token", services.settings.pms_token),
            "provider_public_url": _setting(session, "provider_public_url", services.settings.provider_public_url),
            "plex_provider_group_name": _setting(
                session,
                "plex_provider_group_name",
                services.settings.plex_provider_group_name,
            ),
        }
    return _render(request, "settings.html", {"values": values})


@router.post("/settings")
def save_settings(
    request: Request,
    pms_url: str = Form(...),
    pms_token: str = Form(...),
    provider_public_url: str = Form(...),
    plex_provider_group_name: str = Form(...),
) -> RedirectResponse:
    with _session_factory(request)() as session:
        for key, value in {
            "pms_url": pms_url,
            "pms_token": pms_token,
            "provider_public_url": provider_public_url,
            "plex_provider_group_name": plex_provider_group_name,
        }.items():
            existing = session.get(AppSetting, key)
            if existing is None:
                session.add(AppSetting(key=key, value=value))
            else:
                existing.value = value
        session.commit()
    return RedirectResponse(url=f"{request.url_for('settings_page')}", status_code=303)


@router.post("/register-plex", response_class=HTMLResponse)
def register_with_plex(request: Request) -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        pms_url = _setting(session, "pms_url", services.settings.pms_url)
        pms_token = _setting(session, "pms_token", services.settings.pms_token)
        public_url = _setting(session, "provider_public_url", services.settings.provider_public_url)
        group_name = _setting(
            session,
            "plex_provider_group_name",
            services.settings.plex_provider_group_name,
        )

    if not public_url:
        raise HTTPException(status_code=400, detail="provider_public_url is required")

    plex = services.plex.with_credentials(pms_url, pms_token)
    result = plex.register_provider_and_group(
        provider_uri=f"{public_url.rstrip('/')}/provider/tv",
        provider_identifier="tv.plex.agents.custom.sportscanner.metadata",
        provider_group_name=group_name or services.settings.plex_provider_group_name,
    )
    return _render(request, "plex_registration.html", {"result": result})


@router.post("/rescan")
def rescan(request: Request) -> RedirectResponse:
    request.app.state.services.organizer.rescan_incoming()
    return RedirectResponse(url=f"{request.url_for('dashboard')}", status_code=303)

