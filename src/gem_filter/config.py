from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "staging", "prod", "test"] = "dev"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://gem_filter:gem_filter@postgres:5432/gem_filter"
    auto_create_schema: bool = False

    internal_api_token: SecretStr = SecretStr("change-me-internal")
    auth_signing_secret: SecretStr = SecretStr("change-me-auth-signing-secret")
    bootstrap_admin_email: str = "admin@example.invalid"
    bootstrap_admin_password: SecretStr = SecretStr("change-me-now")

    telegram_source_mode: Literal["mock", "bot_api", "mtproto"] = "mock"
    telegram_source_ids: str = ""
    telegram_bot_token: SecretStr | None = None
    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
    telegram_session_path: str = "/run/secrets/telegram_reader.session"
    ingestion_api_url: str = "http://api:8080/api/v1/ingestion/telegram"
    reader_poll_seconds: float = 1.0

    alert_bot_token: SecretStr | None = None
    alert_destination_chat_ids: str = ""
    watch_alerts_enabled: bool = False
    alerts_kill_switch: bool = False
    alert_rate_per_minute: int = 20
    public_base_url: str = "http://localhost:8080"

    market_provider_base_url: str = "https://api.dexscreener.com"
    market_fallback_base_url: str | None = None
    market_provider_timeout_seconds: float = 5.0
    market_max_snapshot_age_seconds: int = 20
    allowed_quote_symbols: str = "SOL,USDC,USDT"

    helius_webhook_secret: SecretStr | None = None
    webhook_max_skew_seconds: int = 300
    active_rule_version: str = "GEM_FILTER_V2_FLOW_8"

    @field_validator(
        "telegram_api_id",
        "telegram_bot_token",
        "telegram_api_hash",
        "alert_bot_token",
        "market_fallback_base_url",
        "helius_webhook_secret",
        mode="before",
    )
    @classmethod
    def blank_optional_values_are_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def async_postgres_only(cls, value: str) -> str:
        if not value.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
            raise ValueError("DATABASE_URL must use asyncpg (or aiosqlite in tests)")
        return value

    @property
    def source_id_set(self) -> set[int]:
        return {
            int(value.strip()) for value in self.telegram_source_ids.split(",") if value.strip()
        }

    @property
    def alert_chat_ids(self) -> tuple[str, ...]:
        return tuple(
            value.strip() for value in self.alert_destination_chat_ids.split(",") if value.strip()
        )

    @property
    def allowed_quotes(self) -> set[str]:
        return {
            value.strip().upper()
            for value in self.allowed_quote_symbols.split(",")
            if value.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
