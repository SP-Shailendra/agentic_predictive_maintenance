"""Add identity attribution and tamper-evident workflow event chains.

Revision ID: 20261007_0002
Revises: 20261007_0001
Create Date: 2026-10-07
"""

from collections import defaultdict

from alembic import op
import sqlalchemy as sa

from core.audit import workflow_event_hash

revision = "20261007_0002"
down_revision = "20261007_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_orders", sa.Column("created_by_sub", sa.String(length=255), nullable=True))
    op.add_column("work_orders", sa.Column("approved_by_sub", sa.String(length=255), nullable=True))
    op.add_column("work_orders", sa.Column("rejected_by_sub", sa.String(length=255), nullable=True))
    op.add_column("workflow_events", sa.Column("actor_id", sa.String(length=255), nullable=True))
    op.add_column(
        "workflow_events",
        sa.Column("actor_roles", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column(
        "workflow_events",
        sa.Column("actor_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("workflow_events", sa.Column("previous_hash", sa.String(length=64), nullable=True))
    op.add_column("workflow_events", sa.Column("event_hash", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, work_order_id, event_type, actor_type, from_status, to_status, details, created_at "
        "FROM workflow_events ORDER BY work_order_id, created_at, id"
    )).mappings()
    previous_by_work_order: dict[str, str | None] = defaultdict(lambda: None)
    for row in rows:
        event_id = str(row["id"])
        work_order_id = str(row["work_order_id"])
        created_at = row["created_at"]
        previous_hash = previous_by_work_order[work_order_id]
        details = row["details"] or {}
        digest = workflow_event_hash(
            event_id=event_id,
            work_order_id=work_order_id,
            event_type=row["event_type"],
            actor_type=row["actor_type"],
            actor_id=None,
            actor_roles=[],
            actor_verified=False,
            from_status=row["from_status"],
            to_status=row["to_status"],
            details=details,
            previous_hash=previous_hash,
            created_at=created_at,
        )
        bind.execute(
            sa.text("UPDATE workflow_events SET previous_hash=:previous_hash, event_hash=:event_hash WHERE id=:id"),
            {"previous_hash": previous_hash, "event_hash": digest, "id": row["id"]},
        )
        previous_by_work_order[work_order_id] = digest

    op.alter_column("workflow_events", "event_hash", nullable=False)
    op.execute("ALTER TABLE workflow_events ALTER COLUMN actor_roles DROP DEFAULT")
    op.execute("ALTER TABLE workflow_events ALTER COLUMN actor_verified DROP DEFAULT")
    op.execute("""
        CREATE FUNCTION prevent_workflow_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'workflow_events is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE FUNCTION verify_workflow_event_chain() RETURNS trigger AS $$
        DECLARE expected_hash TEXT;
        BEGIN
            SELECT event_hash INTO expected_hash
            FROM workflow_events
            WHERE work_order_id = NEW.work_order_id
            ORDER BY created_at DESC, id DESC
            LIMIT 1;
            IF expected_hash IS DISTINCT FROM NEW.previous_hash THEN
                RAISE EXCEPTION 'workflow event hash chain mismatch';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER workflow_events_no_update_delete
        BEFORE UPDATE OR DELETE ON workflow_events
        FOR EACH ROW EXECUTE FUNCTION prevent_workflow_event_mutation()
    """)
    op.execute("""
        CREATE TRIGGER workflow_events_no_truncate
        BEFORE TRUNCATE ON workflow_events
        FOR EACH STATEMENT EXECUTE FUNCTION prevent_workflow_event_mutation()
    """)
    op.execute("""
        CREATE TRIGGER workflow_events_verify_chain
        BEFORE INSERT ON workflow_events
        FOR EACH ROW EXECUTE FUNCTION verify_workflow_event_chain()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS workflow_events_verify_chain ON workflow_events")
    op.execute("DROP TRIGGER IF EXISTS workflow_events_no_truncate ON workflow_events")
    op.execute("DROP TRIGGER IF EXISTS workflow_events_no_update_delete ON workflow_events")
    op.execute("DROP FUNCTION IF EXISTS verify_workflow_event_chain()")
    op.execute("DROP FUNCTION IF EXISTS prevent_workflow_event_mutation()")
    op.drop_column("workflow_events", "event_hash")
    op.drop_column("workflow_events", "previous_hash")
    op.drop_column("workflow_events", "actor_verified")
    op.drop_column("workflow_events", "actor_roles")
    op.drop_column("workflow_events", "actor_id")
    op.drop_column("work_orders", "rejected_by_sub")
    op.drop_column("work_orders", "approved_by_sub")
    op.drop_column("work_orders", "created_by_sub")