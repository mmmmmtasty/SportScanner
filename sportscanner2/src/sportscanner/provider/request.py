from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

from sportscanner.provider.schemas import MatchRequestModel


def coerce_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}") from exc


def coerce_date(value: Any, field_name: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}") from exc


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def paginate(request: Request, items: list[Any]) -> tuple[list[Any], int, int]:
    start = coerce_int(
        request.headers.get("X-Plex-Container-Start")
        or request.query_params.get("X-Plex-Container-Start")
        or request.query_params.get("start"),
        "start",
    ) or 0
    size = coerce_int(
        request.headers.get("X-Plex-Container-Size")
        or request.query_params.get("X-Plex-Container-Size")
        or request.query_params.get("size"),
        "size",
    ) or 20
    start = max(start, 0)
    size = max(size, 0)
    return (items[start : start + size], start, len(items))


async def parse_match_request(request: Request) -> MatchRequestModel:
    merged = dict(request.query_params)
    raw_body = await request.body()
    if raw_body:
        content_type = request.headers.get("content-type", "")
        body: dict[str, Any] = {}
        if "application/json" in content_type:
            try:
                body = await request.json()
            except Exception:
                body = {}
        elif "application/x-www-form-urlencoded" in content_type or not content_type:
            try:
                body = dict(parse_qsl(raw_body.decode()))
            except Exception:
                body = {}
        merged = {**body, **merged}

    payload = MatchRequestModel(
        type=coerce_int(merged.get("type"), "type"),
        title=merged.get("title"),
        guid=merged.get("guid"),
        parentIndex=coerce_int(merged.get("parentIndex"), "parentIndex"),
        index=coerce_int(merged.get("index"), "index"),
        grandparentTitle=merged.get("grandparentTitle"),
        parentTitle=merged.get("parentTitle"),
        year=coerce_int(merged.get("year"), "year"),
        date=coerce_date(merged.get("date"), "date"),
        manual=truthy(merged.get("manual")),
        includeChildren=truthy(merged.get("includeChildren")),
        filename=merged.get("filename"),
        metadata=merged.get("metadata") if isinstance(merged.get("metadata"), dict) else None,
    )
    if payload.type is None:
        raise HTTPException(status_code=400, detail="type is required")
    return payload
