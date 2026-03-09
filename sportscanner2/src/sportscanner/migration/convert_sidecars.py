from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class MigrationResult:
    source: Path
    target: Path
    changed: bool
    content: str


def render_yaml(mapping: dict[str, object]) -> str:
    lines: list[str] = []
    for key, value in mapping.items():
        if value is None:
            continue
        if isinstance(value, str):
            escaped = value.replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def convert_legacy_sidecar(path: Path, *, dry_run: bool = False) -> MigrationResult | None:
    if path.name.endswith(".SportScanner"):
        lines = path.read_text(encoding="utf-8").splitlines()
        subepisode = None
        if lines:
            try:
                subepisode = int(lines[0].strip())
            except ValueError:
                subepisode = None
        title_suffix = lines[1].strip() if len(lines) > 1 else None
        target = path.with_suffix(".sportscanner.yml")
        content = render_yaml(
            {
                "title_suffix": title_suffix,
                "legacy_subepisode": subepisode,
                "migrated_from": "SportScanner_v1",
            }
        )
        if not dry_run:
            target.write_text(content, encoding="utf-8")
        return MigrationResult(source=path, target=target, changed=True, content=content)

    if path.name == "SportScanner.txt":
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        season_pattern = "cross_year" if lines and lines[0].upper() == "XXYY" else "single_year"
        split_month = None
        split_day = None
        if season_pattern == "cross_year" and len(lines) > 1 and "," in lines[1]:
            month_text, day_text = lines[1].split(",", 1)
            split_month = int(month_text)
            split_day = int(day_text)
        target = path.with_name("competition.sportscanner.yml")
        content = render_yaml(
            {
                "season_pattern": season_pattern,
                "season_split_month": split_month,
                "season_split_day": split_day,
                "migrated_from": "SportScanner_v1",
            }
        )
        if not dry_run:
            target.write_text(content, encoding="utf-8")
        return MigrationResult(source=path, target=target, changed=True, content=content)

    return None


def convert_library_root(library_root: Path, *, dry_run: bool = False) -> list[MigrationResult]:
    results: list[MigrationResult] = []
    for path in sorted(library_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "SportScanner.txt" or path.name.endswith(".SportScanner"):
            result = convert_legacy_sidecar(path, dry_run=dry_run)
            if result is not None:
                results.append(result)
    return results

