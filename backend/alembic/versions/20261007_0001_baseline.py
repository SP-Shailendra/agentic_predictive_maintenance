"""Initial Fieldnote schema baseline.

Revision ID: 20261007_0001
Revises:
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261007_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "plants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("location", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plants_code", "plants", ["code"], unique=True)

    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("asset_tag", sa.String(length=80), nullable=False),
        sa.Column("asset_type", sa.String(length=80), nullable=False),
        sa.Column("location", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="operational", nullable=False),
        sa.Column("criticality", sa.String(length=24), server_default="medium", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assets_plant_id", "assets", ["plant_id"], unique=False)
    op.create_index("ix_assets_name", "assets", ["name"], unique=False)
    op.create_index("ix_assets_asset_tag", "assets", ["asset_tag"], unique=True)
    op.create_index("ix_assets_status", "assets", ["status"], unique=False)

    op.create_table(
        "sensor_data",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vibration_mm_s", sa.Float(), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=False),
        sa.Column("pressure_bar", sa.Float(), nullable=False),
        sa.Column("rpm", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=40), server_default="simulator", nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", "timestamp"),
    )
    op.create_index("ix_sensor_data_timestamp", "sensor_data", ["timestamp"], unique=False)
    op.create_index("ix_sensor_data_asset_timestamp", "sensor_data", ["asset_id", "timestamp"], unique=False)
    op.execute("SELECT create_hypertable('sensor_data', 'timestamp', if_not_exists => TRUE, migrate_data => TRUE)")

    op.create_table(
        "work_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="processing", nullable=False),
        sa.Column("summary", sa.String(length=240), nullable=False),
        sa.Column("diagnosis", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("workflow_state", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("cmms_reference", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_orders_asset_id", "work_orders", ["asset_id"], unique=False)
    op.create_index("ix_work_orders_status", "work_orders", ["status"], unique=False)

    op.create_table(
        "workflow_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_type", sa.String(length=24), server_default="api_client", nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("details", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_events_work_order_id", "workflow_events", ["work_order_id"], unique=False)
    op.create_index("ix_workflow_events_created_at", "workflow_events", ["created_at"], unique=False)
    op.execute(
        "CREATE TABLE knowledge_chunks ("
        "id UUID PRIMARY KEY, content TEXT NOT NULL, metadata JSONB NOT NULL DEFAULT '{}'::jsonb, "
        "embedding vector(384) NOT NULL)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    op.drop_table("workflow_events")
    op.drop_table("work_orders")
    op.drop_index("ix_sensor_data_asset_timestamp", table_name="sensor_data")
    op.drop_index("ix_sensor_data_timestamp", table_name="sensor_data")
    op.drop_table("sensor_data")
    op.drop_index("ix_assets_status", table_name="assets")
    op.drop_index("ix_assets_asset_tag", table_name="assets")
    op.drop_index("ix_assets_name", table_name="assets")
    op.drop_index("ix_assets_plant_id", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_plants_code", table_name="plants")
    op.drop_table("plants")