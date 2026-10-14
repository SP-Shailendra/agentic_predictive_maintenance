import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class SimulatorScenario(Base):
    __tablename__ = "simulator_scenarios"
    __table_args__ = (
        CheckConstraint("scenario IN ('vibration_rise', 'overheating', 'sudden_spike', 'data_dropout', 'invalid_reading', 'sensor_flatline', 'healthy_baseline')", name="ck_simulator_scenario_name"),
        CheckConstraint("severity >= 0 AND severity <= 1", name="ck_simulator_scenario_severity"),
        CheckConstraint("duration_readings BETWEEN 1 AND 120", name="ck_simulator_scenario_duration"),
        CheckConstraint("remaining_readings >= 0", name="ck_simulator_scenario_remaining"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    duration_readings: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_readings: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    force_llm_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_by_sub: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    recent_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SimulatorScenarioEvent(Base):
    __tablename__ = "simulator_scenario_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255))
    actor_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    actor_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)