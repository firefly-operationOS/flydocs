# API reference

The canonical reference for the HTTP surface in v1. Every example here
mirrors the wire format the server emits — running `task openapi`
produces the machine-readable OpenAPI 3.1 spec from the same DTOs.

> **What this doc covers:** endpoint paths, status codes, headers,
> request/response shape outlines, and the error catalogue. **When to
> read it:** while integrating against the HTTP API, or to audit what
> your client is on the wire.
>
> **Where else to look:**
> - Full request/response shapes with worked examples: [`payload-reference.md`](payload-reference.md).
> - Migrating from v0: [`migration-v0-to-v1.md`](migration-v0-to-v1.md).
> - Stage internals (timeouts, concurrency, cost): [`pipeline.md`](pipeline.md).
> - Built-in validators: [`validators.md`](validators.md).
> - Business rule semantics: [`rule-engine.md`](rule-engine.md).

---

## 1. Surface at a glance

| Method   | Path                                  | Purpose                                                                |
| -------- | ------------------------------------- | ---------------------------------------------------------------------- |
| `POST`   | `/api/v1/extract`                     | Synchronous extraction. Blocks until the pipeline finishes.            |
| `POST`   | `/api/v1/extract:validate`            | Dry-run the semantic validator (no LLM call, no DB write).             |
| `POST`   | `/api/v1/extractions`                 | Submit a queued extraction. Returns `202` + `Extraction`.              |
| `GET`    | `/api/v1/extractions`                 | Filtered, paginated listing of extractions.                            |
| `GET`    | `/api/v1/extractions/{id}`            | Current state of an `Extraction` (incl. post-processing block).        |
| `GET`    | `/api/v1/extractions/{id}/result`     | Final `ExtractionResult`. Long-poll for grounded bboxes with `wait_for_bboxes`. |
| `DELETE` | `/api/v1/extractions/{id}`            | Cancel an extraction that is still `queued`.                           |
| `GET`    | `/api/v1/version`                     | Build + model + EDA-adapter info.                                       |
| `GET`    | `/actuator/health`                    | Composite health (DB + EDA).                                            |
| `GET`    | `/actuator/health/liveness`           | Liveness probe (always responds while the process is alive).            |
| `GET`    | `/actuator/health/readiness`          | Readiness probe — `503` when `database_health` or `eda_health` is `DOWN`. |
| `GET`    | `/actuator/metrics`                   | Prometheus metrics.                                                     |
| `GET`    | `/admin`                              | PyFly Admin dashboard — beans, mappings, env, CQRS, traces, loggers, health. |
| `GET`    | `/docs`                               | Swagger UI (OpenAPI 3.1).                                               |
| `GET`    | `/openapi.json`                       | Machine-readable OpenAPI 3.1 spec.                                      |

Resource ids are prefixed ULIDs (`ext_01HEM2ZZ7M0Q8…`). Timestamps are
UTC RFC 3339 strings; durations land on `*_ms` fields in milliseconds.

Errors follow RFC 7807 (`application/problem+json`). The advice at
`web/advice/exception_advice.py` maps domain exceptions to:

```jsonc
{
  "type":       "https://flydocs.dev/problems/<code>",
  "title":      "Short human-readable summary",
  "status":     409,
  "code":       "not_ready",                   // stable application code (snake_case)
  "detail":     "Human-readable explanation.",
  "instance":   null,
  "extensions": { /* arbitrary extra context */ }
}
```

See [§ 8 (Error codes)](#8-error-codes) for the catalogue.

### Request headers honoured

| Header              | Surface(s)                              | Meaning                                                                                          |
| ------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `Idempotency-Key`   | `POST /api/v1/extractions`              | Replays the original `Extraction` response when the same key is seen twice (no duplicate row).   |
| `X-Correlation-Id`  | every endpoint                          | Propagated through every pipeline stage, every `outbound_call` log line, and every EDA event / webhook. |
| `X-Request-Id`      | every endpoint                          | Echoed back in the response. Generated server-side when absent.                                   |
| `X-Tenant-Id`       | every endpoint                          | Echoed back; copied into the EDA event / webhook envelopes as `tenant_id`.                        |
| `traceparent`       | every endpoint                          | W3C trace context. Propagated to OTLP spans and downstream HTTP calls.                            |
| `tracestate`        | every endpoint                          | W3C trace state. Same propagation as `traceparent`.                                              |
| `Authorization`     | every endpoint (when API keys enabled)  | `Bearer <key>` or the configured scheme. See [§ 7 (Authentication)](#7-authentication).          |

---

## 2. Synchronous extraction — `POST /api/v1/extract`

Blocks until the orchestrator finishes or `FLYDOCS_SYNC_TIMEOUT_S`
elapses (default 60 s). On timeout the controller returns `408 timeout`.

The endpoint accepts either `application/json` (with
`files[].content_base64`) or `multipart/form-data` (with file parts +
a `request` JSON part). The pipeline path is identical after parse.

The request always carries a non-empty `files[]` list — a single file
is just a one-element list. See [§ 2c](#2c-multi-file--sub-document-discovery)
for the multi-file case where each file is processed independently
and may pin its own `expected_type`.

### Request (JSON mode)

```jsonc
{
  "intention": "KYC review for a Spanish power-of-attorney deed.",
  "files": [
    {
      "filename":       "deed.pdf",
      "content_base64": "JVBERi0xLjQK...",
      "content_type":   "application/pdf"
    }
  ],
  "document_types": [
    {
      "id":          "escritura_poderes",
      "description": "Escritura notarial de poderes",
      "country":     "ES",
      "field_groups": [
        {
          "name":        "otorgamiento",
          "description": "Otorgamiento data",
          "fields": [
            {
              "name":        "fecha",
              "description": "Date of issuance in ISO format.",
              "type":        "string",
              "validators":  [{"name": "date"}]
            },
            {
              "name":        "otorgante_dni_nie",
              "description": "DNI or NIE of the grantor.",
              "type":        "string",
              "validators": [
                {"name": "nif", "severity": "warning"},
                {"name": "nie", "severity": "warning"}
              ]
            }
          ]
        }
      ],
      "visual_checks": [
        {"name": "firma_notario",  "description": "The notary's signature is present."},
        {"name": "sello_notarial", "description": "The notarial seal is present."}
      ]
    }
  ],
  "rules": [
    {
      "id": "kyc_complete",
      "predicate": "Both DNI/NIE fields are populated AND fecha is populated.",
      "parents": [
        {"kind": "field", "document_type": "escritura_poderes",
         "fields": ["otorgante_dni_nie", "fecha"]}
      ],
      "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
    }
  ],
  "options": {
    "model":         "anthropic:claude-opus-4-7",
    "language_hint": "es",
    "stages": {
      "splitter":              false,
      "field_validation":      true,
      "visual_authenticity":   true,
      "content_authenticity":  false,
      "judge":                 true,
      "bbox_refine":           true,
      "transform":             true,
      "rule_engine":           true
    },
    "transformations": [
      {
        "type":         "entity_resolution",
        "target_group": "personas",
        "match_by":     ["dni", "nombre"],
        "scope":        "request"
      }
    ]
  }
}
```

> Full field-by-field reference: [payload-reference.md](payload-reference.md).
> Transformation semantics: [transformations.md](transformations.md).

### Request (multipart mode)

```http
POST /api/v1/extract HTTP/1.1
Content-Type: multipart/form-data; boundary=---xyz

-----xyz
Content-Disposition: form-data; name="request"
Content-Type: application/json

{
  "document_types": [ /* … */ ],
  "rules":          [ /* … */ ],
  "options":        { /* … */ },
  "file_options": {
    "deed.pdf":     { "expected_type": "escritura_poderes" },
    "id_front.jpg": { "expected_type": "dni" }
  }
}
-----xyz
Content-Disposition: form-data; name="files"; filename="deed.pdf"
Content-Type: application/pdf

<binary bytes>
-----xyz
Content-Disposition: form-data; name="files"; filename="id_front.jpg"
Content-Type: image/jpeg

<binary bytes>
-----xyz--
```

`filename` and `content_type` come from the part headers; `content_base64`
is absent. `expected_type` rides in `file_options`, keyed by filename.

### Response — 200 OK

```jsonc
{
  "id":     "ext_01HEM2ZZ7M0Q8...",
  "status": "success",                          // "success" | "partial"

  "files": [                                    // one entry per input file
    {
      "filename":       "deed.pdf",
      "media_type":     "application/pdf",
      "page_count":     21,
      "bytes":          384112,
      "matched_type":   "escritura_poderes",     // caller's expected_type OR classifier verdict
      "classification": null                     // null when classifier was skipped
    }
  ],

  "documents": [
    {
      "type":         "escritura_poderes",
      "source_file":  "deed.pdf",
      "missing":      false,
      "pages":        [1, 2, /* ... */ 21],
      "confidence":   1.0,
      "description":  "Escritura notarial de poderes",
      "notes":        null,
      "field_groups": [
        {
          "name": "otorgamiento",
          "fields": [
            {
              "name":       "fecha",
              "value":      "2025-05-15",
              "pages":      [1],
              "confidence": 0.98,
              "bbox": {
                "xmin": 0.15, "ymin": 0.26, "xmax": 0.85, "ymax": 0.30,
                "source": "pdf_text", "quality": "good", "quality_score": 0.94,
                "refinement_confidence": 0.91
              },
              "validation": {"valid": true, "errors": []},
              "judge": {
                "status":          "pass",
                "confidence":      0.99,
                "evidence":        "15 May 2025",
                "notes":           "Date matches the otorgamiento date.",
                "flag_for_review": false
              },
              "notes": "Otorgamiento date on first page header."
            }
          ]
        }
      ],
      "authenticity": {
        "visual": [
          {"name": "firma_notario",  "passed": true, "confidence": 0.85, "notes": null},
          {"name": "sello_notarial", "passed": true, "confidence": 0.90, "notes": null}
        ],
        "content": null
      }
    }
  ],

  "discovered_documents": [],

  "rule_results": [
    {
      "rule_id":        "kyc_complete",
      "predicate":      "Both DNI/NIE fields are populated AND fecha is populated.",
      "output":         "true",
      "summary":        "All required identity fields are present.",
      "notes":          [],
      "human_revision": null
    }
  ],

  "request_transformations": [],

  "pipeline": {
    "model":      "anthropic:claude-opus-4-7",
    "latency_ms": 43580,
    "trace": [
      {"node": "load",                 "started_at": "...", "completed_at": "...", "latency_ms":   173.16, "status": "success"},
      {"node": "discover",             "started_at": "...", "completed_at": "...", "latency_ms": 14725.81, "status": "success"},
      {"node": "classify",             "started_at": "...", "completed_at": "...", "latency_ms": 12474.10, "status": "success"},
      {"node": "plan_tasks",           "started_at": "...", "completed_at": "...", "latency_ms":   234.83, "status": "success"},
      {"node": "extract",              "started_at": "...", "completed_at": "...", "latency_ms": 21352.88, "status": "success"},
      {"node": "bbox_validation",      "started_at": "...", "completed_at": "...", "latency_ms":     0.43, "status": "success"},
      {"node": "bbox_refine",          "started_at": "...", "completed_at": "...", "latency_ms":  4112.05, "status": "success"},
      {"node": "field_validation",     "started_at": "...", "completed_at": "...", "latency_ms":     0.79, "status": "success"},
      {"node": "visual_authenticity",  "started_at": "...", "completed_at": "...", "latency_ms":  7385.81, "status": "success"},
      {"node": "judge",                "started_at": "...", "completed_at": "...", "latency_ms": 20721.86, "status": "success"},
      {"node": "rules",                "started_at": "...", "completed_at": "...", "latency_ms": 26099.84, "status": "success"},
      {"node": "assemble",             "started_at": "...", "completed_at": "...", "latency_ms":     0.05, "status": "success"}
    ],
    "errors":     [],
    "escalation": null,
    "usage": {
      "total_input_tokens":    162109,
      "total_output_tokens":    22218,
      "total_tokens":          184327,
      "total_cost_usd":           3.0651,
      "total_requests":             0,
      "total_latency_ms":      96739.0,
      "record_count":              27,
      "cache_creation_tokens":      0,
      "cache_read_tokens":          0,
      "by_agent": {
        "flydocs-extractor": {"input_tokens": 78936, "output_tokens": 6057, "total_tokens": 84993, "cost_usd": 1.638},
        "flydocs-judge":     {"input_tokens": 73023, "output_tokens": 5719, "total_tokens": 78742, "cost_usd": 1.524}
      },
      "by_model": {
        "anthropic:claude-opus-4-7": {"input_tokens": 318338, "output_tokens": 17797, "total_tokens": 336135, "cost_usd": 6.110}
      }
    }
  }
}
```

Top-level shape rationale:

- Caller-facing data sits at the top level: `files[]`,
  `documents[]`, `discovered_documents[]`, `rule_results[]`,
  `request_transformations[]`.
- Pipeline meta (`model`, `latency_ms`, `trace`, `errors`,
  `escalation`, `usage`) groups under `pipeline` so business data
  isn't drowned in instrumentation.
- `status: "partial"` is the flag for "result returned but at least
  one non-fatal stage failed and surfaced under `pipeline.errors`".

#### `pipeline.usage` block

Aggregated token counts and estimated USD cost across every LLM call
the request made. Scoped via `correlation_id` to this request.

| Field                    | Meaning                                                                                                  |
|--------------------------|----------------------------------------------------------------------------------------------------------|
| `total_input_tokens`     | Sum of prompt tokens across all calls.                                                                   |
| `total_output_tokens`    | Sum of completion tokens.                                                                                |
| `total_tokens`           | Sum of input + output.                                                                                   |
| `total_cost_usd`         | Estimated USD cost from the configured pricing table.                                                    |
| `record_count`           | Number of distinct LLM calls behind this request.                                                        |
| `total_latency_ms`       | Sum of per-call wall-clock times (with `asyncio.gather` parallelism this can exceed `pipeline.latency_ms`). |
| `cache_creation_tokens`  | Prompt tokens written to the provider's prompt cache (Anthropic / Bedrock-Anthropic with `cache_control`; Gemini via `CachedContent`). Always 0 when `FLYDOCS_PROMPT_CACHE=off`. |
| `cache_read_tokens`      | Prompt tokens served from the provider's prompt cache on a hit.                                          |
| `by_agent`               | Per-agent breakdown (extractor, classifier, splitter, judge, visual, content, rule-engine).              |
| `by_model`               | Per-model breakdown — useful when fallback or escalation switched models mid-request.                    |

`null` when cost tracking is disabled or no LLM call fired.

#### `pipeline.trace` block

One entry per executed pipeline node, ordered as the DAG ran them.
Each entry has `node`, `started_at`, `completed_at`, `latency_ms`, and
a `status` of `success` | `failed` | `skipped`. Useful for spotting
which stages dominate a request's latency.

#### Operational notes

The cost number is a provider-agnostic **estimate** from
`genai-prices` — Anthropic, OpenAI, Google, Mistral, etc. are priced
uniformly. Local overrides for fast-moving Claude 4 models live in
`core/observability/pricing.py`. The same per-call data is also
emitted on `outbound_call` log lines (one per LLM call, with
`correlation_id`, `in_tokens`, `out_tokens`, `cost_usd`), so spend
forensics work without parsing the response.

### Error responses

See [§ 8 (Error codes)](#8-error-codes) for the full catalogue. The
sync endpoint can return:

| Status | Code                          | When                                                                                  |
| -----: | ----------------------------- | ------------------------------------------------------------------------------------- |
|    400 | `invalid_request`             | Pydantic validation failed (RFC 7807 body with field errors).                         |
|    408 | `timeout`                     | Sync pipeline exceeded `FLYDOCS_SYNC_TIMEOUT_S`.                                      |
|    413 | `file_too_large`              | Decoded per-file size exceeds `FLYDOCS_MAX_BYTES` (default 32 MiB).                   |
|    422 | `invalid_base64`              | A `content_base64` field failed strict base64 parsing.                                |
|    422 | `validation_failed`           | Semantic validator rejected the payload. Body embeds the full report.                 |
|    422 | `encrypted_pdf`               | The submitted PDF is password-protected.                                              |
|    422 | `unsupported_file`            | MIME not on the supported list (and could not be sniffed).                            |
|    422 | `office_conversion_failed`    | Gotenberg / LibreOffice refused conversion.                                           |
|    422 | `archive_extraction_failed`   | Bundle (ZIP / 7z / TAR / GZIP / EML / MSG) failed to unpack.                          |
|    422 | `image_conversion_failed`     | Pillow / pillow-heif / cairosvg failed to normalise the image.                        |

### 2b. Dry-run the validator — `POST /api/v1/extract:validate`

Runs only the semantic `RequestValidator` — no LLM call, no document
load, no DB write. Use it to check a payload from a CI pipeline, a UI
before submit, or while iterating on rule definitions. Always returns
`200`; the caller inspects `ok` to decide whether to proceed.

```jsonc
// Request body is exactly the same shape as POST /api/v1/extract.

// Response body
{
  "ok":            false,
  "error_count":   2,
  "warning_count": 1,
  "errors": [
    {"severity": "error", "code": "document_type_unknown",
     "message": "expected_type 'utility_bill' is not declared in document_types[].",
     "path":    "files[2].expected_type"},
    {"severity": "error", "code": "rule_unknown_field",
     "message": "Rule 'kyc_complete' references field 'nif' which is not declared on document_type 'passport'.",
     "path":    "rules[0]"}
  ],
  "warnings": [
    {"severity": "warning", "code": "no_field_groups",
     "message": "DocumentType 'cover_page' has only one field group; consider grouping.",
     "path":    "document_types[1]"}
  ]
}
```

The same payload shape is embedded under `extensions` of the `422
validation_failed` response that the real `/extract` and `/extractions`
endpoints emit, so a 422 carries everything you would see from a dry-run.

### 2c. Multi-file & sub-document discovery

The pipeline supports two complementary shapes for getting multiple
documents out of a single request:

1. **Multi-file submission**: `files[]` is always a list — a single
   file is just a one-element list, and a multi-file submission is the
   exact same payload with more entries. Each entry carries its own
   `filename`, `content_base64`, `content_type`, and optional
   `expected_type` pin.
2. **Sub-document discovery**: enable `options.stages.splitter` and a
   single uploaded PDF that contains several documents inside (deed
   + ID + utility bill, for example) is split into its sub-documents
   automatically. Each sub-document is then classified against the
   declared `document_types[]` and extracted independently.

The two work in any combination — you can submit five files and turn
on the splitter, and every file gets its own discover → classify →
extract sub-pipeline.

Each entry in `files[]` carries an optional `expected_type` pin in
addition to the file content:

```jsonc
{
  "intention": "KYC pack: deed + Spanish DNI + utility bill.",
  "files": [
    {
      "filename":       "deed.pdf",
      "content_base64": "JVBERi0xLjQK...",
      "content_type":   "application/pdf",
      "expected_type":  "escritura_poderes"
    },
    {
      "filename":       "dni.jpg",
      "content_base64": "/9j/4AAQ...",
      "content_type":   "image/jpeg"
                                              // no pin — classifier picks the document_type
    },
    {
      "filename":       "utility.pdf",
      "content_base64": "JVBERi0xLjQK...",
      "content_type":   "application/pdf"
    }
  ],
  "document_types": [
    { "id": "escritura_poderes", "description": "...", "country": "ES", "field_groups": [/* ... */] },
    { "id": "dni",               "description": "...", "country": "ES", "field_groups": [/* ... */] },
    { "id": "utility_bill",      "description": "...", "country": "ES", "field_groups": [/* ... */] }
  ],
  "options": {
    "stages": {
      "classifier":       true,
      "splitter":         false,
      "field_validation": true,
      "judge":            true
    }
  }
}
```

The response shape is the same `ExtractionResult`:

- `files[]` has one entry per input file. For unpinned files,
  `files[i].classification` carries the classifier verdict
  (`document_type`, `matched`, `confidence`, `description`, `notes`).
- `documents[i].source_file` carries the input filename that each
  extracted document came from — so the caller can map per-task output
  back to the file that produced it.
- Files the classifier marks `unmatched` skip extraction and appear in
  `discovered_documents` with `type: "unmatched"` and `source_file`
  set to the original filename.

Two correctness notes:

- An `expected_type` pin **must** reference a `document_types[].id`.
  Unknown pins are rejected with `422 validation_failed /
  code=document_type_unknown` before the pipeline runs.
- Per-file size limits (`FLYDOCS_MAX_BYTES`) are enforced
  individually — a single oversized file rejects the whole request
  with `413 file_too_large` naming that file.

---

## 3. Async extractions — `POST /api/v1/extractions`

For documents that may take longer than the sync ceiling, or for
fire-and-forget workflows with a webhook callback. The submit endpoint
returns immediately; the worker drives the same orchestrator behind
the scenes.

> **Multi-file is supported on async too.** Submit a non-empty
> `files[]` list exactly like the sync endpoint — the worker drives
> the same orchestrator and refines bboxes out-of-band via the
> `BboxRefineWorker` (see [pipeline.md](pipeline.md#bbox-refinement-sync-vs-async)).

### Submit

```http
POST /api/v1/extractions
Content-Type: application/json
Idempotency-Key: 4b2e8c70-8d10-4f04-92ee-9d8...   ; optional, replays the response if reused

{
  "intention": "...",
  "files":         [ { "filename": "...", "content_base64": "...", "content_type": "..." } ],
  "document_types":[ /* same as /extract */ ],
  "rules":         [ /* same as /extract */ ],
  "options":       { /* same as /extract */ },
  "callback_url":  "https://workflow.example.com/idp/webhook",
  "metadata":      { "tenant_id": "acme", "external_id": "..." }
}
```

```http
202 Accepted
Content-Type: application/json

{
  "id":              "ext_01HEM2ZZ7M0Q8...",
  "status":          "queued",
  "submitted_at":    "2026-05-14T10:42:00Z",
  "started_at":      null,
  "finished_at":     null,
  "attempts":        0,
  "error":           null,
  "post_processing": null
}
```

Multipart mode is identical to the sync endpoint — same `request`
JSON part shape.

### List extractions — `GET /api/v1/extractions`

Filterable, paginated listing. All filters are optional and combine
with `AND`. Newest-first ordering (`submitted_at DESC`).

| Query param              | Type             | Default | Meaning                                                                                       |
| ------------------------ | ---------------- | ------: | --------------------------------------------------------------------------------------------- |
| `status`                 | CSV of statuses  |   `""`  | Match any of the listed values. Valid: `queued`, `running`, `succeeded`, `failed`, `cancelled`. |
| `post_processing_status` | CSV              |   `""`  | Match the bbox-refinement leg. Valid: `pending`, `running`, `succeeded`, `failed`.            |
| `idempotency_key`        | string           |   `""`  | Exact match against the submit-time `Idempotency-Key` header.                                 |
| `created_after`          | RFC 3339         |  `null` | Inclusive lower bound on `submitted_at`.                                                      |
| `created_before`         | RFC 3339         |  `null` | Inclusive upper bound on `submitted_at`.                                                      |
| `limit`                  | int (1–500)      |    `50` | Page size. Capped server-side at 500.                                                         |
| `offset`                 | int ≥ 0          |     `0` | Skip this many rows. Pair with `total` to paginate.                                           |

```http
GET /api/v1/extractions?status=succeeded&post_processing_status=running&limit=25
```

```jsonc
{
  "items":  [ /* Extraction[] — same shape as the single-extraction GET below */ ],
  "total":  187,                    // filtered count, ignores limit/offset
  "limit":  25,
  "offset": 0
}
```

### Poll state — `GET /api/v1/extractions/{id}`

```jsonc
{
  "id":           "ext_01HEM2ZZ7M0Q8...",
  "status":       "succeeded",                   // "queued" | "running" | "succeeded" | "failed" | "cancelled"
  "submitted_at": "2026-05-14T10:42:00Z",
  "started_at":   "2026-05-14T10:42:03Z",
  "finished_at":  "2026-05-14T10:42:48Z",
  "attempts":     1,
  "error":        null,

  // Populated only when options.stages.bbox_refine=true on async requests.
  "post_processing": {
    "bbox_refinement": {
      "status":      "running",                  // "pending" | "running" | "succeeded" | "failed"
      "started_at":  "2026-05-14T10:42:49Z",
      "finished_at": null,
      "attempts":    1,
      "error":       null
    }
  }
}
```

The state machine — one linear track, with post-processing decoupled:

```text
main pipeline:
  queued ─▶ running ─▶ succeeded | failed
  queued ─▶ cancelled              (only while still queued)

post-processing (bbox refinement, when stages.bbox_refine=true):
  null ─▶ pending ─▶ running ─▶ succeeded | failed
```

`status == "succeeded"` is reached as soon as the main pipeline ends.
The post-processing block evolves independently and is the only place
that signals refinement progress.

Unknown `id` → `404 not_found`.

### Fetch the result — `GET /api/v1/extractions/{id}/result`

Returns the `ExtractionResult` as soon as `status == "succeeded"`.
While the extraction is still `queued` / `running` / `failed` /
`cancelled`, the controller returns `409 not_ready`. Unknown `id` →
`404 not_found`.

Bboxes carry `source: "llm"` until refinement completes. When
`post_processing.bbox_refinement.status` transitions to `succeeded`,
the persisted result is updated in place and subsequent `GET /result`
calls return refined bboxes (`source: "pdf_text"` or `"ocr"`).

| Query param        | Type     | Default | Meaning                                                                                              |
| ------------------ | -------- | ------: | ---------------------------------------------------------------------------------------------------- |
| `wait_for_bboxes`  | bool     | `false` | Long-poll until `post_processing.bbox_refinement.status` is terminal, or `timeout` elapses.          |
| `timeout`          | float, s |  `60.0` | Long-poll ceiling in seconds. On timeout the unrefined result is returned with `200`.                |

```http
GET /api/v1/extractions/ext_01HEM2ZZ7M0Q8.../result?wait_for_bboxes=true&timeout=120
```

```jsonc
{
  "id":     "ext_01HEM2ZZ7M0Q8...",
  "result": { /* full ExtractionResult, same shape as /extract response */ }
}
```

### Cancel — `DELETE /api/v1/extractions/{id}`

Only valid while `status == "queued"`. After that the worker has
started on the extraction and there is no mid-flight cancellation hook.

```http
DELETE /api/v1/extractions/ext_01HEM2ZZ7M0Q8...
→ 200 { "id": "...", "status": "cancelled", ... }     // Extraction shape
→ 409 { "code": "not_cancellable", ... }              // already running / done
→ 404 { "code": "not_found", ... }
```

### Webhook

When the main pipeline reaches a terminal status and `callback_url` is
set on the submit, the worker POSTs the unified envelope. The payload
mirrors the EDA event so external consumers see the same identity +
lifecycle surface as the in-cluster workers. See [§ 4](#4-events--webhooks)
for the full envelope.

---

## 4. Events & webhooks

EDA events and webhook deliveries share **one** envelope shape.

### 4.1 Envelope

```jsonc
{
  "event_id":       "f0c7b3aa-2f43-4d34-bf6c-3b09e6efbb19",   // UUID v4 — dedupe on this on the client
  "event_type":     "extraction.completed",                    // dotted snake_case
  "version":        "1.0.0",
  "occurred_at":    "2026-05-14T10:43:01Z",
  "correlation_id": "req-...",
  "tenant_id":      "acme",

  "extraction": {                                              // current state snapshot
    "id":              "ext_01HEM2ZZ7M0Q8...",
    "status":          "succeeded",
    "submitted_at":    "...",
    "started_at":      "...",
    "finished_at":     "...",
    "attempts":        1,
    "error":           null,
    "post_processing": { /* same shape as on /extractions/{id} */ }
  },

  "result":   { /* ExtractionResult, populated on extraction.completed when status=="succeeded" */ },
  "metadata": { "external_id": "..." }                          // verbatim echo of submit-time metadata
}
```

### 4.2 Event types

| `event_type`                            | Triggered by                                            | `result` populated? |
|-----------------------------------------|---------------------------------------------------------|---------------------|
| `extraction.submitted`                  | `SubmitExtractionHandler` persists the row              | `null`              |
| `extraction.completed`                  | Main pipeline reaches a terminal `extraction.status`    | `ExtractionResult` if status==`succeeded`, else `null` |
| `extraction.post_processing.requested`  | Main pipeline emits the bbox-refine fan-out             | `null`              |
| `extraction.post_processing.completed`  | `BboxRefineWorker` finishes                             | `null` (the updated result is fetched via `/result`) |

### 4.3 Webhook delivery

Same envelope, posted to `callback_url` on the events that mark a
user-visible lifecycle transition:

| Event                                   | Webhook fires? |
|-----------------------------------------|----------------|
| `extraction.submitted`                  | No — server-internal acknowledgement. Use the 202 response to learn the id. |
| `extraction.completed`                  | **Yes** — main pipeline reached a terminal status. `result` is populated when status==`succeeded`. |
| `extraction.post_processing.requested`  | No — internal fan-out. |
| `extraction.post_processing.completed`  | **Yes** — when `callback_url` was set, refined-bbox availability is delivered too. `result == null`; the caller refetches `/result`. |

```http
POST <callback_url>
Content-Type: application/json
X-Flydocs-Signature: sha256=<hex-digest-of-raw-body>

{ /* EventEnvelope as in § 4.1 */ }
```

`X-Flydocs-Signature` is HMAC-SHA256 of the raw body using
`FLYDOCS_WEBHOOK_HMAC_SECRET`. The publisher retries `5xx` and `429`
with exponential back-off + jitter up to
`FLYDOCS_WEBHOOK_MAX_ATTEMPTS`; other 4xx is treated as permanent.
**Dedupe by `event_id` on the client** — at-least-once delivery means
the same `event_id` may arrive more than once.

---

## 5. Common DTO building blocks

For full per-field reference (constraints, defaults, examples), see
[payload-reference.md](payload-reference.md). The snapshots below give
the canonical shape only.

### `FileInput`

```jsonc
{
  "filename":       "deed.pdf",
  "content_base64": "JVBERi0xLjQK...",    // required in JSON mode; absent in multipart mode
  "content_type":   "application/pdf",     // optional MIME hint; sniffed when omitted
  "expected_type":  "escritura_poderes"    // optional pin; must reference a document_types[].id
}
```

Accepted binary inputs (the `BinaryNormalizer` turns the rest into
provider-readable rasters before extraction):

| Family                      | Formats                                                    | Path                                              |
| --------------------------- | ---------------------------------------------------------- | ------------------------------------------------- |
| PDF                         | PDF/A, encrypted-on-failure                                | passthrough (or 422 `encrypted_pdf`)              |
| Raster the LLM reads        | PNG, JPEG, GIF, WebP                                       | passthrough                                       |
| Raster the LLM doesn't read | HEIC/HEIF, AVIF, multi-frame TIFF, SVG, BMP                | Pillow + pillow-heif + cairosvg                   |
| Office                      | DOCX, XLSX, PPTX, RTF, ODT, HTML                           | `OfficeConverter` (Gotenberg / LibreOffice)       |
| Archive / bundle            | ZIP, 7z, TAR, GZIP, EML, MSG                               | fanned out into multiple `files[]` entries        |

### `DocumentTypeSpec`

```jsonc
{
  "id":            "escritura_poderes",
  "description":   "Spanish notarial power of attorney",
  "country":       "ES",
  "field_groups":  [ FieldGroup, ... ],
  "visual_checks": [ {"name": "...", "description": "..."}, ... ]
}
```

### `FieldGroup` (request side)

```jsonc
{
  "name":        "totals",
  "description": "Money block at the top",
  "fields":      [ Field, ... ]
}
```

### `Field` (recursive)

```jsonc
{
  "name":        "line_items",
  "description": "One row per line item",
  "type":        "array",                       // string | number | integer | boolean | array | object
  "required":    true,
  "pattern":     null,
  "format":      null,                          // "date" | "date-time" | "time" | "email" | "uri" | "uuid" | "currency"
  "enum":        null,
  "minimum":     null,
  "maximum":     null,

  "items":       Field | null,                  // required when type == "array"
  "fields":      [ Field, ... ] | null,         // required when type == "object"

  "validators":  [ ValidatorSpec, ... ]
}
```

Constraints:

- `type == "array"` requires `items` (the row shape) and forbids
  `fields`.
- `type == "object"` requires `fields` (the member shape) and forbids
  `items`.
- Primitive types forbid both `items` and `fields`.
- `minimum <= maximum` when both set.

Worked array-of-objects example (line items):

```jsonc
{
  "name": "line_items",
  "type": "array",
  "items": {
    "type":   "object",
    "name":   "line_item",
    "fields": [
      { "name": "description", "type": "string" },
      { "name": "quantity",    "type": "number", "minimum": 0 },
      { "name": "unit_price",  "type": "number", "minimum": 0 }
    ]
  }
}
```

### `ValidatorSpec`

```jsonc
{
  "name":     "iban",
  "params":   { "country": "ES" },
  "severity": "error"                          // "error" | "warning"
}
```

See [validators.md](validators.md) for every built-in's behaviour and
params.

### `RuleSpec`

```jsonc
{
  "id":        "iban_valid",
  "predicate": "The IBAN field passes the mod-97 checksum.",
  "parents":   [ RuleParent, ... ],
  "output":    {"type": "boolean", "valid_outputs": ["true", "false"]}
}
```

### `RuleParent` (discriminator: `kind`)

```jsonc
// field parent
{ "kind": "field",     "document_type": "invoice", "fields": ["iban"] }

// validator parent
{ "kind": "validator", "document_type": "invoice", "validator": "iban" }

// rule parent
{ "kind": "rule",      "rule": "totals_consistent" }
```

See [rule-engine.md](rule-engine.md) for the DAG semantics.

### `StageToggles`

```jsonc
{
  "splitter":             false,
  "classifier":           true,
  "field_validation":     true,
  "visual_authenticity":  false,
  "content_authenticity": false,
  "judge":                false,
  "judge_escalation":     false,
  "bbox_refine":          false,
  "transform":            false,
  "rule_engine":          false
}
```

The extractor and `bbox_validation` are always on; `assemble`/`load`
are unconditional. The `transform` toggle is a no-op when
`options.transformations` is empty.

### `EscalationConfig`

```jsonc
{
  "threshold": 0.25,
  "model":     "anthropic:claude-opus-4-7"
}
```

`null` when `options.stages.judge_escalation` is off.

### `Transformation` (discriminator: `type`)

```jsonc
// declarative entity resolution
{
  "type":              "entity_resolution",
  "target_group":      "personas",
  "output_group":      null,                    // null = mutate in place
  "scope":             "request",                // "task" | "request"
  "match_by":          ["dni", "nombre"],
  "min_shared_tokens": 2
}

// free-form LLM transformation
{
  "type":         "llm",
  "target_group": "cargos",
  "output_group": null,
  "scope":        "task",
  "intention":    "Normalize each cargo to a closed taxonomy.",
  "prompt_id":    null
}
```

See [transformations.md](transformations.md) for the rationale behind
both types.

### `BoundingBox`

```jsonc
{
  "xmin": 0.15,                                // all values in [0, 1]
  "ymin": 0.26,
  "xmax": 0.85,
  "ymax": 0.30,
  "quality":               "good",              // "good" | "poor" | "suspicious" | "invalid"
  "quality_score":         0.94,                // continuous geometric score in [0, 1]
  "source":                "pdf_text",          // "llm" | "pdf_text" | "ocr"
  "refinement_confidence": 0.91                 // null for source == "llm"
}
```

`null` at the field site signals absence. There is no synthetic
"empty" box — `bbox: null` is the canonical value.

### `ExtractedField` (recursive)

```jsonc
{
  "name":       "iban",
  "value":      "ES7600491500051234567892",     // string | int | float | bool | ExtractedField[] | null
  "pages":      [3],
  "confidence": 0.98,
  "bbox":       { /* BoundingBox */ },
  "validation": {
    "valid":  true,
    "errors": []                                  // [{rule, message}]; rule ∈ {type|pattern|format|enum|minimum|maximum|validator}
  },
  "judge": {
    "status":          "pass",                    // "pass" | "fail" | "uncertain"
    "confidence":      0.99,
    "evidence":        "ES76 0049 1500 0512 3456 7892",
    "notes":           "Matches the IBAN on the bottom-right block.",
    "flag_for_review": false
  },
  "notes": null
}
```

For `type: "array"`, `value` is a list of `ExtractedField` rows whose
`name`s mirror the schema-side row shape. For `type: "object"`,
`value` is itself a list of `ExtractedField` members whose `name`s
mirror the schema-side member shape. Recursion is unbounded.

### `FileSummary`

```jsonc
{
  "filename":       "deed.pdf",
  "media_type":     "application/pdf",
  "page_count":     21,
  "bytes":          384112,
  "matched_type":   "escritura_poderes",         // caller's expected_type OR classifier verdict; null when neither resolved
  "classification": {
    "document_type": "escritura_poderes",
    "matched":       true,
    "confidence":    0.97,
    "description":   "Spanish notarial power of attorney.",
    "notes":         null
  }
}
```

`classification` is `null` when the classifier was skipped (either
pinned via `expected_type`, or `stages.classifier == false`).

### `Document` (response-side, one per extracted instance)

```jsonc
{
  "type":         "escritura_poderes",
  "source_file":  "deed.pdf",
  "missing":      false,
  "pages":        [1, 2, 3],
  "confidence":   1.0,
  "description":  "Spanish notarial power of attorney",
  "notes":        null,
  "field_groups": [ /* FieldGroup, response-side: {name, fields: [ExtractedField...]} */ ],
  "authenticity": { "visual": [...], "content": null }
}
```

`discovered_documents[]` entries carry the same shape with `type:
"unmatched"` and `field_groups: []` (the discoverer cannot extract
fields without a schema).

### `EscalationInfo`

`pipeline.escalation`. `null` unless `stages.judge_escalation` is on
AND the judge's first pass exceeded the threshold:

```jsonc
{
  "triggered":            true,
  "primary_model":        "anthropic:claude-haiku-4-5",
  "escalation_model":     "anthropic:claude-opus-4-7",
  "primary_fail_rate":    0.66,
  "escalation_fail_rate": 0.10,
  "accepted":             true
}
```

### `TraceEntry`

```jsonc
{
  "node":         "extract",       // load | discover | classify | plan_tasks | extract | bbox_validation | bbox_refine | field_validation | visual_authenticity | content_authenticity | judge | judge_escalation | transform | rules | assemble
  "started_at":   "2026-05-15T16:42:03.140Z",
  "completed_at": "2026-05-15T16:42:24.493Z",
  "latency_ms":   21352.88,
  "status":       "success"        // "success" | "failed" | "skipped"
}
```

### `PipelineError`

```jsonc
{
  "node":    "judge",
  "code":    "stage_timeout",
  "message": "Judge stage exceeded its per-call timeout."
}
```

### `ExtractionStatus` enum

`queued` · `running` · `succeeded` · `failed` · `cancelled`.

### `PostProcessingStatus` enum

`pending` · `running` · `succeeded` · `failed`. Populated on
`post_processing.bbox_refinement.status` only when the extraction was
submitted with `options.stages.bbox_refine=true`; the
`post_processing` block is `null` otherwise.

### `VersionInfo` — `GET /api/v1/version`

```jsonc
{
  "service":        "flydocs",
  "version":        "26.6.0",
  "model":          "anthropic:claude-sonnet-4-6",
  "fallback_model": "openai:gpt-4o",            // "" disables the fallback
  "eda_adapter":    "postgres"                  // postgres | memory | redis | kafka
}
```

---

## 6. Glossary — `file` vs `document_type` vs `document`

The v1 contract uses three precise words for the three layers callers
routinely confuse:

- **`file`** — a binary input. One entry in the request's `files[]`,
  one entry in the response's `files[]` (as `FileSummary`).
- **`document_type`** — a schema template. One entry in
  `document_types[]`. Identified by `document_types[].id`. Referenced
  from `files[].expected_type`, `documents[].type`,
  `rules[].parents[].document_type`.
- **`document`** — an extracted instance, one per `(file_or_segment,
  document_type)` pair the orchestrator resolved. One entry in
  `documents[]` (or `discovered_documents[]` when unmatched).

Never use one of these words for another layer's concept.

---

## 7. Authentication

Two layers, both optional.

- **API keys** — set `FLYDOCS_API_KEYS` to a comma-separated list
  of secrets; `fireflyframework-pyfly` enforces them via the
  `security-api-key` starter when the env var is present.
- **OIDC / OAuth2** — out of scope here; use `fireflyframework-pyfly`'s
  `security-jwt` starter and add an extra `@bean` for the JWT decoder.

For development the API is open. Production deployments should set at
least one of the two.

---

## 8. Error codes

Every error response is RFC 7807 `application/problem+json` with a
stable `code` that callers can branch on. The catalogue:

| Status | `code`                          | Endpoint(s)                                | When                                                                                                              |
| -----: | ------------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
|    400 | `invalid_request`               | every endpoint                             | Pydantic schema validation failed. Body lists offending paths under `extensions.errors`.                          |
|    401 | `unauthorized`                  | every endpoint (when API keys enabled)     | Missing or invalid `Authorization`.                                                                               |
|    404 | `not_found`                     | `/api/v1/extractions/{id}*`                | Unknown extraction id.                                                                                            |
|    408 | `timeout`                       | `POST /api/v1/extract`                     | Sync pipeline exceeded `FLYDOCS_SYNC_TIMEOUT_S` (default 60 s). Retry as an async extraction.                     |
|    409 | `not_ready`                     | `GET /api/v1/extractions/{id}/result`      | Status is `queued` / `running` / `failed` / `cancelled`. Body includes the current `Extraction` under `extensions`. |
|    409 | `not_cancellable`               | `DELETE /api/v1/extractions/{id}`          | Already running or terminated. Only `queued` extractions can be cancelled.                                        |
|    413 | `file_too_large`                | `POST /api/v1/extract`, `POST /api/v1/extractions` | Decoded per-file size exceeds `FLYDOCS_MAX_BYTES` (default 32 MiB). Body names the offending file under `extensions.filename`. |
|    422 | `invalid_base64`                | `POST /api/v1/extract`, `POST /api/v1/extractions` | A `content_base64` field failed strict base64 parsing.                                                            |
|    422 | `validation_failed`             | `POST /api/v1/extract`, `POST /api/v1/extractions` | Semantic validator rejected the payload (rule references unknown field, duplicate ids, cycles, …). Full report under `extensions`. |
|    422 | `encrypted_pdf`                 | `POST /api/v1/extract`, `POST /api/v1/extractions` | Password-protected PDF.                                                                                            |
|    422 | `unsupported_file`              | `POST /api/v1/extract`, `POST /api/v1/extractions` | MIME not on supported list and could not be sniffed.                                                              |
|    422 | `office_conversion_failed`      | `POST /api/v1/extract`, `POST /api/v1/extractions` | Gotenberg / LibreOffice refused conversion.                                                                       |
|    422 | `archive_extraction_failed`     | `POST /api/v1/extract`, `POST /api/v1/extractions` | Bundle (ZIP / 7z / TAR / GZIP / EML / MSG) failed to unpack.                                                       |
|    422 | `image_conversion_failed`       | `POST /api/v1/extract`, `POST /api/v1/extractions` | Pillow / pillow-heif / cairosvg failed to normalise the image.                                                    |
|    503 | _composite_                     | `GET /actuator/health/readiness`           | At least one indicator (`database_health`, `eda_health`) reported `down`.                                          |

Non-fatal pipeline-stage failures don't surface as HTTP errors — they
land in `ExtractionResult.pipeline.errors[]` so the request still
returns the partial result.
