"""Add document ingestion records and chunk provenance.

Revision ID: 20261009_0009
Revises: 20261008_0008
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261009_0009"
down_revision = "20261008_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("manufacturer", sa.String(length=160), nullable=True),
        sa.Column("asset_type", sa.String(length=120), nullable=True),
        sa.Column("revision", sa.String(length=80), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("allowed_roles", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("ingestion_status", sa.String(length=20), server_default="processing", nullable=False),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("uploaded_by_sub", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ingestion_status IN ('processing', 'indexed', 'failed')", name="ck_knowledge_documents_status"),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256", name="uq_knowledge_documents_sha256"),
    )
    op.create_index("ix_knowledge_documents_plant_id", "knowledge_documents", ["plant_id"], unique=False)
    op.create_index("ix_knowledge_documents_status", "knowledge_documents", ["ingestion_status"], unique=False)
    op.add_column("knowledge_chunks", sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("chunk_index", sa.Integer(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("page_number", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_knowledge_chunks_document_id_knowledge_documents",
        "knowledge_chunks",
        "knowledge_documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"], unique=False)
    op.create_index(
        "uq_knowledge_chunks_document_chunk",
        "knowledge_chunks",
        ["document_id", "chunk_index"],
        unique=True,
        postgresql_where=sa.text("document_id IS NOT NULL AND chunk_index IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_knowledge_chunks_document_chunk", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_document_id", table_name="knowledge_chunks")
    op.drop_constraint("fk_knowledge_chunks_document_id_knowledge_documents", "knowledge_chunks", type_="foreignkey")
    op.drop_column("knowledge_chunks", "page_number")
    op.drop_column("knowledge_chunks", "chunk_index")
    op.drop_column("knowledge_chunks", "document_id")
    op.drop_index("ix_knowledge_documents_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_plant_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")