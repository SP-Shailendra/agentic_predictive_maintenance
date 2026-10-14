"""Add the transactional CMMS dispatch outbox.

Revision ID: 20261007_0003
Revises: 20261007_0002
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261007_0003"
down_revision = "20261007_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cmms_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cmms_reference", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_order_id", name="uq_cmms_outbox_work_order"),
        sa.UniqueConstraint("idempotency_key", name="uq_cmms_outbox_idempotency_key"),
    )
    op.create_index("ix_cmms_outbox_ready", "cmms_outbox", ["status", "next_attempt_at"], unique=False)
    op.create_index("ix_cmms_outbox_status", "cmms_outbox", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cmms_outbox_status", table_name="cmms_outbox")
    op.drop_index("ix_cmms_outbox_ready", table_name="cmms_outbox")
    op.drop_table("cmms_outbox")