"""Add persisted segment metadata snapshots."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_segment_metadata_snapshots"
down_revision = "0002_log_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segment", sa.Column("metadata_source", sa.String(length=64), nullable=True))
    op.add_column("segment", sa.Column("metadata_record", sa.JSON(), nullable=True))
    op.add_column("segment", sa.Column("metadata_images", sa.JSON(), nullable=True))
    op.add_column("segment", sa.Column("metadata_refreshed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("segment", "metadata_refreshed_at")
    op.drop_column("segment", "metadata_images")
    op.drop_column("segment", "metadata_record")
    op.drop_column("segment", "metadata_source")
