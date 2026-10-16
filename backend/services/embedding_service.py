import threading
from collections.abc import Sequence

from core.config import settings

EMBEDDING_DIMENSIONS = 384

_model = None
_model_lock = threading.Lock()
_reranker = None
_reranker_lock = threading.Lock()


def embedding_model_name() -> str:
    return settings.embedding_model_name


def embed_texts(contents: Sequence[str]) -> list[list[float]]:
    if not contents:
        return []
    model = _get_model()
    embeddings = [list(map(float, vector)) for vector in model.embed(list(contents))]
    if len(embeddings) != len(contents):
        raise RuntimeError("The embedding model returned an unexpected number of vectors.")
    if any(len(vector) != EMBEDDING_DIMENSIONS for vector in embeddings):
        raise RuntimeError(f"The embedding model must return {EMBEDDING_DIMENSIONS}-dimensional vectors.")
    return embeddings


def reranker_model_name() -> str | None:
    return settings.rag_reranker_model_name


def rerank_documents(query: str, documents: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    if not documents:
        return []
    model = _get_reranker()
    scores = list(model.rerank(query, [str(document["content"]) for document in documents]))
    if len(scores) != len(documents):
        raise RuntimeError("The reranker returned an unexpected number of scores.")
    return [
        document
        for _, document in sorted(
            zip(scores, documents),
            key=lambda scored: float(scored[0]),
            reverse=True,
        )
    ]


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from fastembed import TextEmbedding

                _model = TextEmbedding(model_name=settings.embedding_model_name)
    return _model


def _get_reranker():
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                if not settings.rag_reranker_model_name:
                    raise RuntimeError("Set RAG_RERANKER_MODEL_NAME to enable cross-encoder reranking.")
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                _reranker = TextCrossEncoder(model_name=settings.rag_reranker_model_name)
    return _reranker