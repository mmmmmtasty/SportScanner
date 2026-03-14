from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from sportscanner.db.models import Competition, CompetitionSeason, Event, Recording, RecordingStatus
from sportscanner.provider.items import (
    dedupe_metadata,
    episode_display_title,
    episode_metadata,
    recording_event_dates,
    season_metadata,
    sequence_score,
    show_metadata,
    with_score,
)
from sportscanner.provider.rating_keys import parse_guid
from sportscanner.provider.schemas import MatchRequestModel, MetadataItemModel


class ProviderMatchService:
    def __init__(self, *, session_factory, provider_identifier: str, asset_url_builder=None) -> None:
        self.session_factory = session_factory
        self.provider_identifier = provider_identifier
        self.asset_url_builder = asset_url_builder

    def match(self, payload: MatchRequestModel) -> list[MetadataItemModel]:
        with self.session_factory() as session:
            if payload.type == 2:
                return self._match_show_items(session, payload)
            if payload.type == 3:
                return self._match_season_items(session, payload)
            return self._match_episode_items(session, payload)

    def _competition_for_recording(self, session, recording_id: str) -> Competition | None:
        recording = session.get(Recording, recording_id)
        if recording is None:
            return None
        season = session.get(CompetitionSeason, recording.competition_season_id)
        if season is None:
            return None
        return session.get(Competition, season.competition_id)

    def _season_for_recording(self, session, recording_id: str) -> CompetitionSeason | None:
        recording = session.get(Recording, recording_id)
        if recording is None:
            return None
        return session.get(CompetitionSeason, recording.competition_season_id)

    def _competition_from_guid(self, session, guid: str | None) -> Competition | None:
        if not guid:
            return None
        try:
            entity_type, values = parse_guid(guid, self.provider_identifier)
        except ValueError:
            return None
        if entity_type in {"show", "season"}:
            return session.get(Competition, values[0])
        return self._competition_for_recording(session, values[0])

    def _season_from_guid(self, session, guid: str | None) -> CompetitionSeason | None:
        if not guid:
            return None
        try:
            entity_type, values = parse_guid(guid, self.provider_identifier)
        except ValueError:
            return None
        if entity_type == "season":
            return session.scalar(
                select(CompetitionSeason).where(
                    CompetitionSeason.competition_id == values[0],
                    CompetitionSeason.season_number == int(values[1]),
                )
            )
        if entity_type == "episode":
            return self._season_for_recording(session, values[0])
        return None

    def _recording_from_filename(self, session, filename: str | None) -> Recording | None:
        if not filename:
            return None
        basename = Path(filename).name
        if not basename:
            return None
        matches = list(
            session.scalars(
                select(Recording)
                .where(
                    Recording.status == RecordingStatus.PUBLISHED.value,
                    (Recording.managed_path.like(f"%/{basename}")) | (Recording.source_path.like(f"%/{basename}")),
                )
                .order_by(Recording.id.asc())
            )
        )
        if len(matches) == 1:
            return matches[0]
        return None

    def _competition_candidates(
        self,
        session,
        *,
        title: str | None,
        guid: str | None,
        filename: str | None = None,
        year: int | None = None,
    ) -> list[tuple[int, Competition]]:
        competition = self._competition_from_guid(session, guid)
        if competition is not None:
            return [(100, competition)]

        recording = self._recording_from_filename(session, filename)
        if recording is not None:
            competition = self._competition_for_recording(session, recording.id)
            if competition is not None:
                return [(95, competition)]

        if not title:
            return []

        competitions = list(session.scalars(select(Competition).order_by(Competition.name.asc())))
        candidates: list[tuple[int, Competition]] = []
        for competition in competitions:
            score = max(sequence_score(title, name) for name in competition.all_names())
            if year is not None and competition.formed_year == year:
                score = min(100, score + 10)
            if score >= 60:
                candidates.append((score, competition))
        candidates.sort(key=lambda item: (-item[0], item[1].name))
        return candidates

    def _match_show_items(self, session, payload: MatchRequestModel) -> list[MetadataItemModel]:
        title = payload.title or payload.parentTitle or payload.grandparentTitle
        candidates = self._competition_candidates(
            session,
            title=title,
            guid=payload.guid,
            filename=payload.filename,
            year=payload.year,
        )
        if not payload.manual:
            candidates = candidates[:1]
        return dedupe_metadata(
            [
                with_score(
                    show_metadata(
                        session,
                        competition,
                        self.provider_identifier,
                        include_children=payload.includeChildren,
                        asset_url_builder=self.asset_url_builder,
                    ),
                    score,
                )
                for score, competition in candidates
            ]
        )

    def _match_season_items(self, session, payload: MatchRequestModel) -> list[MetadataItemModel]:
        direct_competition = self._competition_from_guid(session, payload.guid)
        direct_season = self._season_from_guid(session, payload.guid)
        filename_recording = self._recording_from_filename(session, payload.filename)
        if direct_season is None and filename_recording is not None:
            direct_season = session.get(CompetitionSeason, filename_recording.competition_season_id)
        if direct_competition is None and direct_season is not None:
            direct_competition = session.get(Competition, direct_season.competition_id)
        direct_season_number = direct_season.season_number if direct_season is not None else None

        if direct_competition is not None:
            competition_candidates = [(100, direct_competition)]
        else:
            competition_candidates = self._competition_candidates(
                session,
                title=payload.parentTitle or payload.title or payload.grandparentTitle,
                guid=None,
                filename=payload.filename,
                year=payload.year,
            )

        season_hints = [
            hint
            for hint in (
                direct_season_number,
                payload.parentIndex,
                payload.year,
                payload.date.year if payload.date else None,
            )
            if hint is not None
        ]
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
                    title_score = sequence_score(payload.title, season.label)
                    if title_score >= 60:
                        score = max(score, title_score)
                items.append((score, season, competition))

        items.sort(key=lambda item: (item[0], item[1].season_number), reverse=True)
        if not payload.manual:
            items = items[:1]
        return dedupe_metadata(
            [
                with_score(
                    season_metadata(
                        session,
                        competition,
                        season,
                        self.provider_identifier,
                        include_children=payload.includeChildren,
                        asset_url_builder=self.asset_url_builder,
                    ),
                    score,
                )
                for score, season, competition in items
            ]
        )

    def _match_episode_items(self, session, payload: MatchRequestModel) -> list[MetadataItemModel]:
        direct_recording_id: str | None = None
        direct_competition_id: str | None = None
        direct_season_number: int | None = None
        if payload.guid:
            try:
                entity_type, values = parse_guid(payload.guid, self.provider_identifier)
                if entity_type == "episode":
                    direct_recording_id = values[0]
                direct_competition_id = values[0]
                if entity_type == "season":
                    direct_season_number = int(values[1])
            except ValueError:
                pass

        if direct_recording_id is None:
            filename_recording = self._recording_from_filename(session, payload.filename)
            if filename_recording is not None:
                direct_recording_id = filename_recording.id

        if direct_recording_id is not None:
            recording = session.get(Recording, direct_recording_id)
            if recording is None or recording.status != RecordingStatus.PUBLISHED.value:
                return []
            season = session.get(CompetitionSeason, recording.competition_season_id)
            if season is None:
                return []
            competition = session.get(Competition, season.competition_id)
            if competition is None:
                return []
            event = session.get(Event, recording.event_id) if recording.event_id else None
            return [
                with_score(
                    episode_metadata(
                        competition,
                        season,
                        event,
                        recording,
                        self.provider_identifier,
                        self.asset_url_builder,
                    ),
                    100,
                )
            ]

        if payload.index is None and payload.date is None and not payload.title:
            return []

        if direct_competition_id is not None:
            competition = session.get(Competition, direct_competition_id)
            competition_candidates = [(100, competition)] if competition is not None else []
        else:
            competition_candidates = self._competition_candidates(
                session,
                title=payload.grandparentTitle or payload.parentTitle or payload.title,
                guid=None,
                filename=payload.filename,
                year=payload.year,
            )

        requested_season = direct_season_number or payload.parentIndex
        items: list[tuple[int, Recording, CompetitionSeason, Competition, Event | None]] = []
        for competition_score, competition in competition_candidates:
            seasons = {
                season.id: season
                for season in session.scalars(
                    select(CompetitionSeason).where(CompetitionSeason.competition_id == competition.id)
                )
            }
            recordings = list(
                session.scalars(
                    select(Recording)
                    .join(CompetitionSeason, CompetitionSeason.id == Recording.competition_season_id)
                    .where(
                        CompetitionSeason.competition_id == competition.id,
                        Recording.status == RecordingStatus.PUBLISHED.value,
                    )
                    .order_by(
                        CompetitionSeason.season_number.desc(),
                        Recording.episode_number.desc(),
                        Recording.id.asc(),
                    )
                )
            )
            event_dates = recording_event_dates(session, recordings)
            for recording in recordings:
                season = seasons[recording.competition_season_id]
                score = competition_score
                if requested_season is not None:
                    if season.season_number != requested_season:
                        continue
                    score = min(100, score + 15)
                if payload.index is not None:
                    if recording.episode_number != payload.index:
                        continue
                    score = min(100, score + 30)
                recording_date = event_dates.get(recording.event_id or "") or recording.air_date
                if payload.date is not None:
                    if recording_date != payload.date:
                        continue
                    score = min(100, score + 20)
                event = session.get(Event, recording.event_id) if recording.event_id else None
                if payload.title:
                    display_title = episode_display_title(recording, event, competition.name)
                    title_score = max(
                        sequence_score(payload.title, recording.title),
                        sequence_score(payload.title, display_title),
                    )
                    if title_score < 60 and payload.index is None and payload.date is None:
                        continue
                    score = max(score, min(100, title_score))
                items.append((score, recording, season, competition, event))

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
        return dedupe_metadata(
            [
                with_score(
                    episode_metadata(
                        competition,
                        season,
                        event,
                        recording,
                        self.provider_identifier,
                        self.asset_url_builder,
                    ),
                    score,
                )
                for score, recording, season, competition, event in items
            ]
        )
