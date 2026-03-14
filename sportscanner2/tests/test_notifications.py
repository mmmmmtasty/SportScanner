from __future__ import annotations

import logging

from sportscanner.notifications import create_notification_from_log_record


class _BrokenSession:
    def __enter__(self):
        raise RuntimeError("db unavailable")

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _BrokenSessionFactory:
    def __call__(self) -> _BrokenSession:
        return _BrokenSession()


def test_create_notification_logs_debug_when_persistence_fails(caplog) -> None:
    record = logging.LogRecord(
        name="sportscanner.plex",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="plex refresh failed",
        args=(),
        exc_info=None,
    )

    with caplog.at_level(logging.DEBUG, logger="sportscanner.notifications"):
        create_notification_from_log_record(_BrokenSessionFactory(), record)

    assert "Failed to create notification from log record" in caplog.text
