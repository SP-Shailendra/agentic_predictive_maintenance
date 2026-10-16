import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    asset_tag: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(80), nullable=False)
    location: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="operational", index=True)
    alert_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    alert_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alert_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alert_acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    alert_snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alert_snoozed_by: Mapped[str | None] = mapped_column(String(255))
    alert_normal_readings: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    alert_last_reading_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criticality: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    vibration_threshold_mm_s: Mapped[float | None] = mapped_column(Float)
    temperature_threshold_c: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    plant: Mapped["Plant"] = relationship(back_populates="assets")
    telemetry: Mapped[list["SensorData"]] = relationship(back_populates="asset", cascade="all, delete-orphan")
    work_orders: Mapped[list["WorkOrder"]] = relationship(back_populates="asset")
