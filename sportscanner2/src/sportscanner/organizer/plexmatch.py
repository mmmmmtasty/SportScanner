from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

from sportscanner.db.models import Competition, CompetitionSeason, Segment
from sportscanner.provider.rating_keys import make_season_guid, make_show_guid


def render_show_plexmatch(competition: Competition) -> str:
    return "\n".join(
        [
            f"title: {competition.name}",
            f"guid: {make_show_guid(competition.id)}",
            "",
        ]
    )


def render_season_plexmatch(
    competition: Competition,
    season: CompetitionSeason,
    segments: Iterable[Segment],
) -> str:
    lines = [
        f"title: {competition.name}",
        f"season: {season.season_number}",
        f"guid: {make_season_guid(competition.id, season.season_number)}",
    ]
    for segment in sorted(segments, key=lambda item: (item.episode_number or 0, item.title, item.source_path)):
        if segment.episode_number is None:
            continue
        filename = os.path.basename(segment.managed_path or segment.source_path)
        lines.append(f"ep: {segment.episode_number}: {filename}")
    lines.append("")
    return "\n".join(lines)


def write_atomic_if_changed(target: Path, content: str) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text() == content:
        return False
    with tempfile.NamedTemporaryFile("w", delete=False, dir=target.parent, encoding="utf-8") as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, target)
    return True

