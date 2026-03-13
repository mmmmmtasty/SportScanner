from __future__ import annotations

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
