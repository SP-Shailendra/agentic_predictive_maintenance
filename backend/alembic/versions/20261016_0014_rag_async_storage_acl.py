"""Retain knowledge originals and add ACL/async indexing provenance."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261016_0014"
down_revision = "20261016_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("allowed_subjects", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False))
    op.add_column("knowledge_documents", sa.Column("allowed_groups", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False))
    op.add_column("knowledge_documents", sa.Column("ingestion_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("knowledge_documents", sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_documents", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_documents", sa.Column("source_work_order_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_knowledge_documents_source_work_order_id",
        "knowledge_documents",
        "work_orders",
        ["source_work_order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_knowledge_documents_source_work_order",
        "knowledge_documents",
        ["source_work_order_id"],
    )
    op.create_index("ix_knowledge_documents_processing_lease", "knowledge_documents", ["ingestion_status", "processing_lease_until"], unique=False)
    op.create_table(
        "knowledge_document_files",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_document_files")
    op.drop_index("ix_knowledge_documents_processing_lease", table_name="knowledge_documents")
    op.drop_constraint("uq_knowledge_documents_source_work_order", "knowledge_documents", type_="unique")
    op.drop_constraint("fk_knowledge_documents_source_work_order_id", "knowledge_documents", type_="foreignkey")
    op.drop_column("knowledge_documents", "source_work_order_id")
    op.drop_column("knowledge_documents", "next_attempt_at")
    op.drop_column("knowledge_documents", "processing_lease_until")
    op.drop_column("knowledge_documents", "ingestion_attempts")
    op.drop_column("knowledge_documents", "allowed_groups")
    op.drop_column("knowledge_documents", "allowed_subjects")