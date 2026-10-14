"""Separate persistent alerts from live asset condition."""

from alembic import op
import sqlalchemy as sa

revision = "20261014_0010"
down_revision = "20261009_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("alert_active", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("assets", sa.Column("alert_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assets", sa.Column("alert_acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assets", sa.Column("alert_acknowledged_by", sa.String(255), nullable=True))
    op.add_column("assets", sa.Column("alert_normal_readings", sa.Integer(), server_default="0", nullable=False))
    op.add_column("assets", sa.Column("alert_last_reading_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE assets SET alert_last_reading_at = (SELECT max(timestamp) FROM sensor_data WHERE asset_id = assets.id)")
    op.execute("UPDATE assets SET alert_active = TRUE, alert_started_at = COALESCE(alert_last_reading_at, CURRENT_TIMESTAMP) WHERE status = 'attention'")


def downgrade() -> None:
    for column in (
        "alert_last_reading_at", "alert_normal_readings", "alert_acknowledged_by",
        "alert_acknowledged_at", "alert_started_at", "alert_active",
    ):
        op.drop_column("assets", column)