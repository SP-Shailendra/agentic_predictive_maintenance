import hashlib
import json
import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import KnowledgeDocument
from services.chunking_service import chunk_segments
from services.document_loader import DocumentLoadError, load_document
from services.rag_service import _hash_embedding

logger = logging.getLogger(__name__)


class DocumentIngestionConflict(Exception):
    pass


async def ingest_document(
    db: AsyncSession,
    *,
    filename: str,
    raw: bytes,
    document_type: str,
    manufacturer: str | None,
    asset_type: str | None,
    revision: str | None,
    source_uri: str | None,
    plant_id: uuid.UUID | None,
    allowed_roles: list[str],
    uploaded_by_sub: str | None,
) -> KnowledgeDocument:
    checksum = hashlib.sha256(raw).hexdigest()
    document = await db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.sha256 == checksum).with_for_update()
    )
    if document is not None and document.ingestion_status in {"processing", "indexed"}:
        raise DocumentIngestionConflict(str(document.id))

    if document is None:
        document = KnowledgeDocument(
            id=uuid.uuid4(),
            filename=filename,
            sha256=checksum,
            document_type=document_type,
            manufacturer=manufacturer,
            asset_type=asset_type,
            revision=revision,
            source_uri=source_uri,
            plant_id=plant_id,
            allowed_roles=allowed_roles,
            ingestion_status="processing",
            uploaded_by_sub=uploaded_by_sub,
        )
        db.add(document)
    else:
        document.filename = filename
        document.document_type = document_type
        document.manufacturer = manufacturer
        document.asset_type = asset_type
        document.revision = revision
        document.source_uri = source_uri
        document.plant_id = plant_id
        document.allowed_roles = allowed_roles
        document.ingestion_status = "processing"
        document.failure_code = None
        document.chunk_count = 0
        document.uploaded_by_sub = uploaded_by_sub
        await db.execute(
            text("DELETE FROM knowledge_chunks WHERE document_id = :document_id"),
            {"document_id": document.id},
        )
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise DocumentIngestionConflict("duplicate_checksum") from error

    document_id = document.id
    try:
        segments = load_document(filename, raw)
        chunks = chunk_segments(segments)
        async with db.begin():
            current = await db.scalar(
                select(KnowledgeDocument).where(KnowledgeDocument.id == document_id).with_for_update()
            )
            if current is None or current.ingestion_status != "processing":
                raise DocumentIngestionConflict(str(document_id))
            for chunk in chunks:
                chunk_id = uuid.uuid4()
                embedding = _hash_embedding(chunk.text)
                vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
                metadata = {
                    "document_id": str(document_id),
                    "chunk_id": str(chunk_id),
                    "filename": filename,
                    "document_type": document_type,
                    "manufacturer": manufacturer,
                    "asset_type": asset_type,
                    "revision": revision,
                    "source_uri": source_uri,
                    "plant_id": str(plant_id) if plant_id else None,
                    "allowed_roles": allowed_roles,
                    "section_title": chunk.section_title,
                }
                await db.execute(
                    text(
                        "INSERT INTO knowledge_chunks "
                        "(id, document_id, chunk_index, content, embedding, page_number, metadata) "
                        "VALUES (:id, :document_id, :chunk_index, :content, "
                        "CAST(:embedding AS vector), :page_number, CAST(:metadata AS jsonb))"
                    ),
                    {
                        "id": chunk_id,
                        "document_id": document_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.text,
                        "embedding": vector_literal,
                        "page_number": chunk.page_number,
                        "metadata": json.dumps(metadata),
                    },
                )
            current.ingestion_status = "indexed"
            current.failure_code = None
            current.chunk_count = len(chunks)
    except DocumentIngestionConflict:
        await db.rollback()
        raise
    except DocumentLoadError as error:
        await db.rollback()
        await _mark_failed(db, document_id, error.code)
        raise
    except Exception as error:
        await db.rollback()
        await _mark_failed(db, document_id, "ingestion_failed")
        logger.warning("Knowledge document ingestion failed (%s)", type(error).__name__)
        raise DocumentLoadError("ingestion_failed", "Document ingestion failed; re-upload the same file to retry.") from error

    await db.refresh(document)
    return document


async def _mark_failed(db: AsyncSession, document_id: uuid.UUID, failure_code: str) -> None:
    document = await db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document_id).with_for_update()
    )
    if document is None:
        return
    document.ingestion_status = "failed"
    document.failure_code = failure_code[:80]
    await db.commit()