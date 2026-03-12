from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time
from typing import Iterable


MAX_RANGE_SIZE = 10
_LATE_DATE = date.max
_LATE_TIME = time.max
_YEAR_PREFIX_RE = re.compile(r"^\d{4}(?:/\d{2,4})?\s+")
_DASH_SESSION_SUFFIX_RE = re.compile(
    r"\s*[-–]\s*\b(?:free\s+practice\s*\d*|fp\d+|practice\s*\d*|"
    r"sprint\s+qualifying|sprint\s+race|sprint|qualifying|qual|race|"
    r"pregame|postgame|highlights|analysis|condensed)\b.*$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class EventSequenceInput:
    event_id: str
    name: str
    event_date: date | None
    event_time: time | None
    round_number: int | None
    weekend_group: str | None = None


@dataclass(slots=True)
class RecordingCodeInput:
    recording_id: str
    kind: str
    title: str
    source_path: str


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def derive_weekend_group(name: str) -> str:
    text = _YEAR_PREFIX_RE.sub("", name).strip()
    text = _DASH_SESSION_SUFFIX_RE.sub("", text).strip()
    normalized = normalize_title(text)
    normalized = re.sub(
        r"\b(free\s+practice\s*\d*|fp\d+|practice\s*\d+|practice|"
        r"sprint\s+qualifying|sprint\s+race|sprint|qualifying|qual|race|"
        r"pregame|postgame|highlights|analysis|condensed)\b",
        "",
        normalized,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or normalize_title(name)


def compute_event_sequences(events: Iterable[EventSequenceInput], event_order: str) -> dict[str, int]:
    items = list(events)
    if event_order not in {"official", "weekend", "round"}:
        raise ValueError(f"Unsupported event order: {event_order}")

    def official_key(item: EventSequenceInput) -> tuple:
        return (
            item.event_date or _LATE_DATE,
            item.round_number if item.round_number is not None else 10**9,
            item.event_time or _LATE_TIME,
            normalize_title(item.name),
        )

    def round_key(item: EventSequenceInput) -> tuple:
        return (
            item.round_number if item.round_number is not None else 10**9,
            item.event_date or _LATE_DATE,
            item.event_time or _LATE_TIME,
            normalize_title(item.name),
        )

    if event_order == "weekend":
        grouped: dict[str, list[EventSequenceInput]] = {}
        for item in items:
            grouped.setdefault(
                normalize_title(item.weekend_group or derive_weekend_group(item.name)),
                [],
            ).append(item)
        ordered_groups = sorted(
            grouped.items(),
            key=lambda item: min(official_key(event) for event in item[1]),
        )
        sequence_map: dict[str, int] = {}
        for index, (_, group_items) in enumerate(ordered_groups, start=1):
            for item in group_items:
                sequence_map[item.event_id] = index
        return sequence_map

    key_fn = {
        "official": official_key,
        "round": round_key,
    }[event_order]

    ordered = sorted(items, key=key_fn)
    return {item.event_id: index for index, item in enumerate(ordered, start=1)}


def _allocate_range(
    items: list[RecordingCodeInput],
    start: int,
    *,
    offset: int = 0,
) -> dict[str, int]:
    items = sorted(items, key=lambda item: (normalize_title(item.title), item.source_path))
    if offset + len(items) > MAX_RANGE_SIZE:
        raise ValueError("Recording range exhausted")
    return {
        item.recording_id: start + offset + index
        for index, item in enumerate(items)
    }


def _practice_code(recording: RecordingCodeInput) -> int | None:
    title = normalize_title(recording.title)
    if "practice 1" in title or "fp1" in title:
        return 11
    if "practice 2" in title or "fp2" in title:
        return 12
    if "practice 3" in title or "fp3" in title:
        return 13
    return None


def _looks_like_alt_feed(recording: RecordingCodeInput) -> bool:
    title = normalize_title(recording.title)
    return recording.kind == "alt_feed" or any(token in title for token in ["alt feed", "alternate", "home feed", "away feed"])


def compute_recording_codes(recordings: Iterable[RecordingCodeInput]) -> dict[str, int]:
    items = list(recordings)
    codes: dict[str, int] = {}

    pregame = [item for item in items if item.kind == "pregame"]
    codes.update(_allocate_range(pregame, 0))

    practice = [item for item in items if item.kind == "practice"]
    practice_remaining: list[RecordingCodeInput] = []
    for item in practice:
        explicit_code = _practice_code(item)
        if explicit_code is None:
            practice_remaining.append(item)
        else:
            codes[item.recording_id] = explicit_code
    codes.update(_allocate_range(practice_remaining, 10, offset=4))

    codes.update(_allocate_range([item for item in items if item.kind == "sprint_qualifying"], 20))
    codes.update(_allocate_range([item for item in items if item.kind == "sprint"], 30))
    codes.update(_allocate_range([item for item in items if item.kind == "qualifying"], 40))

    main_primary = [item for item in items if item.kind in {"match", "race"} and not _looks_like_alt_feed(item)]
    main_alt = [item for item in items if item.kind in {"match", "race", "alt_feed"} and item not in main_primary]
    main_primary = sorted(main_primary, key=lambda item: (normalize_title(item.title), item.source_path))
    if main_primary:
        codes[main_primary[0].recording_id] = 50
        if len(main_primary) > 1:
            main_alt.extend(main_primary[1:])
    codes.update(_allocate_range(main_alt, 50, offset=1))

    codes.update(_allocate_range([item for item in items if item.kind == "postgame"], 60))
    codes.update(_allocate_range([item for item in items if item.kind == "highlights"], 70))
    codes.update(_allocate_range([item for item in items if item.kind in {"analysis", "condensed"}], 80))

    assigned = set(codes)
    other = [item for item in items if item.recording_id not in assigned]
    codes.update(_allocate_range(other, 90))
    return codes


def episode_number(event_sequence: int, segment_code: int) -> int:
    return event_sequence * 100 + segment_code
