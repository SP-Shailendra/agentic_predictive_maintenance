"""Add notification outbox and optional technician assignment.

Revision ID: 20261008_0008
Revises: 20261007_0007
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261008_0008"
down_revision = "20261007_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_orders", sa.Column("assigned_to_sub", sa.String(length=255), nullable=True))
    op.create_index("ix_work_orders_assigned_to_sub", "work_orders", ["assigned_to_sub"], unique=False)
    op.create_table(
        "notification_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("dedupe_key", sa.String(length=180), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "dedupe_key", name="uq_notification_outbox_channel_dedupe"),
    )
    op.create_index("ix_notification_outbox_work_order_id", "notification_outbox", ["work_order_id"], unique=False)
    op.create_index("ix_notification_outbox_status", "notification_outbox", ["status"], unique=False)
    op.create_index("ix_notification_outbox_ready", "notification_outbox", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notification_outbox_ready", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_status", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_work_order_id", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_index("ix_work_orders_assigned_to_sub", table_name="work_orders")
    op.drop_column("work_orders", "assigned_to_sub")