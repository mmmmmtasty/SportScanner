"""Add persistent log records."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_log_records"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "log_record",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("logger_name", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_index("ix_log_record_created_at", "log_record", ["created_at"])
    op.create_index("ix_log_record_level", "log_record", ["level"])
    op.create_index("ix_log_record_logger_name", "log_record", ["logger_name"])


def downgrade() -> None:
    op.drop_index("ix_log_record_logger_name", table_name="log_record")
    op.drop_index("ix_log_record_level", table_name="log_record")
    op.drop_index("ix_log_record_created_at", table_name="log_record")
    op.drop_table("log_record")
