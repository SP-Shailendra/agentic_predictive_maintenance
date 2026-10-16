import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import settings
from services.embedding_service import embed_texts, embedding_model_name

BATCH_SIZE = 64


async def reindex(database_url: str) -> int:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    model_name = embedding_model_name()
    updated_count = 0
    try:
        while True:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text(
                        "SELECT id, content FROM knowledge_chunks "
                        "WHERE embedding_model IS DISTINCT FROM :model_name "
                        "ORDER BY id LIMIT :batch_size"
                    ),
                    {"model_name": model_name, "batch_size": BATCH_SIZE},
                )
                rows = result.mappings().all()
            if not rows:
                return updated_count

            embeddings = await asyncio.to_thread(embed_texts, [row["content"] for row in rows])
            updates = [
                {
                    "id": row["id"],
                    "embedding": "[" + ",".join(str(value) for value in embedding) + "]",
                    "model_name": model_name,
                }
                for row, embedding in zip(rows, embeddings)
            ]
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_chunks SET embedding = CAST(:embedding AS vector), "
                        "embedding_model = :model_name WHERE id = :id"
                    ),
                    updates,
                )
            updated_count += len(updates)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed knowledge chunks using the configured semantic model.")
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--confirm", action="store_true", help="Confirm that the target database may be updated.")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("Pass --confirm to update knowledge vectors in the target database.")
    updated = asyncio.run(reindex(args.database_url))
    print(f"Re-embedded {updated} knowledge chunks with {embedding_model_name()}.")


if __name__ == "__main__":
    main()