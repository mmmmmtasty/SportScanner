"""One-shot migration for legacy segment-based SQLite databases.

This script now delegates to the same startup migration path used by the app,
so it stays aligned with the canonical schema in ``sportscanner.db.engine``.

Run from the sportscanner2/ directory:
    PYTHONPATH=src python scripts/migrate_segment_to_recording.py [path/to/sportscanner.db]

The default DB path is ``data/sportscanner.db``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine

from sportscanner.db.engine import init_db


def migrate(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    try:
        init_db(engine)
    finally:
        engine.dispose()
    print("Migration complete. Legacy segment schema has been upgraded to the recording schema.")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/sportscanner.db")
    if not path.exists():
        print(f"ERROR: database not found at {path}")
        sys.exit(1)
    migrate(path)
