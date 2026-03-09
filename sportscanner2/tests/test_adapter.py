from __future__ import annotations

from sportscanner.upstream.thesportsdb.adapter import adapt_competition, adapt_event


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

