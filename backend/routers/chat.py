import asyncio
from collections import deque
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import Principal, require_api_key, require_roles, WORKFLOW_READ_ROLES
from schemas.chat import ChatRequest, ChatResponse
from services.chat_service import answer_chat

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_api_key)])
CHAT_REQUESTS_PER_MINUTE = 20
CHAT_WINDOW_SECONDS = 60.0
_chat_buckets: dict[str, deque[float]] = {}
_chat_lock = asyncio.Lock()


async def _check_chat_limit(request: Request) -> None:
    client_id = request.client.host if request.client else "unknown"
    now = monotonic()
    async with _chat_lock:
        bucket = _chat_buckets.setdefault(client_id, deque())
        while bucket and now - bucket[0] >= CHAT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= CHAT_REQUESTS_PER_MINUTE:
            retry_after = max(1, int(CHAT_WINDOW_SECONDS - (now - bucket[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Chat request rate limit exceeded.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        if len(_chat_buckets) > 5000:
            for stale_id, stale_bucket in list(_chat_buckets.items())[:1000]:
                while stale_bucket and now - stale_bucket[0] >= CHAT_WINDOW_SECONDS:
                    stale_bucket.popleft()
                if stale_id != client_id and not stale_bucket:
                    _chat_buckets.pop(stale_id, None)


@router.post("", response_model=ChatResponse)
async def ask_fieldnote(
    payload: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles(*WORKFLOW_READ_ROLES)),
) -> dict[str, object]:
    await _check_chat_limit(request)
    return await answer_chat(payload, db, principal)