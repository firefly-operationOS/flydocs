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
    # The actual EventPublisher is built by pyfly's EdaAutoConfiguration
    # from ``pyfly.eda.*`` properties (see ``pyfly.yaml``). The value here
    # only drives ``${FLYDESK_IDP_EDA_ADAPTER}`` interpolation in that
    # file. Default ``postgres`` because the service already runs
    # Postgres for persistence — no extra broker is required.
    eda_adapter: str = Field(default="postgres", description="memory | postgres | redis | kafka")
    redis_url: str = "redis://localhost:6379/0"
    jobs_topic: str = "flydesk.idp.jobs"
    jobs_event_type: str = "IDPJobSubmitted"
    jobs_completed_event_type: str = "IDPJobCompleted"
    # Second-stage destination for the out-of-band bbox refiner. Triggered
    # by ``JobWorker`` after main extraction succeeds AND
    # ``options.stages.bbox_refine == true``. Consumed by
    # ``BboxRefineWorker``.
    bbox_refine_topic: str = "flydesk.idp.bbox.refine"
    bbox_refine_event_type: str = "IDPBboxRefineRequested"
    # Retry budget + timeout for the bbox refine leg, independent of the
    # main extraction. Refinement is CPU-bound (PyMuPDF / OCR) so the
    # default ceiling is generous.
    bbox_refine_max_attempts: int = 3
    bbox_refine_timeout_s: int = 600

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
    # Exponential backoff bounds between async job retries. The worker
    # schedules the next attempt at ``min(retry_max_delay_s,
    # retry_base_delay_s * 2^(attempt - 1))`` plus a small jitter.
    retry_base_delay_s: float = 5.0
    retry_max_delay_s: float = 300.0

    # -- Judge-driven escalation ----------------------------------------
    # When the judge marks more than ``escalation_threshold`` of the
    # extracted fields as FAIL or flag_for_review, the orchestrator
    # re-runs the extractor + judge with ``escalation_model`` and keeps
    # the result that has the lower failure rate. The stage is opt-in:
    # threshold <= 0 disables it and the orchestrator skips the re-run.
    escalation_threshold: float = 0.0
    escalation_model: str | None = None

    # -- Webhook --------------------------------------------------------
    webhook_timeout_s: int = 15
    webhook_max_attempts: int = 5
    webhook_hmac_secret: str | None = None

    # -- Binary normalization ------------------------------------------
    # The binary normalizer (``core/services/binary``) turns any caller-
    # supplied binary into one or more LLM-renderable inputs (PDF or
    # PNG/JPG/GIF/WebP). It expands archives + email attachments,
    # converts Office docs via headless LibreOffice, rasterises HEIC /
    # multi-frame TIFF / SVG via Pillow + cairosvg, and rejects
    # encrypted / corrupt PDFs with a typed error.
    binary_normalize_enabled: bool = Field(
        default=True,
        description=(
            "Master kill-switch for the binary normalizer. When False the "
            "loader passes raw bytes through as before — useful for debugging "
            "or for deployments that pre-normalise upstream."
        ),
    )
    binary_max_recursion_depth: int = Field(
        default=3,
        ge=0,
        description=(
            "Max nesting depth for archives / emails. A ZIP containing a ZIP "
            "containing a PDF is depth 3. Prevents zip-bomb style recursion."
        ),
    )
    binary_max_expanded_files: int = Field(
        default=50,
        ge=1,
        description="Hard cap on expanded files per inbound binary.",
    )
    office_converter: str = Field(
        default="gotenberg",
        description=(
            "Adapter used by the binary normalizer for Office → PDF "
            "conversion. ``gotenberg`` (HTTP sidecar, distroless-friendly, "
            "default) or ``libreoffice`` (in-container subprocess; requires "
            "``soffice`` + multilingual font packs in the runtime image)."
        ),
    )
    gotenberg_url: str = Field(
        default="http://gotenberg:3000",
        description=(
            "Base URL of the Gotenberg sidecar. Used only when ``office_converter == 'gotenberg'``."
        ),
    )
    gotenberg_timeout_s: int = Field(
        default=60,
        ge=1,
        description="Per-call HTTP timeout against the Gotenberg sidecar.",
    )
    binary_libreoffice_path: str = Field(
        default="soffice",
        description=(
            "Path to the headless LibreOffice binary. Used only when ``office_converter == 'libreoffice'``."
        ),
    )
    binary_libreoffice_timeout_s: int = Field(
        default=60,
        ge=1,
        description=("Per-call subprocess timeout when ``office_converter == 'libreoffice'``."),
    )

    # -- Bbox refinement ------------------------------------------------
    # The bbox refiner (``core/services/bbox/bbox_refiner.py``) replaces
    # LLM-estimated coordinates with grounded ones by fuzzy-matching
    # each extracted value against the document's real text layer. PDFs
    # with embedded text use PyMuPDF (sub-pixel accurate); image-PDFs
    # and raster inputs use the configured OCR engine.
    bbox_refine_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum fuzz score (0.0-1.0) the value matcher requires to "
            "treat a candidate word-span as a hit. Below this the LLM "
            "bbox is kept tagged ``source=llm, refinement_confidence=null``."
        ),
    )
    bbox_refine_min_text_words: int = Field(
        default=5,
        ge=0,
        description=(
            "Per-page threshold below which the page is treated as image-only "
            "and routed to the OCR engine instead of the PDF text layer. "
            "Pages with < N words from PyMuPDF fall back to OCR; everything "
            "else uses the text layer."
        ),
    )
    bbox_refine_ocr_engine: str = Field(
        default="none",
        description=(
            "OCR engine used for image-PDFs and raster inputs. ``none`` "
            "(default) skips OCR -- LLM bboxes are kept for image pages. "
            "``paddle`` / ``mistral`` adapters land in a follow-up; selecting "
            "them here when the adapter ships will route image pages through "
            "the engine."
        ),
    )
    bbox_refine_max_text_pages: int = Field(
        default=200,
        ge=1,
        description=(
            "Hard cap on pages the text-layer extractor will scan per request. "
            "Protects against exotic 10k-page PDFs that would otherwise hold "
            "the request for minutes."
        ),
    )

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
