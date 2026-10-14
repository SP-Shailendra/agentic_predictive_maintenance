"""Add gated simulation scenarios and scenario audit events.

Revision ID: 20261007_0007
Revises: 20261007_0006
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261007_0007"
down_revision = "20261007_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulator_scenarios",
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.Float(), nullable=False),
        sa.Column("duration_readings", sa.Integer(), nullable=False),
        sa.Column("remaining_readings", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("force_llm_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_by_sub", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("recent_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scenario IN ('vibration_rise', 'overheating', 'sudden_spike', 'data_dropout', 'invalid_reading', 'sensor_flatline', 'healthy_baseline')", name="ck_simulator_scenario_name"),
        sa.CheckConstraint("severity >= 0 AND severity <= 1", name="ck_simulator_scenario_severity"),
        sa.CheckConstraint("duration_readings BETWEEN 1 AND 120", name="ck_simulator_scenario_duration"),
        sa.CheckConstraint("remaining_readings >= 0", name="ck_simulator_scenario_remaining"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("asset_id"),
    )
    op.create_index("ix_simulator_scenarios_active", "simulator_scenarios", ["active"], unique=False)
    op.create_table(
        "simulator_scenario_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("actor_type", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("actor_roles", sa.JSON(), nullable=False),
        sa.Column("actor_verified", sa.Boolean(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulator_scenario_events_asset_id", "simulator_scenario_events", ["asset_id"], unique=False)
    op.create_index("ix_simulator_scenario_events_created_at", "simulator_scenario_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_simulator_scenario_events_created_at", table_name="simulator_scenario_events")
    op.drop_index("ix_simulator_scenario_events_asset_id", table_name="simulator_scenario_events")
    op.drop_table("simulator_scenario_events")
    op.drop_index("ix_simulator_scenarios_active", table_name="simulator_scenarios")
    op.drop_table("simulator_scenarios")