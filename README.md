<div align="center">

# flydesk-idp

### **Intelligent Document Processing for Firefly Desk**

Pure-multimodal field extraction with bounding boxes, structured
validation, LLM cross-checking, and a business-rule engine — exposed
as a production HTTP service with both synchronous and queue-backed
asynchronous APIs.

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org)
[![pyfly](https://img.shields.io/badge/runtime-fireflyframework--pyfly-orange)](https://github.com/fireflyframework/fireflyframework-pyfly)
[![agentic](https://img.shields.io/badge/genai-fireflyframework--agentic-purple)](https://github.com/fireflyframework/fireflyframework-agentic)
[![OpenAPI](https://img.shields.io/badge/api-openapi%203.1-green)](docs/api-reference.md)
[![PR gate](https://github.com/firefly-operationOS/flydesk-idp/actions/workflows/pr-gate.yaml/badge.svg)](https://github.com/firefly-operationOS/flydesk-idp/actions/workflows/pr-gate.yaml)
[![Docker publish](https://github.com/firefly-operationOS/flydesk-idp/actions/workflows/docker-publish.yaml/badge.svg)](https://github.com/firefly-operationOS/flydesk-idp/actions/workflows/docker-publish.yaml)
[![Image](https://img.shields.io/badge/ghcr-flydesk--idp-blue)](https://github.com/firefly-operationOS/flydesk-idp/pkgs/container/flydesk-idp)

</div>

---

## Why this service exists

KYC reviews, contract intake, claims triage, invoice processing — every
operations team has the same workflow underneath:

> _"Take this document, tell me what it says, decide whether it passes
> our checks, and route it accordingly."_

Doing that with traditional OCR pipelines is brittle: layouts change,
new document types arrive every quarter, and the team ends up
hand-coding extraction rules that don't survive a single redesign.

**flydesk-idp** collapses the whole workflow into one HTTP call. You
ship the document, declare the fields and rules you care about as
JSON, and the service returns a structured verdict — every value
tagged with a bounding box, a confidence score, a validation outcome,
an LLM judge re-check, and the resolved business rules. No layout
templates, no OCR coordinates, no model fine-tuning.

It is built to drop into a production back-office pipeline: idempotent
APIs, queue-backed async jobs with HMAC-signed webhooks, observability
out of the box, and clean failure isolation per pipeline stage.

---

## What you get back

You give the service one HTTP request. The response is a single JSON
object containing, for every document you asked about:

| Layer                       | What it tells you                                                                              |
| --------------------------- | ---------------------------------------------------------------------------------------------- |
| **Fields**                  | The extracted value, page number, normalised bounding box (with a geometric quality verdict), model confidence, and free-text notes. |
| **Field validation**        | Per-field PASS / FAIL plus a verdict from any built-in `StandardValidator` (IBAN, NIF, IBAN, Luhn, phone, postal code, …). |
| **Visual authenticity**     | Yes/no verdicts on caller-defined visual validators (signature present, stamp present, photo present, …). |
| **Content authenticity**    | Document-level integrity audit: date consistency, totals tally, expected boilerplate, tampering signals. |
| **Judge**                   | A second LLM pass re-checks each extracted value against the document and stamps PASS / FAIL / UNCERTAIN with evidence. |
| **Business rules**          | Boolean / categorical decisions over the data, evaluated as a DAG — _"is this KYC-complete?"_, _"escalate to manual review?"_, _"approve / reject"_. |
| **Audit trail**             | Request id, per-stage latencies, per-doc model used, structured logs.                          |
| **Cost telemetry**          | Aggregated `usage` block in every response: input/output tokens + estimated USD cost (live Anthropic tariffs via `genai-prices`), broken down by agent and by model. Plus a per-call `cost_usd` on every `outbound_call` log line. |
| **Prompt caching**          | Anthropic prompt caching is on for every agent: system prompt + last user-message block are cached with a 5-minute TTL. Cache writes / reads are surfaced as `cache_creation_tokens` / `cache_read_tokens` on the response and on every `outbound_call` log line. |

A single request can carry **one file or many**. Submit
`documents: [...]` to ship several at once: pin each file's
`document_type` when you know it, or let the LLM classifier decide.
Each extracted document carries a `source_file` field so callers can
map output back to the input file that produced it. The full
multi-file shape is documented in
[docs/api-reference.md § 2a](docs/api-reference.md#2a-multi-file-extraction).

The same call works **synchronously** (`POST /api/v1/extract`, blocks
until done) or as a **queued job** with a webhook
(`POST /api/v1/jobs`, returns 202 + a job id). The async endpoint is
single-file only for now.

---

## Quickstart

Local dev:

```bash
git clone <this repo>
cd firefly-operationOS/flydesk-idp
task deps:install        # uv sync --extra dev
task env:init            # copy env_template -> .env
task dev:db              # bring up Postgres + Redis in Docker
task dev:migrate         # alembic upgrade head
task dev:serve           # API on http://localhost:8400/docs
task dev:worker          # in another terminal — subscribes to the EDA bus
```

Or just the container stack:

```bash
task docker:up           # api + worker + Postgres + Redis
task health              # GET /actuator/health
task docker:logs         # tail every container
```

Smoke test against a real document:

```bash
curl -s http://localhost:8400/api/v1/extract \
  -H 'content-type: application/json' \
  -d @docs/examples/extract.json | jq .documents[0].fields
```

---

## How the request flows

The service runs the request as a DAG inside the
`fireflyframework-agentic` `PipelineEngine`. Stages are toggled per
request through `ExtractionOptions.stages`; the engine builds a fresh
DAG for each call so the audit trail reflects exactly what executed.

```
                ┌──────────────────────────────────────────────────────────────────┐
   POST  ──────▶│ load → discover? → classify? → plan_tasks → extract →            │──────▶ JSON
 (PDF/PNG/…)    │ bbox_validation → field_validation? → visual_auth? →             │  (fields + bbox
                │ content_auth? → judge? → judge_escalation? → rules? → assemble   │   + verdicts)
                └──────────────────────────────────────────────────────────────────┘
                              │
                              │  per-segment concurrency (asyncio.gather)
                              │  per-stage timeouts + error capture
                              ▼
                       structured trace
                       (request_id, latency_ms, pipeline_errors)
```

The extractor and the geometric bbox check are the only mandatory
stages. Everything else is a caller-chosen trade-off between cost,
latency, and rigor. With `splitter` enabled, every file -- even a
single uploaded PDF -- is split into its sub-documents and each is
independently classified against the declared `DocSpec`s, so a pack
that bundles a deed + an ID + a utility bill comes out as three
separate routed documents without the caller having to know what's
inside.

See [docs/pipeline.md](docs/pipeline.md) for the deep dive.

---

## Built on the Firefly Framework

Every cross-cutting concern is delegated to the framework so the
business logic stays small.

| Concern                          | Provided by                                            |
| -------------------------------- | ------------------------------------------------------ |
| Dependency injection             | `fireflyframework-pyfly` `@configuration` + `@bean`    |
| CQRS (commands / queries / bus)  | `fireflyframework-pyfly` `@command_handler` / `@query_handler` |
| REST surface                     | `fireflyframework-pyfly` `@rest_controller` over FastAPI |
| Async pipeline DAG               | `fireflyframework-agentic` `PipelineEngine` / `PipelineBuilder` |
| Prompt management                | `fireflyframework-agentic` `PromptTemplate` + `PromptRegistry` (YAML-backed) |
| LLM agents (multimodal)          | `fireflyframework-agentic` `FireflyAgent` over `pydantic-ai` |
| EDA / async jobs                 | pyfly `EventPublisher` — default `postgres` (durable outbox + LISTEN/NOTIFY); flip `FLYDESK_IDP_EDA_ADAPTER` to `memory` / `redis` / `kafka` |
| W3C trace context                | pyfly `CorrelationFilter` (default web filter) + `pyfly.observability.correlation` |
| K8s probes                       | `/actuator/health/liveness` + `/actuator/health/readiness` with `database_health` + `eda_health` indicators |
| Multi-arch container             | `ghcr.io/firefly-operationos/flydesk-idp:latest` — linux/amd64 + linux/arm64 manifest |
| Observability                    | structlog JSON, OTLP tracing, Prometheus metrics, actuator |
| Persistence                      | SQLAlchemy async, Alembic, Postgres (SQLite for tests)  |
| RFC 7807 error responses         | `@controller_advice` exception handler                  |

Everything is wired through pyfly's container — including the prompt
catalog, the EDA event publisher, the webhook publisher, and the async
worker — so the application has **no manually-constructed singletons**
outside the DI graph.

---

## Project layout

```
src/flydesk_idp/
├── interfaces/              Public DTOs + enums — the stable HTTP contract
├── models/                  SQLAlchemy entities + async repositories
├── core/
│   ├── configuration.py     @configuration with every @bean
│   └── services/
│       ├── extract/         CQRS: sync extract command + handler
│       ├── jobs/            CQRS: submit / get / cancel job
│       ├── extraction/      MultimodalExtractor + PromptCatalog
│       ├── splitting/       LLM document splitter
│       ├── validation/      Pure-Python FieldValidator + StandardValidators
│       ├── authenticity/    Visual + content audits
│       ├── judge/           LLM judge / re-evaluator
│       ├── rules/           DAG-based business rule engine
│       ├── pipeline/        PipelineOrchestrator (agentic PipelineEngine)
│       ├── webhook/         Outbound webhook publisher with HMAC
│       └── workers/         JobWorker (subscribes to pyfly.eda)
├── resources/
│   └── prompts/             YAML prompt templates (one per LLM stage)
└── web/
    ├── controllers/         @rest_controller beans
    └── advice/              @controller_advice exception mapping
```

---

## Public API at a glance

| Endpoint                                  | Purpose                                                |
| ----------------------------------------- | ------------------------------------------------------ |
| `POST   /api/v1/extract`                  | Synchronous extraction. Blocks until done.             |
| `POST   /api/v1/extract:validate`         | Dry-run the semantic validator on a payload (no LLM).  |
| `POST   /api/v1/jobs`                     | Submit an async extraction. Returns `202` + job id.    |
| `GET    /api/v1/jobs/{id}`                | Status of an async job.                                |
| `GET    /api/v1/jobs/{id}/result`         | Final `ExtractionResult` (when `SUCCEEDED`).           |
| `DELETE /api/v1/jobs/{id}`                | Cancel a job that is still `QUEUED`.                   |
| `GET    /api/v1/version`                  | Build + model info.                                    |
| `GET    /actuator/health`                 | Composite health.                                      |
| `GET    /actuator/metrics`                | Prometheus metrics.                                    |
| `GET    /admin`                           | PyFly Admin dashboard — beans, mappings, env, CQRS, traces, loggers, health. |
| `GET    /docs`                            | Swagger UI (OpenAPI 3.1).                              |

Full request / response shapes in [docs/api-reference.md](docs/api-reference.md).

---

## What's bundled

**Standard validators** — pure-Python checkers you can declare per
field. They run after extraction and never call the LLM:

| Group        | Validators                                                                       |
| ------------ | -------------------------------------------------------------------------------- |
| Network      | `email`, `uri`, `url`, `domain`, `slug`, `ipv4`, `ipv6`                          |
| Temporal     | `date`, `datetime`, `time`, `iso_8601`                                           |
| Identifiers  | `uuid`, `json`, `hex_color`                                                      |
| Finance      | `iban` (mod-97), `bic`, `credit_card` (Luhn), `currency_code`, `amount`          |
| Telephony    | `phone_e164`                                                                     |
| Geographic   | `country_code`, `language_code`, `postal_code` (country-aware), `latitude`, `longitude` |
| National IDs | `nif` (ES, mod-23), `nie`, `cif`, `vat_id`, `ssn`, `passport_number`             |

Each one accepts optional `params` (e.g. `{"country": "ES"}`) and a
`severity` (`error` flips the field invalid; `warning` records the
finding but keeps the field valid). See
[docs/standard-validators.md](docs/standard-validators.md).

**Prompt catalog** — every LLM stage reads its system + user prompt
from a YAML file under `src/flydesk_idp/resources/prompts/`. The
catalog is a normal pyfly bean; you can swap templates, bump versions,
or A/B-test prompts without touching Python. See
[docs/prompts.md](docs/prompts.md).

**Business rule engine** — declare predicates that depend on fields,
validator outcomes, or other rules. Rules form a DAG; the engine
evaluates them level-by-level via `graphlib.TopologicalSorter` and
groups same-level rules into a single LLM call to amortise cost.
Cycles are rejected before any LLM call is issued. See
[docs/rule-engine.md](docs/rule-engine.md).

```jsonc
{
  "id": "kyc_complete",
  "predicate": "All identity fields are populated AND nif is valid.",
  "parents": [
    {"parentType": "field", "documentType": "passport",
     "fieldNames": ["full_name", "nif"]}
  ],
  "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
}
```

---

## Documentation map

| Document                                       | Read it when…                                                            |
| ---------------------------------------------- | ------------------------------------------------------------------------ |
| [docs/overview.md](docs/overview.md)           | You're new and want a guided tour of the system.                         |
| [docs/architecture.md](docs/architecture.md)   | You need to know how pyfly + agentic plug together.                      |
| [docs/pipeline.md](docs/pipeline.md)           | You're touching the orchestrator or adding a new stage.                  |
| [docs/api-reference.md](docs/api-reference.md) | You're integrating with the HTTP API.                                    |
| [docs/standard-validators.md](docs/standard-validators.md) | You want to know what validators are built-in.               |
| [docs/rule-engine.md](docs/rule-engine.md)     | You're designing business rules.                                         |
| [docs/prompts.md](docs/prompts.md)             | You're editing or adding YAML prompt templates.                          |
| [docs/deployment.md](docs/deployment.md)       | You're shipping the service to a real environment.                       |
| [docs/troubleshooting.md](docs/troubleshooting.md) | A real-world problem just blew up.                                   |

---

## Operations & developer workflows

```bash
task deps:install        # uv sync --extra dev
task lint:check          # ruff + pyright
task test                # unit suite (~26 tests, <1s)
task test:llm            # real Claude smoke test (requires ANTHROPIC_API_KEY)
task dev:serve           # API on :8400
task dev:worker          # async job consumer
task migrate             # alembic upgrade head
task docker:build        # build the production image
task docker:up           # full stack — api + worker + Postgres + Redis
task docker:up:test      # stack with mock-llm for integration tests
task health              # GET /actuator/health
task version             # GET /api/v1/version
task openapi             # dump the OpenAPI spec
```

Full task surface is in [Taskfile.yml](Taskfile.yml).

---

<div align="center">

**flydesk-idp** is part of [Firefly OperationOS](../) — the back-office
plane for Firefly Desk.

Copyright © 2026 Firefly Software Solutions Inc

</div>
