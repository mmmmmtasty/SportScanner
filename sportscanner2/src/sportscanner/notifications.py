from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sportscanner.db.models import Notification, NotificationSeverity, NotificationStatus

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NotificationEvent:
    dedupe_key: str
    category: str
    source: str
    severity: str
    title: str
    message: str
    details: dict[str, object] | None = None


def record_notification(session: Session, event: NotificationEvent) -> Notification:
    notification = session.scalar(select(Notification).where(Notification.dedupe_key == event.dedupe_key))
    now = datetime.now(UTC)
    if notification is None:
        notification = Notification(
            dedupe_key=event.dedupe_key,
            category=event.category,
            source=event.source,
            severity=event.severity,
            status=NotificationStatus.OPEN.value,
            title=event.title,
            message=event.message,
            details=event.details,
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(notification)
        session.flush()
        return notification

    notification.category = event.category
    notification.source = event.source
    notification.severity = event.severity
    notification.status = NotificationStatus.OPEN.value
    notification.title = event.title
    notification.message = event.message
    notification.details = event.details
    notification.occurrence_count += 1
    notification.last_seen_at = now
    notification.dismissed_at = None
    session.flush()
    return notification


def list_notifications(
    session: Session,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[Notification]:
    query = select(Notification).order_by(Notification.last_seen_at.desc(), Notification.id.desc())
    if status:
        query = query.where(Notification.status == status)
    return list(session.scalars(query.limit(limit)))


def dismiss_notification(session: Session, notification_id: int) -> bool:
    notification = session.get(Notification, notification_id)
    if notification is None:
        return False
    notification.status = NotificationStatus.DISMISSED.value
    notification.dismissed_at = datetime.now(UTC)
    session.flush()
    return True


def dismiss_all_notifications(session: Session) -> int:
    notifications = list(
        session.scalars(
            select(Notification).where(Notification.status == NotificationStatus.OPEN.value)
        )
    )
    now = datetime.now(UTC)
    for notification in notifications:
        notification.status = NotificationStatus.DISMISSED.value
        notification.dismissed_at = now
    session.flush()
    return len(notifications)


def create_notification_from_log_record(
    session_factory: sessionmaker[Session],
    record: logging.LogRecord,
) -> None:
    event = classify_notification_event(record)
    if event is None:
        return
    try:
        with session_factory() as session:
            record_notification(session, event)
            session.commit()
    except Exception as exc:
        logger.debug("Failed to create notification from log record: %s", exc)
        return


def classify_notification_event(record: logging.LogRecord) -> NotificationEvent | None:
    if record.levelno < logging.WARNING:
        return None

    payload = getattr(record, "structured_data", None)
    details = payload if isinstance(payload, dict) else None
    logger_name = record.name
    message = record.getMessage().strip()
    severity = NotificationSeverity.ERROR.value if record.levelno >= logging.ERROR else NotificationSeverity.WARNING.value

    if logger_name.startswith("sportscanner.upstream.thesportsdb"):
        endpoint = _payload_value(details, "endpoint") or "request"
        status_code = _payload_value(details, "status_code")
        detail_message = f"TheSportsDB {endpoint} failed"
        if status_code:
            detail_message = f"{detail_message} with status {status_code}"
        return NotificationEvent(
            dedupe_key=f"thesportsdb:{endpoint}:{status_code or logger_name}",
            category="upstream",
            source="thesportsdb",
            severity=severity,
            title="TheSportsDB request failed",
            message=detail_message,
            details=details,
        )

    if logger_name.startswith("sportscanner.plex_refresh"):
        section_id = _extract_token(message, "section_id")
        job_id = _extract_token(message, "job_id")
        return NotificationEvent(
            dedupe_key=f"plex-refresh:{section_id or 'worker'}:{job_id or 'latest'}",
            category="plex",
            source="plex_refresh",
            severity=severity,
            title="Plex refresh failed",
            message=message,
            details=details,
        )

    if logger_name.startswith("sportscanner.plex"):
        operation = message.split(" ", 1)[0]
        return NotificationEvent(
            dedupe_key=f"plex:{operation}",
            category="plex",
            source="plex",
            severity=severity,
            title="Plex connectivity issue",
            message=message,
            details=details,
        )

    if logger_name.startswith("sportscanner.admin.router") and (
        "metadata_refresh_job_failed" in message or "background_job_failed" in message
    ):
        prefix = "metadata" if "metadata_refresh_job_failed" in message else "background"
        return NotificationEvent(
            dedupe_key=f"{prefix}:{_extract_token(message, 'job_key') or prefix}",
            category="system",
            source="admin",
            severity=severity,
            title="Background job failed",
            message=message,
            details=details,
        )

    return None


def _payload_value(payload: dict[str, object] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return str(value) if value is not None else None


def _extract_token(message: str, key: str) -> str | None:
    prefix = f"{key}="
    for token in message.split():
        if token.startswith(prefix):
            return token[len(prefix):]
    return None
