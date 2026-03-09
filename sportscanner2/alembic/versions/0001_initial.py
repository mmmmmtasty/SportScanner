"""Initial SportScanner 2 schema."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competition",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("tsdb_id", sa.Integer(), nullable=True, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("alternate_names", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sport", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("formed_year", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("season_pattern", sa.String(length=32), nullable=False, server_default="single_year"),
        sa.Column("season_split_month", sa.Integer(), nullable=True),
        sa.Column("season_split_day", sa.Integer(), nullable=True),
        sa.Column("event_order", sa.String(length=32), nullable=False, server_default="official"),
        sa.Column("poster_url", sa.String(length=1024), nullable=True),
        sa.Column("banner_url", sa.String(length=1024), nullable=True),
        sa.Column("fanart_url", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "competition_season",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("competition_id", sa.String(length=255), sa.ForeignKey("competition.id"), nullable=False),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("competition_id", "season_number", name="uq_competition_season"),
    )
    op.create_index("ix_competition_season_competition_id", "competition_season", ["competition_id"])

    op.create_table(
        "event",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("tsdb_id", sa.Integer(), nullable=True, unique=True),
        sa.Column("competition_season_id", sa.String(length=255), sa.ForeignKey("competition_season.id"), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False, server_default="upstream"),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("time", sa.Time(), nullable=True),
        sa.Column("round", sa.Integer(), nullable=True),
        sa.Column("venue", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=255), nullable=True),
        sa.Column("home_team", sa.String(length=255), nullable=True),
        sa.Column("away_team", sa.String(length=255), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("thumb_url", sa.String(length=1024), nullable=True),
        sa.Column("event_sequence", sa.Integer(), nullable=True),
        sa.Column("weekend_group", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_event_competition_season_id", "event", ["competition_season_id"])
    op.create_index("ix_event_date", "event", ["date"])
    op.create_index("ix_event_competition_season_sequence", "event", ["competition_season_id", "event_sequence"])

    op.create_table(
        "segment",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("event_id", sa.String(length=255), sa.ForeignKey("event.id"), nullable=True),
        sa.Column("competition_season_id", sa.String(length=255), sa.ForeignKey("competition_season.id"), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("segment_code", sa.Integer(), nullable=True),
        sa.Column("air_date", sa.Date(), nullable=True),
        sa.Column("air_time", sa.Time(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("thumb_url", sa.String(length=1024), nullable=True),
        sa.Column("source_path", sa.String(length=2048), nullable=False, unique=True),
        sa.Column("managed_path", sa.String(length=2048), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("match_method", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="staged"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_segment_competition_season_id", "segment", ["competition_season_id"])

    op.create_table(
        "asset",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("asset_type", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("cached_path", sa.String(length=2048), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "override",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("segment_id", sa.String(length=255), sa.ForeignKey("segment.id"), nullable=False),
        sa.Column("field", sa.String(length=128), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "review_task",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("segment_id", sa.String(length=255), sa.ForeignKey("segment.id"), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("resolution", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_review_task_status", "review_task", ["status"])

    op.create_table(
        "api_cache",
        sa.Column("cache_key", sa.String(length=255), primary_key=True),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_setting")
    op.drop_table("api_cache")
    op.drop_index("ix_review_task_status", table_name="review_task")
    op.drop_table("review_task")
    op.drop_table("override")
    op.drop_table("asset")
    op.drop_index("ix_segment_competition_season_id", table_name="segment")
    op.drop_table("segment")
    op.drop_index("ix_event_competition_season_sequence", table_name="event")
    op.drop_index("ix_event_date", table_name="event")
    op.drop_index("ix_event_competition_season_id", table_name="event")
    op.drop_table("event")
    op.drop_index("ix_competition_season_competition_id", table_name="competition_season")
    op.drop_table("competition_season")
    op.drop_table("competition")
