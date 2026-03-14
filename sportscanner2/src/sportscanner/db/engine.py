from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from sportscanner.config import Settings
from sportscanner.db.models import Base


def create_sqlite_engine(settings: Settings) -> Engine:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def _column_names(connection: Connection, table_name: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table_name)}


def _index_names(connection: Connection, table_name: str) -> set[str]:
    return {index["name"] for index in inspect(connection).get_indexes(table_name)}


def _row_count(connection: Connection, table_name: str) -> int:
    return int(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table_name}").scalar_one())


def _migrate_legacy_schema(connection: Connection) -> None:
    table_names = _table_names(connection)

    if "segment" in table_names and "recording" in table_names:
        if _row_count(connection, "recording") == 0:
            connection.exec_driver_sql("DROP TABLE recording")
            table_names.remove("recording")
        else:
            raise RuntimeError(
                "Both legacy 'segment' and new 'recording' tables exist with data; "
                "manual migration is required before SportScanner can continue."
            )

    if "segment" in table_names and "recording" not in table_names:
        connection.exec_driver_sql("ALTER TABLE segment RENAME TO recording")
        table_names.remove("segment")
        table_names.add("recording")

    if "recording" in table_names:
        recording_columns = _column_names(connection, "recording")
        if "segment_code" in recording_columns and "recording_code" not in recording_columns:
            connection.exec_driver_sql("ALTER TABLE recording RENAME COLUMN segment_code TO recording_code")

        recording_columns = _column_names(connection, "recording")
        recording_additions = {
            "metadata_source": "VARCHAR(64)",
            "metadata_record": "JSON",
            "metadata_images": "JSON",
            "metadata_refreshed_at": "DATETIME",
            "match_explanation": "JSON",
            "file_fingerprint": "VARCHAR(255)",
            "plex_refresh_status": "VARCHAR(32)",
            "plex_refreshed_at": "DATETIME",
        }
        for column_name, column_type in recording_additions.items():
            if column_name not in recording_columns:
                connection.exec_driver_sql(f"ALTER TABLE recording ADD COLUMN {column_name} {column_type}")

        if "ix_recording_file_fingerprint" not in _index_names(connection, "recording"):
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_recording_file_fingerprint ON recording (file_fingerprint)"
            )

    for table_name, additions in (
        ("competition", {"upstream_metadata": "JSON"}),
        ("event", {"upstream_metadata": "JSON"}),
    ):
        if table_name not in table_names:
            continue
        columns = _column_names(connection, table_name)
        for column_name, column_type in additions.items():
            if column_name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    for table_name, old_column, new_column in (
        ("override", "segment_id", "recording_id"),
        ("review_task", "segment_id", "recording_id"),
    ):
        if table_name not in table_names:
            continue
        columns = _column_names(connection, table_name)
        if old_column in columns and new_column not in columns:
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} RENAME COLUMN {old_column} TO {new_column}"
            )


def init_db(engine: Engine) -> None:
    with engine.begin() as connection:
        _migrate_legacy_schema(connection)
        Base.metadata.create_all(connection)
