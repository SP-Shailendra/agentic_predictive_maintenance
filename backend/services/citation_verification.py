import json
import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, ValidationError

from core.config import settings

logger = logging.getLogger(__name__)


class CitationCheck(BaseModel):
    claim: str
    verdict: Literal["supported", "unsupported", "uncertain"]
    chunk_ids: list[str]


class CitationCheckResponse(BaseModel):
    checks: list[CitationCheck]


def validate_citation_checks(
    claims: list[str],
    checks: list[CitationCheck],
    retrieved_chunk_ids: set[str],
) -> tuple[str, list[dict[str, object]]]:
    if len(checks) != len(claims):
        return "invalid", []
    checks_by_claim = {check.claim.strip().casefold(): check for check in checks}
    if len(checks_by_claim) != len(checks):
        return "invalid", []
    normalized = []
    for claim in claims:
        check = checks_by_claim.get(claim.strip().casefold())
        if check is None:
            return "invalid", []
        valid_chunk_ids = sorted(set(check.chunk_ids) & retrieved_chunk_ids)
        verdict = check.verdict
        if not valid_chunk_ids and verdict == "supported":
            verdict = "unsupported"
        normalized.append({
            "claim": claim,
            "verdict": verdict,
            "chunk_ids": valid_chunk_ids,
        })
    status = "model_supported" if all(item["verdict"] == "supported" for item in normalized) else "issues"
    return status, normalized


async def verify_claims_with_sources(
    claims: list[str],
    retrieved_chunks: list[dict[str, object]],
) -> tuple[str, list[dict[str, object]]]:
    claims = [claim.strip() for claim in claims if claim.strip()]
    retrieved_chunk_ids = {str(chunk["chunk_id"]) for chunk in retrieved_chunks}
    if not claims or not retrieved_chunk_ids or not settings.groq_api_key:
        return "unavailable", []

    evidence = [
        {"chunk_id": str(chunk["chunk_id"]), "content": str(chunk["content"])}
        for chunk in retrieved_chunks
    ]
    model = ChatGroq(
        model=settings.groq_model,
        temperature=0,
        api_key=settings.groq_api_key,
    )
    messages = [
        SystemMessage(content=(
            "Verify whether each provided maintenance claim is directly supported by the supplied evidence passages. "
            "Use only the passages; do not rely on external knowledge. Mark a claim supported only when the passage entails it, "
            "unsupported when contradicted or absent, and uncertain when evidence is ambiguous. Return only JSON with a checks array; "
            "each item must include claim, verdict (supported/unsupported/uncertain), and chunk_ids from the supplied passages."
        )),
        HumanMessage(content=json.dumps({"claims": claims, "evidence": evidence})),
    ]
    try:
        response = await model.ainvoke(messages)
        content = response.content
        if not isinstance(content, str):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Citation verifier did not return a JSON object")
        result = CitationCheckResponse.model_validate_json(content[start:end + 1])
        return validate_citation_checks(claims, result.checks, retrieved_chunk_ids)
    except (ValidationError, ValueError, TypeError) as error:
        logger.warning("Citation verification output was invalid (%s)", type(error).__name__)
        return "unavailable", []
    except Exception as error:
        logger.warning("Citation verification failed (%s)", type(error).__name__)
        return "unavailable", []
