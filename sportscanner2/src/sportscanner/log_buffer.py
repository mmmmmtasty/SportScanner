from __future__ import annotations

import collections
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from sportscanner.db.models import LogRecord

_STRUCTURED_PAYLOAD_MARKER = "\n[SPORTSCANNER_STRUCTURED_PAYLOAD]\n"


@dataclass(slots=True)
class LogEntry:
    id: int
    timestamp: str
    level: str
    logger_name: str
    message: str
    payload_json: str | None = None


class LogBuffer(logging.Handler):
    """Thread-safe recent log buffer with optional durable SQLite storage."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | None = None,
        maxlen: int = 1000,
        persistent_limit: int = 5000,
    ) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._entries: collections.deque[LogEntry] = collections.deque(maxlen=maxlen)
        self._counter = 0
        self._session_factory = session_factory
        self._persistent_limit = max(persistent_limit, maxlen)
        self._emit_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        payload_json = _serialize_structured_payload(getattr(record, "structured_data", None))
        created_at = datetime.fromtimestamp(record.created, tz=timezone.utc)
        ts = created_at.strftime("%Y-%m-%dT%H:%M:%S")
        entry_id = self._append_persistent_entry(
            created_at=created_at,
            level=record.levelname,
            logger_name=record.name,
            message=_pack_message(msg, payload_json),
        )
        with self._lock:
            if entry_id is None:
                self._counter += 1
                entry_id = self._counter
            self._entries.append(
                LogEntry(
                    id=entry_id,
                    timestamp=ts,
                    level=record.levelname,
                    logger_name=record.name,
                    message=msg,
                    payload_json=payload_json,
                )
            )

    def _append_persistent_entry(
        self,
        *,
        created_at: datetime,
        level: str,
        logger_name: str,
        message: str,
    ) -> int | None:
        if self._session_factory is None:
            return None
        try:
            with self._session_factory() as session:
                row = LogRecord(
                    created_at=created_at,
                    level=level,
                    logger_name=logger_name,
                    message=message,
                )
                session.add(row)
                session.commit()
                entry_id = row.id
            self._emit_count += 1
            if self._emit_count % 100 == 0:
                self._prune_persistent_entries()
            return entry_id
        except Exception:
            return None

    def _prune_persistent_entries(self) -> None:
        if self._session_factory is None:
            return
        try:
            with self._session_factory() as session:
                cutoff_id = session.scalar(
                    select(LogRecord.id)
                    .order_by(LogRecord.id.desc())
                    .offset(self._persistent_limit)
                    .limit(1)
                )
                if cutoff_id is None:
                    return
                session.execute(delete(LogRecord).where(LogRecord.id <= cutoff_id))
                session.commit()
        except Exception:
            return

    def entries(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        keyword: str | None = None,
        since_id: int = 0,
        limit: int = 200,
    ) -> list[LogEntry]:
        if self._session_factory is not None:
            persisted_entries = self._persistent_entries(
                level=level,
                component=component,
                keyword=keyword,
                since_id=since_id,
                limit=limit,
            )
            if persisted_entries:
                return persisted_entries
        normalized_level = level.upper() if level else None
        with self._lock:
            snapshot = list(self._entries)
        out = []
        for entry in snapshot:
            if entry.id <= since_id:
                continue
            if normalized_level and entry.level.upper() != normalized_level:
                continue
            if component and not entry.logger_name.startswith(component):
                continue
            search_text = entry.message
            if entry.payload_json:
                search_text = f"{search_text}\n{entry.payload_json}"
            if keyword and keyword.lower() not in search_text.lower():
                continue
            out.append(entry)
        return list(reversed(out[-limit:]))

    def _persistent_entries(
        self,
        *,
        level: str | None,
        component: str | None,
        keyword: str | None,
        since_id: int,
        limit: int,
    ) -> list[LogEntry]:
        assert self._session_factory is not None
        normalized_level = level.upper() if level else None
        with self._session_factory() as session:
            rows = session.scalars(
                select(LogRecord).where(LogRecord.id > since_id).order_by(LogRecord.id.desc())
            ).all()
        out = []
        for row in rows:
            if normalized_level and row.level.upper() != normalized_level:
                continue
            if component and not row.logger_name.startswith(component):
                continue
            message, payload_json = _unpack_message(row.message)
            search_text = message
            if payload_json:
                search_text = f"{search_text}\n{payload_json}"
            if keyword and keyword.lower() not in search_text.lower():
                continue
            out.append(
                LogEntry(
                    id=row.id,
                    timestamp=(
                        row.created_at.astimezone(timezone.utc)
                        if row.created_at.tzinfo is not None
                        else row.created_at.replace(tzinfo=timezone.utc)
                    ).strftime("%Y-%m-%dT%H:%M:%S"),
                    level=row.level,
                    logger_name=row.logger_name,
                    message=message,
                    payload_json=payload_json,
                )
            )
            if len(out) >= limit:
                break
        return out


def _serialize_structured_payload(payload: object | None) -> str | None:
    if payload is None:
        return None
    try:
        return json.dumps(payload, indent=2, sort_keys=True, default=str)
    except TypeError:
        return json.dumps({"value": str(payload)}, indent=2, sort_keys=True)


def _pack_message(message: str, payload_json: str | None) -> str:
    if not payload_json:
        return message
    return f"{message}{_STRUCTURED_PAYLOAD_MARKER}{payload_json}"


def _unpack_message(message: str) -> tuple[str, str | None]:
    if _STRUCTURED_PAYLOAD_MARKER not in message:
        return message, None
    plain_message, payload_json = message.split(_STRUCTURED_PAYLOAD_MARKER, 1)
    payload_json = payload_json.strip() or None
    return plain_message, payload_json
