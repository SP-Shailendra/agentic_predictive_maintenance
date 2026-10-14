"""Add per-asset advisory thresholds and threshold change history.

Revision ID: 20261007_0005
Revises: 20261007_0004
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261007_0005"
down_revision = "20261007_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("vibration_threshold_mm_s", sa.Float(), nullable=True))
    op.add_column("assets", sa.Column("temperature_threshold_c", sa.Float(), nullable=True))
    op.create_table(
        "asset_threshold_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("changed_by_sub", sa.String(length=255), nullable=True),
        sa.Column("vibration_old_mm_s", sa.Float(), nullable=True),
        sa.Column("vibration_new_mm_s", sa.Float(), nullable=True),
        sa.Column("temperature_old_c", sa.Float(), nullable=True),
        sa.Column("temperature_new_c", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_threshold_changes_asset_id", "asset_threshold_changes", ["asset_id"], unique=False)
    op.create_index("ix_asset_threshold_changes_created_at", "asset_threshold_changes", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_asset_threshold_changes_created_at", table_name="asset_threshold_changes")
    op.drop_index("ix_asset_threshold_changes_asset_id", table_name="asset_threshold_changes")
    op.drop_table("asset_threshold_changes")
    op.drop_column("assets", "temperature_threshold_c")
    op.drop_column("assets", "vibration_threshold_mm_s")