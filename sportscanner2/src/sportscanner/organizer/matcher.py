from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Iterable

from sportscanner.db.models import Competition, SeasonPattern
from sportscanner.organizer.parser import ParsedFile
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


_COMPETITION_NOISE_SUFFIXES = re.compile(
    r"\b(?:world\s+championship|championship|series)\s*$",
    re.IGNORECASE,
)


def _normalize_competition_name(name: str) -> str:
    normalized = _COMPETITION_NOISE_SUFFIXES.sub("", name).strip()
    return normalized or name


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


def _season_event_confidence(
    parsed: ParsedFile,
    event: UpstreamEvent,
    *,
    same_day_count: int,
) -> float:
    title_score = similarity(parsed.title, event.name)
    if parsed.event_date and event.date == parsed.event_date and same_day_count == 1 and title_score < 0.8:
        return 0.7
    return title_score


def season_event_candidates(parsed: ParsedFile, season_events: Iterable[UpstreamEvent]) -> list[dict]:
    events = list(season_events)
    same_day_count = 0
    if parsed.event_date is not None:
        same_day_count = sum(1 for event in events if event.date == parsed.event_date)

    scored = [
        {
            "event_id": event.id,
            "name": event.name,
            "confidence": _season_event_confidence(parsed, event, same_day_count=same_day_count),
            "_same_day": parsed.event_date is not None and event.date == parsed.event_date,
        }
        for event in events
    ]
    scored.sort(key=lambda item: item["confidence"], reverse=True)
    scored.sort(key=lambda item: item["_same_day"], reverse=True)
    for item in scored:
        item.pop("_same_day", None)
    return scored


def best_db_competition_match(query: str, competitions: Iterable[Competition], threshold: float = 0.75) -> Competition | None:
    normalized_query = _normalize_competition_name(query)
    best_score = 0.0
    best_competition: Competition | None = None
    for competition in competitions:
        for name in competition.all_names():
            raw_score = similarity(query, name)
            norm_score = similarity(normalized_query, _normalize_competition_name(name))
            score = max(raw_score, norm_score)
            if score > best_score:
                best_score = score
                best_competition = competition
    if best_score >= threshold:
        return best_competition
    return None


def best_upstream_competition_match(
    query: str,
    competitions: Iterable[UpstreamCompetition],
    threshold: float = 0.75,
) -> CompetitionMatch | None:
    normalized_query = _normalize_competition_name(query)
    best: CompetitionMatch | None = None
    for competition in competitions:
        names = [name for name in [competition.name, *competition.alternate_names] if name]
        if not names:
            continue
        score = max(
            max(similarity(query, name) for name in names),
            max(similarity(normalized_query, _normalize_competition_name(name)) for name in names),
        )
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
        return (start_year, f"{start_year}-{start_year + 1}")
    return (event_date.year, str(event_date.year))


def build_match_explanation(
    *,
    method: str,
    confidence: float,
    event_name: str | None = None,
    competition_name: str | None = None,
    via_alias: str | None = None,
) -> dict:
    """Return a structured match explanation dict stored on Recording.match_explanation."""
    signals: list[str] = []

    if method == "sidecar_event_id":
        signals.append("direct event ID from sidecar file")
    elif method == "searchfilename":
        signals.append("filename search match")
        if confidence >= 0.9:
            signals.append("high title similarity")
    elif method == "eventsday_title":
        signals.append("exact date match")
        if confidence >= 0.8:
            signals.append("high title similarity")
        else:
            signals.append("moderate title similarity")
    elif method == "single_event_on_date":
        signals.append("only event on this date")
    elif method == "eventsday_ambiguous":
        signals.append("multiple events on date – ambiguous")
    elif method == "manual_resolution":
        signals.append("manually matched by user")
    elif method == "manual_lookup_event_id":
        signals.append("imported from TheSportsDB by user")
    elif method == "reassigned":
        signals.append("competition reassigned by user")
    elif method == "season_incomplete":
        signals.append("season schedule not yet complete")
    elif method == "competition_unknown":
        signals.append("competition could not be identified")
    elif method == "special_requires_review":
        signals.append("flagged as Season 0 special")

    if via_alias:
        signals.append(f"competition matched via alias '{via_alias}'")

    conf_pct = round(confidence * 100)
    if signals:
        summary = f"{', '.join(signals).capitalize()} ({conf_pct}%)"
    else:
        summary = f"Matched via {method.replace('_', ' ')} ({conf_pct}%)"

    return {
        "method": method,
        "confidence": confidence,
        "event_name": event_name,
        "competition": competition_name,
        "via_alias": via_alias,
        "signals": signals,
        "summary": summary,
        "history": [],
    }


def match_event(
    parsed: ParsedFile,
    *,
    search_results: Iterable[UpstreamEvent],
    season_events: Iterable[UpstreamEvent],
) -> EventMatch:
    normalized_show = parsed.show.lower()
    normalized_show_n = _normalize_competition_name(parsed.show).lower()
    filtered_search = [
        item for item in search_results
        if not item.competition_name or max(
            similarity(normalized_show, item.competition_name.lower()),
            similarity(normalized_show_n, _normalize_competition_name(item.competition_name).lower()),
        ) >= 0.6
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
