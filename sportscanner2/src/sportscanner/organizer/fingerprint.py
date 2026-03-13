from __future__ import annotations

import hashlib
import os
from pathlib import Path


def compute_fingerprint(path: str | Path) -> str | None:
    """Return a stable fingerprint for a media file.

    Uses SHA-256 of the first 64 KB so it is fast even for large files and
    stable across renames.  Falls back to an inode-based fingerprint when the
    file cannot be read (e.g. permissions, network share unavailable).
    Returns None only when the file cannot be accessed at all.
    """
    p = Path(path)
    try:
        with open(p, "rb") as fh:
            chunk = fh.read(65536)
        h = hashlib.sha256(chunk).hexdigest()
        return f"sha256:{h}"
    except (OSError, IOError):
        pass
    try:
        st = p.stat()
        return f"inode:{st.st_ino}:{st.st_dev}"
    except OSError:
        return None
