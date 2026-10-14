import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class AssetThresholdChange(Base):
    __tablename__ = "asset_threshold_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    changed_by_sub: Mapped[str | None] = mapped_column(String(255))
    vibration_old_mm_s: Mapped[float | None] = mapped_column(Float)
    vibration_new_mm_s: Mapped[float | None] = mapped_column(Float)
    temperature_old_c: Mapped[float | None] = mapped_column(Float)
    temperature_new_c: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)