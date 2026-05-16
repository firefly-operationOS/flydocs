# API reference

The canonical reference for the HTTP surface. Every example here is a
real, working payload — running `task openapi` produces the
machine-readable OpenAPI 3.1 spec from the same DTOs.

---

## 1. Surface at a glance

| Method   | Path                            | Purpose                                                                |
| -------- | ------------------------------- | ---------------------------------------------------------------------- |
| `POST`   | `/api/v1/extract`               | Synchronous extraction. Blocks until the pipeline finishes.            |
| `POST`   | `/api/v1/extract:validate`      | Dry-run the semantic validator (no LLM call, no DB write).             |
| `POST`   | `/api/v1/jobs`                  | Submit a queued extraction. Returns `202` + job id.                    |
| `GET`    | `/api/v1/jobs`                  | Filtered, paginated listing of jobs.                                   |
| `GET`    | `/api/v1/jobs/{id}`             | Current status of a job (including bbox-refine sub-state).             |
| `GET`    | `/api/v1/jobs/{id}/result`      | Final `ExtractionResult`. Long-poll for grounded bboxes via `wait_for_bboxes`. |
| `DELETE` | `/api/v1/jobs/{id}`             | Cancel a job that is still `QUEUED`.                                   |
| `GET`    | `/api/v1/version`               | Build + model + EDA-adapter info.                                      |
| `GET`    | `/actuator/health`              | Composite health (DB + EDA).                                           |
| `GET`    | `/actuator/health/liveness`     | Liveness probe (always responds while the process is alive).           |
| `GET`    | `/actuator/health/readiness`    | Readiness probe — `503` when `database_health` or `eda_health` is `DOWN`. |
| `GET`    | `/actuator/metrics`             | Prometheus metrics.                                                    |
| `GET`    | `/admin`                        | PyFly Admin dashboard — beans, mappings, env, CQRS, traces, loggers, health. |
| `GET`    | `/docs`                         | Swagger UI (OpenAPI 3.1).                                              |
| `GET`    | `/openapi.json`                 | Machine-readable OpenAPI 3.1 spec.                                     |

Errors follow RFC 7807 (`application/problem+json`). The advice at
`web/advice/exception_advice.py` maps domain exceptions to bodies
shaped as:

```jsonc
{
  "type":    "https://flydesk.dev/problems/<slug>",   // URI reference identifying the problem class
  "title":   "Short human-readable summary",
  "status":  409,                                     // mirrors the HTTP status
  "detail":  "Human-readable explanation for this occurrence.",
  "code":    "job_not_ready",                         // stable application code (snake_case)
  "instance": null,                                   // optional URI for this specific occurrence
  "extensions": { /* arbitrary extra context */ }
}
```

See [§ 6 (Error codes)](#6-error-codes) for the full catalogue.

### Request headers honoured

| Header              | Surface(s)                              | Meaning                                                                                                  |
| ------------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `Idempotency-Key`   | `POST /api/v1/jobs`                     | Replays the original `SubmitJobResponse` when the same key is seen twice (no duplicate job created).      |
| `X-Correlation-Id`  | every endpoint                          | Propagated through every pipeline stage, every `outbound_call` log line, and every EDA event / webhook.   |
| `X-Request-Id`      | every endpoint                          | Echoed back in the response. Generated server-side when absent.                                          |
| `X-Tenant-Id`       | every endpoint                          | Echoed back; copied into the EDA event and webhook envelopes as `tenant_id`.                              |
| `traceparent`       | every endpoint                          | W3C trace context. Propagated to OTLP spans and downstream HTTP calls.                                   |
| `tracestate`        | every endpoint                          | W3C trace state. Same propagation as `traceparent`.                                                      |
| `Authorization`     | every endpoint (when API keys enabled)  | `Bearer <key>` or the configured scheme. See [§ 5 (Authentication)](#5-authentication).                  |

---

## 2. Synchronous extraction — `POST /api/v1/extract`

Blocks until the orchestrator finishes or `FLYDESK_IDP_SYNC_TIMEOUT_S`
elapses (default 60 s). On timeout the controller returns
`408 extraction_timeout`.

The request always carries a non-empty `documents` list — a single
file is just a one-element list. See [§ 2c](#2c-multi-file--sub-document-discovery)
for the multi-file case where each file is processed independently
and may pin its own `document_type`.

### Request

```jsonc
{
  "intention": "KYC review for a Spanish power-of-attorney deed.",
  "documents": [
    {
      "filename": "deed.pdf",
      "content_base64": "JVBERi0xLjQK...",     // base64-encoded document bytes
      "content_type": "application/pdf"         // optional; sniffed if omitted
    }
  ],
  "docs": [
    {
      "docType": {
        "documentType": "escritura_poderes",
        "description": "Escritura notarial de poderes",
        "country": "ES"
      },
      "fieldGroups": [
        {
          "fieldGroupName": "otorgamiento",
          "fieldGroupDesc": "Otorgamiento data",
          "fieldGroupFields": [
            {
              "fieldName": "fecha",
              "fieldDescription": "Date of issuance in ISO format.",
              "fieldType": "string",
              "standard_validators": [{"type": "date"}]
            },
            {
              "fieldName": "otorgante_dni_nie",
              "fieldDescription": "DNI or NIE of the grantor.",
              "fieldType": "string",
              "standard_validators": [
                {"type": "nif", "severity": "warning"},
                {"type": "nie", "severity": "warning"}
              ]
            }
          ]
        }
      ],
      "validators": {
        "visual": [
          {"name": "firma_notario", "description": "The notary's signature is present."},
          {"name": "sello_notarial", "description": "The notarial seal is present."}
        ]
      }
    }
  ],
  "rules": [
    {
      "id": "kyc_complete",
      "predicate": "Both DNI/NIE fields are populated AND fecha is populated.",
      "parents": [
        {"parentType": "field", "documentType": "escritura_poderes",
         "fieldNames": ["otorgante_dni_nie", "fecha"]}
      ],
      "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
    }
  ],
  "options": {
    "model": "anthropic:claude-opus-4-7",
    "language_hint": "es",
    "stages": {
      "splitter": false,
      "field_validation": true,
      "visual_authenticity": true,
      "content_authenticity": false,
      "judge": true,
      "bbox_refine": true,
      "transform": true,
      "rule_engine": true
    },
    "transformations": [
      {
        "type": "entity_resolution",
        "target_group": "personas",
        "match_by": ["dni", "nombre"],
        "scope": "request"
      }
    ]
  }
}
```

> See [docs/transformations.md](transformations.md) for the full
> reference on the `transform` stage (declarative entity resolution +
> free-form LLM transformations).

### Response — 200 OK

```jsonc
{
  "request_id": "8d6624d3-96b0-43e4-b99f-e03258a99b22",
  "files": [                                     // per-file summary; one entry per input file
    {
      "filename": "deed.pdf",
      "media_type": "application/pdf",
      "page_count": 21,
      "bytes": 384112,
      "document_type": null,                     // null when neither pin nor classifier set one
      "classification": null                      // null when the classifier stage was skipped
    }
  ],
  "documents": [
    {
      "document_type": "escritura_poderes",
      "missing": false,
      "pages": [1, 2, /* ... */ 21],
      "description": "Escritura notarial de poderes",
      "confidence": 1.0,
      "source_file": "deed.pdf",                  // filename of the input file this document came from
      "fields": [
        {
          "fieldGroupName": "otorgamiento",
          "fieldGroupFields": [
            {
              "fieldName": "fecha",
              "fieldValueFound": "2025-05-15",
              "confidence": 0.98,
              "pagesFound": [1],
              "bbox": {
                "xmin": 0.15, "ymin": 0.26, "xmax": 0.85, "ymax": 0.30,
                "quality": "good",                // bbox_validation verdict; good|poor|suspicious|invalid|empty
                "quality_score": 0.94             // continuous score in [0, 1]
              },
              "notes": "Otorgamiento date on first page header.",
              "field_validation": {"valid": true, "errors": []},
              "judge": {
                "status": "PASS",
                "confidence": 0.99,
                "evidence": "15 May 2025",
                "notes": "Date matches the otorgamiento date.",
                "flag_for_review": false
              }
            }
          ]
        }
      ],
      "authenticity": {
        "visual": [
          {"name": "firma_notario", "passed": true, "confidence": 0.85, "notes": "..."},
          {"name": "sello_notarial", "passed": true, "confidence": 0.90, "notes": "..."}
        ],
        "content": null
      }
    }
  ],
  "additional_documents": [],
  "rule_results": [
    {
      "rule_id": "kyc_complete",
      "predicate": "Both DNI/NIE fields are populated AND fecha is populated.",
      "output": "true",
      "summary": "All required identity fields are present.",
      "notes": [],
      "human_revision": ""
    }
  ],
  "model": "anthropic:claude-opus-4-7",
  "latency_ms": 43580,
  "pipeline_errors": [],
  "usage": {
    "total_input_tokens": 162109,
    "total_output_tokens": 22218,
    "total_tokens": 184327,
    "total_cost_usd": 3.0651,
    "total_requests": 0,
    "total_latency_ms": 96739.0,
    "record_count": 27,
    "cache_creation_tokens": 0,
    "cache_read_tokens": 0,
    "by_agent": {
      "flydesk-idp-splitter":   {"input_tokens": 48598, "output_tokens": 746,  "total_tokens": 49344, "cost_usd": 0.785},
      "flydesk-idp-classifier": {"input_tokens": 63325, "output_tokens": 2307, "total_tokens": 65632, "cost_usd": 1.123},
      "flydesk-idp-extractor":  {"input_tokens": 78936, "output_tokens": 6057, "total_tokens": 84993, "cost_usd": 1.638},
      "flydesk-idp-judge":      {"input_tokens": 73023, "output_tokens": 5719, "total_tokens": 78742, "cost_usd": 1.524},
      "flydesk-idp-visual-auth":{"input_tokens": 40847, "output_tokens": 642,  "total_tokens": 41489, "cost_usd": 0.661},
      "flydesk-idp-rule-engine":{"input_tokens": 13609, "output_tokens": 2326, "total_tokens": 15935, "cost_usd": 0.379}
    },
    "by_model": {
      "anthropic:claude-opus-4-7": {"input_tokens": 318338, "output_tokens": 17797, "total_tokens": 336135, "cost_usd": 6.110}
    }
  },
  "trace": [
    {"node": "load",            "started_at": "...", "completed_at": "...", "latency_ms": 173.16,   "status": "success"},
    {"node": "discover",        "started_at": "...", "completed_at": "...", "latency_ms": 14725.81, "status": "success"},
    {"node": "classify",        "started_at": "...", "completed_at": "...", "latency_ms": 12474.10, "status": "success"},
    {"node": "plan_tasks",      "started_at": "...", "completed_at": "...", "latency_ms": 234.83,   "status": "success"},
    {"node": "extract",         "started_at": "...", "completed_at": "...", "latency_ms": 21352.88, "status": "success"},
    {"node": "bbox_validation", "started_at": "...", "completed_at": "...", "latency_ms": 0.43,     "status": "success"},
    {"node": "field_validation","started_at": "...", "completed_at": "...", "latency_ms": 0.79,     "status": "success"},
    {"node": "visual_authenticity","started_at": "...", "completed_at": "...", "latency_ms": 7385.81,  "status": "success"},
    {"node": "judge",           "started_at": "...", "completed_at": "...", "latency_ms": 20721.86, "status": "success"},
    {"node": "rules",           "started_at": "...", "completed_at": "...", "latency_ms": 26099.84, "status": "success"},
    {"node": "assemble",        "started_at": "...", "completed_at": "...", "latency_ms": 0.05,     "status": "success"}
  ]
}
```

#### `usage` block

Aggregated token counts and estimated USD cost across every LLM call
the request made. Scoped to the request via the framework's
``correlation_id``: each call records a :class:`UsageRecord` keyed by
``request_id``, and the orchestrator queries the tracker for this
request when assembling the response.

| Field                    | Meaning                                                                                                  |
|--------------------------|----------------------------------------------------------------------------------------------------------|
| `total_input_tokens`     | Sum of prompt tokens across all calls.                                                                   |
| `total_output_tokens`    | Sum of completion tokens.                                                                                |
| `total_tokens`           | Sum of input + output.                                                                                   |
| `total_cost_usd`         | Estimated USD cost using the configured price table (see operational notes below).                       |
| `record_count`           | Number of distinct LLM calls behind this request.                                                        |
| `total_latency_ms`       | Sum of per-call wall-clock times (with `asyncio.gather` parallelism this can exceed `latency_ms`).       |
| `cache_creation_tokens`  | Prompt tokens written to the provider's prompt cache (Anthropic-specific feature — non-zero only when the active provider exposes prompt caching and `FLYDESK_IDP_PROMPT_CACHE=1`). |
| `cache_read_tokens`      | Prompt tokens served from the provider's prompt cache (same caveat).                                     |
| `by_agent`               | Per-agent breakdown (extractor, classifier, splitter, judge, visual, content, rule-engine).             |
| `by_model`               | Per-model breakdown — useful when fallback or escalation switched models mid-request.                   |

`null` when cost tracking is disabled or no LLM call fired.

#### `trace` block

One entry per executed pipeline node, ordered as the DAG ran them. Each
entry has `node`, `started_at`, `completed_at`, `latency_ms`, and a
`status` of `success` | `failed` | `skipped`. Useful for spotting which
stages dominate a request's latency.

#### Operational notes

The cost number is a provider-agnostic **estimate** sourced from
`genai-prices` — the same library `fireflyframework-genai` /
`fireflyframework-agentic` use internally, so Anthropic, OpenAI,
Google, Mistral, etc. are all priced uniformly. Local overrides for
fast-moving Claude 4 models live in `core/observability/pricing.py`;
add equivalents there if a new model lands before `genai-prices`
ships the tariff. The same per-call data is also emitted on the
``outbound_call`` log lines (one per LLM call, with `correlation_id`,
`in_tokens`, `out_tokens`, `cost_usd`), so spend forensics work even
without parsing the response.

### Error responses

See [§ 6 (Error codes)](#6-error-codes) for the full catalogue. The
sync endpoint can return:

| Status | Code                          | When                                                                                                              |
| -----: | ----------------------------- | ----------------------------------------------------------------------------------------------------------------- |
|    400 | _various_                     | Pydantic validation failed (RFC 7807 body with field errors).                                                     |
|    408 | `extraction_timeout`          | Sync pipeline exceeded `FLYDESK_IDP_SYNC_TIMEOUT_S`.                                                              |
|    413 | `document_too_large`          | Decoded document exceeds `FLYDESK_IDP_MAX_BYTES` (default 32 MiB).                                                |
|    422 | `invalid_base64`              | A `content_base64` field failed strict base64 parsing.                                                            |
|    422 | `invalid_request`             | Semantic validator rejected the payload (a `document_type` pin references an undeclared docType, rule points at an unknown field, …). The body embeds the full report so the caller can fix every issue at once. |
|    422 | `encrypted_pdf`               | The submitted PDF is password-protected. Decrypt it before submitting.                                            |
|    422 | `unsupported_binary`          | The submitted media type is not on the supported list (and could not be sniffed).                                 |
|    422 | `office_conversion_failed`    | The Office adapter (Gotenberg / LibreOffice) refused to convert the file.                                         |
|    422 | `archive_extraction_failed`   | A submitted archive (ZIP / 7z / TAR / GZIP / EML / MSG) could not be unpacked.                                    |
|    422 | `image_conversion_failed`     | Pillow / pillow-heif / cairosvg could not normalise the image into a provider-readable raster.                    |

### 2b. Dry-run the validator — `POST /api/v1/extract:validate`

Runs only the semantic [`RequestValidator`](#5-authentication) — no
LLM call, no document load, no DB write. Use it to check a payload
from a CI pipeline, a UI before submit, or while iterating on rule
definitions. Always returns `200`; the caller inspects `ok` to decide
whether to proceed.

```jsonc
// Request body is exactly the same shape as POST /api/v1/extract.

// Response body
{
  "ok": false,
  "error_count": 2,
  "warning_count": 1,
  "errors": [
    {"severity": "error",   "code": "document_type_unknown", "message": "Pin 'utility_bill' is not declared in docs[].", "path": "documents[2].document_type"},
    {"severity": "error",   "code": "rule_unknown_field",    "message": "Rule 'kyc_complete' references field 'nif' which is not declared on docType 'passport'.", "path": "rules[0]"}
  ],
  "warnings": [
    {"severity": "warning", "code": "no_field_groups",       "message": "DocSpec 'cover_page' has only one field group; consider grouping.", "path": "docs[1]"}
  ]
}
```

The same payload shape is embedded under `extensions` of the `422
invalid_request` response that the real `/extract` and `/jobs`
endpoints emit, so a 422 carries everything you would see from a
dry-run validate call.

### 2c. Multi-file & sub-document discovery

The pipeline supports two complementary shapes for getting multiple
documents out of a single request:

1. **Multi-file submission**: `documents` is always a list — a single
   file is just a one-element list, and a multi-file submission is the
   exact same payload with more entries. Each entry carries its own
   `filename`, `content_base64`, `content_type`, and optional
   `document_type` pin.
2. **Sub-document discovery**: enable `options.stages.splitter` and a
   single uploaded PDF that contains several documents inside (deed
   + ID + utility bill, for example) is split into its sub-documents
   automatically. Each sub-document is then classified against the
   declared `DocSpec`s and extracted independently.

The two work in any combination -- you can submit five files and turn
on the splitter, and every file gets its own discover → classify →
extract sub-pipeline.

Each entry in `documents` carries an optional `document_type` pin in
addition to the file content:

```jsonc
{
  "intention": "KYC pack: deed + spanish DNI + utility bill.",
  "documents": [
    {
      "filename": "deed.pdf",
      "content_base64": "JVBERi0xLjQK...",
      "content_type": "application/pdf",
      "document_type": "escritura_poderes"     // caller pin -- skips the classifier
    },
    {
      "filename": "dni.jpg",
      "content_base64": "/9j/4AAQ...",
      "content_type": "image/jpeg"
                                                // no pin -- classifier picks the docType
    },
    {
      "filename": "utility.pdf",
      "content_base64": "JVBERi0xLjQK...",
      "content_type": "application/pdf"
    }
  ],
  "docs": [
    { "docType": {"documentType": "escritura_poderes", "description": "...", "country": "ES"}, "fieldGroups": [/* ... */] },
    { "docType": {"documentType": "dni",               "description": "...", "country": "ES"}, "fieldGroups": [/* ... */] },
    { "docType": {"documentType": "utility_bill",      "description": "...", "country": "ES"}, "fieldGroups": [/* ... */] }
  ],
  "options": {
    "stages": {
      "classifier": true,                       // default; on for multi-file with unpinned files
      "splitter": false,                        // splitter is single-file only
      "field_validation": true,
      "judge": true
    }
  }
}
```

The response shape is the same `ExtractionResult`:

- `files[]` has one entry per input file. For unpinned files,
  `files[i].classification` carries the classifier verdict
  (`document_type`, `matched`, `confidence`, `description`, `notes`).
- `documents[i].source_file` carries the input filename that each
  extracted document came from -- so the caller can map per-task
  output back to the file that produced it.
- Files the classifier marks `unmatched` skip extraction and appear in
  `additional_documents` with `document_type: "unmatched"` and
  `source_file` set to the original filename.

Two correctness notes:

- A `document_type` pin **must** reference a docType declared in
  `docs[]`. Unknown pins are rejected with `422 invalid_request /
  code=document_type_unknown` before the pipeline runs.
- Per-file size limits (`FLYDESK_IDP_MAX_BYTES`) are enforced
  individually -- a single oversized file rejects the whole request
  with `413 document_too_large` naming that file.

---

## 3. Async extraction — `POST /api/v1/jobs`

For documents that may take longer than the sync ceiling, or for
fire-and-forget workflows with a webhook callback. The submit endpoint
returns immediately; the worker drives the same orchestrator behind
the scenes.

> **Multi-file is supported on async too.** Submit a non-empty
> `documents` list exactly like the sync endpoint — the worker drives
> the same orchestrator and refines bboxes out-of-band via the
> `BboxRefineWorker` (see [docs/pipeline.md](pipeline.md#bbox-refinement-sync-vs-async)).

### Submit

```http
POST /api/v1/jobs
Content-Type: application/json
Idempotency-Key: 4b2e8c70-8d10-4f04-92ee-9d8...   ; optional, replays the response if reused

{
  "intention": "...",
  "documents": [
    { "filename": "...", "content_base64": "...", "content_type": "..." }
  ],
  "docs": [ /* same as /extract */ ],
  "rules": [ /* same as /extract */ ],
  "options": { /* same as /extract */ },
  "callback_url": "https://workflow.example.com/idp/webhook",
  "metadata": { "tenant_id": "acme", "external_id": "..." }
}
```

```http
202 Accepted
Content-Type: application/json

{
  "job_id": "01HEM2ZZ7M0Q8...",
  "status": "QUEUED",
  "submitted_at": "2026-05-14T10:42:00Z"
}
```

### List jobs — `GET /api/v1/jobs`

Filterable, paginated listing. All filters are optional and combine
with `AND`. Newest-first ordering (`created_at DESC`).

| Query param          | Type             | Default | Meaning                                                                                       |
| -------------------- | ---------------- | ------: | --------------------------------------------------------------------------------------------- |
| `status`             | CSV of statuses  |   `""`  | Match any of the listed values. Valid: `QUEUED`, `RUNNING`, `PARTIAL_SUCCEEDED`, `REFINING_BBOXES`, `SUCCEEDED`, `FAILED`, `CANCELLED`. |
| `bbox_refine_status` | CSV of sub-states|   `""`  | Match the bbox-refine leg. Valid: `pending`, `running`, `succeeded`, `failed`.                |
| `idempotency_key`    | string           |   `""`  | Exact match against the submit-time `Idempotency-Key` header.                                 |
| `created_after`      | RFC 3339         |  `null` | Inclusive lower bound on `created_at`.                                                        |
| `created_before`     | RFC 3339         |  `null` | Inclusive upper bound on `created_at`.                                                        |
| `limit`              | int (1–500)      |    `50` | Page size. Capped server-side at 500.                                                         |
| `offset`             | int ≥ 0          |     `0` | Skip this many rows. Pair with `total` to paginate.                                           |

```http
GET /api/v1/jobs?status=SUCCEEDED,PARTIAL_SUCCEEDED&bbox_refine_status=failed&limit=25
```

```jsonc
{
  "items": [ /* JobStatusResponse[] — same shape as the single-job GET below */ ],
  "total":  187,                    // filtered count, ignores limit/offset
  "limit":  25,
  "offset": 0
}
```

### Poll status — `GET /api/v1/jobs/{id}`

```jsonc
{
  "job_id":        "01HEM2ZZ7M0Q8...",
  "status":        "PARTIAL_SUCCEEDED",   // QUEUED | RUNNING | PARTIAL_SUCCEEDED | REFINING_BBOXES | SUCCEEDED | FAILED | CANCELLED
  "submitted_at":  "2026-05-14T10:42:00Z",
  "started_at":    "2026-05-14T10:42:03Z",
  "finished_at":   "2026-05-14T10:42:48Z",
  "attempts":      1,
  "error_code":    null,
  "error_message": null,

  // Bbox-refine sub-state — populated only when options.stages.bbox_refine=true
  "bbox_refine_status":         "running",         // pending | running | succeeded | failed | null
  "bbox_refine_attempts":       1,
  "bbox_refine_started_at":     "2026-05-14T10:42:49Z",
  "bbox_refine_finished_at":    null,
  "bbox_refine_error_code":     null,
  "bbox_refine_error_message":  null
}
```

The two state machines:

```text
default flow (bbox_refine off):
  QUEUED ─▶ RUNNING ─▶ SUCCEEDED | FAILED
  QUEUED ─▶ CANCELLED     (only while still QUEUED)

bbox-refine flow (bbox_refine on):
  QUEUED ─▶ RUNNING ─▶ PARTIAL_SUCCEEDED ─▶ REFINING_BBOXES ─▶ SUCCEEDED
                                          \─▶ stays PARTIAL_SUCCEEDED if
                                              bbox refine fails (the
                                              LLM-bbox result is still
                                              readable; bbox_refine_status
                                              column carries the failure).
```

Unknown `job_id` → `404 JOB_NOT_FOUND`.

### Fetch the result — `GET /api/v1/jobs/{id}/result`

Returns the `ExtractionResult` when the job is in `SUCCEEDED`,
`PARTIAL_SUCCEEDED`, or `REFINING_BBOXES`. While the job is still
queued / running / cancelled / failed, the controller returns
`409 job_not_ready`. Unknown `job_id` → `404 JOB_NOT_FOUND`.

| Query param        | Type     | Default | Meaning                                                                                                              |
| ------------------ | -------- | ------: | -------------------------------------------------------------------------------------------------------------------- |
| `wait_for_bboxes`  | bool     | `false` | Long-poll the row until the bbox refiner finishes (`status` -> `SUCCEEDED`) or `timeout` elapses.                    |
| `timeout`          | float, s |  `60.0` | Long-poll ceiling in seconds. On timeout the partial result (LLM bboxes) is returned with `200`.                     |

```http
GET /api/v1/jobs/01HEM2ZZ7M0Q8.../result?wait_for_bboxes=true&timeout=120
```

```jsonc
{
  "job_id": "01HEM2ZZ7M0Q8...",
  "result": { /* full ExtractionResult, same shape as /extract */ }
}
```

### Cancel — `DELETE /api/v1/jobs/{id}`

Only valid while `status == QUEUED`. After that the worker has started
on the job and there is no mid-flight cancellation hook.

```http
DELETE /api/v1/jobs/01HEM2ZZ7M0Q8...
→ 200 { "job_id": "...", "status": "CANCELLED", ... }     // JobStatusResponse shape
→ 409 { "code": "job_not_cancellable", ... }              // already RUNNING / done
→ 404 { "code": "JOB_NOT_FOUND", ... }
```

### Webhook

When the job leaves a terminal state (`SUCCEEDED` / `PARTIAL_SUCCEEDED`
/ `FAILED` / `CANCELLED`) and `callback_url` is set, the worker POSTs
the full envelope. The payload mirrors the EDA event so external
consumers see the same identity + lifecycle surface as the in-cluster
workers.

```http
POST <callback_url>
Content-Type: application/json
X-Flydesk-Signature: sha256=<hex>

{
  "event_id":       "f0c7b3aa-2f43-4d34-bf6c-3b09e6efbb19",  // UUID v4 — dedupe by this on the client
  "event_type":     "IDPJobCompleted",                       // mirrors the EDA event type that triggered delivery
  "version":        "1.0.0",                                 // semver of the payload shape
  "job_id":         "01HEM2ZZ7M0Q8...",
  "status":         "SUCCEEDED",                             // JobStatus value
  "occurred_at":    "2026-05-14T10:43:01Z",                  // UTC ISO-8601 — when the producer emitted the event
  "started_at":     "2026-05-14T10:42:03Z",                  // when the worker first picked the job up
  "finished_at":    "2026-05-14T10:43:01Z",                  // terminal-state timestamp
  "attempts":       1,                                       // worker attempts consumed
  "correlation_id": "req-…",                                 // echoes inbound X-Correlation-Id
  "tenant_id":      "acme",                                  // echoes X-Tenant-Id when set
  "metadata":       { "external_id": "...", "..." },         // verbatim copy of submit-time metadata
  "result":         { /* full ExtractionResult */ },          // null on FAILED / CANCELLED
  "error_code":     null,
  "error_message":  null
}
```

`X-Flydesk-Signature` is an HMAC-SHA256 of the raw body using
`FLYDESK_IDP_WEBHOOK_HMAC_SECRET`. The publisher retries on `5xx` and
`429` up to `FLYDESK_IDP_WEBHOOK_MAX_ATTEMPTS` with exponential
back-off + jitter; anything else `4xx` is treated as permanent.
**Dedupe by `event_id` on the client** — the publisher's at-least-once
delivery semantics mean the same `event_id` may arrive more than once
if the receiver returned a 5xx or timed out.

---

## 4. Common DTO building blocks

### `DocSpec`

```jsonc
{
  "docType": {
    "documentType": "passport",
    "description": "EU passport",
    "country": "ES"
  },
  "fieldGroups": [ /* one or more FieldGroup */ ],
  "validators": {
    "visual": [
      {"name": "photo_present", "description": "A passport photo is visible."}
    ]
  }
}
```

### `FieldSpec`

```jsonc
{
  "fieldName": "iban",
  "fieldDescription": "Recipient IBAN.",
  "fieldType": "string",
  "regex": "^[A-Z]{2}\\d{2}[A-Z0-9]+$",      // optional
  "enum": ["EUR", "USD"],                     // optional (string fields)
  "min": 0,                                   // optional (numeric)
  "max": 1000000,                             // optional (numeric)
  "standard_validators": [
    {"type": "iban", "severity": "error"},
    {"type": "country_code", "params": {"country": "ES"}, "severity": "warning"}
  ]
}
```

### `RuleSpec`

```jsonc
{
  "id": "iban_valid",
  "predicate": "The IBAN field passes the mod-97 checksum.",
  "parents": [
    {"parentType": "field", "documentType": "invoice", "fieldNames": ["iban"]}
  ],
  "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
}
```

Parents can be `field`, `validator`, or `rule`. See
[rule-engine.md](rule-engine.md).

### `StageToggles`

```jsonc
{
  "splitter": false,
  "classifier": true,
  "field_validation": true,
  "visual_authenticity": false,
  "content_authenticity": false,
  "judge": false,
  "judge_escalation": false,
  "bbox_refine": false,
  "transform": false,
  "rule_engine": false
}
```

The extractor and `bbox_validation` are always on; `assemble`/`load`
are unconditional. The `transform` toggle is a no-op when
`options.transformations` is empty.

### EDA event envelopes (audit + webhook payload)

Every event the service publishes — `IDPJobSubmitted`,
`IDPJobCompleted`, `IDPBboxRefineRequested`,
`IDPBboxRefineCompleted` — carries a typed envelope:

```jsonc
{
  "event_id":       "f0c7b3aa-2f43-4d34-bf6c-3b09e6efbb19",  // UUID v4
  "event_type":     "IDPJobCompleted",                       // routing discriminator
  "version":        "1.0.0",                                 // semver of the payload shape
  "occurred_at":    "2026-05-15T16:42:11.103Z",              // UTC ISO-8601
  "correlation_id": "req-…",                                 // echoes inbound X-Correlation-Id
  "tenant_id":      "tenant-…",                              // echoes X-Tenant-Id when set
  "job_id":         "…",
  "status":         "SUCCEEDED",                             // type-specific
  "started_at":     "…",
  "finished_at":    "…",
  "attempts":       1
}
```

Webhook deliveries surface the same envelope on the wire, plus the
`result` (for `SUCCEEDED` / `PARTIAL_SUCCEEDED`) and `error_code` /
`error_message` (for `FAILED`). Dedupe by `event_id` on the client
since the publisher retries on delivery failure.

### `Transformation` (discriminated union)

Two `type` values today; the union is open for new declarative types.

```jsonc
// Declarative entity resolution
{
  "type": "entity_resolution",
  "target_group": "personas",
  "output_group": null,                  // null = mutate in place
  "scope": "request",                    // "task" (default) | "request"
  "match_by": ["dni", "nombre"],
  "min_shared_tokens": 2
}

// Free-form LLM transformation
{
  "type": "llm",
  "target_group": "personas",
  "intention": "Normalize each cargo to a closed taxonomy: administrador_unico, consejero, apoderado, otros.",
  "scope": "task"
}
```

See [docs/transformations.md](transformations.md) for fuller examples
and the rationale behind both types.

### `DocumentInput`

Every entry in `documents[]`:

```jsonc
{
  "filename":       "deed.pdf",                     // required, non-empty
  "content_base64": "JVBERi0xLjQK...",              // required; base64 (data: URLs accepted, prefix stripped)
  "content_type":   "application/pdf",              // optional MIME hint; sniffed when omitted
  "document_type":  "escritura_poderes"              // optional pin; must match a docs[].docType.documentType
}
```

Accepted binary inputs (the `BinaryNormalizer` turns the rest into
provider-readable rasters before extraction):

| Family               | Formats                                                                          | Path                           |
| -------------------- | -------------------------------------------------------------------------------- | ------------------------------ |
| PDF                  | PDF/A, encrypted-on-failure                                                       | passthrough (or 422 `encrypted_pdf`) |
| Raster the LLM reads | PNG, JPEG, GIF, WebP                                                              | passthrough                    |
| Raster the LLM doesn't read | HEIC/HEIF, AVIF, multi-frame TIFF, SVG, BMP                                  | Pillow + pillow-heif + cairosvg |
| Office               | DOCX, XLSX, PPTX, RTF, ODT, HTML                                                  | `OfficeConverter` (Gotenberg / LibreOffice) |
| Archive / bundle     | ZIP, 7z, TAR, GZIP, EML, MSG                                                      | fanned out into multiple `documents[]` entries |

### `BoundingBox`

```jsonc
{
  "xmin": 0.15,                                // all values in [0, 1]
  "ymin": 0.26,
  "xmax": 0.85,
  "ymax": 0.30,
  "quality":        "good",                    // "good" | "poor" | "suspicious" | "invalid" | "empty" | null
  "quality_score":  0.94,                      // continuous geometric score in [0, 1]
  "source":         "pdf_text",                // "llm" | "pdf_text" | "ocr" | "none" | null
  "refinement_confidence": 0.91                // null for source in {llm, none}
}
```

`source` is the discriminator that lets strict callers filter
grounded-only boxes (`pdf_text` / `ocr`) and treat `llm` boxes as
approximate region hints. `quality` reflects geometric plausibility,
not whether the box actually fences the real text — see
[docs/pipeline.md](pipeline.md) on LLM bbox imprecision.

### `ExtractedField` (recursive)

```jsonc
{
  "fieldName":       "iban",                    // alias: "name"
  "fieldValueFound": "ES7600491500051234567892", // alias: "value" — string | int | float | bool | ExtractedField[] | null
  "confidence":      0.98,                      // model confidence in [0, 1]
  "pagesFound":      [3],
  "bbox":            { /* BoundingBox */ },
  "notes":           "Bottom-right block on the invoice header.",
  "field_validation": {
    "valid":  true,
    "errors": [
      // { "rule": "type"|"pattern"|"format"|"enum"|"minimum"|"maximum"|"standard", "message": "..." }
    ]
  },
  "judge": {
    "status":          "PASS",                  // "PASS" | "FAIL" | "UNCERTAIN"
    "confidence":      0.99,
    "evidence":        "ES76 0049 1500 0512 3456 7892",
    "notes":           "Matches the IBAN on the bottom-right block.",
    "flag_for_review": false
  }
}
```

For `fieldType: "array"`, `fieldValueFound` is a list of
`ExtractedField` rows whose `fieldName`s mirror the request-side
`items[].fieldName`s. The structure recurses to arbitrary depth.

### `DocumentInfo` (per-input-file summary)

One entry per submitted file in `files[]`:

```jsonc
{
  "filename":      "deed.pdf",
  "media_type":    "application/pdf",
  "page_count":    21,
  "bytes":         384112,
  "document_type": "escritura_poderes",         // caller pin OR classifier verdict; null when neither resolved
  "classification": {                           // null when classifier was skipped (pin set OR stage off)
    "document_type": "escritura_poderes",
    "matched":       true,
    "confidence":    0.97,
    "description":   "Spanish notarial power of attorney.",
    "notes":         ""
  }
}
```

### `EscalationInfo`

Top-level `escalation` block. `null` unless `stages.judge_escalation`
is on AND the judge's first pass exceeded the threshold:

```jsonc
{
  "triggered":            true,
  "primary_model":        "anthropic:claude-haiku-4-5",
  "escalation_model":     "anthropic:claude-opus-4-7",
  "primary_fail_rate":    0.66,
  "escalation_fail_rate": 0.10,
  "accepted":             true                  // true ⇒ escalation result replaced the primary in the response
}
```

### `UsageBreakdown`

Top-level `usage` block. See [§ 2 → `usage` block](#usage-block) for
the per-field meaning and aggregation rules.

### `TraceEntry`

One entry per executed pipeline node, in DAG order:

```jsonc
{
  "node":          "extract",                   // load | discover | classify | plan_tasks | extract | bbox_validation | bbox_refine | field_validation | visual_authenticity | content_authenticity | judge | judge_escalation | transform | rules | assemble
  "started_at":    "2026-05-15T16:42:03.140Z",
  "completed_at":  "2026-05-15T16:42:24.493Z",
  "latency_ms":    21352.88,
  "status":        "success"                    // "success" | "failed" | "skipped"
}
```

### `StandardValidatorSpec`

Pinned to a `FieldSpec.standard_validators[]`. See
[docs/standard-validators.md](standard-validators.md) for every
built-in's behaviour and params.

```jsonc
{
  "type":     "iban",                           // see § 4 → StandardValidatorType for the full enum
  "params":   {"country": "ES"},                // validator-specific (most are empty)
  "severity": "warning"                         // "error" (default) flips field.valid=false; "warning" records but keeps valid
}
```

Enum values (all from `interfaces.enums.standard_validator.StandardValidatorType`):

- **Network**: `email`, `uri`, `url`, `domain`, `slug`, `ipv4`, `ipv6`
- **Temporal**: `date`, `datetime`, `time`, `iso_8601`
- **Identifiers**: `uuid`, `json`, `hex_color`
- **Finance**: `iban`, `bic`, `credit_card`, `currency_code`, `amount`
- **Telephony**: `phone_e164`
- **Geographic**: `country_code`, `language_code`, `postal_code`, `latitude`, `longitude`
- **National IDs**: `nif`, `nie`, `cif`, `vat_id`, `ssn`, `passport_number`

### `FieldType` enum

`FieldSpec.fieldType` and `FieldItem.fieldType` accept:

`string` · `integer` · `number` · `boolean` · `date` · `datetime` ·
`time` · `array` · `object`

`array` requires `items[]` (the columns of every repeating row). All
other types reject `items`.

### `JobStatus` enum

`QUEUED` · `RUNNING` · `PARTIAL_SUCCEEDED` · `REFINING_BBOXES` ·
`SUCCEEDED` · `FAILED` · `CANCELLED`. The state machine is documented
under [§ 3 → Poll status](#poll-status--get-apiv1jobsid).

### `BboxRefineStatus` enum

`pending` · `running` · `succeeded` · `failed`. Populated on the
`bbox_refine_status` column / API field only when the job was
submitted with `options.stages.bbox_refine=true`; `null` otherwise.

### `VersionInfo` — `GET /api/v1/version`

```jsonc
{
  "service":        "flydesk-idp",
  "version":        "0.1.0",                    // semantic version baked into the wheel
  "model":          "anthropic:claude-sonnet-4-6",
  "fallback_model": "openai:gpt-4o",            // "" disables the fallback
  "eda_adapter":    "postgres"                  // postgres | memory | redis | kafka
}
```

---

## 5. Authentication

Two layers, both optional.

- **API keys** — set `FLYDESK_IDP_API_KEYS` to a comma-separated list
  of secrets; `fireflyframework-pyfly` enforces them via the
  `security-api-key` starter when the env var is present.
- **OIDC / OAuth2** — out of scope here; use `fireflyframework-pyfly`'s
  `security-jwt` starter and add an extra `@bean` for the JWT decoder.

For development the API is open. Production deployments should set at
least one of the two.

---

## 6. Error codes

Every error response is RFC 7807 `application/problem+json` with a
stable `code` that callers can branch on. The catalogue:

| Status | `code`                          | Endpoint(s)                              | When                                                                                                              |
| -----: | ------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
|    400 | `invalid_request`               | every endpoint                           | Generic `ValueError` raised before the handler — typically a hand-rolled cross-field check that pydantic couldn't express. |
|    400 | _various_ (pydantic field path) | every endpoint                           | Pydantic validation failed. The body lists every offending path.                                                  |
|    404 | `JOB_NOT_FOUND`                 | `/api/v1/jobs/{id}*`                     | Unknown `job_id`.                                                                                                 |
|    408 | `extraction_timeout`            | `POST /api/v1/extract`                   | Sync pipeline exceeded `FLYDESK_IDP_SYNC_TIMEOUT_S` (default 60 s). Retry as an async job.                        |
|    409 | `job_not_ready`                 | `GET /api/v1/jobs/{id}/result`           | Job is in `QUEUED` / `RUNNING` / `FAILED` / `CANCELLED`. Body includes the current status under `extensions`.     |
|    409 | `job_not_cancellable`           | `DELETE /api/v1/jobs/{id}`               | Job has already started or terminated. Only `QUEUED` jobs can be cancelled.                                       |
|    413 | `document_too_large`            | `POST /api/v1/extract`, `POST /jobs`     | Decoded per-file size exceeds `FLYDESK_IDP_MAX_BYTES` (default 32 MiB). The body names the offending file.        |
|    422 | `invalid_base64`                | `POST /api/v1/extract`, `POST /jobs`     | A `content_base64` field failed strict base64 parsing.                                                            |
|    422 | `invalid_request`               | `POST /api/v1/extract`, `POST /jobs`     | Semantic validator rejected the payload. Body embeds the full `ValidationReport` (`errors[]` + `warnings[]`).      |
|    422 | `encrypted_pdf`                 | `POST /api/v1/extract`, `POST /jobs`     | Submitted PDF is password-protected.                                                                              |
|    422 | `unsupported_binary`            | `POST /api/v1/extract`, `POST /jobs`     | MIME type not on the supported list and could not be sniffed.                                                     |
|    422 | `office_conversion_failed`      | `POST /api/v1/extract`, `POST /jobs`     | Office adapter (Gotenberg / LibreOffice) rejected the conversion.                                                 |
|    422 | `archive_extraction_failed`     | `POST /api/v1/extract`, `POST /jobs`     | Archive bundle (ZIP / 7z / TAR / GZIP / EML / MSG) failed to unpack.                                              |
|    422 | `image_conversion_failed`       | `POST /api/v1/extract`, `POST /jobs`     | Image normaliser failed to produce a provider-readable raster (corrupt HEIC / SVG / etc.).                        |
|    503 | _composite_                     | `GET /actuator/health/readiness`         | At least one of `database_health` / `eda_health` reported `DOWN`. Body lists every indicator.                     |

Non-fatal pipeline-stage failures don't surface as HTTP errors — they
land in `ExtractionResult.pipeline_errors[]` so the request still
returns the partial result.
