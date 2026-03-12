from __future__ import annotations

import logging
import threading
from pathlib import Path

from sportscanner.organizer.parser import is_media_file

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - optional when watchdog is unavailable
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]

logger = logging.getLogger("sportscanner.organizer.watcher")


class _Handler(FileSystemEventHandler):  # pragma: no cover - thin watchdog wrapper
    def __init__(self, organizer, debounce_seconds: float) -> None:
        self.organizer = organizer
        self.debounce_seconds = debounce_seconds
        self._timers: dict[str, threading.Timer] = {}

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not is_media_file(path):
            return
        logger.info("watcher_detected_file path=%s", path)
        existing = self._timers.get(event.src_path)
        if existing is not None:
            existing.cancel()
        timer = threading.Timer(self.debounce_seconds, self.organizer.ingest_path_if_parseable, args=(path,))
        timer.daemon = True
        timer.start()
        self._timers[event.src_path] = timer


class OrganizerWatcher:
    def __init__(self, organizer, incoming_dir: Path, debounce_seconds: float = 5.0) -> None:
        self.organizer = organizer
        self.incoming_dir = incoming_dir
        self.debounce_seconds = debounce_seconds
        self._observer = None

    def start(self) -> None:
        if Observer is None or not self.incoming_dir.exists():
            logger.info("watcher_disabled incoming_dir=%s observer_available=%s", self.incoming_dir, Observer is not None)
            return
        handler = _Handler(self.organizer, self.debounce_seconds)
        observer = Observer()
        observer.schedule(handler, str(self.incoming_dir), recursive=True)
        observer.start()
        self._observer = observer
        logger.info("watcher_started incoming_dir=%s debounce_seconds=%s", self.incoming_dir, self.debounce_seconds)

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=5)
        self._observer = None
        logger.info("watcher_stopped incoming_dir=%s", self.incoming_dir)
