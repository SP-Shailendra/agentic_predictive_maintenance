"""Add maintenance close-out feedback and lifecycle timestamps.

Revision ID: 20261007_0004
Revises: 20261007_0003
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20261007_0004"
down_revision = "20261007_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_orders", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("work_orders", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("work_orders", sa.Column("in_progress_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("work_orders", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("work_orders", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("work_orders", sa.Column("confirmed_root_cause", sa.String(length=80), nullable=True))
    op.add_column("work_orders", sa.Column("alert_was_useful", sa.Boolean(), nullable=True))
    op.add_column("work_orders", sa.Column("closed_by_sub", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("work_orders", "closed_by_sub")
    op.drop_column("work_orders", "alert_was_useful")
    op.drop_column("work_orders", "confirmed_root_cause")
    op.drop_column("work_orders", "closed_at")
    op.drop_column("work_orders", "completed_at")
    op.drop_column("work_orders", "in_progress_at")
    op.drop_column("work_orders", "dispatched_at")
    op.drop_column("work_orders", "approved_at")