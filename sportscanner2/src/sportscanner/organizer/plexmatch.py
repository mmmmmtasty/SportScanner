from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

from sportscanner.db.models import Competition, CompetitionSeason, Recording
from sportscanner.provider.rating_keys import make_season_guid, make_show_guid


def render_show_plexmatch(competition: Competition, guid_prefix: str) -> str:
    return "\n".join(
        [
            f"title: {competition.name}",
            f"guid: {make_show_guid(competition.id, guid_prefix)}",
            "",
        ]
    )


def render_season_plexmatch(
    competition: Competition,
    season: CompetitionSeason,
    recordings: Iterable[Recording],
    guid_prefix: str,
) -> str:
    lines = [
        f"title: {competition.name}",
        f"season: {season.season_number}",
        f"guid: {make_season_guid(competition.id, season.season_number, guid_prefix)}",
    ]
    for recording in sorted(recordings, key=lambda item: (item.episode_number or 0, item.title, item.source_path)):
        if recording.episode_number is None:
            continue
        filename = os.path.basename(recording.managed_path or recording.source_path)
        lines.append(f"ep: {recording.episode_number}: {filename}")
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
