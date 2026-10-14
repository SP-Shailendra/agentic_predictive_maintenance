import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScenarioName = Literal[
    "vibration_rise",
    "overheating",
    "sudden_spike",
    "data_dropout",
    "invalid_reading",
    "sensor_flatline",
    "healthy_baseline",
]


class ScenarioStart(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    asset_id: uuid.UUID
    scenario: ScenarioName
    duration_readings: int = Field(ge=1, le=120)
    severity: float = Field(ge=0, le=1)
    force_llm_fallback: bool = False


class ScenarioRead(BaseModel):
    asset_id: uuid.UUID
    scenario: ScenarioName
    severity: float
    duration_readings: int
    remaining_readings: int
    active: bool
    force_llm_fallback: bool
    started_at: datetime
    recent_until: datetime