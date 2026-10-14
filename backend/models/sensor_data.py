import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class SensorData(Base):
    __tablename__ = "sensor_data"
    __table_args__ = (
        Index("ix_sensor_data_asset_timestamp", "asset_id", "timestamp"),
        Index(
            "uq_sensor_data_source_event",
            "asset_id",
            "timestamp",
            "source_event_id",
            unique=True,
            postgresql_where=text("source_event_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    vibration_mm_s: Mapped[float] = mapped_column(Float, nullable=False)
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    pressure_bar: Mapped[float] = mapped_column(Float, nullable=False)
    rpm: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="simulator")
    source_event_id: Mapped[str | None] = mapped_column(String(120))

    asset: Mapped["Asset"] = relationship(back_populates="telemetry")
