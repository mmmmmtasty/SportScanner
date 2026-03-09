from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Iterable

from sportscanner.db.models import Competition, SeasonPattern
from sportscanner.organizer.parser import ParsedFile
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


@dataclass(slots=True)
class CompetitionMatch:
    name: str
    competition_id: str | None
    tsdb_id: int | None
    confidence: float
    source: str


@dataclass(slots=True)
class EventMatch:
    event: UpstreamEvent | None
    confidence: float
    method: str
    candidates: list[dict]


def best_db_competition_match(query: str, competitions: Iterable[Competition], threshold: float = 0.8) -> Competition | None:
    best_score = 0.0
    best_competition: Competition | None = None
    for competition in competitions:
        for name in competition.all_names():
            score = similarity(query, name)
            if score > best_score:
                best_score = score
                best_competition = competition
    if best_score >= threshold:
        return best_competition
    return None


def best_upstream_competition_match(
    query: str,
    competitions: Iterable[UpstreamCompetition],
    threshold: float = 0.8,
) -> CompetitionMatch | None:
    best: CompetitionMatch | None = None
    for competition in competitions:
        score = max(similarity(query, name) for name in [competition.name, *competition.alternate_names] if name)
        if best is None or score > best.confidence:
            best = CompetitionMatch(
                name=competition.name,
                competition_id=competition.id,
                tsdb_id=competition.tsdb_id,
                confidence=score,
                source="upstream",
            )
    if best is None or best.confidence < threshold:
        return None
    return best


def season_for_date(event_date: date, competition: Competition) -> tuple[int, str]:
    if competition.season_pattern == SeasonPattern.CROSS_YEAR.value:
        split_month = competition.season_split_month or 7
        split_day = competition.season_split_day or 1
        if (event_date.month, event_date.day) < (split_month, split_day):
            start_year = event_date.year - 1
        else:
            start_year = event_date.year
        return (start_year, f"{start_year}-{str(start_year + 1)[-2:]}")
    return (event_date.year, str(event_date.year))


def match_event(
    parsed: ParsedFile,
    *,
    search_results: Iterable[UpstreamEvent],
    season_events: Iterable[UpstreamEvent],
) -> EventMatch:
    normalized_show = parsed.show.lower()
    filtered_search = [
        item for item in search_results
        if not item.competition_name or similarity(normalized_show, item.competition_name.lower()) >= 0.7
    ]
    if filtered_search:
        best_search = max(filtered_search, key=lambda item: similarity(parsed.title, item.name))
        score = similarity(parsed.title, best_search.name)
        if score >= 0.9:
            return EventMatch(
                event=best_search,
                confidence=0.95,
                method="searchfilename",
                candidates=[{"event_id": best_search.id, "name": best_search.name, "confidence": score}],
            )

    same_day = [item for item in season_events if parsed.event_date and item.date == parsed.event_date]
    if same_day:
        ranked = sorted(same_day, key=lambda item: similarity(parsed.title, item.name), reverse=True)
        best = ranked[0]
        best_score = similarity(parsed.title, best.name)
        if best_score >= 0.8:
            return EventMatch(
                event=best,
                confidence=best_score,
                method="eventsday_title",
                candidates=[{"event_id": item.id, "name": item.name, "confidence": similarity(parsed.title, item.name)} for item in ranked[:5]],
            )
        if len(same_day) == 1:
            return EventMatch(
                event=best,
                confidence=0.7,
                method="single_event_on_date",
                candidates=[{"event_id": best.id, "name": best.name, "confidence": 0.7}],
            )
        return EventMatch(
            event=None,
            confidence=best_score,
            method="eventsday_ambiguous",
            candidates=[{"event_id": item.id, "name": item.name, "confidence": similarity(parsed.title, item.name)} for item in ranked[:5]],
        )

    ranked = sorted(season_events, key=lambda item: similarity(parsed.title, item.name), reverse=True)
    top = ranked[:5]
    return EventMatch(
        event=None,
        confidence=similarity(parsed.title, top[0].name) if top else 0.0,
        method="unmatched",
        candidates=[{"event_id": item.id, "name": item.name, "confidence": similarity(parsed.title, item.name)} for item in top],
    )

