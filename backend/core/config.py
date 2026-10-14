from functools import lru_cache
import os
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        secrets_dir=os.getenv("FIELDNOTE_SECRETS_DIR"),
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Agentic Predictive Maintenance"
    environment: str = Field(default="development", min_length=1)
    database_url: str = "postgresql+asyncpg://maintenance:change-me@localhost:5432/maintenance"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    api_key: str | None = None
    metrics_api_key: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_roles_claim: str = "roles"
    cmms_api_url: str | None = None
    cmms_api_token: str | None = None
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://localhost:4173"]
    simulator_enabled: bool = True
    simulator_interval_seconds: float = Field(default=5.0, gt=0)
    rate_limit_requests_per_minute: int = Field(default=120, ge=1, le=10000)
    telemetry_retention_days: int = Field(default=0, ge=0, le=36500)
    default_vibration_threshold_mm_s: float = Field(default=7.1, ge=0, le=500)
    default_temperature_threshold_c: float = Field(default=85.0, ge=-50, le=500)
    knowledge_max_upload_mb: int = Field(default=10, ge=1, le=100)
    approval_stale_after_minutes: int = Field(default=60, ge=1, le=10080)
    approval_sla_minutes: int = Field(default=60, ge=1, le=10080)
    notification_email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_from: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    notification_webhook_url: str | None = None
    notification_webhook_format: Literal["slack", "teams"] = "slack"
    frontend_base_url: str = "http://localhost:4173"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        email_fields = (self.notification_email_to, self.smtp_host, self.smtp_from)
        if any(email_fields) and not all(email_fields):
            raise ValueError("Email notifications require NOTIFICATION_EMAIL_TO, SMTP_HOST, and SMTP_FROM.")
        if bool(self.smtp_username) != bool(self.smtp_password):
            raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be configured together.")
        if self.notification_webhook_url and not self.notification_webhook_url.startswith("https://"):
            raise ValueError("Notification webhook URLs must use HTTPS.")
        if self.environment.lower() not in {"production", "prod"}:
            return self

        if not self.oidc_issuer or not self.oidc_audience or not self.oidc_jwks_url:
            raise ValueError("Production requires OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL.")
        if not self.oidc_issuer.startswith("https://") or not self.oidc_jwks_url.startswith("https://"):
            raise ValueError("Production OIDC issuer and JWKS URLs must use HTTPS.")
        if self.api_key and len(self.api_key) < 32:
            raise ValueError("A configured service API_KEY must contain at least 32 characters.")
        if not self.metrics_api_key or len(self.metrics_api_key) < 32:
            raise ValueError("Production requires a METRICS_API_KEY of at least 32 characters.")
        if self.simulator_enabled:
            raise ValueError("The telemetry simulator must be disabled in production.")
        if not self.cors_origins or any(not origin.startswith("https://") for origin in self.cors_origins):
            raise ValueError("Production CORS origins must use HTTPS.")
        if not self.cmms_api_url or not self.cmms_api_url.startswith("https://"):
            raise ValueError("Production requires an HTTPS CMMS_API_URL.")
        if not self.cmms_api_token:
            raise ValueError("Production requires a CMMS_API_TOKEN.")
        database_password = make_url(self.database_url).password
        if not database_password or database_password.lower() in {"change-me", "password", "postgres"}:
            raise ValueError("Production requires a non-default database password.")
        return self


@lru_cache

def get_settings() -> Settings:
    return Settings()


settings = get_settings()
