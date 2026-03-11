from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from sportscanner import __version__
from sportscanner.config import validate_plex_provider_identifier
from sportscanner.db.models import AppSetting, Competition, CompetitionSeason, Event, Segment, SegmentStatus
from sportscanner.provider.rating_keys import (
    make_episode_guid,
    make_episode_rating_key,
    make_season_guid,
    make_season_rating_key,
    make_show_guid,
    make_show_rating_key,
    parse_guid,
    parse_rating_key,
)
from sportscanner.provider.schemas import ChildrenModel, ImageModel, MatchRequestModel, MediaContainerModel, MetadataItemModel

router = APIRouter(prefix="/provider/tv", tags=["provider"])


def _session_factory(request: Request):
    return request.app.state.services.session_factory


def _coerce_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}") from exc


def _coerce_date(value: Any, field_name: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}") from exc


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _paginate(request: Request, items: list[Any]) -> tuple[list[Any], int, int]:
    start = _coerce_int(request.headers.get("X-Plex-Container-Start", request.query_params.get("start")), "start") or 0
    size = _coerce_int(request.headers.get("X-Plex-Container-Size", request.query_params.get("size")), "size") or 20
    start = max(start, 0)
    size = max(size, 0)
    sliced = items[start : start + size]
    return (sliced, start, len(items))


def _container(request: Request, **kwargs: Any) -> dict[str, Any]:
    return {
        "MediaContainer": MediaContainerModel(
            identifier=_provider_identifier(request),
            **kwargs,
        ).model_dump(exclude_none=True)
    }


def _setting(request: Request, key: str, fallback: str) -> str:
    with _session_factory(request)() as session:
        setting = session.get(AppSetting, key)
    return setting.value if setting is not None else fallback


def _provider_identifier(request: Request) -> str:
    configured = _setting(
        request,
        "plex_provider_identifier",
        request.app.state.services.settings.plex_provider_identifier,
    )
    try:
        return validate_plex_provider_identifier(configured)
    except ValueError:
        return request.app.state.services.settings.plex_provider_identifier


def _provider_title(request: Request) -> str:
    return _setting(
        request,
        "plex_provider_group_name",
        request.app.state.services.settings.plex_provider_group_name,
    )


def _parse_rating_key_or_404(rating_key: str) -> tuple[str, tuple[str, ...]]:
    try:
        return parse_rating_key(rating_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown metadata item") from exc


def _sequence_score(left: str, right: str) -> int:
    return round(SequenceMatcher(None, left.lower(), right.lower()).ratio() * 100)


def _show_leaf_count(session, competition_id: str) -> int:
    return session.scalar(
        select(func.count(Segment.id))
        .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
        .where(
            CompetitionSeason.competition_id == competition_id,
            Segment.status == SegmentStatus.PUBLISHED.value,
        )
    ) or 0


def _segment_event_dates(session, segments: list[Segment]) -> dict[str, date | None]:
    event_ids = sorted({segment.event_id for segment in segments if segment.event_id})
    if not event_ids:
        return {}
    rows = session.execute(select(Event.id, Event.date).where(Event.id.in_(event_ids))).all()
    return {str(event_id): event_date for event_id, event_date in rows}


def _episode_metadata(
    competition: Competition,
    season: CompetitionSeason,
    event_date: date | None,
    segment: Segment,
    guid_prefix: str,
) -> MetadataItemModel:
    rating_key = make_episode_rating_key(segment.id)
    season_rating_key = make_season_rating_key(competition.id, season.season_number)
    show_rating_key = make_show_rating_key(competition.id)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_episode_guid(segment.id, guid_prefix),
        key=f"/library/metadata/{rating_key}",
        type="episode",
        title=segment.title,
        summary=segment.summary,
        index=segment.episode_number,
        parentKey=f"/library/metadata/{season_rating_key}",
        parentGuid=make_season_guid(competition.id, season.season_number, guid_prefix),
        parentIndex=season.season_number,
        parentRatingKey=season_rating_key,
        parentTitle=season.label,
        parentType="season",
        grandparentKey=f"/library/metadata/{show_rating_key}",
        grandparentGuid=make_show_guid(competition.id, guid_prefix),
        grandparentRatingKey=show_rating_key,
        grandparentTitle=competition.name,
        originallyAvailableAt=event_date or segment.air_date,
        thumb=segment.thumb_url,
    )


def _season_episode_items(
    session,
    competition: Competition,
    season: CompetitionSeason,
    guid_prefix: str,
) -> list[MetadataItemModel]:
    segments = list(
        session.scalars(
            select(Segment)
            .where(
                Segment.competition_season_id == season.id,
                Segment.status == SegmentStatus.PUBLISHED.value,
            )
            .order_by(Segment.episode_number.asc(), Segment.id.asc())
        )
    )
    event_dates = _segment_event_dates(session, segments)
    return [
        _episode_metadata(
            competition,
            season,
            event_dates.get(segment.event_id or ""),
            segment,
            guid_prefix,
        )
        for segment in segments
    ]


def _season_metadata(
    session,
    competition: Competition,
    season: CompetitionSeason,
    guid_prefix: str,
    *,
    include_children: bool = False,
) -> MetadataItemModel:
    rating_key = make_season_rating_key(competition.id, season.season_number)
    show_rating_key = make_show_rating_key(competition.id)
    children = None
    if include_children:
        episode_items = _season_episode_items(session, competition, season, guid_prefix)
        children = ChildrenModel(size=len(episode_items), Metadata=episode_items)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_season_guid(competition.id, season.season_number, guid_prefix),
        key=f"/library/metadata/{rating_key}/children",
        type="season",
        title=season.label,
        index=season.season_number,
        parentKey=f"/library/metadata/{show_rating_key}",
        parentGuid=make_show_guid(competition.id, guid_prefix),
        parentRatingKey=show_rating_key,
        parentTitle=competition.name,
        parentType="show",
        Children=children,
    )


def _show_season_items(session, competition: Competition, guid_prefix: str) -> list[MetadataItemModel]:
    seasons = list(
        session.scalars(
            select(CompetitionSeason)
            .where(CompetitionSeason.competition_id == competition.id)
            .order_by(CompetitionSeason.season_number.asc())
        )
    )
    return [_season_metadata(session, competition, season, guid_prefix) for season in seasons]


def _show_episode_items(session, competition: Competition, guid_prefix: str) -> list[MetadataItemModel]:
    seasons = {
        season.id: season
        for season in session.scalars(
            select(CompetitionSeason)
            .where(CompetitionSeason.competition_id == competition.id)
            .order_by(CompetitionSeason.season_number.asc())
        )
    }
    segments = list(
        session.scalars(
            select(Segment)
            .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
            .where(
                CompetitionSeason.competition_id == competition.id,
                Segment.status == SegmentStatus.PUBLISHED.value,
            )
            .order_by(CompetitionSeason.season_number.asc(), Segment.episode_number.asc(), Segment.id.asc())
        )
    )
    event_dates = _segment_event_dates(session, segments)
    return [
        _episode_metadata(
            competition,
            seasons[segment.competition_season_id],
            event_dates.get(segment.event_id or ""),
            segment,
            guid_prefix,
        )
        for segment in segments
    ]


def _show_metadata(
    session,
    request: Request,
    competition: Competition,
    guid_prefix: str,
    *,
    include_children: bool = False,
) -> MetadataItemModel:
    rating_key = make_show_rating_key(competition.id)
    children = None
    if include_children:
        season_items = _show_season_items(session, competition, guid_prefix)
        children = ChildrenModel(size=len(season_items), Metadata=season_items)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_show_guid(competition.id, guid_prefix),
        key=f"/library/metadata/{rating_key}/children",
        type="show",
        title=competition.name,
        summary=competition.description,
        year=competition.formed_year,
        leafCount=_show_leaf_count(session, competition.id),
        thumb=competition.poster_url,
        art=competition.fanart_url,
        Children=children,
    )


def _with_score(item: MetadataItemModel, score: int) -> MetadataItemModel:
    return item.model_copy(update={"score": score})


def _dedupe_metadata(items: list[MetadataItemModel]) -> list[MetadataItemModel]:
    seen: set[str] = set()
    deduped: list[MetadataItemModel] = []
    for item in items:
        if item.ratingKey in seen:
            continue
        seen.add(item.ratingKey)
        deduped.append(item)
    return deduped


def _competition_candidates(
    session,
    *,
    title: str | None,
    guid: str | None,
    guid_prefix: str,
    year: int | None = None,
) -> list[tuple[int, Competition]]:
    if guid:
        try:
            _, values = parse_guid(guid, guid_prefix)
            competition = session.get(Competition, values[0])
            if competition is not None:
                return [(100, competition)]
        except ValueError:
            pass

    if not title:
        return []

    competitions = list(session.scalars(select(Competition).order_by(Competition.name.asc())))
    candidates: list[tuple[int, Competition]] = []
    for competition in competitions:
        score = max(_sequence_score(title, name) for name in competition.all_names())
        if year is not None and competition.formed_year == year:
            score = min(100, score + 10)
        if score >= 60:
            candidates.append((score, competition))
    candidates.sort(key=lambda item: (-item[0], item[1].name))
    return candidates


def _match_show_items(
    session,
    request: Request,
    payload: MatchRequestModel,
    guid_prefix: str,
) -> list[MetadataItemModel]:
    title = payload.title or payload.parentTitle or payload.grandparentTitle
    candidates = _competition_candidates(
        session,
        title=title,
        guid=payload.guid,
        guid_prefix=guid_prefix,
        year=payload.year,
    )
    if not payload.manual:
        candidates = candidates[:1]
    return _dedupe_metadata(
        [
            _with_score(
                _show_metadata(
                    session,
                    request,
                    competition,
                    guid_prefix,
                    include_children=payload.includeChildren,
                ),
                score,
            )
            for score, competition in candidates
        ]
    )


def _match_season_items(
    session,
    request: Request,
    payload: MatchRequestModel,
    guid_prefix: str,
) -> list[MetadataItemModel]:
    direct_competition_id: str | None = None
    direct_season_number: int | None = None
    if payload.guid:
        try:
            entity_type, values = parse_guid(payload.guid, guid_prefix)
            direct_competition_id = values[0]
            if entity_type == "season":
                direct_season_number = int(values[1])
        except ValueError:
            pass

    if direct_competition_id is not None:
        competition = session.get(Competition, direct_competition_id)
        competition_candidates = [(100, competition)] if competition is not None else []
    else:
        competition_candidates = _competition_candidates(
            session,
            title=payload.parentTitle or payload.title or payload.grandparentTitle,
            guid=None,
            guid_prefix=guid_prefix,
            year=payload.year,
        )

    season_hints = [hint for hint in (direct_season_number, payload.parentIndex, payload.year, payload.date.year if payload.date else None) if hint is not None]
    items: list[tuple[int, CompetitionSeason, Competition]] = []
    for competition_score, competition in competition_candidates:
        seasons = list(
            session.scalars(
                select(CompetitionSeason)
                .where(CompetitionSeason.competition_id == competition.id)
                .order_by(CompetitionSeason.season_number.desc())
            )
        )
        for season in seasons:
            score = competition_score
            if season_hints:
                if season.season_number not in season_hints:
                    continue
                score = min(100, score + 20)
            elif payload.title:
                title_score = _sequence_score(payload.title, season.label)
                if title_score >= 60:
                    score = max(score, title_score)
            items.append((score, season, competition))

    items.sort(key=lambda item: (item[0], item[1].season_number), reverse=True)
    if not payload.manual:
        items = items[:1]
    return _dedupe_metadata(
        [
            _with_score(
                _season_metadata(
                    session,
                    competition,
                    season,
                    guid_prefix,
                    include_children=payload.includeChildren,
                ),
                score,
            )
            for score, season, competition in items
        ]
    )


def _match_episode_items(
    session,
    payload: MatchRequestModel,
    guid_prefix: str,
) -> list[MetadataItemModel]:
    direct_segment_id: str | None = None
    direct_competition_id: str | None = None
    direct_season_number: int | None = None
    if payload.guid:
        try:
            entity_type, values = parse_guid(payload.guid, guid_prefix)
            if entity_type == "episode":
                direct_segment_id = values[0]
            direct_competition_id = values[0]
            if entity_type == "season":
                direct_season_number = int(values[1])
        except ValueError:
            pass

    if direct_segment_id is not None:
        segment = session.get(Segment, direct_segment_id)
        if segment is None or segment.status != SegmentStatus.PUBLISHED.value:
            return []
        season = session.get(CompetitionSeason, segment.competition_season_id)
        if season is None:
            return []
        competition = session.get(Competition, season.competition_id)
        if competition is None:
            return []
        event = session.get(Event, segment.event_id) if segment.event_id else None
        return [
            _with_score(
                _episode_metadata(competition, season, event.date if event is not None else None, segment, guid_prefix),
                100,
            )
        ]

    if payload.index is None and payload.date is None and not payload.title:
        return []

    if direct_competition_id is not None:
        competition = session.get(Competition, direct_competition_id)
        competition_candidates = [(100, competition)] if competition is not None else []
    else:
        competition_candidates = _competition_candidates(
            session,
            title=payload.grandparentTitle or payload.parentTitle or payload.title,
            guid=None,
            guid_prefix=guid_prefix,
            year=payload.year,
        )

    requested_season = direct_season_number or payload.parentIndex
    items: list[tuple[int, Segment, CompetitionSeason, Competition]] = []
    for competition_score, competition in competition_candidates:
        seasons = {
            season.id: season
            for season in session.scalars(
                select(CompetitionSeason).where(CompetitionSeason.competition_id == competition.id)
            )
        }
        segments = list(
            session.scalars(
                select(Segment)
                .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
                .where(
                    CompetitionSeason.competition_id == competition.id,
                    Segment.status == SegmentStatus.PUBLISHED.value,
                )
                .order_by(CompetitionSeason.season_number.desc(), Segment.episode_number.desc(), Segment.id.asc())
            )
        )
        event_dates = _segment_event_dates(session, segments)
        for segment in segments:
            season = seasons[segment.competition_season_id]
            score = competition_score
            if requested_season is not None:
                if season.season_number != requested_season:
                    continue
                score = min(100, score + 15)
            if payload.index is not None:
                if segment.episode_number != payload.index:
                    continue
                score = min(100, score + 30)
            segment_date = event_dates.get(segment.event_id or "") or segment.air_date
            if payload.date is not None:
                if segment_date != payload.date:
                    continue
                score = min(100, score + 20)
            if payload.title:
                title_score = _sequence_score(payload.title, segment.title)
                if title_score < 60 and payload.index is None and payload.date is None:
                    continue
                score = max(score, min(100, title_score))
            items.append((score, segment, season, competition))

    items.sort(
        key=lambda item: (
            item[0],
            item[2].season_number,
            item[1].episode_number or 0,
        ),
        reverse=True,
    )
    if not payload.manual:
        items = items[:1]
    return _dedupe_metadata(
        [
            _with_score(
                _episode_metadata(
                    competition,
                    season,
                    (session.get(Event, segment.event_id).date if segment.event_id and session.get(Event, segment.event_id) else None),
                    segment,
                    guid_prefix,
                ),
                score,
            )
            for score, segment, season, competition in items
        ]
    )


@router.get("")
def provider_root(request: Request) -> dict[str, Any]:
    guid_prefix = _provider_identifier(request)
    provider_title = _provider_title(request)
    return {
        "MediaProvider": {
            "identifier": guid_prefix,
            "title": provider_title,
            "version": __version__,
            "Types": [
                {"type": 2, "Scheme": [{"scheme": guid_prefix}]},
                {"type": 3, "Scheme": [{"scheme": guid_prefix}]},
                {"type": 4, "Scheme": [{"scheme": guid_prefix}]},
            ],
            "Feature": [
                {"type": "metadata", "key": "/library/metadata"},
                {"type": "match", "key": "/library/metadata/matches", "method": "POST", "mediaTypes": [2]},
            ],
        }
    }


@router.post("/library/metadata/matches")
async def metadata_matches(request: Request) -> dict[str, Any]:
    qp = dict(request.query_params)
    body: dict[str, Any] = {}
    raw_body = await request.body()
    if raw_body:
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            try:
                body = await request.json()
            except Exception:
                body = {}
        elif "application/x-www-form-urlencoded" in ct or not ct:
            try:
                import urllib.parse

                body = dict(urllib.parse.parse_qsl(raw_body.decode()))
            except Exception:
                body = {}
    merged = {**body, **{key: value for key, value in qp.items() if value is not None}}
    payload = MatchRequestModel(
        type=_coerce_int(merged.get("type"), "type"),
        title=merged.get("title"),
        guid=merged.get("guid"),
        parentIndex=_coerce_int(merged.get("parentIndex"), "parentIndex"),
        index=_coerce_int(merged.get("index"), "index"),
        grandparentTitle=merged.get("grandparentTitle"),
        parentTitle=merged.get("parentTitle"),
        year=_coerce_int(merged.get("year"), "year"),
        date=_coerce_date(merged.get("date"), "date"),
        manual=_truthy(merged.get("manual")),
        includeChildren=_truthy(merged.get("includeChildren")),
        filename=merged.get("filename"),
        metadata=merged.get("metadata") if isinstance(merged.get("metadata"), dict) else None,
    )
    if payload.type is None:
        raise HTTPException(status_code=400, detail="type is required")

    guid_prefix = _provider_identifier(request)
    session_factory = _session_factory(request)
    with session_factory() as session:
        if payload.type == 2:
            items = _match_show_items(session, request, payload, guid_prefix)
        elif payload.type == 3:
            items = _match_season_items(session, request, payload, guid_prefix)
        else:
            items = _match_episode_items(session, payload, guid_prefix)

    paged, start, total = _paginate(request, items)
    return _container(request, size=len(paged), totalSize=total, offset=start, Metadata=paged)


@router.get("/library/metadata/{rating_key}")
def metadata_by_rating_key(request: Request, rating_key: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    guid_prefix = _provider_identifier(request)
    include_children = _truthy(request.query_params.get("includeChildren"))
    entity_type, values = _parse_rating_key_or_404(rating_key)
    with session_factory() as session:
        if entity_type == "show":
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown show")
            item = _show_metadata(session, request, competition, guid_prefix, include_children=include_children)
        elif entity_type == "season":
            competition_id, season_number = values[0], int(values[1])
            competition = session.get(Competition, competition_id)
            season = session.scalar(
                select(CompetitionSeason).where(
                    CompetitionSeason.competition_id == competition_id,
                    CompetitionSeason.season_number == season_number,
                )
            )
            if competition is None or season is None:
                raise HTTPException(status_code=404, detail="Unknown season")
            item = _season_metadata(session, competition, season, guid_prefix, include_children=include_children)
        else:
            segment = session.scalar(
                select(Segment)
                .where(Segment.id == values[0], Segment.status == SegmentStatus.PUBLISHED.value)
                .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
            )
            if segment is None:
                raise HTTPException(status_code=404, detail="Unknown episode")
            season = session.get(CompetitionSeason, segment.competition_season_id)
            if season is None:
                raise HTTPException(status_code=404, detail="Missing season")
            competition = session.get(Competition, season.competition_id)
            if competition is None:
                raise HTTPException(status_code=404, detail="Missing show")
            event = session.get(Event, segment.event_id) if segment.event_id else None
            item = _episode_metadata(competition, season, event.date if event is not None else None, segment, guid_prefix)
    return _container(request, size=1, totalSize=1, Metadata=[item])


@router.get("/library/metadata/{rating_key}/children")
def metadata_children(request: Request, rating_key: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    guid_prefix = _provider_identifier(request)
    entity_type, values = _parse_rating_key_or_404(rating_key)
    with session_factory() as session:
        if entity_type == "show":
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown show")
            items = _show_season_items(session, competition, guid_prefix)
        elif entity_type == "season":
            competition_id, season_number = values[0], int(values[1])
            competition = session.get(Competition, competition_id)
            season = session.scalar(
                select(CompetitionSeason).where(
                    CompetitionSeason.competition_id == competition_id,
                    CompetitionSeason.season_number == season_number,
                )
            )
            if competition is None or season is None:
                raise HTTPException(status_code=404, detail="Unknown season")
            items = _season_episode_items(session, competition, season, guid_prefix)
        else:
            raise HTTPException(status_code=404, detail="Episodes do not have children")

    paged, start, total = _paginate(request, items)
    return _container(request, size=len(paged), totalSize=total, offset=start, Metadata=paged)


@router.get("/library/metadata/{rating_key}/grandchildren")
def metadata_grandchildren(request: Request, rating_key: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    entity_type, values = _parse_rating_key_or_404(rating_key)
    if entity_type != "show":
        raise HTTPException(status_code=404, detail="Only shows have grandchildren")

    with session_factory() as session:
        competition = session.get(Competition, values[0])
        if competition is None:
            raise HTTPException(status_code=404, detail="Unknown show")
        items = _show_episode_items(session, competition, _provider_identifier(request))

    paged, start, total = _paginate(request, items)
    return _container(request, size=len(paged), totalSize=total, offset=start, Metadata=paged)


@router.get("/library/metadata/{rating_key}/images")
def metadata_images(request: Request, rating_key: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    entity_type, values = _parse_rating_key_or_404(rating_key)
    with session_factory() as session:
        if entity_type == "show":
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown show")
            images = [
                ImageModel(type="coverPoster", url=competition.poster_url, alt=competition.name)
                for _ in [competition.poster_url]
                if competition.poster_url
            ]
            if competition.fanart_url:
                images.append(ImageModel(type="background", url=competition.fanart_url, alt=competition.name))
        elif entity_type == "season":
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown season")
            images = [ImageModel(type="coverPoster", url=competition.poster_url, alt=competition.name)] if competition.poster_url else []
        else:
            segment = session.scalar(
                select(Segment).where(Segment.id == values[0], Segment.status == SegmentStatus.PUBLISHED.value)
            )
            if segment is None:
                raise HTTPException(status_code=404, detail="Unknown episode")
            images = [ImageModel(type="background", url=segment.thumb_url, alt=segment.title)] if segment.thumb_url else []
    return _container(request, size=len(images), totalSize=len(images), Image=images)
