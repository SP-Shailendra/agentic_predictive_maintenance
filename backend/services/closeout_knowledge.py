import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import WORKFLOW_READ_ROLES
from models import Asset, KnowledgeDocument, KnowledgeDocumentFile, WorkOrder


async def enqueue_closed_work_order_knowledge(db: AsyncSession, work_order: WorkOrder) -> uuid.UUID | None:
    if work_order.status != "closed" or not work_order.confirmed_root_cause:
        return None
    state = work_order.workflow_state if isinstance(work_order.workflow_state, dict) else {}
    if state.get("simulated_only") is True or (work_order.cmms_reference or "").startswith("SIM-"):
        return None
    existing = await db.scalar(
        select(KnowledgeDocument.id).where(KnowledgeDocument.source_work_order_id == work_order.id)
    )
    if existing is not None:
        return existing
    asset = await db.get(Asset, work_order.asset_id)
    if asset is None:
        return None

    content = "\n".join((
        f"Source work order: {work_order.id}",
        f"Asset type: {asset.asset_type}",
        f"Confirmed root cause: {work_order.confirmed_root_cause}",
        f"Work order summary: {work_order.summary}",
        f"Diagnosis: {work_order.diagnosis or ''}",
        f"Recommended work: {work_order.recommendation or ''}",
        f"Close-out notes: {state.get('closeout_comment', '')}",
    )).strip()
    raw = content.encode("utf-8")
    document_id = uuid.uuid4()
    document = KnowledgeDocument(
        id=document_id,
        filename=f"work-order-{work_order.id}.txt",
        sha256=hashlib.sha256(raw).hexdigest(),
        document_type="closeout",
        asset_type=asset.asset_type,
        source_uri=f"fieldnote://work-orders/{work_order.id}",
        plant_id=asset.plant_id,
        allowed_roles=list(WORKFLOW_READ_ROLES),
        allowed_subjects=[],
        allowed_groups=[],
        source_work_order_id=work_order.id,
        ingestion_status="processing",
        uploaded_by_sub=work_order.closed_by_sub,
    )
    db.add(document)
    await db.flush()
    db.add(KnowledgeDocumentFile(document_id=document_id, content_type="text/plain", data=raw))
    return document_id