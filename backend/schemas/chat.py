import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)
    plant_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)


class ChatSource(BaseModel):
    type: Literal["plant", "asset", "telemetry", "workflow", "event", "note"]
    id: str
    label: str
    timestamp: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource]
    knowledge_status: Literal["not_searched", "available", "no_matches", "unavailable"] = "not_searched"
    used_llm: bool
    truncated: bool = False
    answered_at: datetime