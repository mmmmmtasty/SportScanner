from __future__ import annotations

import errno
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path


def season_directory_name(season_number: int) -> str:
    return "Season 0" if season_number == 0 else f"Season {season_number}"


def sanitize_filename_component(value: str) -> str:
    return value.replace("/", "-").replace(":", " -").strip()


def build_managed_filename(
    *,
    competition_name: str,
    air_date: date | None,
    title: str,
    source_path: str | Path,
) -> str:
    source = Path(source_path)
    date_part = air_date.isoformat() if air_date else "0000-00-00"
    return f"{sanitize_filename_component(competition_name)} - {date_part} - {sanitize_filename_component(title)}{source.suffix}"


def place_file(source_path: str | Path, destination_path: str | Path) -> str:
    source = Path(source_path)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        return str(destination)

    if source.stat().st_dev == destination.parent.stat().st_dev:
        try:
            os.link(source, destination)
            source.unlink()
            return str(destination)
        except OSError:
            # Network filesystems can report the same device while still rejecting hard links.
            pass

    fd, temp_name = tempfile.mkstemp(prefix=".sportscanner-copy-", dir=destination.parent)
    os.close(fd)
    shutil.copy2(source, temp_name)
    os.replace(temp_name, destination)
    source.unlink()
    return str(destination)


def move_managed_file(source_path: str | Path, destination_path: str | Path) -> str:
    source = Path(source_path)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source == destination:
        return str(destination)

    try:
        os.replace(source, destination)
        return str(destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    fd, temp_name = tempfile.mkstemp(prefix=".sportscanner-move-", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temp_name)
        os.replace(temp_name, destination)
        source.unlink()
    except Exception:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()
        raise
    return str(destination)
