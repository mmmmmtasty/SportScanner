from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


MEDIA_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".ts",
    ".m2ts",
    ".wmv",
}

PATTERNS = [
    re.compile(
        r"^(?P<show>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<title>.+?)(?:\s+-\s+(?P<label>[^-]+))?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<show>.+?)-(?P<season>\d{4})-(?P<date>\d{8})-(?P<title>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<show>.+?)\s+(?P<date>\d{4}\.\d{2}\.\d{2})\s+(?P<title>.+?)(?:\s+-\s+(?P<label>[^-]+))?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<show>.*?)[^0-9A-Za-z]+(?P<year>\d{4})[^0-9A-Za-z]+(?P<month>\d{2})[^0-9A-Za-z]+(?P<day>\d{2})[^0-9A-Za-z]+(?P<title>.+)$",
        re.IGNORECASE,
    ),
]

SEGMENT_KIND_MAP = {
    "match": "match",
    "game": "match",
    "race": "race",
    "main event": "race",
    "pregame": "pregame",
    "pre game": "pregame",
    "pre-race show": "pregame",
    "postgame": "postgame",
    "post game": "postgame",
    "post-race analysis": "analysis",
    "analysis": "analysis",
    "highlights": "highlights",
    "condensed": "condensed",
    "condensed game": "condensed",
    "alternate feed": "alt_feed",
    "alt feed": "alt_feed",
    "alt": "alt_feed",
    "qualifying": "qualifying",
    "qual": "qualifying",
    "sprint qualifying": "sprint_qualifying",
    "shootout qualifying": "sprint_qualifying",
    "sprint": "sprint",
    "fp1": "practice",
    "fp2": "practice",
    "fp3": "practice",
    "practice": "practice",
    "practice 1": "practice",
    "practice 2": "practice",
    "practice 3": "practice",
}

NOISE_SUFFIXES = [
    r"\b(?:720p|1080p|2160p|x264|x265|h\.?264|h\.?265|hevc|hdr|webrip|web-dl|bluray|aac)\b.*$",
]


@dataclass(slots=True)
class ParsedFile:
    source_path: Path
    show: str
    event_date: date | None
    title: str
    segment_kind: str
    segment_label: str | None
    season_hint: int | None = None

    def search_query(self) -> str:
        return f"{self.show} {self.event_date.isoformat() if self.event_date else ''} {self.title}".strip()


def is_media_file(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTENSIONS


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_stem(stem: str) -> str:
    result = stem.replace("_", " ").replace(".", " ")
    for pattern in NOISE_SUFFIXES:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return normalize_whitespace(result)


def infer_segment_kind(label: str | None, title: str) -> str:
    candidates = [label or "", title]
    for candidate in candidates:
        lowered = normalize_whitespace(candidate).lower()
        for key, value in SEGMENT_KIND_MAP.items():
            if key in lowered:
                return value
    if re.search(r"\b(?:vs|v)\b", title, flags=re.IGNORECASE):
        return "match"
    return "other"


def _parse_date(raw: str) -> date:
    if "." in raw:
        raw = raw.replace(".", "-")
    if len(raw) == 8:
        raw = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return date.fromisoformat(raw)


def parse_filename(path: str | Path) -> ParsedFile:
    source_path = Path(path)
    if not is_media_file(source_path):
        raise ValueError(f"Unsupported media file: {source_path}")

    cleaned = clean_stem(source_path.stem)
    for pattern in PATTERNS:
        match = pattern.match(cleaned)
        if not match:
            continue
        groups = match.groupdict()
        if groups.get("date"):
            parsed_date = _parse_date(groups["date"])
        else:
            parsed_date = date(int(groups["year"]), int(groups["month"]), int(groups["day"]))
        title = normalize_whitespace(groups.get("title", ""))
        label = normalize_whitespace(groups["label"]) if groups.get("label") else None
        kind = infer_segment_kind(label, title)
        return ParsedFile(
            source_path=source_path,
            show=normalize_whitespace(groups["show"]),
            event_date=parsed_date,
            title=title,
            segment_kind=kind,
            segment_label=label,
            season_hint=int(groups["season"]) if groups.get("season") else None,
        )
    raise ValueError(f"Could not parse filename: {source_path.name}")
