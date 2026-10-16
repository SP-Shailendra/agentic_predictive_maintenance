from pathlib import PurePath
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import and_, false, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.security import (
    KNOWN_ROLES,
    Principal,
    WORKFLOW_READ_ROLES,
    plant_scope_filter,
    require_api_key,
    require_plant_assignment,
    require_roles,
)
from models import KnowledgeDocument, Plant
from schemas.knowledge import KnowledgeChunkPage, KnowledgeChunkRead, KnowledgeDocumentRead
from services.document_loader import DocumentLoadError
from services.ingestion_service import DocumentIngestionConflict, ingest_document
from services.ingestion_service import DocumentIngestionConflict, queue_document

router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_api_key)])


def _can_read_document(principal: Principal):
    role_filters = [KnowledgeDocument.allowed_roles.contains([role]) for role in principal.roles]
    role_access = or_(*role_filters) if role_filters else false()
    subject_access = KnowledgeDocument.allowed_subjects.contains([principal.subject]) if principal.verified else false()
    group_filters = [KnowledgeDocument.allowed_groups.contains([group]) for group in principal.groups or ()]
    identity_access = or_(
        and_(KnowledgeDocument.allowed_subjects == [], KnowledgeDocument.allowed_groups == []),
        subject_access,
        *group_filters,
    )
    return or_(KnowledgeDocument.id.is_(None), and_(role_access, identity_access))


def _document_plant_filter(principal: Principal, requested_plant_id: uuid.UUID | None = None):
    scope = plant_scope_filter(principal, KnowledgeDocument.plant_id, requested_plant_id)
    return or_(KnowledgeDocument.plant_id.is_(None), scope) if scope is not None else None


def _chunk_plant_filter_sql(
    principal: Principal,
    requested_plant_id: uuid.UUID | None,
    parameters: dict[str, object],
) -> str:
    if principal.plant_ids is None:
        if requested_plant_id is None:
            return ""
        parameters["plant_id"] = requested_plant_id
        return "AND (k.document_id IS NULL OR d.plant_id IS NULL OR d.plant_id = :plant_id)"
    if requested_plant_id is not None:
        require_plant_assignment(principal, requested_plant_id)
        plant_ids = [requested_plant_id]
    else:
        plant_ids = sorted(principal.plant_ids, key=str)
    if not plant_ids:
        raise HTTPException(status_code=403, detail="No plant entitlements are assigned.")
    plant_parameters = []
    for index, plant_id in enumerate(plant_ids):
        name = f"plant_id_{index}"
        parameters[name] = plant_id
        plant_parameters.append(f":{name}")
    return "AND (k.document_id IS NULL OR d.plant_id IS NULL OR d.plant_id IN (" + ", ".join(plant_parameters) + "))"


@router.post("/documents", response_model=KnowledgeDocumentRead, status_code=status.HTTP_201_CREATED)
@router.post("/documents", response_model=KnowledgeDocumentRead, status_code=status.HTTP_202_ACCEPTED)
async def ingest_knowledge_document(
    file: UploadFile = File(...),
    document_type: str = Form(default="manual", max_length=40),
    manufacturer: str | None = Form(default=None, max_length=160),
    asset_type: str | None = Form(default=None, max_length=120),
    revision: str | None = Form(default=None, max_length=80),
    source_uri: str | None = Form(default=None, max_length=2000),
    plant_id: uuid.UUID | None = Form(default=None),
    allowed_roles: list[str] = Form(default=[]),
    allowed_subjects: list[str] = Form(default=[]),
    allowed_groups: list[str] = Form(default=[]),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("manager", "reliability_engineer")),
) -> KnowledgeDocument:
    filename = PurePath((file.filename or "").replace("\\", "/")).name
    if not filename or len(filename) > 255:
        raise HTTPException(status_code=422, detail="A valid document filename is required.")
    max_bytes = settings.knowledge_max_upload_mb * 1024 * 1024
    raw = await file.read(max_bytes + 1)
    await file.close()
    if not raw:
        raise HTTPException(status_code=422, detail="The uploaded document is empty.")
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Document exceeds the {settings.knowledge_max_upload_mb} MB upload limit.")
    require_plant_assignment(principal, plant_id)
    if plant_id is not None and await db.get(Plant, plant_id) is None:
        raise HTTPException(status_code=404, detail="Plant not found.")

    selected_roles = sorted(set(allowed_roles or WORKFLOW_READ_ROLES))
    if not selected_roles or any(role not in KNOWN_ROLES for role in selected_roles):
        raise HTTPException(status_code=422, detail="Document access roles must be recognized Fieldnote roles.")
    try:
        return await queue_document(
            db,
            filename=filename,
            raw=raw,
            content_type=file.content_type,
            document_type=document_type,
            manufacturer=manufacturer,
            asset_type=asset_type,
            revision=revision,
            source_uri=source_uri,
            plant_id=plant_id,
            allowed_roles=selected_roles,
            allowed_subjects=sorted(set(allowed_subjects)),
            allowed_groups=sorted(set(allowed_groups)),
            uploaded_by_sub=principal.subject if principal.verified else None,
        )
    except DocumentIngestionConflict as error:
        raise HTTPException(status_code=409, detail=f"Document is already processing or indexed: {error}.") from error
    except DocumentLoadError as error:
        raise HTTPException(status_code=422, detail={"code": error.code, "message": str(error)}) from error


@router.get("/documents", response_model=list[KnowledgeDocumentRead])
async def list_knowledge_documents(
    plant_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[KnowledgeDocument]:
    query = select(KnowledgeDocument).where(_can_read_document(principal))
    plant_filter = _document_plant_filter(principal, plant_id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    rows = await db.scalars(query.order_by(KnowledgeDocument.created_at.desc()).limit(500))
    return list(rows.all())


@router.get("/documents/{document_id}", response_model=KnowledgeDocumentRead)
async def get_knowledge_document(
    document_id: uuid.UUID,
    plant_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> KnowledgeDocument:
    plant_scope = _document_plant_filter(principal, plant_id)
    filters = [KnowledgeDocument.id == document_id, _can_read_document(principal)]
    if plant_scope is not None:
        filters.append(plant_scope)
    document = await db.scalar(
        select(KnowledgeDocument).where(*filters)
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")
    return document


@router.get("/chunks/{chunk_id}", response_model=KnowledgeChunkRead)
async def get_knowledge_chunk(
    chunk_id: uuid.UUID,
    plant_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> KnowledgeChunkRead:
    parameters: dict[str, object] = {"chunk_id": chunk_id}
    role_filters = []
    group_filters = []
    for index, role in enumerate(sorted(principal.roles)):
        bind_name = f"allowed_role_{index}"
        role_filters.append(f"d.allowed_roles @> CAST(:{bind_name} AS jsonb)")
        parameters[bind_name] = json.dumps([role])
    role_access = " OR ".join(role_filters) if role_filters else "FALSE"
    if principal.verified:
        parameters["allowed_subject"] = json.dumps([principal.subject])
        subject_access = "d.allowed_subjects @> CAST(:allowed_subject AS jsonb)"
    else:
        subject_access = "FALSE"
    for index, group in enumerate(sorted(principal.groups or ())):
        bind_name = f"allowed_group_{index}"
        group_filters.append(f"d.allowed_groups @> CAST(:{bind_name} AS jsonb)")
        parameters[bind_name] = json.dumps([group])
    identity_access = " OR ".join([subject_access, *group_filters]) or "FALSE"
    access_filter = (
        "AND (k.document_id IS NULL OR ((" + role_access + ") AND ("
        "(COALESCE(d.allowed_subjects, '[]'::jsonb) = '[]'::jsonb AND "
        "COALESCE(d.allowed_groups, '[]'::jsonb) = '[]'::jsonb) OR " + identity_access + ")))"
    )
    plant_filter = _chunk_plant_filter_sql(principal, plant_id, parameters)
    row = (await db.execute(text(
        "SELECT k.id AS chunk_id, k.document_id, k.content, k.page_number, k.metadata, "
        "d.filename, d.document_type, d.revision, d.source_uri "
        "FROM knowledge_chunks AS k LEFT JOIN knowledge_documents AS d ON d.id = k.document_id "
        "WHERE k.id = :chunk_id AND (k.document_id IS NULL OR d.ingestion_status = 'indexed') "
        f"{access_filter} {plant_filter}"
    ), parameters)).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found.")
    metadata = row["metadata"] or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return KnowledgeChunkRead(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        content=row["content"],
        filename=row["filename"] or "Legacy maintenance note",
        document_type=row["document_type"],
        revision=row["revision"],
        page_number=row["page_number"],
        section_title=metadata.get("section_title"),
        source_uri=row["source_uri"],
    )


@router.get("/documents/{document_id}/chunks", response_model=KnowledgeChunkPage)
async def list_knowledge_document_chunks(
    document_id: uuid.UUID,
    plant_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> KnowledgeChunkPage:
    filters = [
        KnowledgeDocument.id == document_id,
        KnowledgeDocument.ingestion_status == "indexed",
        _can_read_document(principal),
    ]
    plant_filter = _document_plant_filter(principal, plant_id)
    if plant_filter is not None:
        filters.append(plant_filter)
    document = await db.scalar(select(KnowledgeDocument).where(*filters))
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")
    total = int(await db.scalar(text(
        "SELECT count(*) FROM knowledge_chunks WHERE document_id = :document_id"
    ), {"document_id": document_id}) or 0)
    rows = (await db.execute(text(
        "SELECT id AS chunk_id, document_id, content, page_number, metadata "
        "FROM knowledge_chunks WHERE document_id = :document_id "
        "ORDER BY chunk_index, id LIMIT :limit OFFSET :offset"
    ), {"document_id": document_id, "limit": limit, "offset": offset})).mappings().all()
    items = []
    for row in rows:
        metadata = row["metadata"] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        items.append(KnowledgeChunkRead(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            content=row["content"],
            filename=document.filename,
            document_type=document.document_type,
            revision=document.revision,
            page_number=row["page_number"],
            section_title=metadata.get("section_title"),
            source_uri=document.source_uri,
        ))
    return KnowledgeChunkPage(items=items, total=total, limit=limit, offset=offset)