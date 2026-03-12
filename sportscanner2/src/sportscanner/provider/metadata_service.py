from __future__ import annotations

from sqlalchemy import select

from fastapi import HTTPException

from sportscanner.db.models import Competition, CompetitionSeason, Event, Segment, SegmentStatus
from sportscanner.metadata_snapshot import effective_episode_thumb
from sportscanner.provider.items import episode_metadata, season_episode_items, season_metadata, show_episode_items, show_metadata, show_season_items
from sportscanner.provider.rating_keys import parse_rating_key
from sportscanner.provider.schemas import ImageModel, MetadataItemModel


def parse_rating_key_or_404(rating_key: str) -> tuple[str, tuple[str, ...]]:
    try:
        return parse_rating_key(rating_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown metadata item") from exc


class ProviderMetadataService:
    def __init__(self, *, session_factory, provider_identifier: str) -> None:
        self.session_factory = session_factory
        self.provider_identifier = provider_identifier

    def by_rating_key(self, rating_key: str, *, include_children: bool = False) -> MetadataItemModel:
        entity_type, values = parse_rating_key_or_404(rating_key)
        with self.session_factory() as session:
            if entity_type == "show":
                competition = session.get(Competition, values[0])
                if competition is None:
                    raise HTTPException(status_code=404, detail="Unknown show")
                return show_metadata(
                    session,
                    competition,
                    self.provider_identifier,
                    include_children=include_children,
                )

            if entity_type == "season":
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
                return season_metadata(
                    session,
                    competition,
                    season,
                    self.provider_identifier,
                    include_children=include_children,
                )

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
            return episode_metadata(
                competition,
                season,
                event,
                segment,
                self.provider_identifier,
            )

    def children(self, rating_key: str) -> list[MetadataItemModel]:
        entity_type, values = parse_rating_key_or_404(rating_key)
        with self.session_factory() as session:
            if entity_type == "show":
                competition = session.get(Competition, values[0])
                if competition is None:
                    raise HTTPException(status_code=404, detail="Unknown show")
                return show_season_items(session, competition, self.provider_identifier)

            if entity_type == "season":
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
                return season_episode_items(session, competition, season, self.provider_identifier)

        raise HTTPException(status_code=404, detail="Episodes do not have children")

    def grandchildren(self, rating_key: str) -> list[MetadataItemModel]:
        entity_type, values = parse_rating_key_or_404(rating_key)
        if entity_type != "show":
            raise HTTPException(status_code=404, detail="Only shows have grandchildren")
        with self.session_factory() as session:
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown show")
            return show_episode_items(session, competition, self.provider_identifier)

    def images(self, rating_key: str) -> list[ImageModel]:
        entity_type, values = parse_rating_key_or_404(rating_key)
        with self.session_factory() as session:
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
                return images

            if entity_type == "season":
                competition = session.get(Competition, values[0])
                if competition is None:
                    raise HTTPException(status_code=404, detail="Unknown season")
                return [
                    ImageModel(type="coverPoster", url=competition.poster_url, alt=competition.name)
                ] if competition.poster_url else []

            segment = session.scalar(
                select(Segment).where(Segment.id == values[0], Segment.status == SegmentStatus.PUBLISHED.value)
            )
            if segment is None:
                raise HTTPException(status_code=404, detail="Unknown episode")
            event = session.get(Event, segment.event_id) if segment.event_id else None
            thumb = effective_episode_thumb(segment, event)
            return [ImageModel(type="snapshot", url=thumb, alt=segment.title)] if thumb else []
