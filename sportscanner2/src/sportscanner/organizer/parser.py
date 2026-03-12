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
        r"^(?P<show>.+?)\s+(?P<day>\d{2})-(?P<month>\d{2})-(?P<year>\d{4})\s+(?P<title>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<show>.+?)\s+(?:(?P<prefix>rs|ps|match\s+day\s*\d+|md\d+|round\s+\d+)\s+)?"
        r"(?P<year>\d{4})\s+(?P<title>.+?)\s+(?P<day>\d{2})\s+(?P<month>\d{2})$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<show>.*?)[^0-9A-Za-z]+(?P<year>\d{4})[^0-9A-Za-z]+(?P<month>\d{2})[^0-9A-Za-z]+(?P<day>\d{2})[^0-9A-Za-z]+(?P<title>.+)$",
        re.IGNORECASE,
    ),
]

RECORDING_KIND_MAP = {
    # Multi-word sprint sessions must come before bare "race" and "sprint".
    "sprint race": "sprint",
    "sprint qualifying": "sprint_qualifying",
    "shootout qualifying": "sprint_qualifying",
    # Explicit free-practice forms should win before bare "practice".
    "free practice 1": "practice",
    "free practice 2": "practice",
    "free practice 3": "practice",
    "fp1": "practice",
    "fp2": "practice",
    "fp3": "practice",
    "practice 1": "practice",
    "practice 2": "practice",
    "practice 3": "practice",
    "practice": "practice",
    "qualifying": "qualifying",
    "qual": "qualifying",
    "sprint": "sprint",
    "race": "race",
    "main event": "race",
    "match": "match",
    "game": "match",
    "pregame": "pregame",
    "pre game": "pregame",
    "pre-race show": "pregame",
    "postgame": "postgame",
    "post game": "postgame",
    "post-race analysis": "analysis",
    "analysis": "analysis",
    "highlights": "highlights",
    "condensed game": "condensed",
    "condensed": "condensed",
    "alternate feed": "alt_feed",
    "alt feed": "alt_feed",
    "alt": "alt_feed",
}

NOISE_SUFFIXES = [
    r"\b(?:\d{3,4}(?:p|i)(?:en)?(?:\d{2}(?:fps?)?)?|x264|x265|h\.?264|h\.?265|hevc|hdr|"
    r"webrip|web-dl|web|bluray|aac|hdtv|uhdtv|avc1|ddp?5\.1|4k)\b.*$",
]
TITLE_PREFIX_RE = re.compile(
    r"^(?:rs|ps|regular season|preseason|match\s+day\s*\d+|md\d+|round\s+\d+)\b[\s:-]*",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ParsedFile:
    source_path: Path
    show: str
    event_date: date | None
    title: str
    kind: str
    kind_label: str | None
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


def clean_title(value: str) -> str:
    return normalize_whitespace(TITLE_PREFIX_RE.sub("", value))


def infer_recording_kind(label: str | None, title: str) -> str:
    candidates = [label or "", title]
    for candidate in candidates:
        lowered = normalize_whitespace(candidate).lower()
        for key, value in RECORDING_KIND_MAP.items():
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
        title = clean_title(groups.get("title", ""))
        label = normalize_whitespace(groups["label"]) if groups.get("label") else None
        kind = infer_recording_kind(label, title)
        return ParsedFile(
            source_path=source_path,
            show=normalize_whitespace(groups["show"]),
            event_date=parsed_date,
            title=title,
            kind=kind,
            kind_label=label,
            season_hint=int(groups["season"]) if groups.get("season") else None,
        )
    raise ValueError(f"Could not parse filename: {source_path.name}")
