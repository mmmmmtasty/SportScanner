from __future__ import annotations

from sqlalchemy import create_engine, inspect

from sportscanner.db.engine import init_db


def test_init_db_migrates_legacy_segment_schema(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE competition (
                id VARCHAR(255) NOT NULL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                alternate_names JSON NOT NULL DEFAULT '[]',
                season_pattern VARCHAR(32) NOT NULL DEFAULT 'single_year',
                event_order VARCHAR(32) NOT NULL DEFAULT 'official',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE competition_season (
                id VARCHAR(255) NOT NULL PRIMARY KEY,
                competition_id VARCHAR(255) NOT NULL REFERENCES competition(id),
                season_number INTEGER NOT NULL,
                label VARCHAR(100) NOT NULL,
                is_complete BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE event (
                id VARCHAR(255) NOT NULL PRIMARY KEY,
                competition_season_id VARCHAR(255) NOT NULL REFERENCES competition_season(id),
                origin VARCHAR(32) NOT NULL DEFAULT 'upstream',
                name VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE segment (
                id VARCHAR(255) NOT NULL PRIMARY KEY,
                event_id VARCHAR(255) REFERENCES event(id),
                competition_season_id VARCHAR(255) NOT NULL REFERENCES competition_season(id),
                kind VARCHAR(64) NOT NULL,
                title VARCHAR(255) NOT NULL,
                episode_number INTEGER,
                segment_code INTEGER,
                air_date DATE,
                air_time TIME,
                duration_ms INTEGER,
                summary TEXT,
                thumb_url VARCHAR(1024),
                source_path VARCHAR(2048) NOT NULL UNIQUE,
                managed_path VARCHAR(2048),
                match_confidence FLOAT,
                match_method VARCHAR(64),
                metadata_source VARCHAR(64),
                metadata_record JSON,
                metadata_images JSON,
                metadata_refreshed_at DATETIME,
                status VARCHAR(32) NOT NULL DEFAULT 'staged',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE override (
                id INTEGER NOT NULL PRIMARY KEY,
                segment_id VARCHAR(255) NOT NULL REFERENCES segment(id),
                field VARCHAR(128) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE review_task (
                id INTEGER NOT NULL PRIMARY KEY,
                segment_id VARCHAR(255) NOT NULL REFERENCES segment(id),
                task_type VARCHAR(64) NOT NULL,
                candidates JSON NOT NULL DEFAULT '[]',
                status VARCHAR(32) NOT NULL DEFAULT 'open',
                resolution JSON NOT NULL DEFAULT '{}',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO competition (id, name)
            VALUES ('comp_1', 'Formula 1')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO competition_season (id, competition_id, season_number, label)
            VALUES ('season_1', 'comp_1', 2025, '2025')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO event (id, competition_season_id, name)
            VALUES ('event_1', 'season_1', 'Australian Grand Prix')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO segment (
                id,
                event_id,
                competition_season_id,
                kind,
                title,
                episode_number,
                segment_code,
                source_path,
                managed_path,
                match_confidence,
                match_method,
                status
            )
            VALUES (
                'seg_1',
                'event_1',
                'season_1',
                'race',
                'Australian Grand Prix',
                101,
                1,
                '/incoming/australian-grand-prix.mkv',
                '/library/Formula 1/Australian Grand Prix.mkv',
                0.91,
                'eventsday_title',
                'published'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO override (id, segment_id, field)
            VALUES (1, 'seg_1', 'title')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO review_task (id, segment_id, task_type)
            VALUES (1, 'seg_1', 'match_review')
            """
        )

    init_db(engine)

    with engine.connect() as connection:
        inspector = inspect(connection)
        assert "segment" not in inspector.get_table_names()
        assert "recording" in inspector.get_table_names()
        assert "competition_alias" in inspector.get_table_names()
        assert "plex_refresh_job" in inspector.get_table_names()
        assert "metadata_refresh_job" in inspector.get_table_names()
        assert "notification" in inspector.get_table_names()

        recording_columns = {column["name"]: column for column in inspector.get_columns("recording")}
        refresh_job_columns = {column["name"] for column in inspector.get_columns("plex_refresh_job")}
        metadata_refresh_job_columns = {column["name"] for column in inspector.get_columns("metadata_refresh_job")}
        assert "recording_code" in recording_columns
        assert "segment_code" not in recording_columns
        assert "source" in refresh_job_columns
        assert {"target_type", "target_id", "target_label", "source", "status"} <= metadata_refresh_job_columns
        for nullable_column in (
            "match_explanation",
            "file_fingerprint",
            "plex_refresh_status",
            "plex_refreshed_at",
        ):
            assert nullable_column in recording_columns
            assert recording_columns[nullable_column]["nullable"] is True

        competition_columns = {column["name"] for column in inspector.get_columns("competition")}
        event_columns = {column["name"] for column in inspector.get_columns("event")}
        assert "upstream_metadata" in competition_columns
        assert "upstream_metadata" in event_columns
        assert {"poster_url", "banner_url", "fanart_url"} <= event_columns

        override_columns = {column["name"] for column in inspector.get_columns("override")}
        review_task_columns = {column["name"] for column in inspector.get_columns("review_task")}
        assert "recording_id" in override_columns
        assert "segment_id" not in override_columns
        assert "recording_id" in review_task_columns
        assert "segment_id" not in review_task_columns

        recording_row = connection.exec_driver_sql(
            """
            SELECT id, title, recording_code, source_path, managed_path, match_method
            FROM recording
            WHERE id = 'seg_1'
            """
        ).one()
        assert recording_row == (
            "seg_1",
            "Australian Grand Prix",
            1,
            "/incoming/australian-grand-prix.mkv",
            "/library/Formula 1/Australian Grand Prix.mkv",
            "eventsday_title",
        )

        index_names = {index["name"] for index in inspector.get_indexes("recording")}
        assert "ix_recording_file_fingerprint" in index_names

    engine.dispose()
