"""Persist CMMS status reconciliation state."""

from alembic import op
import sqlalchemy as sa

revision = "20261016_0012"
down_revision = "20261016_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_orders", sa.Column("cmms_external_status", sa.String(length=80), nullable=True))
    op.add_column("work_orders", sa.Column("cmms_status_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_work_orders_cmms_status_checked_at", "work_orders", ["cmms_status_checked_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_work_orders_cmms_status_checked_at", table_name="work_orders")
    op.drop_column("work_orders", "cmms_status_checked_at")
    op.drop_column("work_orders", "cmms_external_status")