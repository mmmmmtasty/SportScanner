from __future__ import annotations

import re


_URL_TOKEN_RE = re.compile(r"(?:https?://\S+|www\.\S+)", re.IGNORECASE)
_TRAILING_SEPARATOR_RE = re.compile(r"(?:\s+(?:vs|v\.?))+$", re.IGNORECASE)


def sanitize_event_name(value: str | None) -> str:
    if not value:
        return ""
    cleaned = _URL_TOKEN_RE.sub("", value).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = _TRAILING_SEPARATOR_RE.sub("", cleaned).strip(" -|:/")
    return cleaned
