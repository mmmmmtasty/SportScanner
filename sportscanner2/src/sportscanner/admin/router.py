from __future__ import annotations

from datetime import date
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import DetachedInstanceError

from sportscanner.config import validate_plex_provider_identifier
from sportscanner.db.models import (
    AppSetting,
    Competition,
    CompetitionSeason,
    Event,
    PlexRefreshJob,
    Recording,
    RecordingStatus,
    ReviewTask,
)
from sportscanner.db.queries import (
    create_alias,
    create_refresh_job,
    get_or_create_competition_season,
    get_season_with_events_and_recordings,
    list_competition_aliases,
)
from sportscanner.metadata_snapshot import sync_recording_metadata_snapshot
from sportscanner.organizer.matcher import season_for_date, similarity
from sportscanner.plex import PlexRegistrationResult

router = APIRouter(prefix="/admin", tags=["admin"])
_LOG_LEVEL_OPTIONS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _render(request: Request, template_name: str, context: dict) -> HTMLResponse:
    templates = request.app.state.templates
    merged = {
        "request": request,
        "status_meta": _status_meta,
        "format_episode_code": _format_episode_code,
        **context,
    }
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
            "log_level": _setting(session, "log_level", services.settings.log_level),
        }


def _redirect_target(request: Request, fallback: str) -> str:
    referer = request.headers.get("referer")
    if referer and referer.startswith(str(request.base_url)):
        return referer
    return fallback


def _current_log_level(request: Request) -> str:
    with _session_factory(request)() as session:
        configured = _setting(session, "log_level", request.app.state.services.settings.log_level)
    level = configured.strip().upper()
    if level not in _LOG_LEVEL_OPTIONS:
        return "INFO"
    return level


def _persist_log_level(request: Request, level: str) -> None:
    normalized = level.strip().upper()
    if normalized not in _LOG_LEVEL_OPTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported log level: {level}")
    with _session_factory(request)() as session:
        existing = session.get(AppSetting, "log_level")
        if existing is None:
            session.add(AppSetting(key="log_level", value=normalized))
        else:
            existing.value = normalized
        session.commit()
    logging.getLogger("sportscanner").setLevel(normalized)


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


def _confidence_class(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"


def _status_meta(status: str, *, has_refresh_pending: bool = False) -> dict[str, str]:
    if status == RecordingStatus.PUBLISHED.value:
        label = "In Plex"
        if has_refresh_pending:
            label = "Plex Refresh Pending"
        return {"label": label, "class_name": "status-plex" if has_refresh_pending else "status-published"}
    if status == RecordingStatus.REVIEW.value:
        return {"label": "Needs Match", "class_name": "status-review"}
    if status == RecordingStatus.STAGED.value:
        return {"label": "Waiting for Schedule", "class_name": "status-staged"}
    if status == RecordingStatus.IGNORED.value:
        return {"label": "Ignored", "class_name": "status-ignored"}
    if status == RecordingStatus.ERROR.value:
        return {"label": "Error", "class_name": "status-error"}
    return {"label": status.replace("_", " ").title(), "class_name": "status-neutral"}


def _format_episode_code(recording: Recording | None) -> str:
    if recording is None or recording.episode_number is None:
        return "Unassigned"
    season = getattr(recording, "season_number", None)
    if season is None:
        try:
            competition_season = getattr(recording, "competition_season", None)
        except DetachedInstanceError:
            competition_season = None
        season = competition_season.season_number if competition_season is not None else None
    if season is None:
        return f"E{recording.episode_number:03d}"
    return f"S{season % 100:02d}E{recording.episode_number:03d}"


def _system_status(request: Request) -> tuple[dict[str, int], dict[str, object], dict[str, str | None], list[str]]:
    services = request.app.state.services
    values = _settings_values(request)
    with _session_factory(request)() as session:
        stats = {
            "competitions": session.scalar(select(func.count(Competition.id))) or 0,
            "seasons": session.scalar(select(func.count(CompetitionSeason.id))) or 0,
            "recordings": session.scalar(select(func.count(Recording.id))) or 0,
            "published_recordings": session.scalar(
                select(func.count(Recording.id)).where(Recording.status == RecordingStatus.PUBLISHED.value)
            ) or 0,
            "staged_recordings": session.scalar(
                select(func.count(Recording.id)).where(Recording.status == RecordingStatus.STAGED.value)
            ) or 0,
            "review_recordings": session.scalar(
                select(func.count(Recording.id)).where(Recording.status == RecordingStatus.REVIEW.value)
            ) or 0,
            "review_tasks": session.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")) or 0,
            "resolved_review_tasks": session.scalar(
                select(func.count(ReviewTask.id)).where(ReviewTask.status == "resolved")
            ) or 0,
            "pending_refresh_jobs": session.scalar(
                select(func.count(PlexRefreshJob.id)).where(PlexRefreshJob.status == "pending")
            ) or 0,
        }

    api_mode = services.metadata_source.probe() if services.metadata_source is not None else "disabled"
    plex_status: dict[str, object] = {
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
    return stats, plex_status, values, next_steps


@router.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request) -> RedirectResponse:
    return RedirectResponse(url=str(request.url_for("inbox")), status_code=307)


@router.get("/inbox", response_class=HTMLResponse, name="inbox")
def inbox(
    request: Request,
    status: str | None = None,
    competition_id: str | None = None,
    confidence: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> HTMLResponse:
    stats, plex_status, values, next_steps = _system_status(request)
    with _session_factory(request)() as session:
        stmt = (
            select(Recording)
            .options(joinedload(Recording.competition_season), joinedload(Recording.event))
            .order_by(Recording.updated_at.desc(), Recording.created_at.desc())
        )
        if status:
            stmt = stmt.where(Recording.status == status)
        if confidence == "high":
            stmt = stmt.where(Recording.match_confidence >= 0.8)
        elif confidence == "medium":
            stmt = stmt.where(Recording.match_confidence >= 0.5, Recording.match_confidence < 0.8)
        elif confidence == "low":
            stmt = stmt.where(or_(Recording.match_confidence < 0.5, Recording.match_confidence.is_(None)))
        if date_from is not None:
            stmt = stmt.where(Recording.air_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Recording.air_date <= date_to)

        recordings = list(session.scalars(stmt))
        rows = []
        competitions = {}
        for recording in recordings:
            season = recording.competition_season
            competition = session.get(Competition, season.competition_id) if season is not None else None
            if competition is not None:
                competitions[competition.id] = competition
            if competition_id and (competition is None or competition.id != competition_id):
                continue
            review_task = session.scalar(
                select(ReviewTask)
                .where(ReviewTask.recording_id == recording.id, ReviewTask.status == "open")
                .order_by(ReviewTask.created_at.asc())
                .limit(1)
            )
            action_label = "View"
            action_href = str(request.url_for("file_detail", recording_id=recording.id))
            if recording.status == RecordingStatus.REVIEW.value:
                action_label = "Review ->"
                action_href = (
                    str(request.url_for("review_task_detail", task_id=review_task.id))
                    if review_task is not None
                    else action_href
                )
            elif recording.status == RecordingStatus.STAGED.value:
                action_label = "Browse Events ->"
            elif recording.status == RecordingStatus.IGNORED.value:
                action_label = "View"
            elif recording.status == RecordingStatus.ERROR.value:
                action_label = "Retry"
            explanation = None
            if recording.match_explanation:
                explanation = recording.match_explanation.get("summary")
            if not explanation and recording.match_method:
                explanation = f"Matched via {recording.match_method.replace('_', ' ')}"
            rows.append(
                {
                    "recording": recording,
                    "season": season,
                    "competition": competition,
                    "event": recording.event,
                    "review_task": review_task,
                    "status": _status_meta(
                        recording.status,
                        has_refresh_pending=recording.plex_refresh_status == "pending",
                    ),
                    "confidence_score": round((recording.match_confidence or 0) * 100),
                    "confidence_class": _confidence_class(recording.match_confidence or 0),
                    "explanation": explanation or "No explanation stored yet.",
                    "action_label": action_label,
                    "action_href": action_href,
                }
            )

    return _render(
        request,
        "inbox.html",
        {
            "rows": rows,
            "stats": stats,
            "plex_status": plex_status,
            "values": values,
            "next_steps": next_steps,
            "filters": {
                "status": status or "",
                "competition_id": competition_id or "",
                "confidence": confidence or "",
                "date_from": date_from.isoformat() if date_from else "",
                "date_to": date_to.isoformat() if date_to else "",
            },
            "competitions": sorted(competitions.values(), key=lambda comp: comp.name.lower()),
        },
    )


@router.get("/review", response_class=HTMLResponse)
def review_queue(request: Request) -> HTMLResponse:
    with _session_factory(request)() as session:
        tasks = list(
            session.scalars(
                select(ReviewTask)
                .options(joinedload(ReviewTask.recording))
                .where(ReviewTask.status == "open")
                .order_by(ReviewTask.created_at.asc())
            )
        )
        rows = []
        for task in tasks:
            recording = task.recording
            season = session.get(CompetitionSeason, recording.competition_season_id) if recording else None
            competition = session.get(Competition, season.competition_id) if season else None
            rows.append(
                {
                    "task": task,
                    "recording": recording,
                    "season": season,
                    "competition": competition,
                }
            )
        summary = {
            "open_tasks": len(rows),
            "resolved_tasks": session.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.status == "resolved")) or 0,
            "review_recordings": session.scalar(
                select(func.count(Recording.id)).where(Recording.status == RecordingStatus.REVIEW.value)
            ) or 0,
            "staged_recordings": session.scalar(
                select(func.count(Recording.id)).where(Recording.status == RecordingStatus.STAGED.value)
            ) or 0,
        }
    return _render(request, "review_queue.html", {"tasks": rows, "summary": summary})


@router.get("/review/{task_id}", response_class=HTMLResponse)
def review_task_detail(request: Request, task_id: int) -> HTMLResponse:
    with _session_factory(request)() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Unknown review task")
        recording = session.get(Recording, task.recording_id)
        season = session.get(CompetitionSeason, recording.competition_season_id) if recording else None
        competition = session.get(Competition, season.competition_id) if season else None
        all_competitions = list(session.scalars(select(Competition).order_by(Competition.name.asc())))
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
            recording.title if recording else None,
            recording.air_date.isoformat() if recording and recording.air_date else None,
        )
        if part
    )
    return _render(
        request,
        "review_detail.html",
        {
            "task": task,
            "recording": recording,
            "season": season,
            "competition": competition,
            "all_competitions": all_competitions,
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


@router.get("/review/{task_id}/season-events", response_class=HTMLResponse)
def review_season_events(request: Request, task_id: int) -> HTMLResponse:
    with _session_factory(request)() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Unknown review task")
        recording = session.get(Recording, task.recording_id)
        season = session.get(CompetitionSeason, recording.competition_season_id) if recording else None
        events = []
        if season:
            events = list(
                session.scalars(
                    select(Event)
                    .where(Event.competition_season_id == season.id)
                    .order_by(Event.date.asc().nulls_last(), Event.round.asc().nulls_last(), Event.name.asc())
                )
            )
    return _render(
        request,
        "review_season_events.html",
        {
            "task_id": task_id,
            "season": season,
            "events": events,
        },
    )


@router.post("/review/{task_id}/reassign")
def reassign_review_task(
    request: Request,
    task_id: int,
    competition_id: str = Form(...),
    season_label: str = Form(...),
) -> RedirectResponse:
    with _session_factory(request)() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Unknown review task")
        competition = session.get(Competition, competition_id)
        if competition is None:
            raise HTTPException(status_code=404, detail="Unknown competition")
        recording = session.get(Recording, task.recording_id)
        if recording is None:
            raise HTTPException(status_code=404, detail="Unknown recording")

        label = season_label.strip()
        try:
            season_number = int(label.split("-")[0]) if "-" in label else int(label)
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid season label")

        season = get_or_create_competition_season(
            session,
            competition=competition,
            season_number=season_number,
            label=label,
            is_complete=False,
        )
        session.flush()

        recording.competition_season_id = season.id
        recording.event_id = None
        recording.match_confidence = None
        recording.match_method = "reassigned"
        recording.status = RecordingStatus.REVIEW.value

        task.status = "open"
        task.candidates = []
        task.resolution = {"type": "reassigned", "competition_id": competition_id, "season_label": label}
        session.commit()

    return RedirectResponse(
        url=str(request.url_for("review_task_detail", task_id=task_id)),
        status_code=303,
    )


@router.get("/review/{task_id}/search", response_class=HTMLResponse)
def search_events_for_review(
    request: Request,
    task_id: int,
    q: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
) -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Unknown review task")
        recording = session.get(Recording, task.recording_id)
        season = session.get(CompetitionSeason, recording.competition_season_id) if recording else None
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
                    preferred_date=recording.air_date if recording else None,
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
                            "round": ev.round,
                            "home_team": ev.home_team,
                            "away_team": ev.away_team,
                            "competition": row_comp.name if row_comp else "Unknown",
                            "season": row_season.label if row_season else "Unknown",
                            "source": "Cached event",
                            "action_label": "Use This Event",
                            "score_pct": round(score * 100),
                            "confidence_class": _confidence_class(score),
                        },
                    )
                )
            for raw_score, item in sorted(ranked_rows, key=lambda row: (row[0], row[1]["date"] or date.min), reverse=True):
                if date_from is not None and item["date"] is not None and item["date"] < date_from:
                    continue
                if date_to is not None and item["date"] is not None and item["date"] > date_to:
                    continue
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
                        preferred_date=recording.air_date if recording else None,
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
                                "round": getattr(upstream, "round", None),
                                "home_team": upstream.home_team,
                                "away_team": upstream.away_team,
                                "competition": upstream.competition_name or (competition.name if competition else "Unknown"),
                                "season": season_label,
                                "source": source_label,
                                "action_label": action_label,
                                "score_pct": round(score * 100),
                                "confidence_class": _confidence_class(score),
                            },
                        )
                    )
                for raw_score, item in sorted(
                    ranked_upstream,
                    key=lambda row: (row[0], row[1]["date"] or date.min),
                    reverse=True,
                ):
                    if date_from is not None and item["date"] is not None and item["date"] < date_from:
                        continue
                    if date_to is not None and item["date"] is not None and item["date"] > date_to:
                        continue
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
        {"task_id": task_id, "recording_id": None, "results": results, "query": q.strip()},
    )


@router.get("/competitions", response_class=HTMLResponse, name="competitions")
def competitions_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url=str(request.url_for("library_page")), status_code=307)


@router.get("/library", response_class=HTMLResponse, name="library_page")
def library_page(request: Request) -> HTMLResponse:
    with _session_factory(request)() as session:
        competitions = list(session.scalars(select(Competition).order_by(Competition.name.asc())))
        rows = []
        for competition in competitions:
            season_count = session.scalar(
                select(func.count(CompetitionSeason.id)).where(CompetitionSeason.competition_id == competition.id)
            ) or 0
            recording_count = session.scalar(
                select(func.count(Recording.id))
                .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
                .where(CompetitionSeason.competition_id == competition.id)
            ) or 0
            last_refreshed = session.scalar(
                select(func.max(Recording.metadata_refreshed_at))
                .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
                .where(CompetitionSeason.competition_id == competition.id)
            )
            rows.append(
                {
                    "competition": competition,
                    "season_count": season_count,
                    "recording_count": recording_count,
                    "last_refreshed": last_refreshed,
                }
            )
    return _render(request, "library.html", {"rows": rows})


@router.post("/competitions/{competition_id}/refresh-metadata")
def competition_refresh_metadata(request: Request, competition_id: str) -> RedirectResponse:
    services = request.app.state.services
    try:
        services.organizer.refresh_competition_metadata(competition_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(
        url=_redirect_target(request, str(request.url_for("library_competition_detail", competition_id=competition_id))),
        status_code=303,
    )


@router.get("/competitions/{competition_id}", response_class=HTMLResponse, name="competition_detail")
def competition_detail_redirect(request: Request, competition_id: str) -> RedirectResponse:
    return RedirectResponse(
        url=str(request.url_for("library_competition_detail", competition_id=competition_id)),
        status_code=307,
    )


@router.get("/library/{competition_id}", response_class=HTMLResponse, name="library_competition_detail")
def library_competition_detail(request: Request, competition_id: str) -> HTMLResponse:
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
        recordings = list(
            session.scalars(
                select(Recording)
                .options(joinedload(Recording.competition_season))
                .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
                .where(CompetitionSeason.competition_id == competition_id)
                .order_by(CompetitionSeason.season_number.asc(), Recording.episode_number.asc())
            )
        )
    return _render(
        request,
        "library_competition.html",
        {"competition": competition, "seasons": seasons, "recordings": recordings},
    )


@router.get("/recordings/{recording_id}", response_class=HTMLResponse, name="recording_detail")
def recording_detail(request: Request, recording_id: str) -> RedirectResponse:
    return RedirectResponse(url=str(request.url_for("file_detail", recording_id=recording_id)), status_code=307)


@router.get("/files/{recording_id}", response_class=HTMLResponse, name="file_detail")
def file_detail(request: Request, recording_id: str) -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        recording = session.scalar(
            select(Recording)
            .options(joinedload(Recording.competition_season), joinedload(Recording.event))
            .where(Recording.id == recording_id)
        )
        if recording is None:
            raise HTTPException(status_code=404, detail="Unknown recording")
        event = session.get(Event, recording.event_id) if recording.event_id else None
        season = session.get(CompetitionSeason, recording.competition_season_id)
        competition = session.get(Competition, season.competition_id) if season is not None else None
        review_task = session.scalar(
            select(ReviewTask)
            .where(ReviewTask.recording_id == recording.id, ReviewTask.status == "open")
            .order_by(ReviewTask.created_at.asc())
            .limit(1)
        )
        if (
            recording.status == RecordingStatus.PUBLISHED.value
            and recording.metadata_record is None
            and event is not None
            and season is not None
            and competition is not None
        ):
            sync_recording_metadata_snapshot(
                session,
                recording=recording,
                metadata_source_name=getattr(services.metadata_source, "name", None),
            )
            session.commit()
    return _render(
        request,
        "file_detail.html",
        {
            "recording": recording,
            "event": event,
            "season": season,
            "competition": competition,
            "review_task": review_task,
            "status": _status_meta(
                recording.status,
                has_refresh_pending=recording.plex_refresh_status == "pending",
            ),
            "explanation": (
                (recording.match_explanation or {}).get("summary")
                or (f"Matched via {recording.match_method.replace('_', ' ')}" if recording.match_method else None)
                or "No explanation stored yet."
            ),
        },
    )


@router.post("/recordings/{recording_id}/refresh-metadata")
def refresh_recording_metadata(request: Request, recording_id: str) -> RedirectResponse:
    services = request.app.state.services
    try:
        services.organizer.refresh_recording_metadata(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(
        url=_redirect_target(request, str(request.url_for("file_detail", recording_id=recording_id))),
        status_code=303,
    )


@router.post("/recordings/{recording_id}")
def update_recording(
    request: Request,
    recording_id: str,
    title: str = Form(...),
    kind: str = Form(...),
) -> RedirectResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise HTTPException(status_code=404, detail="Unknown recording")
        recording.title = title
        recording.kind = kind
        if recording.event_id is not None:
            sync_recording_metadata_snapshot(
                session,
                recording=recording,
                metadata_source_name=getattr(services.metadata_source, "name", None),
            )
        session.commit()
    return RedirectResponse(url=f"{request.url_for('file_detail', recording_id=recording_id)}", status_code=303)


@router.get("/settings", response_class=HTMLResponse, name="settings_page")
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


@router.get("/register-plex", response_class=HTMLResponse, name="plex_registration_page")
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


@router.get("/logs", response_class=HTMLResponse, name="logs_page")
def logs_page(request: Request) -> HTMLResponse:
    entries = request.app.state.log_buffer.entries()
    return _render(
        request,
        "logs.html",
        {
            "entries": entries,
            "current_log_level": _current_log_level(request),
            "log_level_options": _LOG_LEVEL_OPTIONS,
        },
    )


@router.get("/advanced", response_class=HTMLResponse, name="advanced_page")
def advanced_page(request: Request) -> HTMLResponse:
    return RedirectResponse(url=str(request.url_for("logs_page")), status_code=307)


@router.post("/logs/configure", name="configure_logs")
def configure_logs(request: Request, level: str = Form(...)) -> RedirectResponse:
    _persist_log_level(request, level)
    return RedirectResponse(url=str(request.url_for("logs_page")), status_code=303)


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
            "recordings": session.scalar(select(func.count(Recording.id))) or 0,
            "published_recordings": session.scalar(
                select(func.count(Recording.id)).where(Recording.status == RecordingStatus.PUBLISHED.value)
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
            agent=registration.provider_identifier,
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
    return RedirectResponse(url=f"{request.url_for('inbox')}", status_code=303)


@router.get("/plex-libraries", response_class=HTMLResponse, name="plex_libraries_page")
def plex_libraries_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url=str(request.url_for("plex_page")), status_code=307)


@router.get("/plex", response_class=HTMLResponse, name="plex_page")
def plex_page(request: Request) -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        pms_url = _setting(session, "pms_url", services.settings.pms_url)
        pms_token = _setting(session, "pms_token", services.settings.pms_token)
        provider_identifier = _setting(
            session, "plex_provider_identifier", services.settings.plex_provider_identifier
        )
        refresh_jobs = list(
            session.scalars(select(PlexRefreshJob).order_by(PlexRefreshJob.created_at.desc()).limit(50))
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
        "plex.html",
        {
            "sections": sections,
            "error": error,
            "provider_identifier": provider_identifier,
            "refresh_jobs": refresh_jobs,
        },
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
    return RedirectResponse(url=str(request.url_for("plex_page")), status_code=303)


# ---------------------------------------------------------------------------
# File re-match endpoints (A5)
# ---------------------------------------------------------------------------


@router.post("/files/{recording_id}/match", name="rematch_recording")
def rematch_recording_endpoint(
    request: Request,
    recording_id: str,
    event_id: str | None = Form(default=None),
    tsdb_event_id: int | None = Form(default=None),
) -> RedirectResponse:
    services = request.app.state.services
    try:
        services.organizer.rematch_recording(
            recording_id,
            event_id=event_id or None,
            tsdb_event_id=tsdb_event_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Queue a Plex refresh for all show sections
    with _session_factory(request)() as session:
        pms_url = _setting(session, "pms_url", services.settings.pms_url)
        pms_token = _setting(session, "pms_token", services.settings.pms_token)
        plex = services.plex.with_credentials(pms_url, pms_token)
        for section_id in plex.find_show_section_ids():
            create_refresh_job(session, section_id=section_id, recording_id=recording_id)
        session.commit()
    return RedirectResponse(
        url=str(request.url_for("file_detail", recording_id=recording_id)),
        status_code=303,
    )


@router.get("/files/{recording_id}/search", response_class=HTMLResponse, name="search_events_for_file")
def search_events_for_file(
    request: Request,
    recording_id: str,
    q: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
) -> HTMLResponse:
    services = request.app.state.services
    with _session_factory(request)() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise HTTPException(status_code=404, detail="Unknown recording")
        season = session.get(CompetitionSeason, recording.competition_season_id)
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
                    preferred_date=recording.air_date if recording else None,
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
                            "round": ev.round,
                            "home_team": ev.home_team,
                            "away_team": ev.away_team,
                            "competition": row_comp.name if row_comp else "Unknown",
                            "season": row_season.label if row_season else "Unknown",
                            "source": "Cached event",
                            "action_label": "Use This Event",
                            "score_pct": round(score * 100),
                            "confidence_class": _confidence_class(score),
                        },
                    )
                )
            for _raw_score, item in sorted(ranked_rows, key=lambda r: (r[0], r[1]["date"] or date.min), reverse=True):
                if date_from is not None and item["date"] is not None and item["date"] < date_from:
                    continue
                if date_to is not None and item["date"] is not None and item["date"] > date_to:
                    continue
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
                                not upstream.competition_name
                                or similarity(competition.name, upstream.competition_name) >= 0.6
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
                        preferred_date=recording.air_date if recording else None,
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
                                "round": getattr(upstream, "round", None),
                                "home_team": upstream.home_team,
                                "away_team": upstream.away_team,
                                "competition": upstream.competition_name or (competition.name if competition else "Unknown"),
                                "season": season_label,
                                "source": source_label,
                                "action_label": action_label,
                                "score_pct": round(score * 100),
                                "confidence_class": _confidence_class(score),
                            },
                        )
                    )
                for _raw_score, item in sorted(
                    ranked_upstream,
                    key=lambda r: (r[0], r[1]["date"] or date.min),
                    reverse=True,
                ):
                    if date_from is not None and item["date"] is not None and item["date"] < date_from:
                        continue
                    if date_to is not None and item["date"] is not None and item["date"] > date_to:
                        continue
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
        {"recording_id": recording_id, "task_id": None, "results": results, "query": q.strip()},
    )


# ---------------------------------------------------------------------------
# Bulk auto-resolve endpoint (A8)
# ---------------------------------------------------------------------------


@router.post("/inbox/resolve-high-confidence", name="resolve_high_confidence")
def resolve_high_confidence(request: Request) -> RedirectResponse:
    services = request.app.state.services
    resolved_ids = services.organizer.resolve_high_confidence_recordings(threshold=0.70)
    if resolved_ids:
        with _session_factory(request)() as session:
            pms_url = _setting(session, "pms_url", services.settings.pms_url)
            pms_token = _setting(session, "pms_token", services.settings.pms_token)
            plex = services.plex.with_credentials(pms_url, pms_token)
            for section_id in plex.find_show_section_ids():
                create_refresh_job(session, section_id=section_id)
            session.commit()
    return RedirectResponse(url=str(request.url_for("inbox")), status_code=303)


# ---------------------------------------------------------------------------
# Season detail page (D1 / A7)
# ---------------------------------------------------------------------------


@router.get("/library/{competition_id}/seasons/{season_id}", response_class=HTMLResponse, name="library_season_detail")
def library_season_detail(request: Request, competition_id: str, season_id: str) -> HTMLResponse:
    with _session_factory(request)() as session:
        competition = session.get(Competition, competition_id)
        if competition is None:
            raise HTTPException(status_code=404, detail="Unknown competition")
        season, events, recordings_by_event_id = get_season_with_events_and_recordings(session, season_id)
        if season is None:
            raise HTTPException(status_code=404, detail="Unknown season")
        aliases = list_competition_aliases(session, competition_id)
        rows = []
        for event in events:
            event_recordings = recordings_by_event_id.get(event.id, [])
            rows.append(
                {
                    "event": event,
                    "recordings": event_recordings,
                    "status": (
                        _status_meta(event_recordings[0].status) if event_recordings else None
                    ),
                }
            )
    return _render(
        request,
        "library_season.html",
        {
            "competition": competition,
            "season": season,
            "rows": rows,
            "aliases": aliases,
        },
    )
