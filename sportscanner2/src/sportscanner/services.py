from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from sportscanner.config import Settings
from sportscanner.organizer.service import OrganizerService
from sportscanner.organizer.watcher import OrganizerWatcher
from sportscanner.plex import PlexPmsClient
from sportscanner.upstream.base import MetadataSource


@dataclass(slots=True)
class SportScannerServices:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    metadata_source: MetadataSource | None
    organizer: OrganizerService
    plex: PlexPmsClient
    watcher: OrganizerWatcher | None = None

