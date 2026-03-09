from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from sportscanner import __version__
from sportscanner.db.models import Competition, CompetitionSeason, ReviewTaskStatus, Segment, SegmentStatus
from sportscanner.provider.rating_keys import (
    GUID_PREFIX,
    make_episode_guid,
    make_episode_rating_key,
    make_season_guid,
    make_season_rating_key,
    make_show_guid,
    make_show_rating_key,
    parse_guid,
    parse_rating_key,
)
from sportscanner.provider.schemas import (
    ImageModel,
    MatchRequestModel,
    MediaContainerModel,
    MetadataItemModel,
    SearchResultModel,
)

router = APIRouter(prefix="/provider/tv", tags=["provider"])


def _session_factory(request: Request):
    return request.app.state.services.session_factory


def _paginate(request: Request, items: list[Any]) -> tuple[list[Any], int, int]:
    start = int(request.headers.get("X-Plex-Container-Start", request.query_params.get("start", 0)))
    size = int(request.headers.get("X-Plex-Container-Size", request.query_params.get("size", 200)))
    sliced = items[start : start + size]
    return (sliced, start, len(items))


def _container(**kwargs: Any) -> dict[str, Any]:
    return {"MediaContainer": MediaContainerModel(**kwargs).model_dump(exclude_none=True)}


def _sequence_score(left: str, right: str) -> int:
    return round(SequenceMatcher(None, left.lower(), right.lower()).ratio() * 100)


def _show_metadata(request: Request, competition: Competition) -> MetadataItemModel:
    session_factory = _session_factory(request)
    with session_factory() as session:
        leaf_count = session.scalar(
            select(func.count(Segment.id))
            .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
            .where(
                CompetitionSeason.competition_id == competition.id,
                Segment.status == SegmentStatus.PUBLISHED.value,
            )
        ) or 0
    rating_key = make_show_rating_key(competition.id)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_show_guid(competition.id),
        key=f"/library/metadata/{rating_key}",
        type="show",
        title=competition.name,
        summary=competition.description,
        leafCount=leaf_count,
        thumb=competition.poster_url,
        art=competition.fanart_url,
    )


def _season_metadata(competition: Competition, season: CompetitionSeason) -> MetadataItemModel:
    rating_key = make_season_rating_key(competition.id, season.season_number)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_season_guid(competition.id, season.season_number),
        key=f"/library/metadata/{rating_key}",
        type="season",
        title=season.label,
        index=season.season_number,
        parentGuid=make_show_guid(competition.id),
        parentRatingKey=make_show_rating_key(competition.id),
        parentTitle=competition.name,
    )


def _episode_metadata(competition: Competition, season: CompetitionSeason, segment: Segment) -> MetadataItemModel:
    rating_key = make_episode_rating_key(segment.id)
    return MetadataItemModel(
        ratingKey=rating_key,
        guid=make_episode_guid(segment.id),
        key=f"/library/metadata/{rating_key}",
        type="episode",
        title=segment.title,
        summary=segment.summary,
        index=segment.episode_number,
        parentGuid=make_season_guid(competition.id, season.season_number),
        parentIndex=season.season_number,
        parentRatingKey=make_season_rating_key(competition.id, season.season_number),
        parentTitle=season.label,
        grandparentGuid=make_show_guid(competition.id),
        grandparentRatingKey=make_show_rating_key(competition.id),
        grandparentTitle=competition.name,
        originallyAvailableAt=segment.air_date,
        thumb=segment.thumb_url,
    )


@router.get("")
def provider_root() -> dict[str, Any]:
    return {
        "MediaProvider": {
            "identifier": GUID_PREFIX,
            "title": "SportScanner 2",
            "version": __version__,
            "Types": [
                {"type": 2, "Scheme": [{"scheme": GUID_PREFIX}]},
                {"type": 3, "Scheme": [{"scheme": GUID_PREFIX}]},
                {"type": 4, "Scheme": [{"scheme": GUID_PREFIX}]},
            ],
            "Feature": [
                {"type": "metadata", "key": "/library/metadata"},
                {"type": "match", "key": "/library/metadata/matches"},
            ],
        }
    }


@router.post("/library/metadata/matches")
def metadata_matches(request: Request, payload: MatchRequestModel) -> dict[str, Any]:
    session_factory = _session_factory(request)
    if payload.guid:
        try:
            entity_type, values = parse_guid(payload.guid)
        except ValueError:
            entity_type = ""
            values = ()
        with session_factory() as session:
            if entity_type == "show":
                competition = session.get(Competition, values[0])
                if competition is not None:
                    return _container(
                        size=1,
                        totalSize=1,
                        SearchResult=[
                            SearchResultModel(
                                id=competition.id,
                                guid=make_show_guid(competition.id),
                                name=competition.name,
                                score=100,
                            )
                        ],
                    )

    title = payload.title or payload.grandparentTitle or payload.parentTitle
    if not title:
        return _container(size=0, totalSize=0, SearchResult=[])

    with session_factory() as session:
        competitions = list(session.scalars(select(Competition).order_by(Competition.name.asc())))

    results = []
    for competition in competitions:
        score = max(_sequence_score(title, name) for name in competition.all_names())
        if score >= 60:
            results.append(
                SearchResultModel(
                    id=competition.id,
                    guid=make_show_guid(competition.id),
                    name=competition.name,
                    score=score,
                    year=competition.formed_year,
                )
            )
    results = sorted(results, key=lambda item: item.score, reverse=True)
    return _container(size=len(results), totalSize=len(results), SearchResult=results)


@router.get("/library/metadata/{rating_key}")
def metadata_by_rating_key(request: Request, rating_key: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    entity_type, values = parse_rating_key(rating_key)
    with session_factory() as session:
        if entity_type == "show":
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown show")
            item = _show_metadata(request, competition)
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
            item = _season_metadata(competition, season)
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
            item = _episode_metadata(competition, season, segment)
    return _container(size=1, totalSize=1, Metadata=[item])


@router.get("/library/metadata/{rating_key}/children")
def metadata_children(request: Request, rating_key: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    entity_type, values = parse_rating_key(rating_key)
    with session_factory() as session:
        if entity_type == "show":
            competition_id = values[0]
            competition = session.get(Competition, competition_id)
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown show")
            seasons = list(
                session.scalars(
                    select(CompetitionSeason)
                    .where(CompetitionSeason.competition_id == competition_id)
                    .order_by(CompetitionSeason.season_number.asc())
                )
            )
            items = [_season_metadata(competition, season) for season in seasons]
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
            segments = list(
                session.scalars(
                    select(Segment)
                    .where(
                        Segment.competition_season_id == season.id,
                        Segment.status == SegmentStatus.PUBLISHED.value,
                    )
                    .order_by(Segment.episode_number.asc())
                )
            )
            items = [_episode_metadata(competition, season, segment) for segment in segments]
        else:
            raise HTTPException(status_code=404, detail="Episodes do not have children")

    paged, start, total = _paginate(request, items)
    return _container(size=len(paged), totalSize=total, offset=start, Metadata=paged)


@router.get("/library/metadata/{rating_key}/grandchildren")
def metadata_grandchildren(request: Request, rating_key: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    entity_type, values = parse_rating_key(rating_key)
    if entity_type != "show":
        raise HTTPException(status_code=404, detail="Only shows have grandchildren")

    competition_id = values[0]
    with session_factory() as session:
        competition = session.get(Competition, competition_id)
        if competition is None:
            raise HTTPException(status_code=404, detail="Unknown show")
        seasons = {
            season.id: season
            for season in session.scalars(
                select(CompetitionSeason).where(CompetitionSeason.competition_id == competition_id)
            )
        }
        segments = list(
            session.scalars(
                select(Segment)
                .join(CompetitionSeason, CompetitionSeason.id == Segment.competition_season_id)
                .where(
                    CompetitionSeason.competition_id == competition_id,
                    Segment.status == SegmentStatus.PUBLISHED.value,
                )
                .order_by(CompetitionSeason.season_number.asc(), Segment.episode_number.asc())
            )
        )
        items = [
            _episode_metadata(competition, seasons[segment.competition_season_id], segment)
            for segment in segments
        ]

    paged, start, total = _paginate(request, items)
    return _container(size=len(paged), totalSize=total, offset=start, Metadata=paged)


@router.get("/library/metadata/{rating_key}/images")
def metadata_images(request: Request, rating_key: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    entity_type, values = parse_rating_key(rating_key)
    with session_factory() as session:
        if entity_type == "show":
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown show")
            images = [
                ImageModel(type="poster", url=competition.poster_url, alt=competition.name)
                for _ in [competition.poster_url]
                if competition.poster_url
            ]
            if competition.fanart_url:
                images.append(ImageModel(type="art", url=competition.fanart_url, alt=competition.name))
        elif entity_type == "season":
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown season")
            images = [ImageModel(type="poster", url=competition.poster_url, alt=competition.name)] if competition.poster_url else []
        else:
            segment = session.scalar(
                select(Segment).where(Segment.id == values[0], Segment.status == SegmentStatus.PUBLISHED.value)
            )
            if segment is None:
                raise HTTPException(status_code=404, detail="Unknown episode")
            images = [ImageModel(type="thumb", url=segment.thumb_url, alt=segment.title)] if segment.thumb_url else []
    return _container(size=len(images), totalSize=len(images), Image=images)
