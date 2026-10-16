from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import Principal, plant_scope_filter, require_api_key, require_roles, WORKFLOW_READ_ROLES
from models import Asset, Plant, WorkOrder
from schemas.asset import PlantRead

router = APIRouter(prefix="/plants", tags=["plants"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[PlantRead])
async def list_plants(
    db: AsyncSession = Depends(get_db),
    _principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> list[PlantRead]:
    asset_stats = (
        select(
            Asset.plant_id.label("plant_id"),
            func.count(Asset.id).label("asset_count"),
            func.count(Asset.id).filter(Asset.alert_active.is_(True)).label("attention_count"),
        )
        .group_by(Asset.plant_id)
        .subquery()
    )
    work_order_stats = (
        select(
            Asset.plant_id.label("plant_id"),
            func.count(WorkOrder.id)
            .filter(WorkOrder.status == "awaiting_approval")
            .label("pending_work_order_count"),
        )
        .join(Asset, Asset.id == WorkOrder.asset_id)
        .group_by(Asset.plant_id)
        .subquery()
    )
    query = (
        select(
            Plant,
            func.coalesce(asset_stats.c.asset_count, 0),
            func.coalesce(asset_stats.c.attention_count, 0),
            func.coalesce(work_order_stats.c.pending_work_order_count, 0),
        )
        .outerjoin(asset_stats, asset_stats.c.plant_id == Plant.id)
        .outerjoin(work_order_stats, work_order_stats.c.plant_id == Plant.id)
        .order_by(Plant.code)
    )
    plant_filter = plant_scope_filter(_principal, Plant.id)
    if plant_filter is not None:
        query = query.where(plant_filter)
    rows = await db.execute(query)
    return [
        PlantRead.model_validate(plant).model_copy(update={
            "asset_count": asset_count,
            "attention_count": attention_count,
            "pending_work_order_count": pending_count,
        })
        for plant, asset_count, attention_count, pending_count in rows.all()
    ]