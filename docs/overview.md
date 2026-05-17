# flydocs — Overview

A guided tour for someone landing on this codebase for the first time.
By the end you should know **what the service does for the business**,
**how a request travels through it**, and **where to look in the
source** when you need to change something.

---

## 1. The product, in plain language

flydocs turns documents into structured, validated, audit-ready
decisions. A single HTTP call does five things that operations teams
normally have to glue together themselves:

1. **Read** the document — any layout, any binary the **binary
   normalizer** can resolve to LLM-renderable bytes: PDFs and provider-
   native rasters (PNG, JPEG, GIF, WebP) pass straight through; Office
   docs (DOCX/XLSX/PPTX/RTF/ODT/HTML) go to PDF via a Gotenberg sidecar
   (or in-container LibreOffice as fallback); images the providers
   don't read (HEIC/HEIF/AVIF, multi-frame TIFF, SVG, BMP) convert via
   Pillow + cairosvg; archives + email bundles (ZIP/7z/TAR/EML/MSG)
   fan out into multiple per-attachment requests.
2. **Extract** the fields you asked for — each one with a value, a
   page number, a normalised bounding box, and a confidence score.
   The bbox can optionally be **grounded** against the document's real
   text layer (PyMuPDF for born-digital PDFs, OCR for image-only pages)
   by enabling ``stages.bbox_refine`` — sub-pixel accurate, multilingual,
   keeps the LLM bbox tagged ``source=llm`` when no fuzzy match lands.
3. **Validate** every field with deterministic checkers (IBAN
   checksum, NIF/NIE, Luhn, phone E.164, country-aware postal codes,
   and a few dozen others).
4. **Audit** the document with a separate LLM pass — visual checks
   (signature present, stamp present, photo present, …), content
   coherence (dates agree, totals tally, no obvious tampering), and a
   "judge" that re-grades every extracted value against the source.
5. **Decide** with a business-rule engine — caller-defined predicates
   over the extracted data, evaluated as a DAG. Outputs feed back into
   the workflow that called us.

The same call works synchronously (blocking, sub-minute) or as a
queue-backed async job with an HMAC-signed webhook. When bbox grounding
is enabled, async jobs go through a two-stage state machine: the main
extraction lands in ``PARTIAL_SUCCEEDED`` with the LLM-bbox result
immediately readable, then a second EDA worker grounds the coordinates
and flips the job to ``SUCCEEDED``. Callers can poll status, fetch the
partial result, or long-poll ``GET /api/v1/jobs/{id}/result?wait_for_bboxes=true``
to block until grounding finishes.

---

## 2. Who it's for

- **Operations engineers** wiring a KYC, claims, or onboarding flow
  that needs structured data + decisions out of unstructured
  documents.
- **Product owners** who want to deprecate hand-coded extraction rules
  whenever a vendor changes a form layout.
- **Backend developers** who want the freedom of a multimodal LLM
  with the discipline of a typed API contract.

The audit fields (`request_id`, `pipeline_errors`, per-stage latency,
per-doc model used) are first-class so the service plays nicely with
compliance teams and incident reviews.

---

## 3. Mental model

Three concepts unlock the rest of the codebase.

### 3a. The `ExtractionRequest`

The single payload for both APIs. Three required pieces:

- **`document`** — base64 content + filename + optional declared
  media type.
- **`docs[]`** — one `DocSpec` per document type you expect in the
  source. Each contains the field schema you want extracted, plus any
  visual / content validators and `standard_validators` per field.
- **`rules[]`** — optional business rules, each declaring its
  dependencies (`parents`) on fields, validators, or other rules.

Plus `options` (which pipeline stages to run, language hint, model
override) and `intention` (a one-paragraph description of why you're
extracting — the LLM reads it).

### 3b. The pipeline

```
load → split? → extract → field_validation? → visual_authenticity?
     → content_authenticity? → judge? → rules? → assemble
```

The extractor is always on. The rest are caller-toggled through
`ExtractionOptions.stages`. The DAG is built fresh per request from
`fireflyframework-agentic`'s `PipelineEngine`, so the trace mirrors
exactly what ran.

### 3c. The `ExtractionResult`

Mirror image of the request. Per-document blocks of extracted fields
with their validation and judge verdicts; per-document visual + content
audits; the resolved rule outputs; an `audit` block with the request
id, latency, model id, and any per-stage errors.

A failed _stage_ doesn't fail the whole call — the error is recorded
in `pipeline_errors[]` and the rest of the pipeline keeps running.

---

## 4. Walking a real request through the service

```
HTTP POST /api/v1/extract
      │
      ▼
ExtractController     ← @rest_controller, RFC 7807 on validation error
      │
      ▼  CommandBus.send(ExtractCommand)
      ▼
ExtractHandler        ← @command_handler. asyncio.wait_for(SYNC_TIMEOUT_S).
      │
      ▼
PipelineOrchestrator  ← builds a fireflyframework-agentic PipelineEngine DAG
      │
      ├──▶ load           DocumentLoader: sniff media_type + page count
      ├──▶ split?         DocumentSplitter (LLM): pages per docType
      ├──▶ extract        MultimodalExtractor (LLM): fields + bbox
      ├──▶ validate?      FieldValidator: pure-Python, regex/enum/range + StandardValidators
      ├──▶ visual?        VisualAuthenticityChecker (LLM): caller-defined yes/no checks
      ├──▶ content?       ContentAuthenticityChecker (LLM): integrity audit
      ├──▶ judge?         Judge (LLM): re-grade every extracted value
      └──▶ rules?         RuleEngine (LLM): DAG of caller predicates
      │
      ▼
ExtractionResult      ← serialised back through ExtractController
      │
      ▼
HTTP 200 application/json
```

Async path (`POST /api/v1/jobs`):

```
JobsController → SubmitJobCommand → SubmitJobHandler → ExtractionJob table
                                                      └──▶ fireflyframework-pyfly EventPublisher.publish
                                                            (IDPJobSubmitted event)
                                                              │
                                                              ▼  durable outbox in Postgres
                                                            pyfly_eda_outbox row
                                                              │
                                                              ▼  pg_notify + LISTEN
                                                          JobWorker subscribe handler
                                                              │
                                                              ▼  same orchestrator
                                                          mark_succeeded(job_id)
                                                              │
                                                              ▼
                                                          WebhookPublisher (HMAC + retries)
```

Switch broker by flipping `FLYDOCS_EDA_ADAPTER` to `memory`,
`redis` (Streams), or `kafka` — the orchestrator and the worker don't
care; only the bus implementation changes.

---

## 5. Where things live

| You want to…                                      | Look here                                                                |
| ------------------------------------------------- | ------------------------------------------------------------------------ |
| Change the HTTP contract                          | `interfaces/dtos/extract.py`, `interfaces/dtos/job.py`                   |
| Add a new pipeline stage                          | `core/services/<stage>/` + register a `@bean` in `core/configuration.py` |
| Edit a prompt                                     | `resources/prompts/<stage>.yaml`                                         |
| Add a new built-in validator                      | `interfaces/enums/standard_validator.py` + `core/services/validation/standard_validator_registry.py` |
| Tune timeouts / limits                            | `config.py` (`IDPSettings`) — driven by env vars                         |
| Wire a new bean                                   | `core/configuration.py` (`@bean`) or decorate the class with `@service` |
| Diagnose a request                                | Grep the structured logs by `request_id` (UUID stamped in the response) |
| Run only the unit tests                           | `task test`                                                              |
| Run the real-LLM smoke test                       | `ANTHROPIC_API_KEY=… task test:llm` (or `OPENAI_API_KEY=…` etc., depending on the model id in `FLYDOCS_MODEL`) |

---

## 6. Building blocks

| Block                              | Purpose                                                              | Default                                          |
| ---------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------ |
| **PyFly application**              | Spring-Boot-style framework: DI, CQRS, web, EDA, actuator, security  | `flydocs.app.FlydocsApplication`         |
| **Configuration**                  | Single `@configuration` declaring every cross-cutting bean           | `core/configuration.py::IDPCoreConfiguration`   |
| **PromptCatalog**                  | Loads YAML prompts at boot, registers them with the `fireflyframework-agentic` registry | `core/services/extraction/prompts.py`           |
| **Pipeline orchestrator**          | Builds + runs a `fireflyframework-agentic` `PipelineEngine` per request                | `core/services/pipeline/orchestrator.py`        |
| **Event bus**                      | `fireflyframework-pyfly` `EventPublisher` — default Postgres outbox + LISTEN/NOTIFY; swap to Redis Streams / Kafka via `FLYDOCS_EDA_ADAPTER` | injected by `pyfly.eda.auto_configuration.EdaAutoConfiguration` |
| **WebhookPublisher**               | HMAC-SHA256 signed, retries on 5xx / 429 with `tenacity`             | `core/services/webhook/webhook_publisher.py`    |
| **Database**                       | Async SQLAlchemy + Alembic                                           | `models/repositories/extraction_job_repository.py` |
| **Settings**                       | Pydantic settings; every knob is a `FLYDOCS_*` env var           | `config.py`                                      |

---

## 7. What's coming next in the docs

- [architecture.md](architecture.md) — Firefly Framework deep dive: how
  `fireflyframework-pyfly` resolves the bean graph and how
  `fireflyframework-agentic` builds the pipeline.
- [pipeline.md](pipeline.md) — every stage in detail, with timeouts,
  concurrency, and debugging recipes.
- [api-reference.md](api-reference.md) — full request/response schemas.
- [standard-validators.md](standard-validators.md) — every built-in
  checker with parameter docs.
- [rule-engine.md](rule-engine.md) — DAG mechanics + a worked KYC
  example.
- [prompts.md](prompts.md) — YAML prompt format and how to edit safely.
- [deployment.md](deployment.md) — topology, env vars, scaling, cost.
- [troubleshooting.md](troubleshooting.md) — failure modes and fixes.
