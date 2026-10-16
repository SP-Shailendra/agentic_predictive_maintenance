from collections.abc import Sequence
from math import log2
from typing import Any


def score_retrieval_case(
    relevant_phrases: Sequence[str],
    ranked_contents: Sequence[str],
    *,
    k: int = 5,
) -> dict[str, float | int]:
    if not relevant_phrases:
        raise ValueError("Each evaluation case must identify at least one relevant source phrase.")
    if k < 1:
        raise ValueError("k must be at least one.")

    ranked = [content.casefold() for content in ranked_contents[:k]]
    relevant_positions = [
        position
        for phrase in relevant_phrases
        if (position := next((index for index, content in enumerate(ranked, start=1) if phrase.casefold() in content), None)) is not None
    ]
    first_rank = min(relevant_positions) if relevant_positions else None
    relevant_set = {phrase.casefold() for phrase in relevant_phrases}
    gains = [
        1.0 if any(phrase in content for phrase in relevant_set) else 0.0
        for content in ranked
    ]
    dcg = sum(gain / log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_relevant = min(len(relevant_set), k)
    ideal_dcg = sum(1.0 / log2(rank + 1) for rank in range(1, ideal_relevant + 1))
    return {
        "recall_at_k": len(relevant_positions) / len(relevant_phrases),
        "mrr_at_k": 1 / first_rank if first_rank is not None else 0.0,
        "ndcg_at_k": dcg / ideal_dcg if ideal_dcg else 0.0,
        "relevant_sources": len(relevant_positions),
        "expected_sources": len(relevant_phrases),
        "first_relevant_rank": first_rank or 0,
    }


def score_retrieval_suite(
    cases: Sequence[dict[str, Any]],
    ranked_contents: Sequence[Sequence[str]],
    *,
    k: int = 5,
) -> dict[str, float | int]:
    if len(cases) != len(ranked_contents):
        raise ValueError("Each evaluation case needs one ranked retrieval result.")
    if not cases:
        raise ValueError("Evaluation suite must contain at least one case.")
    results = [
        score_retrieval_case(case["relevant_phrases"], contents, k=k)
        for case, contents in zip(cases, ranked_contents)
    ]
    return {
        "case_count": len(results),
        "k": k,
        "mean_recall_at_k": sum(result["recall_at_k"] for result in results) / len(results),
        "mean_mrr_at_k": sum(result["mrr_at_k"] for result in results) / len(results),
        "mean_ndcg_at_k": sum(result["ndcg_at_k"] for result in results) / len(results),
    }