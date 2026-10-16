import asyncio
import json
import uuid
from typing import Any, Iterable

from sqlalchemy import text
from prometheus_client import Counter

from core.database import engine
from services.embedding_service import embed_texts, embedding_model_name, rerank_documents, reranker_model_name

RAG_RETRIEVAL_REQUESTS = Counter(
    "rag_retrieval_requests_total",
    "Knowledge retrieval requests by outcome.",
    ("outcome",),
)


async def initialize_knowledge_store() -> None:
    async with engine.connect() as connection:
        count = await connection.scalar(text("SELECT count(*) FROM knowledge_chunks"))
    if count:
        return
    documents = [
        "Elevated pump vibration: inspect bearing wear, coupling alignment, looseness, and lubrication. Capture a vibration spectrum before component replacement.",
        "High motor temperature: verify with a calibrated instrument, inspect airflow and cooling, review load, and check bearing friction.",
        "Critical equipment alert: follow the site's emergency response and lockout-tagout procedures. Do not bypass guards or interlocks.",
        "Pressure deviations: validate transmitter calibration, inspect suction restrictions and leaks, and compare against the equipment operating envelope.",
    ]
    embeddings = await asyncio.to_thread(embed_texts, documents)
    async with engine.begin() as connection:
        for document, embedding in zip(documents, embeddings):
            vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
            await connection.execute(
                text("INSERT INTO knowledge_chunks (id, content, embedding, embedding_model) VALUES (:id, :content, CAST(:embedding AS vector), :embedding_model)"),
                {"id": uuid.uuid4(), "content": document, "embedding": vector_literal, "embedding_model": embedding_model_name()},
            )


async def retrieve_documents(
    query: str,
    limit: int = 3,
    *,
    allowed_roles: Iterable[str] | None = None,
    allowed_subject: str | None = None,
    allowed_groups: Iterable[str] | None = None,
    plant_id: uuid.UUID | None = None,
    allowed_plant_ids: Iterable[uuid.UUID] | None = None,
    use_reranker: bool | None = None,
) -> list[dict[str, Any]]:
    try:
        embedding = (await asyncio.to_thread(embed_texts, [query]))[0]
        vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
        roles = sorted(set(allowed_roles or []))
        access_filter = ""
        plant_filter = ""
        result_limit = max(1, min(limit, 20))
        candidate_limit = min(100, max(20, result_limit * 4))
        parameters: dict[str, object] = {
            "embedding": vector_literal,
            "embedding_model": embedding_model_name(),
            "query": query,
            "candidate_limit": candidate_limit,
        }
        if roles:
            role_filters = []
            for index, role in enumerate(roles):
                bind_name = f"allowed_role_{index}"
                role_filters.append(f"d.allowed_roles @> CAST(:{bind_name} AS jsonb)")
                parameters[bind_name] = json.dumps([role])
            access_filter = "AND (k.document_id IS NULL OR (" + " OR ".join(role_filters) + "))"
        else:
            access_filter = "AND k.document_id IS NULL"
        if roles:
            identity_filters = [
                "(COALESCE(d.allowed_subjects, '[]'::jsonb) = '[]'::jsonb "
                "AND COALESCE(d.allowed_groups, '[]'::jsonb) = '[]'::jsonb)"
            ]
            if allowed_subject:
                identity_filters.append("d.allowed_subjects @> CAST(:allowed_subject AS jsonb)")
                parameters["allowed_subject"] = json.dumps([allowed_subject])
            for index, group in enumerate(sorted(set(allowed_groups or []))):
                bind_name = f"allowed_group_{index}"
                identity_filters.append(f"d.allowed_groups @> CAST(:{bind_name} AS jsonb)")
                parameters[bind_name] = json.dumps([group])
            access_filter = (
                "AND (k.document_id IS NULL OR ((" + " OR ".join(role_filters) + ") AND ("
                + " OR ".join(identity_filters)
                + ")))"
            )
        if plant_id is not None:
            plant_filter = "AND (k.document_id IS NULL OR d.plant_id IS NULL OR d.plant_id = :plant_id)"
            parameters["plant_id"] = plant_id
        elif allowed_plant_ids is not None:
            allowed_plants = sorted(set(allowed_plant_ids), key=str)
            if not allowed_plants:
                plant_filter = "AND k.document_id IS NULL"
            else:
                plant_names = []
                for index, allowed_plant_id in enumerate(allowed_plants):
                    bind_name = f"allowed_plant_{index}"
                    plant_names.append(f":{bind_name}")
                    parameters[bind_name] = allowed_plant_id
                plant_filter = (
                    "AND (k.document_id IS NULL OR d.plant_id IS NULL OR d.plant_id IN ("
                    + ", ".join(plant_names)
                    + "))"
                )
        eligibility = (
            "FROM knowledge_chunks AS k LEFT JOIN knowledge_documents AS d ON d.id = k.document_id "
            "WHERE (k.document_id IS NULL OR d.ingestion_status = 'indexed') "
            f"{access_filter} {plant_filter} "
        )
        columns = (
            "k.id AS chunk_id, k.document_id, k.content, k.page_number, k.metadata, "
            "d.filename, d.document_type, d.manufacturer, d.asset_type, d.revision, d.source_uri"
        )
        async with engine.connect() as connection:
            vector_result = await connection.execute(
                text(
                    f"SELECT {columns} {eligibility} "
                    "AND k.embedding_model = :embedding_model "
                    "ORDER BY k.embedding <=> CAST(:embedding AS vector) LIMIT :candidate_limit"
                ),
                parameters,
            )
            keyword_result = await connection.execute(
                text(
                    f"SELECT {columns} {eligibility} "
                    "AND to_tsvector('simple', k.content) @@ websearch_to_tsquery('simple', :query) "
                    "ORDER BY ts_rank_cd(to_tsvector('simple', k.content), "
                    "websearch_to_tsquery('simple', :query)) DESC LIMIT :candidate_limit"
                ),
                parameters,
            )
            vector_rows = vector_result.mappings().all()
            keyword_rows = keyword_result.mappings().all()
        rows = _reciprocal_rank_fusion(vector_rows, keyword_rows, candidate_limit)
        documents = [_format_retrieved_document(row) for row in rows]
        should_rerank = bool(reranker_model_name()) if use_reranker is None else use_reranker
        if should_rerank:
            documents = await asyncio.to_thread(rerank_documents, query, documents)
        documents = documents[:result_limit]
    except Exception:
        RAG_RETRIEVAL_REQUESTS.labels("error").inc()
        raise
    RAG_RETRIEVAL_REQUESTS.labels("hit" if documents else "no_match").inc()
    return documents


def _reciprocal_rank_fusion(
    vector_rows: Iterable[Any],
    keyword_rows: Iterable[Any],
    limit: int,
) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    rows_by_id: dict[str, dict[str, Any]] = {}
    for ranked_rows in (vector_rows, keyword_rows):
        for rank, row in enumerate(ranked_rows, start=1):
            document = dict(row)
            chunk_id = str(document["chunk_id"])
            rows_by_id.setdefault(chunk_id, document)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
    ordered_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return [rows_by_id[chunk_id] for chunk_id in ordered_ids[:limit]]


def _format_retrieved_document(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row["metadata"] or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return {
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
    }


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
