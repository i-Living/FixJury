from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Every field is overridable as FIXJURY_<FIELD>."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="FIXJURY_", extra="ignore")

    database_url: str = "postgresql+asyncpg://fixjury:fixjury@127.0.0.1:55432/fixjury"
    redis_url: str = "redis://127.0.0.1:16379/0"
    log_level: str = "INFO"

    sandbox_image: str = "python:3.12-slim"
    sandbox_cpu: float = 2.0
    sandbox_memory_mb: int = 2048
    sandbox_timeout_s: int = 1200

    pricing_file: str = "config/pricing.yaml"


settings = Settings()
