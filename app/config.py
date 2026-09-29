"""Application settings. All secrets come from the environment (see .env.example)."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-insecure-secret-change-me"  # noqa: S105 - explicit dev placeholder, rejected in production


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ER_", extra="ignore")

    environment: str = Field("development", description="development | test | production")
    database_url: str = "sqlite:///./data/app.db"
    data_dir: Path = Path("./data")
    secret_key: str = _DEV_SECRET
    # Fernet key (urlsafe base64, 32 bytes). Generate: python -m app.cli gen-key
    storage_encryption_key: str | None = None

    max_upload_bytes: int = 25 * 1024 * 1024
    session_max_age_seconds: int = 8 * 3600
    login_max_attempts: int = 5
    login_lockout_seconds: int = 15 * 60

    # Commercial defaults (prefill only — each client stores its own agreed percentage)
    default_success_fee_percentage: Decimal | None = None
    default_retention_days: int = 365

    # Detection tolerances (EUR / units). Rounding on invoices is per line, so allow cents.
    line_amount_tolerance: Decimal = Decimal("0.02")
    total_tolerance: Decimal = Decimal("0.05")
    unit_price_tolerance: Decimal = Decimal("0.000005")
    min_potential_recovery: Decimal = Decimal("0.50")
    consumption_spike_ratio: Decimal = Decimal("2.0")
    consumption_drop_ratio: Decimal = Decimal("0.4")
    min_history_periods: int = 3
    period_boundary_tolerance_days: int = 1
    extraction_review_threshold: float = 0.9

    # AI is OFF by default. Enabling globally still requires per-client consent.
    ai_enabled: bool = False
    ai_provider: str = "none"
    anthropic_api_key: str | None = None

    # Optional outbound webhook for lead/CRM integration (POST JSON, no invoice data)
    lead_webhook_url: str | None = None

    operator_name: str = "Energiefactuur Controle"
    operator_email: str = "info@example.nl"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _check_production_secrets(self) -> "Settings":
        if self.is_production:
            if self.secret_key == _DEV_SECRET or len(self.secret_key) < 32:
                raise ValueError("ER_SECRET_KEY must be set to a strong value in production")
            if not self.storage_encryption_key:
                raise ValueError("ER_STORAGE_ENCRYPTION_KEY is required in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("Use PostgreSQL in production (ER_DATABASE_URL)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
