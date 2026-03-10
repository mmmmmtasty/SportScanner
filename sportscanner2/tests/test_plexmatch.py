from __future__ import annotations

from datetime import date

from sportscanner.db.models import Competition, CompetitionSeason, Segment
from sportscanner.organizer.plexmatch import render_season_plexmatch, render_show_plexmatch, write_atomic_if_changed


def test_render_show_plexmatch_contains_guid() -> None:
    competition = Competition(id="tsdb_4370", name="Formula 1")
    rendered = render_show_plexmatch(competition, "tv.plex.agents.custom.sportscanner.metadata.local")

    assert "guid: tv.plex.agents.custom.sportscanner.metadata.local://show/show_tsdb_4370" in rendered


def test_render_season_plexmatch_writes_ep_lines(tmp_path) -> None:
    competition = Competition(id="tsdb_4370", name="Formula 1")
    season = CompetitionSeason(id="season_tsdb_4370_2025", competition_id="tsdb_4370", season_number=2025, label="2025")
    segment = Segment(
        id="seg_primary",
        competition_season_id=season.id,
        kind="race",
        title="Austrian Grand Prix Race",
        episode_number=1150,
        air_date=date(2025, 6, 29),
        source_path="/tmp/Austrian.mkv",
        managed_path="/library/Formula 1/Season 2025/Formula 1 - 2025-06-29 - Austrian Grand Prix Race.mkv",
    )

    rendered = render_season_plexmatch(
        competition,
        season,
        [segment],
        "tv.plex.agents.custom.sportscanner.metadata.local",
    )
    changed = write_atomic_if_changed(tmp_path / ".plexmatch", rendered)

    assert changed is True
    assert "guid: tv.plex.agents.custom.sportscanner.metadata.local://season/season_tsdb_4370_2025" in rendered
    assert "ep: 1150: Formula 1 - 2025-06-29 - Austrian Grand Prix Race.mkv" in rendered
