# Changelog

All notable changes to flydocs are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses **CalVer `YY.M.PP`** (PEP 440 may normalise patch numbers
for the Python wheel — e.g. `26.06.00` → `26.6.0`).

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
