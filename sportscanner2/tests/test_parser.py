from __future__ import annotations

from pathlib import Path

from sportscanner.organizer.parser import infer_segment_kind, parse_filename


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
    assert parsed.segment_kind == "match"


def test_parse_epl_year_title_day_month_filename() -> None:
    parsed = parse_filename(Path("EPL 2025 Aston Villa vs Manchester United 21 12 1080pEN60fps Peacock.mkv"))

    assert parsed.show == "EPL"
    assert parsed.event_date.isoformat() == "2025-12-21"
    assert parsed.title == "Aston Villa vs Manchester United"
    assert parsed.segment_kind == "match"


def test_parse_nhl_day_first_filename() -> None:
    parsed = parse_filename(Path("NHL 14-01-2026 RS Philadelphia Flyers vs Buffalo Sabres 1080p60_EN_TNT.mkv"))

    assert parsed.show == "NHL"
    assert parsed.event_date.isoformat() == "2026-01-14"
    assert parsed.title == "Philadelphia Flyers vs Buffalo Sabres"
    assert parsed.segment_kind == "match"


def test_parse_nhl_prefix_year_title_day_month_filename() -> None:
    parsed = parse_filename(Path("NHL RS 2026 Tampa Bay Lightning vs Philadelphia Flyers 12 01 720pEN60fps NBCSP.mkv"))

    assert parsed.show == "NHL"
    assert parsed.event_date.isoformat() == "2026-01-12"
    assert parsed.title == "Tampa Bay Lightning vs Philadelphia Flyers"
    assert parsed.segment_kind == "match"


def test_parse_dot_separated_epl_filename_strips_resolution_suffix() -> None:
    parsed = parse_filename(Path("EPL.2025.12.04.Manchester.United.vs.West.Ham.1080p50.x264.EN.SKY.mp4"))

    assert parsed.show == "EPL"
    assert parsed.event_date.isoformat() == "2025-12-04"
    assert parsed.title == "Manchester United vs West Ham"


def test_infer_segment_kind_prefers_sprint_race_over_race() -> None:
    assert infer_segment_kind(None, "Sprint Race") == "sprint"
    assert infer_segment_kind(None, "Austrian Grand Prix Sprint Race") == "sprint"
