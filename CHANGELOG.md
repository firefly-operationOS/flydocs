# Changelog

All notable changes to flydocs are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses **CalVer `YY.M.PP`** (PEP 440 may normalise patch numbers
for the Python wheel — e.g. `26.06.00` → `26.6.0`).

## [26.6.3] - 2026-06-12

### Added

- **Worker health server.** `flydocs worker` and `flydocs bbox-worker` now
  serve `GET /actuator/health`, `/actuator/health/liveness`, and
  `/actuator/health/readiness` over HTTP (Starlette + uvicorn, assembled
  from pyfly's actuator in `src/flydocs/worker_health.py`), so Kubernetes
  probes the worker pods with httpGet instead of `exec` shims. The server
  binds `0.0.0.0`, runs as a sibling asyncio task of the worker and reaper
  (any task dying — including a failed bind — takes the whole process down
  for a clean pod restart), keeps its access log off, and honours pyfly's
  secure-by-default endpoint exposure (`/actuator/loggers`,
  `/actuator/metrics` → 404 unless opted in). Indicator discovery uses
  pyfly ≥ 26.6.98's public `pyfly.actuator.install_health_indicators`;
  `database_health` and `eda_health` participate in both probes, matching
  the API process. See the "Worker health" section in `docs/deployment.md`.
- New setting `worker_health_port` (`FLYDOCS_WORKER_HEALTH_PORT`): unset
  reuses `FLYDOCS_PORT`; `0` disables the worker health server.
- `docker compose` healthchecks for the `worker` and `bbox-worker`
  services against `/actuator/health/readiness`.
- The worker modes now shut down gracefully on SIGTERM: worker, reaper,
  and health server stop, and the pyfly shutdown runs before the process
  exits.

### Changed

- `pyfly` dependency floor raised to 26.6.98 and the `web` extra added, so
  `starlette` and `uvicorn` are declared (previously they only arrived
  transitively).

### Documentation

- `env_template`: realigned `FLYDOCS_JOBS_TOPIC` and
  `FLYDOCS_ASYNC_TIMEOUT_S` with the defaults in `config.py`.
- `docs/deployment.md`: metrics endpoints require exposure opt-in via
  `pyfly.management.endpoints.web.exposure.include`; the secure default
  exposes only `health,info`.

## [26.6.2] - 2026-05-31

### Changed

- **Open-sourced under the Apache License 2.0.** Added the full `LICENSE`
  at the repository root and in both SDKs, and prepended the Apache 2.0
  header to every source file. The copyright holder is now Firefly
  Software Foundation, and the repository is public.
- Declared `org.opencontainers.image.licenses=Apache-2.0` on the published
  container image and surfaced the license in the README and OpenAPI metadata.
- Realigned `__version__` with the packaged release version.

### Removed

- Removed the bundled `flydocs-whitepaper.pdf` from the repository and its
  git history.

## [26.6.1] - 2026-05-30

### Changed

- **Binary normalisation runs on the framework's
  `fireflyframework_agentic.content.binary`.** `BinaryNormalizer` is wired
  in `IDPCoreConfiguration` from a `BinaryConfig` mapped off `IDPSettings`;
  `OfficeConverter` stays pluggable. Rows are `BinaryArtifact`
  (`bytes/media_type/filename/page_count/derived_from` plus a `kind`
  token).
- **Wire contract:** unsupported files return HTTP **415**; the error
  `code` is `unsupported_file`. Corrupt PDFs carry the specific
  `corrupt_pdf` code (422).
- The binary dependencies `pillow-heif`, `cairosvg`, `py7zr` and
  `extract-msg` ship via `fireflyframework-agentic[binary]`. `pypdf`,
  `Pillow`, `pymupdf` and `rapidfuzz` are direct deps (used by the slicer,
  loader, OCR engine and bbox refiner).

## [26.6.0] - 2026-05-26

### BREAKING CHANGES — API v1 redesign

This release replaces the public API contract end-to-end. There is no
backwards-compatible shim. See [docs/migration-v0-to-v1.md](docs/migration-v0-to-v1.md)
for the full rename table and worked examples.

**Highlights:**

- snake_case across every JSON key, enum value, and error code.
- Top-level request body: `files[]` + `document_types[]` + `rules[]` (was `documents[]` + `docs[]`).
- One recursive `Field` (was `FieldSpec` + `FieldItem`). Array `items` is a single `Field`; objects use `type: "object"` + `fields: [Field, ...]`.
- `DocumentTypeSpec.id` flattens the v0 `docs[].docType.documentType` triple-stutter.
- `Extraction` lifecycle collapses to `queued → running → succeeded | failed | cancelled`; refining-bbox state lives under `post_processing.bbox_refinement.{status, started_at, finished_at, attempts, error}` and evolves independently. `PARTIAL_SUCCEEDED` and `REFINING_BBOXES` are gone.
- Unified `EventEnvelope` for EDA events and webhook deliveries. Dotted event types (`extraction.submitted`, `extraction.completed`, `extraction.post_processing.requested`, `extraction.post_processing.completed`).
- New error catalogue (`not_found`, `not_ready`, `not_cancellable`, `timeout`, `file_too_large`, `unsupported_file`, `validation_failed`, …).
- `POST /api/v1/extract` and `POST /api/v1/extractions` accept `multipart/form-data` in addition to JSON.
- Validators: `Field.validators[]` (was `standard_validators[]`); dispatch key is `name` (was `type`).
- Visual checks: `DocumentTypeSpec.visual_checks[]` (was `validators.visual[]`).
- Rule parents: discriminator key is `kind` (was `parentType`); members snake_case (`document_type`, `fields`, `validator`, `rule`).
- Response top-level meta (`model`, `latency_ms`, `trace`, `pipeline_errors`, `escalation`, `usage`) nested under a single `pipeline` block.
- Top-level response `id` (was `request_id`) is a prefixed ULID (`ext_…`).
- Bbox: `bbox: null` signals absence; the v0 `quality: "empty"` / `source: "none"` placeholders are removed.
- New `FieldType.OBJECT` lets schemas nest objects natively.
- `escalation_threshold` / `escalation_model` collapse into a single `escalation` sub-object.
- Endpoint moves: `POST /api/v1/jobs` → `POST /api/v1/extractions` (and every related `/jobs/...` path).

### Changed (server-side)

- Database table `extraction_jobs` → `extractions`; column `created_at` → `submitted_at`; per-column `bbox_refine_*` fields collapse into a `post_processing` JSONB column; per-column `error_code` + `error_message` collapse into an `error` JSONB column.
- Repository `ExtractionJobRepository` → `ExtractionRepository`; entity `ExtractionJob` → `Extraction`.
- CQRS rename: `SubmitJobCommand` / `GetJobQuery` / `ListJobsQuery` / `CancelJobCommand` / `GetJobResultQuery` → `SubmitExtractionCommand` / `GetExtractionQuery` / `ListExtractionsQuery` / `CancelExtractionCommand` / `GetExtractionResultQuery`. Handlers renamed in lockstep.
- Directory `core/services/jobs/` → `core/services/extractions/`.
- Worker `JobWorker` → `ExtractionWorker`; `JobReaper` → `ExtractionReaper` (`BboxReaper` keeps its name).

### Changed (SDKs)

- Python SDK: `DocumentInput` → `FileInput`; `DocSpec` / `DocType` → `DocumentTypeSpec`; `FieldSpec` + `FieldItem` → `Field`; `StandardValidatorSpec` → `ValidatorSpec`; `JobStatus` → `ExtractionStatus`; `BboxRefineStatus` → `PostProcessingStatus`; `SubmitJobRequest` / `SubmitJobResponse` / `JobStatusResponse` / `JobResult` / `JobListResponse` → `SubmitExtractionRequest` / `Extraction` / `ExtractionResultEnvelope` / `ExtractionList`; `JobWebhookPayload` → `WebhookEnvelope`. New methods: `client.extractions.{create, get, get_result, cancel, list}`.
- Java SDK: every record renamed in lockstep with Python. New `client.extractions()` sub-resource handle. `@FlydocsWebhook` resolver now takes `WebhookEnvelope`.

### Migration

Every existing integration (curl, SDK, webhook receiver, EDA consumer) needs to be ported. The migration guide ([docs/migration-v0-to-v1.md](docs/migration-v0-to-v1.md)) has:

- A glossary fixing `file` vs `document_type` vs `document`.
- Side-by-side before/after request body, response body, async submit/poll, webhook envelope, and error problem-details.
- An SDK upgrade quick-reference (Python + Java) covering imports, sync extraction, async submit + result, and webhook handlers.

### Documentation

- Full rewrites: `docs/api-reference.md`, `docs/payload-reference.md`.
- Renamed: `docs/standard-validators.md` → `docs/validators.md` (content rewritten).
- New: `docs/migration-v0-to-v1.md`.
- Sweep-updated: `docs/pipeline.md`, `docs/rule-engine.md`, `docs/transformations.md`, `docs/concurrency.md`, `docs/overview.md`, `docs/architecture.md`, `docs/deployment.md`, `docs/troubleshooting.md`, `docs/cicd.md`, `docs/docling.md`, `QUICKSTART.md`, `README.md`, `CLAUDE.md`, `sdks/README.md`.

### Fixed (post-merge polish from the live KYB smoke run)

These five fixes were committed to the v1 branch after the live end-to-end
test against the real Anthropic API (`claude-sonnet-4-6`) on two Spanish
notarial PDFs (incorporation deed + shareholders agreement):

- **`/api/v1/extract` sync timeout returns HTTP 408 instead of 400.** A new `ExtractionTimedOut(RuntimeError)` is raised by the handler so it propagates through the pyfly CQRS bus to the controller (which previously wrapped `asyncio.TimeoutError`, an `OSError` subclass, as a generic `COMMAND_PROCESSING_ERROR` at HTTP 400). The new `@exception_handler(ExtractionTimedOut)` advice emits the canonical 408 `timeout` problem-detail with `extensions.timeout_s`.
- **`bbox-worker` EDA destination realigned.** docker-compose pinned the bbox subscriber to the v0 topic `flydocs.bbox.refine`. The v1 main worker publishes to `flydocs.extractions.post_processing` per the renamed event-type. Without this fix async jobs with `bbox_refine=true` would hang at `post_processing.bbox_refinement.status=pending` indefinitely.
- **Alembic `migrations/env.py`** still imported `from flydocs.models.entities.extraction_job` after the v1 entity rename → fatal at API container startup when `RUN_MIGRATIONS=true` (the default).
- **`src/flydocs/resources/prompts/transform.yaml`** used legacy `id:` / `system:` / `user:` keys; the catalogue's loader expects `name:` / `system_template:` / `user_template:` (with a `required_variables` declaration). The mismatch crashed `PromptCatalog.from_resources()` at startup.
- **`scripts/kyb_real_test.py`** committed as the canonical live smoke runner. Run against the docker stack (`docker compose up -d` + `ANTHROPIC_API_KEY` in `.env`) to validate sync (`POST /api/v1/extract`) and async (`POST /api/v1/extractions`) end-to-end with multi-file, multi-document-type, recursive `Field`, judge, `bbox_refine` post-processing, six cross-document rules, and `validators` / `visual_checks` declarations. Verified live: sync 72s/175k tokens/$0.60; async 271s/772k tokens/$2.59; all six KYB rules resolve correctly (including a `partial` shareholders-reconciliation verdict that the deed and pacto don't share the same party set).
