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
    sandbox_output_limit: int = 20000

    github_api_url: str = "https://api.github.com"
    github_token: str = ""

    llm_base_url: str = "https://opencode.ai/zen/go/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_session: str = ""
    llm_timeout_s: float = 180.0

    pricing_file: str = "config/pricing.yaml"
    # Costs stay zero until pricing lands; the label keeps such rows out of any
    # cross-version comparison instead of pretending they were priced.
    pricing_version: str = "unpriced"


settings = Settings()
