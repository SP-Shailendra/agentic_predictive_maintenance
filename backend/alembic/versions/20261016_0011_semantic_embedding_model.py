"""Track the model used to create each knowledge chunk embedding."""

from alembic import op
import sqlalchemy as sa

revision = "20261016_0011"
down_revision = "20261014_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_chunks", sa.Column("embedding_model", sa.String(length=200), nullable=True))
    op.create_index("ix_knowledge_chunks_embedding_model", "knowledge_chunks", ["embedding_model"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_embedding_model", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "embedding_model")