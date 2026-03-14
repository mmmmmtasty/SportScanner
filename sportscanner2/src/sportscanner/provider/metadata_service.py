from __future__ import annotations

from sqlalchemy import select

from fastapi import HTTPException

from sportscanner.db.models import Competition, CompetitionSeason, Event, Recording, RecordingStatus
from sportscanner.provider.artwork import competition_fanart_url, competition_poster_url, episode_background_url, episode_cover_poster_url, event_thumb_url
from sportscanner.provider.items import episode_metadata, season_episode_items, season_metadata, show_episode_items, show_metadata, show_season_items
from sportscanner.provider.rating_keys import parse_rating_key
from sportscanner.provider.schemas import ImageModel, MetadataItemModel


def parse_rating_key_or_404(rating_key: str) -> tuple[str, tuple[str, ...]]:
    try:
        return parse_rating_key(rating_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown metadata item") from exc


class ProviderMetadataService:
    def __init__(self, *, session_factory, provider_identifier: str, asset_url_builder=None) -> None:
        self.session_factory = session_factory
        self.provider_identifier = provider_identifier
        self.asset_url_builder = asset_url_builder

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
                    asset_url_builder=self.asset_url_builder,
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
                    asset_url_builder=self.asset_url_builder,
                )

            recording = session.scalar(
                select(Recording)
                .where(Recording.id == values[0], Recording.status == RecordingStatus.PUBLISHED.value)
                .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
            )
            if recording is None:
                raise HTTPException(status_code=404, detail="Unknown episode")
            season = session.get(CompetitionSeason, recording.competition_season_id)
            if season is None:
                raise HTTPException(status_code=404, detail="Missing season")
            competition = session.get(Competition, season.competition_id)
            if competition is None:
                raise HTTPException(status_code=404, detail="Missing show")
            event = session.get(Event, recording.event_id) if recording.event_id else None
            return episode_metadata(
                competition,
                season,
                event,
                recording,
                self.provider_identifier,
                self.asset_url_builder,
            )

    def children(self, rating_key: str) -> list[MetadataItemModel]:
        entity_type, values = parse_rating_key_or_404(rating_key)
        with self.session_factory() as session:
            if entity_type == "show":
                competition = session.get(Competition, values[0])
                if competition is None:
                    raise HTTPException(status_code=404, detail="Unknown show")
                return show_season_items(
                    session,
                    competition,
                    self.provider_identifier,
                    self.asset_url_builder,
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
                return season_episode_items(
                    session,
                    competition,
                    season,
                    self.provider_identifier,
                    self.asset_url_builder,
                )

        raise HTTPException(status_code=404, detail="Episodes do not have children")

    def grandchildren(self, rating_key: str) -> list[MetadataItemModel]:
        entity_type, values = parse_rating_key_or_404(rating_key)
        if entity_type != "show":
            raise HTTPException(status_code=404, detail="Only shows have grandchildren")
        with self.session_factory() as session:
            competition = session.get(Competition, values[0])
            if competition is None:
                raise HTTPException(status_code=404, detail="Unknown show")
            return show_episode_items(
                session,
                competition,
                self.provider_identifier,
                self.asset_url_builder,
            )

    def images(self, rating_key: str) -> list[ImageModel]:
        entity_type, values = parse_rating_key_or_404(rating_key)
        with self.session_factory() as session:
            if entity_type == "show":
                competition = session.get(Competition, values[0])
                if competition is None:
                    raise HTTPException(status_code=404, detail="Unknown show")
                images = [
                    ImageModel(type="coverPoster", url=competition_poster_url(competition, self.asset_url_builder), alt=competition.name)
                    for _ in [competition_poster_url(competition, self.asset_url_builder)]
                    if competition_poster_url(competition, self.asset_url_builder)
                ]
                background = competition_fanart_url(competition, self.asset_url_builder)
                if background:
                    images.append(ImageModel(type="background", url=background, alt=competition.name))
                return images

            if entity_type == "season":
                competition = session.get(Competition, values[0])
                if competition is None:
                    raise HTTPException(status_code=404, detail="Unknown season")
                images = []
                poster = competition_poster_url(competition, self.asset_url_builder)
                background = competition_fanart_url(competition, self.asset_url_builder)
                if poster:
                    images.append(ImageModel(type="coverPoster", url=poster, alt=competition.name))
                if background:
                    images.append(ImageModel(type="background", url=background, alt=competition.name))
                return images

            recording = session.scalar(
                select(Recording).where(Recording.id == values[0], Recording.status == RecordingStatus.PUBLISHED.value)
            )
            if recording is None:
                raise HTTPException(status_code=404, detail="Unknown episode")
            event = session.get(Event, recording.event_id) if recording.event_id else None
            season = session.get(CompetitionSeason, recording.competition_season_id)
            competition = session.get(Competition, season.competition_id) if season else None
            images = []
            thumb = event_thumb_url(recording, event, self.asset_url_builder)
            if thumb:
                images.append(ImageModel(type="snapshot", url=thumb, alt=recording.title))
            if competition:
                poster = episode_cover_poster_url(competition, event, self.asset_url_builder)
                background = episode_background_url(competition, event, self.asset_url_builder)
                if poster:
                    images.append(ImageModel(type="coverPoster", url=poster, alt=competition.name))
                if background:
                    images.append(ImageModel(type="background", url=background, alt=competition.name))
            return images
