# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Runtime settings for flydocs.

Settings are loaded from the environment under the ``FLYDOCS_`` prefix
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
        env_prefix="FLYDOCS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Service --------------------------------------------------------
    log_level: str = "INFO"
    # Business API port (pyfly serves the app here; pyfly.server.port == 8080).
    port: int = 8080
    # Port for the HTTP health server the worker CLI modes (``flydocs
    # worker`` / ``flydocs bbox-worker``) run next to their asyncio tasks
    # so Kubernetes can probe ``/actuator/health/*`` over httpGet. Defaults to
    # the management port (9090) so it matches where the ``serve`` mode exposes
    # the actuator (pyfly.management.server.port). ``0`` disables the server
    # (dev setups running ``serve`` and ``worker`` on the same host, where the
    # serve mode already owns 9090).
    worker_health_port: int | None = Field(default=9090, ge=0, le=65535)

    # -- Persistence ----------------------------------------------------
    database_url: str = "postgresql+asyncpg://idp:idp@localhost:5432/flydocs"

    # -- Queue / EDA ----------------------------------------------------
    # The actual EventPublisher is built by pyfly's EdaAutoConfiguration
    # from ``pyfly.eda.*`` properties (see ``pyfly.yaml``). The value here
    # only drives ``${FLYDOCS_EDA_ADAPTER}`` interpolation in that
    # file. Default ``postgres`` because the service already runs
    # Postgres for persistence — no extra broker is required.
    eda_adapter: str = Field(default="postgres", description="memory | postgres | redis | kafka")
    redis_url: str = "redis://localhost:6379/0"
    # Main extraction topic. Workers subscribe to the
    # ``extraction.submitted`` event type (declared as a constant in
    # ``flydocs.interfaces.dtos.event``); the destination here is the
    # broker channel the bus publishes / drains on.
    jobs_topic: str = "flydocs.extractions"
    # Post-processing topic for the out-of-band bbox refiner. Triggered
    # by the ``ExtractionWorker`` after main extraction succeeds AND
    # ``options.stages.bbox_refine == true``. Consumed by
    # ``BboxRefineWorker``.
    bbox_refine_topic: str = "flydocs.extractions.post_processing"
    # Retry budget + timeout for the bbox refine leg, independent of the
    # main extraction. Refinement is CPU-bound (PyMuPDF / OCR) so the
    # default ceiling is generous.
    bbox_refine_max_attempts: int = 3
    bbox_refine_timeout_s: int = 600
    # Max documents of a single extraction refined concurrently. The refine
    # leg fans out one task per document (OCR + per-page matcher), so a
    # multi-document extraction no longer runs strictly one doc at a time.
    # Bounded because OCR is CPU-bound and each doc multiplies in-flight LLM
    # calls. Set this per deployment to roughly the pod's CPU limit + 1 (the
    # +1 keeps the cores busy across the LLM-matcher I/O waits); lower it under
    # provider rate limits.
    bbox_refine_doc_concurrency: int = 4

    # -- Extraction -----------------------------------------------------
    model: str = "anthropic:claude-sonnet-4-6"
    fallback_model: str | None = "openai:gpt-4o"
    # Optional pre-extraction text rendering. ``"none"`` (default) sends
    # the binary only. ``"docling"`` runs Docling
    # over the document and splices the resulting Markdown into the
    # user message ahead of the binary content so the multimodal LLM
    # can cross-reference layout against a cleaned-up textual view --
    # measurably reduces hallucinations on multilingual scans and long
    # tabular documents. Requires the ``docling`` extra.
    extraction_text_anchor: str = Field(
        default="none",
        description=(
            "Pre-extraction text rendering. ``none`` (default) sends the "
            "binary only; ``docling`` runs Docling and splices a Markdown "
            "anchor into the user prompt for cross-reference. Install the "
            "``docling`` extra to enable."
        ),
    )
    extraction_text_anchor_max_chars: int = Field(
        default=12000,
        ge=0,
        description=(
            "Hard ceiling on the Markdown anchor length (in characters). "
            "Anything above is truncated on a paragraph boundary when one "
            "is in reach, otherwise hard-cut, with a visible ``[anchor "
            "truncated]`` sentinel. Keeps prompt cost predictable on long "
            "documents."
        ),
    )
    # Page count threshold above which the sync path returns 413 and asks the
    # caller to use the async API. The LLM sees the document directly, so
    # there is no DPI to enforce here.
    max_sync_pages: int = 10
    max_bytes: int = 32 * 1024 * 1024  # 32 MiB
    sync_timeout_s: int = 60
    # Total wall-clock budget per async job (``JobWorker`` wraps the
    # whole pipeline in ``asyncio.wait_for`` with this timeout). Must be
    # >= the sum of the per-stage timeouts below. Default sized for
    # 7-PDF multi-file bundles with the empty-array auto-retry path.
    async_timeout_s: int = 1200
    job_max_attempts: int = 3
    # Stale-RUNNING lease window. A job in RUNNING whose ``started_at``
    # is older than this is treated as orphaned (worker crashed mid-run)
    # and becomes re-claimable. The atomic ``mark_running`` claim uses
    # this to reject concurrent second deliveries while still permitting
    # crash recovery via the periodic reaper. Sized to
    # ``async_timeout_s + 60s`` so a legit run that uses the full
    # asyncio.wait_for budget still wins its own lease, with 60s
    # headroom for connection teardown / commit latency.
    job_run_lease_s: int = 1260
    # Same for the bbox-refine leg.
    bbox_refine_lease_s: int = 660
    # ----- Reaper -----------------------------------------------------
    # The reaper runs alongside each worker (one task per process) and
    # republishes events for extractions stuck in non-terminal states.
    # It is the only path that revives orphans:
    #   * ``running`` whose claimant crashed past its lease;
    #   * ``queued`` whose submit handler crashed between row INSERT and
    #     outbox PUBLISH (or whose worker died during ``_delayed_publish``);
    #   * ``succeeded`` with ``post_processing_bbox_status == pending``
    #     whose bbox-refine event was never published;
    #   * ``succeeded`` with ``post_processing_bbox_status == running``
    #     whose bbox claimant crashed past its lease.
    # Each republish is deduped at claim time by the atomic ``mark_*``
    # transitions, so running multiple replicas of the reaper is safe
    # (it just wastes a few outbox INSERTs per stale job).
    reaper_sweep_interval_s: int = 60
    # Threshold to consider a QUEUED row's event lost. Sized to 2x
    # ``retry_max_delay_s`` so a legit in-flight ``_delayed_publish``
    # task is never trampled by the reaper.
    queued_orphan_threshold_s: int = 600
    # Threshold to consider a PARTIAL_SUCCEEDED row's bbox event lost
    # when ``bbox_refine_started_at`` is still NULL (first publish never
    # landed). The clock starts at the row's ``started_at`` (main
    # extraction claim time), so this has to cover any reasonable
    # extraction wall-clock plus the bus poll cycle. Default
    # ``async_timeout_s + 120s``.
    partial_succeeded_orphan_threshold_s: int = 1320
    # ----- Queued-backlog poll (durability fallback) ------------------
    # LISTEN/NOTIFY dispatch is best-effort: a notification fired while the
    # worker is mid-extraction -- or after its LISTEN connection is silently
    # dropped by the server/pooler -- is lost, leaving the row in ``queued``
    # with no redelivery until the reaper's coarse sweep (which republishes
    # over the same lossy bus). This poll is the durable backstop: every
    # ``job_poll_interval_s`` the worker claims ``queued`` rows older than
    # ``job_poll_grace_s`` directly from the DB via the atomic ``mark_running``
    # CAS (which dedupes against a concurrent NOTIFY delivery). NOTIFY stays
    # the low-latency fast path; polling guarantees liveness. The grace lets
    # the NOTIFY path win fresh jobs so the poll only sweeps up what it missed.
    # Set ``job_poll_interval_s = 0`` to disable and rely on NOTIFY + reaper.
    job_poll_interval_s: int = 15
    job_poll_grace_s: int = 10
    job_poll_batch: int = 10

    # Per-pipeline-step timeouts (env-tunable). Conservative defaults so
    # multi-file requests + the empty-array auto-retry don't run into a
    # hard wall on the first call.
    extract_timeout_s: int = 600
    judge_timeout_s: int = 300
    bbox_refine_inline_timeout_s: int = 300
    classifier_timeout_s: int = 180
    splitter_timeout_s: int = 180
    judge_escalation_timeout_s: int = 600
    transform_timeout_s: int = 600
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
    # The result webhook delivers the full extraction (split docs + fields +
    # rule evaluations) to the consumer, which persists it synchronously before
    # acking. That ingestion can take well over the old 15s default (slow
    # downstream services, large results), and on timeout the worker disconnects
    # mid-delivery, leaving the consumer's persistence half-applied and the
    # workflow stuck "in progress" forever even though the pipeline succeeded.
    # Default raised to 300s so a legitimately slow consumer has room to finish;
    # override per-env with ``FLYDOCS_WEBHOOK_TIMEOUT_S``.
    webhook_timeout_s: int = 300
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
        default="tesseract",
        description=(
            "OCR engine used for image-PDFs and raster inputs. ``tesseract`` "
            "(default) shells out to the local ``tesseract`` binary -- the "
            "runtime Dockerfile installs it plus the most common European "
            "language packs (spa/eng/fra/deu/ita/por). ``docling`` runs IBM "
            "Docling's layout-aware pipeline (Heron layout + pluggable OCR) "
            "for cleaner words on noisy scans; install the ``docling`` extra. "
            "``none`` skips OCR entirely (image pages keep the LLM bbox); "
            "``paddle`` / ``mistral`` adapters land in follow-ups."
        ),
    )
    bbox_refine_ocr_dpi: int = Field(
        default=200,
        ge=72,
        le=600,
        description=(
            "DPI at which PDF pages are rasterised before being shipped to "
            "the OCR engine. Higher = more accurate but slower; 200 is a "
            "good balance for printed text. Ignored for raster inputs."
        ),
    )
    bbox_refine_tesseract_lang: str = Field(
        default="spa+eng",
        description=(
            "Default tesseract ``-l`` argument (``+``-joined ISO 639-2/B "
            "language codes). Overridden per-request by mapping the public "
            "``ExtractionOptions.language_hint`` (ISO 639-1, e.g. ``es``) "
            "to its 3-letter equivalent."
        ),
    )
    bbox_refine_matcher: str = Field(
        default="hybrid",
        description=(
            "Strategy that maps each extracted value to OCR / text-layer "
            "word indices. ``hybrid`` (default) cascades: deterministic "
            "rapidfuzz first (free, millisecond-scale), LLM only for the "
            "residual fields it could not resolve -- grounds 70-90% of "
            "fields without an LLM call while still covering spelled-out "
            "numbers and format variants. ``llm`` runs the LLM matcher on "
            "every field (generic + multilingual, one focused per-page "
            "call). ``fuzzy`` is the deterministic rapidfuzz strategy "
            "alone for callers that want zero per-request LLM cost."
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
