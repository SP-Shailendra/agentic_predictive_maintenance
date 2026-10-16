"""Add auditable asset alert snoozes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261016_0013"
down_revision = "20261016_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("alert_snoozed_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assets", sa.Column("alert_snoozed_by", sa.String(length=255), nullable=True))
    op.create_table(
        "asset_alert_snoozes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snoozed_by_sub", sa.String(length=255), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_alert_snoozes_asset_id", "asset_alert_snoozes", ["asset_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_asset_alert_snoozes_asset_id", table_name="asset_alert_snoozes")
    op.drop_table("asset_alert_snoozes")
    op.drop_column("assets", "alert_snoozed_by")
    op.drop_column("assets", "alert_snoozed_until")