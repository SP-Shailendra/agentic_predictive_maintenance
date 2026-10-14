import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing", index=True)
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    diagnosis: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    workflow_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    cmms_reference: Mapped[str | None] = mapped_column(String(120))
    assigned_to_sub: Mapped[str | None] = mapped_column(String(255), index=True)
    created_by_sub: Mapped[str | None] = mapped_column(String(255))
    approved_by_sub: Mapped[str | None] = mapped_column(String(255))
    rejected_by_sub: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_root_cause: Mapped[str | None] = mapped_column(String(80))
    alert_was_useful: Mapped[bool | None] = mapped_column(Boolean)
    closed_by_sub: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    asset: Mapped["Asset"] = relationship(back_populates="work_orders")
