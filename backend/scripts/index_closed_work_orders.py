import argparse
import asyncio

from sqlalchemy import not_, or_, select

from core.database import SessionLocal
from models import KnowledgeDocument, WorkOrder
from services.closeout_knowledge import enqueue_closed_work_order_knowledge

BATCH_SIZE = 100


async def index_closed_work_orders(
    batch_size: int = BATCH_SIZE,
    max_batches: int | None = None,
    session_factory=SessionLocal,
) -> int:
    indexed = 0
    batches = 0
    while max_batches is None or batches < max_batches:
        async with session_factory() as db:
            async with db.begin():
                orders = await db.scalars(
                    select(WorkOrder)
                    .where(
                        WorkOrder.status == "closed",
                        WorkOrder.confirmed_root_cause.is_not(None),
                        or_(WorkOrder.workflow_state["simulated_only"].as_boolean().is_(False),
                            WorkOrder.workflow_state["simulated_only"].as_boolean().is_(None)),
                        or_(WorkOrder.cmms_reference.is_(None), not_(WorkOrder.cmms_reference.like("SIM-%"))),
                        ~WorkOrder.id.in_(
                            select(KnowledgeDocument.source_work_order_id).where(
                                KnowledgeDocument.source_work_order_id.is_not(None)
                            )
                        ),
                    )
                    .order_by(WorkOrder.closed_at, WorkOrder.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                batch = list(orders.all())
                for order in batch:
                    await enqueue_closed_work_order_knowledge(db, order)
            if not batch:
                return indexed
        indexed += len(batch)
        batches += 1
    return indexed


def main() -> None:
    parser = argparse.ArgumentParser(description="Queue eligible historical closed work orders for RAG indexing.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--confirm", action="store_true", help="Confirm eligible close-outs may be added to knowledge retrieval.")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size must be between 1 and 1000")
    if args.max_batches is not None and args.max_batches < 1:
        parser.error("--max-batches must be positive")
    if not args.confirm:
        parser.error("Pass --confirm to queue historical close-outs for retrieval.")
    indexed = asyncio.run(index_closed_work_orders(args.batch_size, args.max_batches))
    print(f"Queued {indexed} eligible closed work orders for background indexing.")


if __name__ == "__main__":
    main()