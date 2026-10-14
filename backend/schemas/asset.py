import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.config import settings

from schemas.failure_modes import FailureMode
from typing import Literal


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plant_id: uuid.UUID
    plant_code: str = ""
    plant_name: str = ""
    name: str
    asset_tag: str
    asset_type: str
    location: str
    status: str
    alert_active: bool = False
    alert_started_at: datetime | None = None
    alert_acknowledged_at: datetime | None = None
    alert_acknowledged_by: str | None = None
    alert_normal_readings: int = 0
    alert_last_reading_at: datetime | None = None
    criticality: str
    vibration_threshold_mm_s: float = 7.1
    temperature_threshold_c: float = 85.0
    vibration_threshold_source: str = "demo"
    temperature_threshold_source: str = "demo"
    created_at: datetime
    latest_vibration_mm_s: float | None = None
    latest_temperature_c: float | None = None

    @field_validator("vibration_threshold_mm_s", mode="before")
    @classmethod
    def use_default_vibration_threshold(cls, value: object) -> object:
        return settings.default_vibration_threshold_mm_s if value is None else value

    @field_validator("temperature_threshold_c", mode="before")
    @classmethod
    def use_default_temperature_threshold(cls, value: object) -> object:
        return settings.default_temperature_threshold_c if value is None else value


class AssetAlertAcknowledgementRead(BaseModel):
    asset_id: uuid.UUID
    alert_active: bool
    alert_acknowledged_at: datetime
    alert_normal_readings: int
    recovery_readings_required: int
    recovery_max_gap_seconds: int
    status: Literal["acknowledged", "cleared"]


class PlantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    location: str
    region: str
    asset_count: int = 0
    attention_count: int = 0
    pending_work_order_count: int = 0


class AssetThresholdUpdate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    vibration_threshold_mm_s: float | None = Field(ge=0, le=500)
    temperature_threshold_c: float | None = Field(ge=-50, le=500)
    reason: str = Field(min_length=5, max_length=1000)


class AssetThresholdChangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_id: uuid.UUID
    changed_by_sub: str | None
    vibration_old_mm_s: float | None
    vibration_new_mm_s: float | None
    temperature_old_c: float | None
    temperature_new_c: float | None
    reason: str
    created_at: datetime


class AssetHistoryEventRead(BaseModel):
    work_order_id: uuid.UUID | None = None
    created_at: datetime
    event_type: str
    title: str
    detail: str | None = None
    actor: str | None = None


class TelemetryIngest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    asset_id: uuid.UUID
    timestamp: datetime | None = None
    vibration_mm_s: float = Field(ge=0, le=500)
    temperature_c: float = Field(ge=-50, le=500)
    pressure_bar: float = Field(ge=0, le=2000)
    rpm: float = Field(ge=0, le=100000)
    source: str = Field(default="external", min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.:-]+$")
    source_event_id: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Telemetry timestamps must include a timezone.")
        if value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("Telemetry timestamp is more than five minutes in the future.")
        return value


class TelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readings: list[TelemetryIngest] = Field(min_length=1, max_length=500)
