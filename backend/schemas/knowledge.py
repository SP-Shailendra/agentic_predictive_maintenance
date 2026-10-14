import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class KnowledgeDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    sha256: str
    document_type: str
    manufacturer: str | None
    asset_type: str | None
    revision: str | None
    source_uri: str | None
    plant_id: uuid.UUID | None
    allowed_roles: list[str]
    ingestion_status: str
    failure_code: str | None
    chunk_count: int
    uploaded_by_sub: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeChunkRead(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID | None
    content: str
    filename: str
    document_type: str | None
    revision: str | None
    page_number: int | None
    section_title: str | None
    source_uri: str | None


class KnowledgeChunkPage(BaseModel):
    items: list[KnowledgeChunkRead]
    total: int
    limit: int
    offset: int