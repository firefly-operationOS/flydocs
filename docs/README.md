# Documentation

The complete reference set for **flydesk-idp**. Start in the main
[README.md](../README.md) for the elevator pitch and the
step-by-step quickstart; come here when you need a specific corner
of the system.

---

## Reading paths

Pick the entry point that matches what you're trying to do:

### "I just want to call the API"

1. [**README.md** § Quickstart](../README.md#quickstart) — clone →
   first sync extraction → first async multi-file job.
2. [**api-reference.md**](api-reference.md) — every endpoint, header,
   query param, DTO, and error code. Includes the full
   `ExtractionRequest` / `ExtractionResult` shapes, the bbox-refine
   sub-state on async jobs, the webhook envelope, and the RFC 7807
   error catalogue.
3. [**standard-validators.md**](standard-validators.md) — what each
   built-in `StandardValidator` does and which `params` it accepts.

### "I'm integrating with the async / EDA surface"

1. [**api-reference.md** § 3 (Async extraction)](api-reference.md#3-async-extraction--post-apiv1jobs) — submit, list, poll status, fetch result (incl. `wait_for_bboxes` long-poll), cancel, webhook envelope, `Idempotency-Key`.
2. [**api-reference.md** § 4 (Common DTO building blocks)](api-reference.md#4-common-dto-building-blocks) — typed EDA event envelopes, `JobStatus` + `BboxRefineStatus` enums, `Transformation` union.
3. [**deployment.md** § 1 (Topology)](deployment.md#1-topology) — how the API + worker + Postgres outbox fit together.
4. [**pipeline.md** § Bbox refinement: sync vs. async](pipeline.md#bbox-refinement-sync-vs-async) — why async jobs skip inline `bbox_refine` and the second-stage `BboxRefineWorker` runs out-of-band.

### "I'm operating / deploying the service"

1. [**deployment.md**](deployment.md) — environment variables,
   topology, building the image, health probes, observability,
   scaling, security, cost tuning.
2. [**cicd.md**](cicd.md) — PR gate workflow, multi-arch publish,
   image consumption, pre-commit hooks.
3. [**troubleshooting.md**](troubleshooting.md) — symptom → likely
   cause for the common gotchas.

### "I want to understand how it works"

1. [**overview.md**](overview.md) — 10-minute guided tour.
2. [**architecture.md**](architecture.md) — `fireflyframework-pyfly`
   DI + CQRS + EDA mechanics, the four bean-registration paths,
   `fireflyframework-agentic`'s `PipelineEngine` runtime.
3. [**pipeline.md**](pipeline.md) — every stage with timeouts,
   concurrency, failure isolation, outbound-call logging, cost
   telemetry, prompt caching.

### "I'm extending the service"

1. [**pipeline.md**](pipeline.md) — where to plug a new stage in the
   DAG.
2. [**transformations.md**](transformations.md) — how to add a new
   declarative `Transformation` type (entity resolution / LLM / your
   own discriminator).
3. [**standard-validators.md**](standard-validators.md) — adding a
   new built-in `StandardValidator`.
4. [**rule-engine.md**](rule-engine.md) — designing business rules,
   the DAG evaluator, level-batching to amortise LLM cost.
5. [**prompts.md**](prompts.md) — adding / editing YAML prompt
   templates, the `PromptCatalog` bean, A/B-testing versions.

---

## Document catalogue

| Document                                                       | Read it when…                                                                              |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| [overview.md](overview.md)                                     | You're new and want a guided tour of the system.                                           |
| [architecture.md](architecture.md)                             | You need to know how `fireflyframework-pyfly` + `fireflyframework-agentic` plug together. |
| [pipeline.md](pipeline.md)                                     | You're touching the orchestrator, adding a new stage, or chasing a slow request.           |
| [api-reference.md](api-reference.md)                           | You're integrating with the HTTP API.                                                      |
| [transformations.md](transformations.md)                       | You want to dedupe, normalise, or run free-form LLM transformations on extracted data.     |
| [standard-validators.md](standard-validators.md)               | You want to know what built-in validators are bundled and their `params`.                  |
| [rule-engine.md](rule-engine.md)                               | You're designing business rules or want to understand the DAG evaluator.                   |
| [prompts.md](prompts.md)                                       | You're editing or adding YAML prompt templates.                                            |
| [docling.md](docling.md)                                       | You want layout-aware OCR or a Markdown text-anchor in the extract prompt (Docling extra). |
| [deployment.md](deployment.md)                                 | You're shipping the service to a real environment.                                         |
| [cicd.md](cicd.md)                                             | You're touching the build, the multi-arch publish, or the pre-commit hooks.                |
| [troubleshooting.md](troubleshooting.md)                       | A real-world problem just blew up.                                                         |
| [audits/](audits/)                                             | Dated technical audits (framework wiring, observability, …).                               |

---

## Cross-cutting topics

Where to read about each topic that spans multiple documents:

| Topic                          | Primary                                                                                                     | Secondary                                                                                                 |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Bounding boxes (LLM + grounded) | [pipeline.md § bbox_refine](pipeline.md), [api-reference.md § `BoundingBox`](api-reference.md#boundingbox)   | [pipeline.md § Bbox refinement: sync vs. async](pipeline.md#bbox-refinement-sync-vs-async), [docling.md](docling.md) |
| Layout-aware OCR + text anchor | [docling.md](docling.md)                                                                                    | [pipeline.md § bbox_refine](pipeline.md), [deployment.md § Docling image variant](deployment.md)          |
| Provider-agnostic LLM calls     | [pipeline.md § 7c (Pricing & prompt caching)](pipeline.md#7c-pricing--prompt-caching)                       | [deployment.md § 2 (Environment)](deployment.md#2-environment)                                            |
| Prompt caching (Anthropic-only) | [pipeline.md § 7c](pipeline.md#7c-pricing--prompt-caching)                                                  | [api-reference.md § `usage` block](api-reference.md#usage-block)                                          |
| EDA / typed event envelopes     | [api-reference.md § EDA event envelopes](api-reference.md#eda-event-envelopes-audit--webhook-payload)       | [overview.md § Async path](overview.md), [deployment.md § 1 (Topology)](deployment.md#1-topology)          |
| Webhooks (HMAC + retry)         | [api-reference.md § Webhook](api-reference.md#webhook)                                                      | [deployment.md § 2 (Environment) → `FLYDESK_IDP_WEBHOOK_*`](deployment.md#2-environment)                  |
| Health probes                   | [deployment.md § 5 (Health + readiness)](deployment.md#5-health--readiness)                                 | [api-reference.md § 1 (Surface at a glance)](api-reference.md#1-surface-at-a-glance)                      |
| W3C trace context               | [deployment.md § 5](deployment.md#5-health--readiness)                                                      | [api-reference.md § Request headers honoured](api-reference.md#request-headers-honoured)                  |
| Cost telemetry                  | [api-reference.md § `usage` block](api-reference.md#usage-block), [pipeline.md § 7](pipeline.md#7-outbound-call-logging--cost-telemetry) | [README.md § What you get back](../README.md#what-you-get-back)                                           |
| Authentication                  | [api-reference.md § 5 (Authentication)](api-reference.md#5-authentication)                                  | [deployment.md § 8 (Security)](deployment.md#8-security)                                                  |
| Error codes (RFC 7807)          | [api-reference.md § 6 (Error codes)](api-reference.md#6-error-codes)                                        | [troubleshooting.md](troubleshooting.md)                                                                  |

---

## Generating the OpenAPI spec

The machine-readable OpenAPI 3.1 document is generated from the same
DTOs documented here. Two paths:

```bash
# Against a running service:
curl -s http://localhost:8400/openapi.json | jq

# Or via the task target (writes to ./openapi.json):
task openapi
```

The Swagger UI at `/docs` browses the same spec.
