import hashlib
import json
import re
import uuid
from typing import Any, Iterable

from sqlalchemy import text
from prometheus_client import Counter

from core.database import engine

EMBEDDING_DIMENSIONS = 384
RAG_RETRIEVAL_REQUESTS = Counter(
    "rag_retrieval_requests_total",
    "Knowledge retrieval requests by outcome.",
    ("outcome",),
)


def _hash_embedding(content: str) -> list[float]:
    """Small deterministic local vectorizer for demo retrieval, not a semantic model."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    tokens = re.findall(r"[a-z0-9]+", content.lower())
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % EMBEDDING_DIMENSIONS
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = sum(value * value for value in vector) ** 0.5
    return [value / norm for value in vector] if norm else vector


async def initialize_knowledge_store() -> None:
    async with engine.begin() as connection:
        count = await connection.scalar(text("SELECT count(*) FROM knowledge_chunks"))
        if count:
            return
        documents = [
            "Elevated pump vibration: inspect bearing wear, coupling alignment, looseness, and lubrication. Capture a vibration spectrum before component replacement.",
            "High motor temperature: verify with a calibrated instrument, inspect airflow and cooling, review load, and check bearing friction.",
            "Critical equipment alert: follow the site's emergency response and lockout-tagout procedures. Do not bypass guards or interlocks.",
            "Pressure deviations: validate transmitter calibration, inspect suction restrictions and leaks, and compare against the equipment operating envelope.",
        ]
        for document in documents:
            embedding = _hash_embedding(document)
            vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
            await connection.execute(
                text("INSERT INTO knowledge_chunks (id, content, embedding) VALUES (:id, :content, CAST(:embedding AS vector))"),
                {"id": uuid.uuid4(), "content": document, "embedding": vector_literal},
            )


async def retrieve_documents(
    query: str,
    limit: int = 3,
    *,
    allowed_roles: Iterable[str] | None = None,
    plant_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    try:
        embedding = _hash_embedding(query)
        vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
        roles = sorted(set(allowed_roles or []))
        access_filter = ""
        plant_filter = ""
        parameters: dict[str, object] = {"embedding": vector_literal, "limit": max(1, min(limit, 20))}
        if roles:
            role_filters = []
            for index, role in enumerate(roles):
                bind_name = f"allowed_role_{index}"
                role_filters.append(f"d.allowed_roles @> CAST(:{bind_name} AS jsonb)")
                parameters[bind_name] = json.dumps([role])
            access_filter = "AND (k.document_id IS NULL OR (" + " OR ".join(role_filters) + "))"
        else:
            access_filter = "AND k.document_id IS NULL"
        if plant_id is not None:
            plant_filter = "AND (k.document_id IS NULL OR d.plant_id IS NULL OR d.plant_id = :plant_id)"
            parameters["plant_id"] = plant_id
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT k.id AS chunk_id, k.document_id, k.content, k.page_number, k.metadata, "
                    "d.filename, d.document_type, d.manufacturer, d.asset_type, d.revision, d.source_uri "
                    "FROM knowledge_chunks AS k LEFT JOIN knowledge_documents AS d ON d.id = k.document_id "
                    "WHERE (k.document_id IS NULL OR d.ingestion_status = 'indexed') "
                    f"{access_filter} {plant_filter} "
                    "ORDER BY k.embedding <=> CAST(:embedding AS vector) LIMIT :limit"
                ),
                parameters,
            )
            rows = result.mappings().all()
        documents = []
        for row in rows:
            metadata = row["metadata"] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            documents.append({
                "chunk_id": str(row["chunk_id"]),
                "document_id": str(row["document_id"]) if row["document_id"] else None,
                "content": row["content"],
                "page_number": row["page_number"],
                "section_title": metadata.get("section_title"),
                "filename": row["filename"],
                "document_type": row["document_type"],
                "manufacturer": row["manufacturer"],
                "asset_type": row["asset_type"],
                "revision": row["revision"],
                "source_uri": row["source_uri"],
            })
    except Exception:
        RAG_RETRIEVAL_REQUESTS.labels("error").inc()
        raise
    RAG_RETRIEVAL_REQUESTS.labels("hit" if documents else "no_match").inc()
    return documents


def build_citation_refs(retrieved_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations = []
    for chunk in retrieved_chunks:
        citations.append({
            "chunk_id": str(chunk["chunk_id"]),
            "document_id": chunk.get("document_id"),
            "filename": chunk.get("filename") or "Legacy maintenance note",
            "document_type": chunk.get("document_type"),
            "revision": chunk.get("revision"),
            "page_number": chunk.get("page_number"),
            "section_title": chunk.get("section_title"),
            "source_uri": chunk.get("source_uri"),
        })
    return validate_citation_refs(citations, retrieved_chunks)


def validate_citation_refs(
    citations: list[dict[str, Any]],
    retrieved_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    retrieved_ids = {str(chunk["chunk_id"]) for chunk in retrieved_chunks}
    if any(str(citation.get("chunk_id")) not in retrieved_ids for citation in citations):
        raise ValueError("A RAG citation references a chunk that was not retrieved.")
    return citations


async def retrieve_similar(
    query: str,
    limit: int = 3,
    *,
    allowed_roles: Iterable[str] | None = None,
    plant_id: uuid.UUID | None = None,
) -> list[str]:
    rows = await retrieve_documents(query, limit, allowed_roles=allowed_roles, plant_id=plant_id)
    return [row["content"] for row in rows]
