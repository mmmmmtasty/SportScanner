from __future__ import annotations

from datetime import date

from sportscanner.db.models import Competition, SeasonPattern
from sportscanner.organizer.matcher import match_event, season_for_date
from sportscanner.organizer.parser import ParsedFile
from sportscanner.upstream.base import UpstreamEvent


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
    assert label == "2024-25"


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

