from __future__ import annotations

from sqlalchemy import create_engine, inspect

from sportscanner.db.engine import init_db


def test_init_db_creates_current_schema(tmp_path) -> None:
    db_path = tmp_path / "sportscanner.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    init_db(engine)

    with engine.connect() as connection:
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        assert {
            "asset",
            "competition",
            "competition_alias",
            "competition_season",
            "event",
            "metadata_refresh_job",
            "notification",
            "plex_refresh_job",
            "recording",
            "review_task",
        } <= table_names

        recording_columns = {column["name"] for column in inspector.get_columns("recording")}
        assert {
            "recording_code",
            "file_fingerprint",
            "match_explanation",
            "metadata_images",
            "metadata_record",
            "metadata_refreshed_at",
            "metadata_source",
            "plex_refreshed_at",
            "plex_refresh_status",
        } <= recording_columns
        assert "segment_code" not in recording_columns

    engine.dispose()
