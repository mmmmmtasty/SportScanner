from __future__ import annotations

from datetime import date

from sportscanner.db.models import Competition, SeasonPattern
from sportscanner.organizer.matcher import best_db_competition_match, best_upstream_competition_match, match_event, season_for_date
from sportscanner.organizer.parser import ParsedFile
from sportscanner.upstream.base import UpstreamCompetition, UpstreamEvent


def test_cross_year_season_resolution() -> None:
    competition = Competition(
        id="manual_pl",
        name="Premier League",
        season_pattern=SeasonPattern.CROSS_YEAR.value,
        season_split_month=7,
        season_split_day=1,
    )
    season_number, label = season_for_date(date(2025, 5, 31), competition)

    assert season_number == 2024
    assert label == "2024-2025"


def test_match_event_prefers_filename_search_exactness() -> None:
    parsed = ParsedFile(
        source_path=None,  # type: ignore[arg-type]
        show="Formula 1",
        event_date=date(2025, 6, 29),
        title="Austrian Grand Prix Race",
        segment_kind="race",
        segment_label="Race",
    )
    search = [
        UpstreamEvent(id="tsdb_1001", name="Austrian Grand Prix Race", competition_name="Formula 1", date=parsed.event_date),
    ]
    match = match_event(parsed, search_results=search, season_events=search)

    assert match.event is not None
    assert match.method == "searchfilename"
    assert match.confidence == 0.95


def test_best_upstream_competition_match_normalizes_world_championship_suffix() -> None:
    match = best_upstream_competition_match(
        "Formula 1",
        [UpstreamCompetition(id="tsdb_1", tsdb_id=1, name="Formula 1 World Championship")],
    )

    assert match is not None
    assert match.tsdb_id == 1


def test_best_db_competition_match_normalizes_world_championship_suffix() -> None:
    match = best_db_competition_match(
        "Formula 1",
        [Competition(id="tsdb_1", tsdb_id=1, name="Formula 1 World Championship", alternate_names=[])],
    )

    assert match is not None
    assert match.tsdb_id == 1


def test_match_event_filters_search_results_with_normalized_competition_names() -> None:
    parsed = ParsedFile(
        source_path=None,  # type: ignore[arg-type]
        show="Formula 1",
        event_date=date(2025, 6, 29),
        title="Australian Grand Prix Race",
        segment_kind="race",
        segment_label="Race",
    )
    search = [
        UpstreamEvent(
            id="tsdb_1001",
            tsdb_id=1001,
            name="Australian Grand Prix Race",
            competition_name="Formula 1 World Championship",
            date=parsed.event_date,
        ),
    ]

    match = match_event(parsed, search_results=search, season_events=search)

    assert match.event is not None
    assert match.method == "searchfilename"
