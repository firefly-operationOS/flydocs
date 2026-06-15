# Changelog

All notable changes to flydocs are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses **CalVer `YY.M.PP`** (PEP 440 may normalise patch numbers
for the Python wheel — e.g. `26.06.00` → `26.6.0`).

## [26.6.11] - 2026-06-15

### Fixed

- **A malformed persisted extraction no longer dumps a raw traceback in the
  worker log.** `ExtractionWorker._process` reconstructed the typed request
  (`_build_request` → pydantic `model_validate`) *outside* its `try/except`, so an
  invalid stored `schema_json`/`options_json` raised a `ValidationError` that
  escaped to the poll loop's `logger.exception(...)` and printed a full stack
  trace. The reconstruction now runs inside the guarded block, where
  `_is_permanent()` classifies it as terminal and the job is marked
  `permanent_error` and logged cleanly — no traceback.

### Changed

- **Upgraded pyfly to `v26.06.104`** for framework-level clean error reporting:
  expected client/domain faults (validation, business-rule, auth — the 4xx
  family) are now logged at WARNING without a stack trace across the CQRS handlers
  and the web request log, and the pyfly CLI prints a clean `Error: ...` line
  instead of a traceback (`--debug` / `PYFLY_DEBUG` restores the full trace).
  Dependency pin and floor moved to `v26.06.104` / `>=26.6.104`.

## [26.6.10] - 2026-06-15

### Changed

- **Adopted pyfly's separate management port (app `8080`, management `9090`).**
  Upgraded to pyfly `v26.06.103`, which serves the actuator (`/actuator/*`) and
  the admin dashboard (`/admin`) on a dedicated management port
  (`pyfly.management.server.port`, default `9090`) instead of the business API
  port. flydocs now runs the API on **`8080`** (`pyfly.server.port`, was `8400`)
  and exposes actuator/admin/health on **`9090`**:
  - `pyfly.yaml`, `IDPSettings.port` and `FLYDOCS_PORT` default to `8080`;
    `pyfly.management.server.port: 9090` is configured explicitly.
  - `Dockerfile` exposes `8080` + `9090`; `docker-compose` maps both for the API
    and exposes each worker's management port (`9091`/`9092`), and every
    health-check now probes `:9090/actuator/health/readiness`.
  - Worker health server (`worker_health_port`) defaults to `9090` to match.
  - **Migration:** point load balancers / clients at `:8080` for the API and
    Kubernetes probes / Prometheus at `:9090`. Set
    `PYFLY_MANAGEMENT_SERVER_PORT=8080` to collapse back to a single port.

- **`pyfly` is now consumed from its published GitHub tag.** `[tool.uv.sources]`
  pins `pyfly` to `git tag v26.06.103` (matching the `fireflyframework-agentic`
  pattern) instead of the local editable path; the dependency floor is
  `>=26.6.103`.

### Fixed

- `main.py` read the removed `pyfly.web.host` key; it now reads
  `pyfly.server.host` (Spring `server.address` parity).

### Added

- **Python SDK:** `Client` / `AsyncClient` accept an optional `management_url`
  so `health()` can target the management port (`:9090`) while API calls use the
  business `base_url` (`:8080`); back-compatible — unset falls back to `base_url`.

## [26.6.9] - 2026-06-15

### Fixed

- **Consolidated rows now carry per-cell provenance from their real source, not
  the first input row's.** `_rebuild_rows` already computed each output row's
  *row-level* pages/confidence from its cited contributors (26.6.8), but every
  *cell* still borrowed its `bbox`/`pages`/`confidence` from `rows[0]` via a
  single `template_by_name` map, and each cell's `judge`/`notes` were dropped to
  the default `uncertain`/0. A cross-document consolidation (e.g. a "current cap
  table" merged across several deeds) therefore stamped every member with the
  *first* member's bounding box, page and confidence — so UI overlays pointed
  every row at the same rectangle on the same page, and the row-level pages no
  longer matched the cell-level pages. Now each output cell resolves its
  provenance from the contributor row it actually derives from, and the judge
  verdict + notes survive the transform.
- **Provenance source is resolved per output row in priority order:** (1) the
  input rows cited via `_source_rows`; (2) for a pure 1:1 rewrite — no row in the
  batch cites anything and the counts match — the input row at the same index;
  (3) input rows matched by a distinctive identity token (an uncited
  consolidation row); (4) `rows[0]` as a last resort. Within a row, each field
  takes its best-grounded contributor cell (one with a bbox wins over one
  without, then higher confidence), so a cell points at its own source even when
  the row merges several documents. Value-rewriting transforms are unchanged when
  no contributor is matched.


### Changed

- **Transforms reconcile on provenance, not bare values
  (`prompts/transform.yaml` → 1.3.0).** The post-extraction transform used to
  flatten each row to `{field: value}`, discarding every signal the extractor
  captured (pages, confidence, the judge's verbatim evidence quote) and then
  stamping output rows with the first input row's metadata — laundering invented
  rows with real-looking provenance. Now each input row carries a reserved,
  read-only `_provenance` block (`source_document`, pages, confidence, per-field
  evidence quotes) and a stable `_row_id`; output rows cite the `_row_id`(s) they
  derive from via `_source_rows`, and rows carry honest provenance computed from
  their actual contributors. The prompt is otherwise kept deliberately gentle and
  domain-agnostic: numeric reconciliation is **not** done by the prompt (an
  earlier draft's aggressive in-prompt "fix the sum / drop the non-member" rules
  were removed — they over-merged unrelated consolidations) but by a
  deterministic post-call guard (below).
- **Request-scope consolidations keep per-row source provenance.** When rows from
  several documents are concatenated, each row is stamped (on a copy — per-task
  groups stay untouched) with its originating document via a new
  `ExtractedField.source`, threaded from the orchestrator and surfaced as
  `_provenance.source_document`.
- **Determinism: every IDP agent now samples greedily (`temperature = 0`).** The
  provider default is `1.0`, which made extraction and especially request-scope
  consolidation differ run to run. A shared `IDP_MODEL_SETTINGS` is merged into
  the extract, judge and transform agents. Paired with total-order tie-breaks in
  the invariant victim selection and entity-resolution canonicalisation, sorted
  page unions, and a rounded parts-of-whole sum, so the same inputs yield the
  same consolidation.
- **Decoupling.** The grounding / anti-fabrication machinery is now **opt-in**,
  triggered only when a transform declares a parts-of-whole invariant — so a
  value-rewriting transform (translate, normalize, reformat) is never penalised
  for legitimately changing its strings. Grounding matches by distinctive
  identity tokens (Unicode-aware, numeric ids included, boilerplate ignored)
  rather than a Latin/4-char heuristic. `entity_resolution` no longer hardcodes
  `dni`/Spanish name fields — the first declared `match_by` field is the strong
  key, the rest drive variant matching. The transform prompt carries no domain or
  language vocabulary.

### Added

- **Caller-declared parts-of-whole invariant on LLM transformations
  (`PartsOfWholeInvariant`, wired end-to-end through the Java SDK and
  orchestration).** A transformation may declare `{share_field, total, tolerance,
  on_violation}` — purely by field name, no domain wording. After the LLM call
  the engine deterministically sums `share_field`; on an over-sum it repairs
  (drops the least-trustworthy / ungrounded rows until it fits) or warns. An
  under-sum is never altered. Available from a workflow template's transform step
  through studio (opaque passthrough) → orchestration → flydocs.
- `LlmTransformation.include_provenance` (default on) gates the `_provenance`
  block so non-reconciling transforms can keep a lean payload.
- SDK parity: the Java and Python SDKs gain `PartsOfWholeInvariant`,
  `LlmTransformation.invariant`/`include_provenance`, and `ExtractedField.source`.

## [26.6.7] - 2026-06-13

### Fixed

- **Runtime version was reported as `26.6.3` after the 26.6.6 release.** The
  version string was duplicated as a literal in `app.py` (the
  `@pyfly_application` decorator) and `__init__.py`, and the 26.6.6 bump only
  touched `pyproject.toml` and the changelog — so `GET /api/v1/version`,
  `/openapi.json`, and the OpenAPI doc all advertised the stale `26.6.3`.
  `app.py` now derives its version from `flydocs.__version__` (single in-code
  source), and a unit test asserts `pyproject.toml` and `__version__` stay in
  lockstep so a release can no longer drift.

### Changed

- **Hardened the generic post-extraction transform prompt
  (`prompts/transform.yaml` → 1.1.0).** Request-scope (and any) LLM transforms
  now reconcile to the state the caller's `intention` asks for: when the
  intention requests a *current / effective / latest / as-of* view, the model
  treats the input rows as a history and excludes entries a later record
  supersedes (replaced, revoked, cancelled, transferred away), collapsing each
  entity to its single current state instead of double-counting it. A companion
  rule makes the model honor numeric invariants the intention implies (parts
  that must sum to a declared whole, quantities that must reconcile, counts that
  must match) by computing the totals and correcting an over/under-count before
  emitting. This is engine-level prompt hardening — domain-agnostic, with no
  use-case-specific wording — so every consolidation (cap tables, powers
  matrices, balances, inventories, …) becomes more reliable without bespoke
  intention text.

## [26.6.6] - 2026-06-12

### Fixed

- **Async extractions could stall in `queued` when a NOTIFY was missed.** The
  worker dispatched solely off the EDA bus (Postgres `LISTEN/NOTIFY` by default),
  which is best-effort: a notification fired while the worker was mid-extraction —
  or after its `LISTEN` connection was silently dropped by the server/pooler — was
  lost, and the row sat in `queued`. The reaper only revived it after a coarse
  (~10 min) sweep, and that revival republished over the same lossy bus, so a
  worker with a dead `LISTEN` never picked it up. The worker now runs a
  **queued-backlog poll** (`job_poll_interval_s`, default 15 s) that claims stale
  `queued` rows directly from the DB via the existing atomic `mark_running` CAS —
  which dedupes against a concurrent NOTIFY delivery. NOTIFY stays the low-latency
  fast path; polling guarantees liveness regardless of bus delivery. Tunable via
  `job_poll_interval_s` / `job_poll_grace_s` / `job_poll_batch`; set the interval
  to `0` to disable.

## [26.6.5] - 2026-06-12

### Fixed

- **Classifier-off + no `expected_type` silently produced zero documents.** When
  `stages.classifier` was off and a file carried no `expected_type`, the segment
  stayed `unmatched` and the file yielded no document — with no error. A single-row
  file now defaults to the sole declared `document_type` in that case (mirroring the
  single-candidate shortcut the classifier itself takes), so the common "one type,
  no classifier" path just works.
- **Request-scope LLM transformation returned empty rows.** The transformer's output
  model wrapped each row under a `values` key, but the prompt instructs the model to
  emit flat `{field: value}` rows — so the structured output never matched and every
  consolidated row came back empty. The output row is now a flat dict, matching the
  prompt, so `result.request_transformations` carries populated rows (e.g. a cap
  table consolidated across several deeds).

## [26.6.4] - 2026-06-12

### Fixed

- **Async worker consumed no jobs (wrong EDA topic).** `pyfly.eda.destinations`
  defaulted to `flydocs.jobs`, but the submit handler publishes
  `extraction.submitted` to `jobs_topic` = `flydocs.extractions`. The `worker`
  container therefore subscribed to a topic nothing publishes to, so every
  queued async extraction sat unprocessed (only the reaper eventually revived
  it). Aligned the `destinations` default to `flydocs.extractions` — still
  env-overridable via `FLYDOCS_EDA_DESTINATIONS`, and `bbox-worker` keeps its
  own `flydocs.extractions.post_processing` override.

### Changed

- Bumped the locked `pyfly` framework dependency to `26.6.99`.

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
