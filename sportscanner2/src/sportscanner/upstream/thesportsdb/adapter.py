from __future__ import annotations

import re
from datetime import date, time
from urllib.parse import urlparse

from sportscanner.text import sanitize_event_name
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


def _csv_text(row: dict, key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    trimmed = value.rstrip("Z")
    if len(trimmed) == 5:
        trimmed = f"{trimmed}:00"
    try:
        return time.fromisoformat(trimmed)
    except ValueError:
        return None


def adapt_competition(payload: dict) -> UpstreamCompetition:
    alternate = payload.get("strLeagueAlternate") or ""
    alternates = [item.strip() for item in alternate.split(",") if item.strip()]
    tsdb_id = payload.get("idLeague")
    return UpstreamCompetition(
        id=f"tsdb_{tsdb_id}" if tsdb_id else f"manual_{payload.get('strLeague', 'competition')}",
        tsdb_id=int(tsdb_id) if tsdb_id else None,
        name=payload.get("strLeague") or payload.get("strLeagueEnglish") or "Unknown Competition",
        alternate_names=alternates,
        sport=payload.get("strSport"),
        country=payload.get("strCountry"),
        formed_year=int(payload["intFormedYear"]) if payload.get("intFormedYear") else None,
        description=payload.get("strDescriptionEN"),
        poster_url=payload.get("strPoster"),
        banner_url=payload.get("strBanner"),
        fanart_url=payload.get("strFanart1") or payload.get("strFanart"),
        source_payload=dict(payload),
    )


def adapt_event(payload: dict, *, competition_name: str | None = None) -> UpstreamEvent:
    tsdb_id = payload.get("idEvent")
    league_id = payload.get("idLeague")
    return UpstreamEvent(
        id=f"tsdb_{tsdb_id}" if tsdb_id else f"manual_{payload.get('strEvent', 'event')}",
        tsdb_id=int(tsdb_id) if tsdb_id else None,
        competition_tsdb_id=int(league_id) if league_id else None,
        name=sanitize_event_name(payload.get("strEvent") or payload.get("strFilename")) or "Unknown Event",
        competition_name=competition_name or payload.get("strLeague") or "",
        date=_parse_date(payload.get("dateEvent") or payload.get("dateEventLocal")),
        time=_parse_time(payload.get("strTime") or payload.get("strTimeLocal")),
        round=int(payload["intRound"]) if payload.get("intRound") else None,
        venue=payload.get("strVenue") or payload.get("strCircuit"),
        city=payload.get("strCity"),
        country=payload.get("strCountry"),
        home_team=payload.get("strHomeTeam"),
        away_team=payload.get("strAwayTeam"),
        home_score=int(payload["intHomeScore"]) if payload.get("intHomeScore") else None,
        away_score=int(payload["intAwayScore"]) if payload.get("intAwayScore") else None,
        description=payload.get("strDescriptionEN"),
        thumb_url=payload.get("strThumb"),
        source_payload=dict(payload),
    )


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return True
    return value.lower().startswith("www.")


def _clean_csv_text(row: dict, *keys: str) -> str:
    for key in keys:
        value = _csv_text(row, key)
        if value:
            return value
    return ""


def _clean_team_name(value: str) -> str:
    return "" if _looks_like_url(value) else value


def _csv_event_name(row: dict, home_team: str, away_team: str) -> str:
    explicit_name = sanitize_event_name(
        _clean_csv_text(
            row,
            "Event",
            "Event Name",
            "Name",
            "strEvent",
            "Description",
        )
    )
    if explicit_name and not _looks_like_url(explicit_name):
        return explicit_name
    if home_team and away_team:
        return sanitize_event_name(f"{home_team} vs {away_team}") or "Unknown Event"
    return sanitize_event_name(home_team or away_team) or "Unknown Event"


def adapt_event_csv(row: dict, *, competition_name: str, competition_tsdb_id: int) -> UpstreamEvent | None:
    """Adapt a row from the TheSportsDB season CSV download to an UpstreamEvent."""
    tsdb_id_raw = _csv_text(row, "idEvent")
    if not tsdb_id_raw:
        return None

    home_team = _clean_team_name(_clean_csv_text(row, "Home Team", "strHomeTeam"))
    away_team = _clean_team_name(_clean_csv_text(row, "Away Team", "strAwayTeam"))
    name = _csv_event_name(row, home_team, away_team)

    round_val: int | None = None
    m = re.match(r"round\s+(\d+)$", _csv_text(row, "Round"), re.IGNORECASE)
    if m:
        round_val = int(m.group(1))

    home_score_raw = _csv_text(row, "Home Score")
    away_score_raw = _csv_text(row, "Away Score")

    return UpstreamEvent(
        id=f"tsdb_{tsdb_id_raw}",
        tsdb_id=int(tsdb_id_raw),
        competition_tsdb_id=competition_tsdb_id,
        name=name,
        competition_name=competition_name,
        date=_parse_date(_clean_csv_text(row, "dateEvent", "Date Event") or None),
        time=_parse_time(_clean_csv_text(row, "strTime", "Time") or None),
        round=round_val,
        venue=_clean_csv_text(row, "Venue", "strVenue", "strCircuit") or None,
        city=_clean_csv_text(row, "City", "strCity") or None,
        country=_clean_csv_text(row, "Country", "strCountry") or None,
        home_team=home_team or None,
        away_team=away_team or None,
        home_score=int(home_score_raw) if home_score_raw and home_score_raw.lstrip("-").isdigit() else None,
        away_score=int(away_score_raw) if away_score_raw and away_score_raw.lstrip("-").isdigit() else None,
        description=_clean_csv_text(row, "Description", "strDescriptionEN") or None,
        thumb_url=_clean_csv_text(row, "Thumb", "strThumb") or None,
        source_payload=dict(row),
    )
