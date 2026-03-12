from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from sportscanner.config import validate_plex_provider_identifier
from sportscanner.db.models import AppSetting, Competition, CompetitionSeason, Event, ReviewTask, Segment, SegmentStatus
from sportscanner.metadata_snapshot import sync_segment_metadata_snapshot
from sportscanner.organizer.matcher import season_for_date, similarity
from sportscanner.plex import PlexRegistrationResult

router = APIRouter(prefix="/admin", tags=["admin"])


def _render(request: Request, template_name: str, context: dict) -> HTMLResponse:
    templates = request.app.state.templates
    merged = {"request": request, **context}
    return templates.TemplateResponse(request, template_name, merged)


def _session_factory(request: Request):
    return request.app.state.services.session_factory


def _setting(session, key: str, fallback: str | None = None) -> str | None:
    setting = session.get(AppSetting, key)
    return setting.value if setting is not None else fallback


def _settings_values(request: Request) -> dict[str, str | None]:
    services = request.app.state.services
    with _session_factory(request)() as session:
        return {
            "pms_url": _setting(session, "pms_url", services.settings.pms_url),
            "pms_token": _setting(session, "pms_token", services.settings.pms_token),
            "provider_public_url": _setting(session, "provider_public_url", services.settings.provider_public_url),
            "plex_provider_identifier": _setting(
                session,
                "plex_provider_identifier",
                services.settings.plex_provider_identifier,
            ),
            "plex_provider_group_name": _setting(
                session,
                "plex_provider_group_name",
                services.settings.plex_provider_group_name,
            ),
        }


def _review_search_score(
    query: str,
    event_name: str,
    competition_name: str,
    *,
    preferred_competition: str | None = None,
    preferred_date: date | None = None,
    event_date: date | None = None,
) -> float:
    score = max(
        similarity(query, event_name),
        similarity(query, f"{competition_name} {event_name}".strip()),
    )
    if preferred_competition and competition_name and similarity(preferred_competition, competition_name) >= 0.8:
        score += 0.15
    if preferred_date is not None and event_date == preferred_date:
        score += 0.10
    return score


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    services = request.app.state.services
    values = _settings_values(request)
    with _session_factory(request)() as session:
        stats = {
            "competitions": session.scalar(select(func.count(Competition.id))) or 0,
            "seasons": session.scalar(select(func.count(CompetitionSeason.id))) or 0,
            "segments": session.scalar(select(func.count(Segment.id))) or 0,
            "published_segments": session.scalar(
                select(func.count(Segment.id)).where(Segment.status == SegmentStatus.PUBLISHED.value)
            ) or 0,
            "staged_segments": session.scalar(
                select(func.count(Segment.id)).where(Segment.status == SegmentStatus.STAGED.value)
            ) or 0,
            "review_segments": session.scalar(
                select(func.count(Segment.id)).where(Segment.status == SegmentStatus.REVIEW.value)
            ) or 0,
            "review_tasks": session.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")) or 0,
            "resolved_review_tasks": session.scalar(
                select(func.count(ReviewTask.id)).where(ReviewTask.status == "resolved")
            ) or 0,
        }
        recent_tasks = list(
            session.scalars(
                select(ReviewTask)
                .options(joinedload(ReviewTask.segment))
                .where(ReviewTask.status == "open")
                .order_by(ReviewTask.created_at.asc())
                .limit(5)
            )
        )
        recent_segments = list(
            session.scalars(
                select(Segment)
                .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
                .where(Segment.status == SegmentStatus.PUBLISHED.value)
                .order_by(Segment.updated_at.desc(), Segment.id.desc())
                .limit(5)
            )
        )
        recent_segment_rows = []
        for segment in recent_segments:
            season = session.get(CompetitionSeason, segment.competition_season_id)
            competition = session.get(Competition, season.competition_id) if season else None
            recent_segment_rows.append(
                {
                    "segment": segment,
                    "season": season.label if season else "Unknown",
                    "competition": competition.name if competition else "Unknown",
                }
            )
    api_mode = services.metadata_source.probe() if services.metadata_source is not None else "disabled"
    plex_status = {
        "configured": bool(values["pms_url"] and values["pms_token"]),
        "reachable": False,
        "library_count": 0,
        "show_library_count": 0,
        "error": None,
    }
    if plex_status["configured"]:
        plex = services.plex.with_credentials(values["pms_url"], values["pms_token"])
        try:
            sections = plex.list_library_sections()
            plex_status["reachable"] = True
            plex_status["library_count"] = len(sections)
            plex_status["show_library_count"] = len([section for section in sections if section["type"] == "show"])
        except ValueError as exc:
            plex_status["error"] = str(exc)
        except httpx.HTTPStatusError as exc:
            plex_status["error"] = f"Plex returned {exc.response.status_code}: {exc.response.text[:200]}"
        except httpx.HTTPError as exc:
            plex_status["error"] = f"Could not reach Plex: {exc}"

    next_steps: list[str] = []
    if not values["pms_url"] or not values["pms_token"]:
        next_steps.append("Connect Plex in Settings so SportScanner can register the provider and inspect libraries.")
    if not values["provider_public_url"]:
        next_steps.append("Set the provider public URL so Plex can reach the metadata endpoint.")
    if stats["review_tasks"]:
        next_steps.append(f"Work through the {stats['review_tasks']} open review task(s) so staged files can publish.")
    if plex_status["configured"] and plex_status["reachable"] and plex_status["show_library_count"] == 0:
        next_steps.append("Create a Plex TV library that points at the managed SportScanner library path.")
    if not next_steps:
        next_steps.append("Rescan incoming files after adding new media or metadata overrides.")

    return _render(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "api_mode": api_mode,
            "plex_status": plex_status,
            "values": values,
            "next_steps": next_steps,
            "recent_tasks": recent_tasks,
            "recent_segments": recent_segment_rows,
        },
    )


@router.get("/review", response_class=HTMLResponse)
def review_queue(request: Request) -> HTMLResponse:
    with _session_factory(request)() as session:
        tasks = list(
            session.scalars(
                select(ReviewTask)
                .options(joinedload(ReviewTask.segment))
                .where(ReviewTask.status == "open")
                .order_by(ReviewTask.created_at.asc())
            )
        )
        rows = []
        for task in tasks:
            segment = task.segment
            season = session.get(CompetitionSeason, segment.competition_season_id) if segment else None
            competition = session.get(Competition, season.competition_id) if season else None
            rows.append(
                {
                    "task": task,
                    "segment": segment,
                    "season": season,
                    "competition": competition,
                }
            )
        summary = {
            "open_tasks": len(rows),
            "resolved_tasks": session.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.status == "resolved")) or 0,
            "review_segments": session.scalar(
                select(func.count(Segment.id)).where(Segment.status == SegmentStatus.REVIEW.value)
            ) or 0,
            "staged_segments": session.scalar(
                select(func.count(Segment.id)).where(Segment.status == SegmentStatus.STAGED.value)
            ) or 0,
        }
    return _render(request, "review_queue.html", {"tasks": rows, "summary": summary})


@router.get("/review/{task_id}", response_class=HTMLResponse)
def review_task_detail(request: Request, task_id: int) -> HTMLResponse:
    with _session_factory(request)() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Unknown review task")
        segment = session.get(Segment, task.segment_id)
        season = session.get(CompetitionSeason, segment.competition_season_id) if segment else None
        competition = session.get(Competition, season.competition_id) if season else None
        open_task_ids = list(
            session.scalars(
                select(ReviewTask.id).where(ReviewTask.status == "open").order_by(ReviewTask.created_at.asc())
            )
        )
    queue_position = open_task_ids.index(task_id) + 1 if task_id in open_task_ids else None
    prev_task_id = open_task_ids[queue_position - 2] if queue_position and queue_position > 1 else None
    next_task_id = open_task_ids[queue_position] if queue_position and queue_position < len(open_task_ids) else None
    suggested_query = " ".join(
        part
        for part in (
            competition.name if competition else None,
            segment.title if segment else None,
            segment.air_date.isoformat() if segment and segment.air_date else None,
        )
        if part
    )
    return _render(
        request,
        "review_detail.html",
        {
            "task": task,
            "segment": segment,
            "season": season,
            "competition": competition,
            "queue_position": queue_position,
            "queue_total": len(open_task_ids),
            "prev_task_id": prev_task_id,
            "next_task_id": next_task_id,
            "suggested_query": suggested_query,
        },
    )


@router.post("/review/{task_id}/resolve")
def resolve_review_task(
    request: Request,
    task_id: int,
    event_id: str | None = Form(default=None),
    tsdb_event_id: int | None = Form(default=None),
    publish_as_special: str | None = Form(default=None),
) -> RedirectResponse:
    organizer = request.app.state.services.organizer
    organizer.resolve_review_task(
        task_id,
        event_id=event_id or None,
        tsdb_event_id=tsdb_event_id,
        publish_as_special=bool(publish_as_special),
    )
    return RedirectResponse(url=f"{request.url_for('review_queue')}", status_code=303)


@router.get("/review/{task_id}/search", response_class=HTMLResponse)
def search_events_for_review(request: Request, task_id: int, q: str = "") -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Unknown review task")
        segment = session.get(Segment, task.segment_id)
        season = session.get(CompetitionSeason, segment.competition_season_id) if segment else None
        competition = session.get(Competition, season.competition_id) if season else None
        results = []
        query = q.strip()
        seen: set[tuple[str, str | None]] = set()
        if query:
            rows = list(
                session.scalars(
                    select(Event)
                    .join(CompetitionSeason, CompetitionSeason.id == Event.competition_season_id)
                    .join(Competition, Competition.id == CompetitionSeason.competition_id)
                    .where(or_(Event.name.ilike(f"%{query}%"), Competition.name.ilike(f"%{query}%")))
                    .order_by(Event.date.desc())
                    .limit(40)
                )
            )
            ranked_rows = []
            for ev in rows:
                row_season = session.get(CompetitionSeason, ev.competition_season_id)
                row_comp = session.get(Competition, row_season.competition_id) if row_season else None
                score = _review_search_score(
                    query,
                    ev.name,
                    row_comp.name if row_comp else "",
                    preferred_competition=competition.name if competition else None,
                    preferred_date=segment.air_date if segment else None,
                    event_date=ev.date,
                )
                ranked_rows.append(
                    (
                        score,
                        {
                            "event_id": ev.id,
                            "tsdb_event_id": None,
                            "name": ev.name,
                            "date": ev.date,
                            "competition": row_comp.name if row_comp else "Unknown",
                            "season": row_season.label if row_season else "Unknown",
                            "source": "Cached event",
                            "action_label": "Use This Event",
                        },
                    )
                )
            for _, item in sorted(ranked_rows, key=lambda row: (row[0], row[1]["date"] or date.min), reverse=True):
                dedupe_key = (item["name"], item["date"].isoformat() if item["date"] else None)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                results.append(item)
                if len(results) >= 12:
                    break

            if services.metadata_source is not None:
                upstream_rows = services.metadata_source.search_filename(query)
                ranked_upstream = []
                for upstream in upstream_rows:
                    existing = (
                        session.scalar(select(Event).where(Event.tsdb_id == upstream.tsdb_id))
                        if upstream.tsdb_id is not None
                        else None
                    )
                    if existing is not None:
                        dedupe_key = (upstream.name, upstream.date.isoformat() if upstream.date else None)
                        if dedupe_key in seen:
                            continue
                        existing_season = session.get(CompetitionSeason, existing.competition_season_id)
                        existing_comp = session.get(Competition, existing_season.competition_id) if existing_season else None
                        season_label = existing_season.label if existing_season else "Unknown"
                        source_label = "Cached event"
                        action_label = "Use Cached Event"
                        event_id = existing.id
                        tsdb_lookup_id = None
                    else:
                        season_label = "Unknown"
                        if upstream.date is not None:
                            if competition is not None and (
                                not upstream.competition_name or similarity(competition.name, upstream.competition_name) >= 0.6
                            ):
                                _, season_label = season_for_date(upstream.date, competition)
                            else:
                                season_label = str(upstream.date.year)
                        source_label = "TheSportsDB"
                        action_label = "Load From TheSportsDB"
                        event_id = None
                        tsdb_lookup_id = upstream.tsdb_id
                    score = _review_search_score(
                        query,
                        upstream.name,
                        upstream.competition_name,
                        preferred_competition=competition.name if competition else None,
                        preferred_date=segment.air_date if segment else None,
                        event_date=upstream.date,
                    )
                    ranked_upstream.append(
                        (
                            score,
                            {
                                "event_id": event_id,
                                "tsdb_event_id": tsdb_lookup_id,
                                "name": upstream.name,
                                "date": upstream.date,
                                "competition": upstream.competition_name or (competition.name if competition else "Unknown"),
                                "season": season_label,
                                "source": source_label,
                                "action_label": action_label,
                            },
                        )
                    )
                for _, item in sorted(
                    ranked_upstream,
                    key=lambda row: (row[0], row[1]["date"] or date.min),
                    reverse=True,
                ):
                    dedupe_key = (item["name"], item["date"].isoformat() if item["date"] else None)
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    results.append(item)
                    if len(results) >= 18:
                        break
    return _render(
        request,
        "review_event_search.html",
        {"task_id": task_id, "results": results, "query": q.strip()},
    )


@router.get("/competitions", response_class=HTMLResponse)
def competitions(request: Request) -> HTMLResponse:
    with _session_factory(request)() as session:
        rows = list(session.scalars(select(Competition).order_by(Competition.name.asc())))
    return _render(request, "competitions.html", {"competitions": rows})


@router.post("/competitions/{competition_id}/refresh-images")
def competition_refresh_images(request: Request, competition_id: str) -> RedirectResponse:
    services = request.app.state.services
    metadata_source = getattr(services, "metadata_source", None)
    if metadata_source is not None:
        with _session_factory(request)() as session:
            competition = session.get(Competition, competition_id)
            if competition is not None and competition.tsdb_id is not None:
                try:
                    rich = metadata_source.lookup_competition(competition.tsdb_id)
                except Exception:
                    rich = None
                if rich is not None:
                    competition.poster_url = rich.poster_url or competition.poster_url
                    competition.banner_url = rich.banner_url or competition.banner_url
                    competition.fanart_url = rich.fanart_url or competition.fanart_url
                    if rich.description and not competition.description:
                        competition.description = rich.description
                    segments = list(
                        session.scalars(
                            select(Segment)
                            .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
                            .where(
                                CompetitionSeason.competition_id == competition_id,
                                Segment.event_id.is_not(None),
                            )
                        )
                    )
                    for segment in segments:
                        sync_segment_metadata_snapshot(
                            session,
                            segment=segment,
                            metadata_source_name=getattr(metadata_source, "name", None),
                        )
                    session.commit()
    return RedirectResponse(
        url=str(request.url_for("competition_detail", competition_id=competition_id)),
        status_code=303,
    )


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
    services = request.app.state.services
    with _session_factory(request)() as session:
        segment = session.get(Segment, segment_id)
        if segment is None:
            raise HTTPException(status_code=404, detail="Unknown segment")
        event = session.get(Event, segment.event_id) if segment.event_id else None
        season = session.get(CompetitionSeason, segment.competition_season_id)
        competition = session.get(Competition, season.competition_id) if season is not None else None
        if (
            segment.status == SegmentStatus.PUBLISHED.value
            and segment.metadata_record is None
            and event is not None
            and season is not None
            and competition is not None
        ):
            sync_segment_metadata_snapshot(
                session,
                segment=segment,
                metadata_source_name=getattr(services.metadata_source, "name", None),
            )
            session.commit()
    return _render(
        request,
        "segment_detail.html",
        {
            "segment": segment,
            "event": event,
            "season": season,
            "competition": competition,
        },
    )


@router.post("/segments/{segment_id}")
def update_segment(
    request: Request,
    segment_id: str,
    title: str = Form(...),
    kind: str = Form(...),
) -> RedirectResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        segment = session.get(Segment, segment_id)
        if segment is None:
            raise HTTPException(status_code=404, detail="Unknown segment")
        segment.title = title
        segment.kind = kind
        if segment.event_id is not None:
            sync_segment_metadata_snapshot(
                session,
                segment=segment,
                metadata_source_name=getattr(services.metadata_source, "name", None),
            )
        session.commit()
    return RedirectResponse(url=f"{request.url_for('segment_detail', segment_id=segment_id)}", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    values = _settings_values(request)
    return _render(request, "settings.html", {"values": values, "error": None})


@router.post("/settings")
def save_settings(
    request: Request,
    pms_url: str = Form(...),
    pms_token: str = Form(...),
    provider_public_url: str = Form(...),
    plex_provider_identifier: str = Form(...),
    plex_provider_group_name: str = Form(...),
) -> Response:
    values = {
        "pms_url": pms_url,
        "pms_token": pms_token,
        "provider_public_url": provider_public_url,
        "plex_provider_identifier": plex_provider_identifier,
        "plex_provider_group_name": plex_provider_group_name,
    }
    try:
        values["plex_provider_identifier"] = validate_plex_provider_identifier(plex_provider_identifier)
    except ValueError as exc:
        response = _render(request, "settings.html", {"values": values, "error": str(exc)})
        response.status_code = 400
        return response

    with _session_factory(request)() as session:
        for key, value in values.items():
            existing = session.get(AppSetting, key)
            if existing is None:
                session.add(AppSetting(key=key, value=value))
            else:
                existing.value = value
        session.commit()
    return RedirectResponse(url=f"{request.url_for('settings_page')}", status_code=303)


@router.post("/register-plex", response_class=HTMLResponse)
def register_with_plex(request: Request) -> RedirectResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        pms_url = _setting(session, "pms_url", services.settings.pms_url)
        pms_token = _setting(session, "pms_token", services.settings.pms_token)
        public_url = _setting(session, "provider_public_url", services.settings.provider_public_url)
        provider_identifier = _setting(
            session,
            "plex_provider_identifier",
            services.settings.plex_provider_identifier,
        )
        group_name = _setting(
            session,
            "plex_provider_group_name",
            services.settings.plex_provider_group_name,
        )

    if not public_url:
        raise HTTPException(status_code=400, detail="provider_public_url is required")

    plex = services.plex.with_credentials(pms_url, pms_token)
    try:
        result = plex.register_provider_and_group(
            provider_uri=f"{public_url.rstrip('/')}/provider/tv",
            provider_identifier=provider_identifier or services.settings.plex_provider_identifier,
            provider_group_name=group_name or services.settings.plex_provider_group_name,
        )
    except ValueError as exc:
        return _render(
            request,
            "plex_registration.html",
            {"error": str(exc), "result": None, "values": _settings_values(request)},
        )
    except httpx.HTTPStatusError as exc:
        detail = f"Plex returned {exc.response.status_code} {exc.response.reason_phrase}."
        body = exc.response.text.strip()
        if body:
            detail = f"{detail} {body[:300]}"
        return _render(
            request,
            "plex_registration.html",
            {"error": detail, "result": None, "values": _settings_values(request)},
        )
    except httpx.HTTPError as exc:
        return _render(
            request,
            "plex_registration.html",
            {"error": f"Could not reach Plex: {exc}", "result": None, "values": _settings_values(request)},
        )
    destination = request.url_for("plex_registration_page").include_query_params(
        provider_identifier=result.provider_identifier,
        provider_uri=result.provider_uri,
        provider_group_id=result.provider_group_id,
    )
    return RedirectResponse(url=str(destination), status_code=303)


@router.get("/register-plex", response_class=HTMLResponse)
def plex_registration_page(
    request: Request,
    provider_identifier: str | None = None,
    provider_uri: str | None = None,
    provider_group_id: int | None = None,
    library_name: str | None = None,
    library_section_id: int | None = None,
) -> HTMLResponse:
    result = None
    if provider_identifier and provider_uri:
        result = PlexRegistrationResult(
            provider_identifier=provider_identifier,
            provider_uri=provider_uri,
            provider_group_id=provider_group_id,
        )
    return _render(
        request,
        "plex_registration.html",
        {
            "result": result,
            "library_name": library_name,
            "library_section_id": library_section_id,
        },
    )


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request) -> HTMLResponse:
    entries = request.app.state.log_buffer.entries()
    return _render(request, "logs.html", {"entries": entries})


@router.get("/logs/entries", response_class=HTMLResponse)
def logs_entries(
    request: Request,
    level: str | None = None,
    component: str | None = None,
    keyword: str | None = None,
) -> HTMLResponse:
    buf = request.app.state.log_buffer
    entries = buf.entries(min_level=level or None, component=component or None, keyword=keyword or None)
    return _render(request, "logs_entries.html", {"entries": entries})


@router.get("/stats.json")
def stats_json(request: Request) -> dict:
    with _session_factory(request)() as session:
        return {
            "competitions": session.scalar(select(func.count(Competition.id))) or 0,
            "seasons": session.scalar(select(func.count(CompetitionSeason.id))) or 0,
            "segments": session.scalar(select(func.count(Segment.id))) or 0,
            "published_segments": session.scalar(
                select(func.count(Segment.id)).where(Segment.status == SegmentStatus.PUBLISHED.value)
            ) or 0,
            "open_review_tasks": session.scalar(
                select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")
            ) or 0,
        }


@router.post("/create-plex-library", response_class=HTMLResponse)
def create_plex_library(
    request: Request,
    library_name: str = Form(...),
    library_location: str = Form(...),
) -> RedirectResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        pms_url = _setting(session, "pms_url", services.settings.pms_url)
        pms_token = _setting(session, "pms_token", services.settings.pms_token)
        public_url = _setting(session, "provider_public_url", services.settings.provider_public_url)
        provider_identifier = _setting(
            session,
            "plex_provider_identifier",
            services.settings.plex_provider_identifier,
        )
        group_name = _setting(
            session,
            "plex_provider_group_name",
            services.settings.plex_provider_group_name,
        )

    plex = services.plex.with_credentials(pms_url, pms_token)
    try:
        if not public_url:
            raise ValueError("provider_public_url is required")
        registration = plex.register_provider_and_group(
            provider_uri=f"{public_url.rstrip('/')}/provider/tv",
            provider_identifier=provider_identifier or services.settings.plex_provider_identifier,
            provider_group_name=group_name or services.settings.plex_provider_group_name,
        )
        if registration.provider_group_id is None:
            raise ValueError("Plex did not return a provider group id")
        section_id = plex.create_tv_shows_library(
            name=library_name,
            location=library_location,
            provider_group_id=registration.provider_group_id,
        )
    except ValueError as exc:
        return _render(
            request,
            "plex_registration.html",
            {"error": str(exc), "result": None},
        )
    except httpx.HTTPStatusError as exc:
        detail = f"Plex returned {exc.response.status_code} {exc.response.reason_phrase}."
        body = exc.response.text.strip()
        if body:
            detail = f"{detail} {body[:300]}"
        return _render(
            request,
            "plex_registration.html",
            {"error": detail, "result": None},
        )
    except httpx.HTTPError as exc:
        return _render(
            request,
            "plex_registration.html",
            {"error": f"Could not reach Plex: {exc}", "result": None},
        )
    destination = request.url_for("plex_registration_page").include_query_params(
        provider_identifier=registration.provider_identifier,
        provider_uri=registration.provider_uri,
        provider_group_id=registration.provider_group_id,
        library_name=library_name,
        library_section_id=section_id,
    )
    return RedirectResponse(url=str(destination), status_code=303)


@router.post("/rescan")
def rescan(request: Request) -> RedirectResponse:
    request.app.state.services.organizer.rescan_incoming()
    return RedirectResponse(url=f"{request.url_for('dashboard')}", status_code=303)


@router.get("/plex-libraries", response_class=HTMLResponse)
def plex_libraries_page(request: Request) -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        pms_url = _setting(session, "pms_url", services.settings.pms_url)
        pms_token = _setting(session, "pms_token", services.settings.pms_token)
        provider_identifier = _setting(
            session, "plex_provider_identifier", services.settings.plex_provider_identifier
        )
    plex = services.plex.with_credentials(pms_url, pms_token)
    sections = []
    error = None
    try:
        sections = plex.list_library_sections()
    except ValueError as exc:
        error = str(exc)
    except httpx.HTTPStatusError as exc:
        error = f"Plex returned {exc.response.status_code}: {exc.response.text[:200]}"
    except httpx.HTTPError as exc:
        error = f"Could not reach Plex: {exc}"
    return _render(
        request,
        "plex_libraries.html",
        {"sections": sections, "error": error, "provider_identifier": provider_identifier},
    )


@router.post("/plex-libraries/{section_id}/refresh")
def plex_refresh_section(request: Request, section_id: int) -> RedirectResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        pms_url = _setting(session, "pms_url", services.settings.pms_url)
        pms_token = _setting(session, "pms_token", services.settings.pms_token)
    plex = services.plex.with_credentials(pms_url, pms_token)
    try:
        plex.refresh_library_section(section_id)
    except (ValueError, httpx.HTTPError):
        pass
    return RedirectResponse(url=str(request.url_for("plex_libraries_page")), status_code=303)
