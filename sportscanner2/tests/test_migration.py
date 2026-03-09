from __future__ import annotations

from pathlib import Path

from sportscanner.migration.convert_sidecars import convert_library_root


def test_convert_legacy_sidecars(tmp_path: Path) -> None:
    legacy = tmp_path / "Formula 1 2025-06-29 Austrian Grand Prix - 03 Post-Race Analysis.SportScanner"
    legacy.write_text("3\n(Post-Race Analysis)\n", encoding="utf-8")

    results = convert_library_root(tmp_path)

    assert len(results) == 1
    output = legacy.with_suffix(".sportscanner.yml").read_text(encoding="utf-8")
    assert 'title_suffix: "(Post-Race Analysis)"' in output
    assert "legacy_subepisode: 3" in output


def test_convert_legacy_competition_config(tmp_path: Path) -> None:
    legacy = tmp_path / "SportScanner.txt"
    legacy.write_text("XXYY\n7,1\n", encoding="utf-8")

    convert_library_root(tmp_path)

    output = (tmp_path / "competition.sportscanner.yml").read_text(encoding="utf-8")
    assert "season_pattern: \"cross_year\"" in output
    assert "season_split_month: 7" in output

