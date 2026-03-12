from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Protocol


@dataclass(slots=True)
class UpstreamCompetition:
    id: str
    name: str
    tsdb_id: int | None = None
    alternate_names: list[str] = field(default_factory=list)
    sport: str | None = None
    country: str | None = None
    formed_year: int | None = None
    description: str | None = None
    poster_url: str | None = None
    banner_url: str | None = None
    fanart_url: str | None = None


@dataclass(slots=True)
class UpstreamEvent:
    id: str
    name: str
    competition_name: str
    date: date | None = None
    time: time | None = None
    round: int | None = None
    venue: str | None = None
    city: str | None = None
    country: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    description: str | None = None
    thumb_url: str | None = None
    tsdb_id: int | None = None
    competition_tsdb_id: int | None = None
    weekend_group: str | None = None


class MetadataSource(Protocol):
    name: str

    def probe(self) -> str:
        ...

    def all_competitions(self) -> list[UpstreamCompetition]:
        ...

    def search_filename(self, query: str) -> list[UpstreamEvent]:
        ...

    def events_on_day(self, competition_name: str, event_date: date) -> list[UpstreamEvent]:
        ...

    def season_events(self, competition: UpstreamCompetition, season_label: str) -> tuple[list[UpstreamEvent], bool]:
        ...

    def lookup_competition(self, tsdb_id: int) -> UpstreamCompetition | None:
        ...

    def lookup_event(self, tsdb_event_id: int) -> UpstreamEvent | None:
        ...

