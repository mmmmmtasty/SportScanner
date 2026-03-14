from __future__ import annotations

from sportscanner.text import sanitize_event_name
from sportscanner.upstream.thesportsdb.adapter import adapt_competition, adapt_event, adapt_event_csv


def test_adapt_competition() -> None:
    competition = adapt_competition(
        {
            "idLeague": "4370",
            "strLeague": "Formula 1",
            "strLeagueAlternate": "F1, Formula One",
            "intFormedYear": "1950",
        }
    )

    assert competition.id == "tsdb_4370"
    assert competition.alternate_names == ["F1", "Formula One"]


def test_adapt_event() -> None:
    event = adapt_event(
        {
            "idEvent": "1001",
            "strEvent": "Austrian Grand Prix Race",
            "dateEvent": "2025-06-29",
            "strTime": "14:00:00",
            "intRound": "11",
        },
        competition_name="Formula 1",
    )

    assert event.id == "tsdb_1001"
    assert event.round == 11
    assert event.date.isoformat() == "2025-06-29"


def test_adapt_event_strips_embedded_url_suffix() -> None:
    event = adapt_event(
        {
            "idEvent": "1002",
            "strEvent": "UFC 326 Holloway vs Oliveira 2 vs https://www.thesportsdb.com/images/media/event/thumb/7yx5pk1772957209.jpg",
        },
        competition_name="UFC",
    )

    assert event.name == "UFC 326 Holloway vs Oliveira 2"


def test_adapt_event_csv_tolerates_null_score_fields() -> None:
    event = adapt_event_csv(
        {
            "idEvent": "1001",
            "Home Team": "Arsenal",
            "Away Team": "Tottenham",
            "Round": "Round 4",
            "Home Score": None,
            "Away Score": None,
            "dateEvent": "2025-04-12",
            "Thumb": None,
        },
        competition_name="English Premier League",
        competition_tsdb_id=4328,
    )

    assert event is not None
    assert event.name == "Arsenal vs Tottenham"
    assert event.round == 4
    assert event.home_score is None
    assert event.away_score is None


def test_adapt_event_csv_ignores_url_like_team_cells() -> None:
    event = adapt_event_csv(
        {
            "idEvent": "1001",
            "Event": "Australian Grand Prix",
            "Home Team": "Australian Grand Prix",
            "Away Team": "https://www.thesportsdb.com/event/1001",
            "Round": "Round 11",
            "dateEvent": "2025-06-29",
        },
        competition_name="Formula 1",
        competition_tsdb_id=4370,
    )

    assert event is not None
    assert event.name == "Australian Grand Prix"
    assert event.away_team is None


def test_sanitize_event_name_strips_trailing_url_and_separator() -> None:
    assert (
        sanitize_event_name(
            "Australian Grand Prix Qualifying vs https://r2.thesportsdb.com/images/media/event/thumb/g9dyu11740496426.jpg"
        )
        == "Australian Grand Prix Qualifying"
    )
