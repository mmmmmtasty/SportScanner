from __future__ import annotations

from pathlib import Path

from sportscanner.organizer.parser import parse_filename


def test_parse_date_first_filename() -> None:
    parsed = parse_filename(Path("Formula 1 2025-06-29 Austrian Grand Prix - Race.mkv"))

    assert parsed.show == "Formula 1"
    assert parsed.event_date.isoformat() == "2025-06-29"
    assert parsed.title == "Austrian Grand Prix"
    assert parsed.segment_kind == "race"


def test_parse_legacy_filename_with_season_hint() -> None:
    parsed = parse_filename(Path("Formula1-2025-20250629-Austrian-Grand-Prix.mp4"))

    assert parsed.season_hint == 2025
    assert parsed.event_date.isoformat() == "2025-06-29"


def test_parse_dot_separated_filename() -> None:
    parsed = parse_filename(Path("English Premier League 2024.12.14 Arsenal vs Bournemouth.mkv"))

    assert parsed.show == "English Premier League"
    assert parsed.title == "Arsenal vs Bournemouth"
    assert parsed.segment_kind == "other"

