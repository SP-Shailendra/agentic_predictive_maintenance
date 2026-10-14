"""Add source event IDs for idempotent telemetry ingestion.

Revision ID: 20261007_0006
Revises: 20261007_0005
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20261007_0006"
down_revision = "20261007_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sensor_data", sa.Column("source_event_id", sa.String(length=120), nullable=True))
    op.create_index(
        "uq_sensor_data_source_event",
        "sensor_data",
        ["asset_id", "timestamp", "source_event_id"],
        unique=True,
        postgresql_where=sa.text("source_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_sensor_data_source_event", table_name="sensor_data")
    op.drop_column("sensor_data", "source_event_id")