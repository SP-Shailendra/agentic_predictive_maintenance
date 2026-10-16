import argparse
import asyncio
import json
import os
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from services import rag_service
from services.rag_evaluation import score_retrieval_case, score_retrieval_suite

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rag_retrieval_eval.json"


async def run_evaluation(
    database_url: str,
    k: int,
    cases_path: Path = FIXTURE_PATH,
    compare_reranker: bool = False,
) -> dict[str, object]:
    database_name = (make_url(database_url).database or "").lower()
    if "test" not in database_name and "eval" not in database_name:
        raise ValueError("Refusing RAG evaluation: database name must contain 'test' or 'eval'.")

    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or any(
        not isinstance(case, dict)
        or not isinstance(case.get("query"), str)
        or not isinstance(case.get("relevant_phrases"), list)
        or not case["relevant_phrases"]
        for case in cases
    ):
        raise ValueError("Evaluation cases must be a JSON list with query and non-empty relevant_phrases fields.")
    reranker_model = rag_service.reranker_model_name()
    if compare_reranker and not reranker_model:
        raise ValueError("Set RAG_RERANKER_MODEL_NAME before comparing cross-encoder reranking.")
    evaluation_engine = create_async_engine(database_url, pool_pre_ping=True)
    previous_engine = rag_service.engine
    rag_service.engine = evaluation_engine
    try:
        baseline_results = []
        selected_results = []
        case_results = []
        for case in cases:
            shared_args = {
                "limit": max(k, 10),
                "allowed_roles": {"manager", "reliability_engineer", "operator", "approver", "technician"},
            }
            baseline = await rag_service.retrieve_documents(
                case["query"],
                **shared_args,
                use_reranker=False,
            )
            baseline_contents = [item["content"] for item in baseline]
            baseline_results.append(baseline_contents)
            if compare_reranker:
                selected = await rag_service.retrieve_documents(
                    case["query"], **shared_args, use_reranker=True,
                )
            elif reranker_model:
                selected = await rag_service.retrieve_documents(case["query"], **shared_args)
            else:
                selected = baseline
            contents = [item["content"] for item in selected]
            selected_results.append(contents)
            case_results.append({
                "id": case["id"],
                **score_retrieval_case(case["relevant_phrases"], contents, k=k),
            })
        result = {
            "embedding_model": rag_service.embedding_model_name(),
            "reranker_model": reranker_model if reranker_model else None,
            "summary": score_retrieval_suite(cases, selected_results, k=k),
            "baseline_summary": score_retrieval_suite(cases, baseline_results, k=k),
            "cases": case_results,
        }
        if compare_reranker:
            baseline = result["baseline_summary"]
            reranked = result["summary"]
            result["reranker_comparison"] = {
                metric: reranked[metric] - baseline[metric]
                for metric in ("mean_recall_at_k", "mean_mrr_at_k", "mean_ndcg_at_k")
            }
        return result
    finally:
        rag_service.engine = previous_engine
        await evaluation_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure RAG retrieval Recall@K and MRR on a disposable evaluation database.")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--cases", type=Path, default=FIXTURE_PATH, help="Path to a labeled JSON query set.")
    parser.add_argument("--compare-reranker", action="store_true", help="Compare baseline RRF to the configured cross-encoder.")
    args = parser.parse_args()
    if args.k < 1:
        parser.error("--k must be at least 1")
    database_url = os.getenv("RAG_EVAL_DATABASE_URL")
    if not database_url:
        parser.error("Set RAG_EVAL_DATABASE_URL to an initialized disposable database named with 'test' or 'eval'.")
    result = asyncio.run(run_evaluation(database_url, args.k, args.cases, args.compare_reranker))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()