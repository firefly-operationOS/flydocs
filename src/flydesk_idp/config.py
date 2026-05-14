# Copyright 2026 Firefly Software Solutions Inc
"""Runtime settings for flydesk-idp.

Settings are loaded from the environment under the ``FLYDESK_IDP_`` prefix
(see :doc:`env_template`). The same settings instance is shared across
the FastAPI process and the worker process so the two paths behave
identically.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IDPSettings(BaseSettings):
    """All knobs that affect runtime behaviour."""

    model_config = SettingsConfigDict(
        env_prefix="FLYDESK_IDP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Service --------------------------------------------------------
    log_level: str = "INFO"
    port: int = 8400

    # -- Persistence ----------------------------------------------------
    database_url: str = "postgresql+asyncpg://idp:idp@localhost:5432/flydesk_idp"

    # -- Queue / EDA ----------------------------------------------------
    eda_adapter: str = Field(default="memory", description="memory | redis | kafka | rabbitmq")
    redis_url: str = "redis://localhost:6379/0"
    jobs_topic: str = "flydesk.idp.jobs"
    jobs_event_type: str = "IDPJobSubmitted"
    jobs_completed_event_type: str = "IDPJobCompleted"

    # -- Extraction -----------------------------------------------------
    model: str = "anthropic:claude-sonnet-4-6"
    fallback_model: str | None = "openai:gpt-4o"
    # Page count threshold above which the sync path returns 413 and asks the
    # caller to use the async API. The LLM sees the document directly so we
    # can no longer enforce DPI here.
    max_sync_pages: int = 10
    max_bytes: int = 32 * 1024 * 1024  # 32 MiB
    sync_timeout_s: int = 60
    async_timeout_s: int = 300
    job_max_attempts: int = 3

    # -- Webhook --------------------------------------------------------
    webhook_timeout_s: int = 15
    webhook_max_attempts: int = 5
    webhook_hmac_secret: str | None = None

    # -- Security -------------------------------------------------------
    api_keys: str | None = Field(
        default=None,
        description="Comma-separated list of static API keys that grant access. None = unauthenticated.",
    )

    @property
    def api_key_set(self) -> set[str]:
        if not self.api_keys:
            return set()
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


@lru_cache(maxsize=1)
def get_settings() -> IDPSettings:
    """Cached settings accessor.

    Tests reset it with ``get_settings.cache_clear()`` after monkey-patching env.
    """
    return IDPSettings()
