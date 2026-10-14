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


async def run_evaluation(database_url: str, k: int) -> dict[str, object]:
    database_name = (make_url(database_url).database or "").lower()
    if "test" not in database_name and "eval" not in database_name:
        raise ValueError("Refusing RAG evaluation: database name must contain 'test' or 'eval'.")

    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    evaluation_engine = create_async_engine(database_url, pool_pre_ping=True)
    previous_engine = rag_service.engine
    rag_service.engine = evaluation_engine
    try:
        ranked_results = []
        case_results = []
        for case in cases:
            retrieved = await rag_service.retrieve_documents(
                case["query"],
                limit=max(k, 10),
                allowed_roles={"manager", "reliability_engineer", "operator", "approver", "technician"},
            )
            contents = [item["content"] for item in retrieved]
            ranked_results.append(contents)
            case_results.append({
                "id": case["id"],
                **score_retrieval_case(case["relevant_phrases"], contents, k=k),
            })
        return {"summary": score_retrieval_suite(cases, ranked_results, k=k), "cases": case_results}
    finally:
        rag_service.engine = previous_engine
        await evaluation_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure RAG retrieval Recall@K and MRR on a disposable evaluation database.")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    if args.k < 1:
        parser.error("--k must be at least 1")
    database_url = os.getenv("RAG_EVAL_DATABASE_URL")
    if not database_url:
        parser.error("Set RAG_EVAL_DATABASE_URL to an initialized disposable database named with 'test' or 'eval'.")
    result = asyncio.run(run_evaluation(database_url, args.k))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()