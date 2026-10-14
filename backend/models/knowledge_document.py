import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint("ingestion_status IN ('processing', 'indexed', 'failed')", name="ck_knowledge_documents_status"),
        UniqueConstraint("sha256", name="uq_knowledge_documents_sha256"),
        Index("ix_knowledge_documents_plant_id", "plant_id"),
        Index("ix_knowledge_documents_status", "ingestion_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(160))
    asset_type: Mapped[str | None] = mapped_column(String(120))
    revision: Mapped[str | None] = mapped_column(String(80))
    source_uri: Mapped[str | None] = mapped_column(Text)
    plant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("plants.id", ondelete="RESTRICT"))
    allowed_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    ingestion_status: Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    failure_code: Mapped[str | None] = mapped_column(String(80))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by_sub: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)