import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal
from models import KnowledgeDocument, KnowledgeDocumentFile
from services.chunking_service import chunk_segments
from services.document_loader import DocumentLoadError, load_document
from services.embedding_service import embed_texts, embedding_model_name

logger = logging.getLogger(__name__)


class DocumentIngestionConflict(Exception):
    pass


async def queue_document(
    db: AsyncSession,
    *,
    filename: str,
    raw: bytes,
    content_type: str | None,
    document_type: str,
    manufacturer: str | None,
    asset_type: str | None,
    revision: str | None,
    source_uri: str | None,
    plant_id: uuid.UUID | None,
    allowed_roles: list[str],
    allowed_subjects: list[str],
    allowed_groups: list[str],
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
            allowed_subjects=allowed_subjects,
            allowed_groups=allowed_groups,
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
        document.allowed_subjects = allowed_subjects
        document.allowed_groups = allowed_groups
        document.ingestion_status = "processing"
        document.failure_code = None
        document.chunk_count = 0
        document.ingestion_attempts = 0
        document.processing_lease_until = None
        document.next_attempt_at = None
        document.uploaded_by_sub = uploaded_by_sub
        await db.execute(
            text("DELETE FROM knowledge_chunks WHERE document_id = :document_id"),
            {"document_id": document.id},
        )
    try:
        await db.flush()
        stored_file = await db.get(KnowledgeDocumentFile, document.id)
        if stored_file is None:
            db.add(KnowledgeDocumentFile(document_id=document.id, content_type=content_type, data=raw))
        else:
            stored_file.content_type = content_type
            stored_file.data = raw
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise DocumentIngestionConflict("duplicate_checksum") from error
    await db.refresh(document)
    return document


async def process_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    retry_on_failure: bool = False,
) -> KnowledgeDocument:
    document = await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    stored_file = await db.get(KnowledgeDocumentFile, document_id)
    if document is None or stored_file is None:
        raise DocumentLoadError("original_missing", "The retained original file is unavailable for indexing.")
    raw = stored_file.data
    await db.commit()
    try:
        segments = await asyncio.to_thread(load_document, document.filename, raw)
        chunks = chunk_segments(segments)
        embeddings = await asyncio.to_thread(embed_texts, [chunk.text for chunk in chunks])
        async with db.begin():
            current = await db.scalar(
                select(KnowledgeDocument).where(KnowledgeDocument.id == document_id).with_for_update()
            )
            if current is None or current.ingestion_status != "processing":
                raise DocumentIngestionConflict(str(document_id))
            for chunk, embedding in zip(chunks, embeddings):
                chunk_id = uuid.uuid4()
                vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
                metadata = {
                    "document_id": str(document_id),
                    "chunk_id": str(chunk_id),
                    "filename": current.filename,
                    "document_type": current.document_type,
                    "manufacturer": current.manufacturer,
                    "asset_type": current.asset_type,
                    "revision": current.revision,
                    "source_uri": current.source_uri,
                    "plant_id": str(current.plant_id) if current.plant_id else None,
                    "allowed_roles": current.allowed_roles,
                    "allowed_subjects": current.allowed_subjects,
                    "allowed_groups": current.allowed_groups,
                    "section_title": chunk.section_title,
                }
                await db.execute(
                    text(
                        "INSERT INTO knowledge_chunks "
                        "(id, document_id, chunk_index, content, embedding, embedding_model, page_number, metadata) "
                        "VALUES (:id, :document_id, :chunk_index, :content, "
                        "CAST(:embedding AS vector), :embedding_model, :page_number, CAST(:metadata AS jsonb))"
                    ),
                    {
                        "id": chunk_id,
                        "document_id": document_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.text,
                        "embedding": vector_literal,
                        "embedding_model": embedding_model_name(),
                        "page_number": chunk.page_number,
                        "metadata": json.dumps(metadata),
                    },
                )
            current.ingestion_status = "indexed"
            current.failure_code = None
            current.chunk_count = len(chunks)
            current.processing_lease_until = None
            current.next_attempt_at = None
    except DocumentIngestionConflict:
        await db.rollback()
        raise
    except DocumentLoadError as error:
        await db.rollback()
        if retry_on_failure:
            await _schedule_retry(db, document_id, error.code)
        else:
            await _mark_failed(db, document_id, error.code)
        raise
    except Exception as error:
        await db.rollback()
        if retry_on_failure:
            await _schedule_retry(db, document_id, "ingestion_failed")
        else:
            await _mark_failed(db, document_id, "ingestion_failed")
        logger.warning("Knowledge document ingestion failed (%s)", type(error).__name__)
        raise DocumentLoadError("ingestion_failed", "Document ingestion failed; re-upload the same file to retry.") from error

    await db.refresh(document)
    return document


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
    allowed_subjects: list[str] | None = None,
    allowed_groups: list[str] | None = None,
) -> KnowledgeDocument:
    document = await queue_document(
        db,
        filename=filename,
        raw=raw,
        content_type=None,
        document_type=document_type,
        manufacturer=manufacturer,
        asset_type=asset_type,
        revision=revision,
        source_uri=source_uri,
        plant_id=plant_id,
        allowed_roles=allowed_roles,
        allowed_subjects=allowed_subjects or [],
        allowed_groups=allowed_groups or [],
        uploaded_by_sub=uploaded_by_sub,
    )
    return await process_document(db, document.id)


async def claim_pending_document() -> uuid.UUID | None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        async with db.begin():
            exhausted = await db.scalars(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.ingestion_status == "processing",
                    KnowledgeDocument.ingestion_attempts >= 3,
                    KnowledgeDocument.processing_lease_until < now,
                )
                .with_for_update(skip_locked=True)
            )
            for document in exhausted.all():
                document.ingestion_status = "failed"
                document.failure_code = document.failure_code or "worker_retries_exhausted"
                document.processing_lease_until = None
                document.next_attempt_at = None
            document = await db.scalar(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.ingestion_status == "processing",
                    KnowledgeDocument.ingestion_attempts < 3,
                    or_(KnowledgeDocument.next_attempt_at.is_(None), KnowledgeDocument.next_attempt_at <= now),
                    or_(
                        KnowledgeDocument.processing_lease_until.is_(None),
                        KnowledgeDocument.processing_lease_until < now,
                    ),
                )
                .order_by(KnowledgeDocument.created_at, KnowledgeDocument.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if document is None:
                return None
            document.ingestion_attempts += 1
            document.processing_lease_until = now + timedelta(minutes=10)
            return document.id


async def process_pending_document() -> bool:
    document_id = await claim_pending_document()
    if document_id is None:
        return False
    async with SessionLocal() as db:
        try:
            await process_document(db, document_id, retry_on_failure=True)
        except Exception as error:
            logger.warning("Knowledge background indexing failed (%s)", type(error).__name__)
    return True


async def _mark_failed(db: AsyncSession, document_id: uuid.UUID, failure_code: str) -> None:
    document = await db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document_id).with_for_update()
    )
    if document is None:
        return
    document.ingestion_status = "failed"
    document.failure_code = failure_code[:80]
    document.processing_lease_until = None
    await db.commit()


async def _schedule_retry(db: AsyncSession, document_id: uuid.UUID, failure_code: str) -> None:
    document = await db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document_id).with_for_update()
    )
    if document is None:
        return
    document.failure_code = failure_code[:80]
    document.processing_lease_until = None
    if document.ingestion_attempts >= 3:
        document.ingestion_status = "failed"
        document.next_attempt_at = None
    else:
        delay_seconds = min(300, 2 ** document.ingestion_attempts)
        document.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    await db.commit()