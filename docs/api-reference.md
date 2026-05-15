# API reference

The canonical reference for the HTTP surface. Every example here is a
real, working payload — running `task openapi` produces the
machine-readable OpenAPI 3.1 spec from the same DTOs.

---

## 1. Surface at a glance

| Method   | Path                            | Purpose                                                    |
| -------- | ------------------------------- | ---------------------------------------------------------- |
| `POST`   | `/api/v1/extract`               | Synchronous extraction. Blocks until pipeline finishes.    |
| `POST`   | `/api/v1/extract:validate`      | Dry-run the semantic validator (no LLM call, no DB write). |
| `POST`   | `/api/v1/jobs`                  | Submit a queued extraction. Returns `202` + job id.         |
| `GET`    | `/api/v1/jobs/{id}`             | Current status of a job.                                    |
| `GET`    | `/api/v1/jobs/{id}/result`      | Final `ExtractionResult` (when `SUCCEEDED`).               |
| `DELETE` | `/api/v1/jobs/{id}`             | Cancel a job that is still `QUEUED`.                        |
| `GET`    | `/api/v1/version`               | Build + model info.                                         |
| `GET`    | `/actuator/health`              | Composite health.                                           |
| `GET`    | `/actuator/health/liveness`     | Liveness probe.                                             |
| `GET`    | `/actuator/health/readiness`    | Readiness probe.                                            |
| `GET`    | `/actuator/metrics`             | Prometheus metrics.                                         |
| `GET`    | `/docs`                         | Swagger UI (OpenAPI 3.1).                                   |

Errors follow RFC 7807. The advice at `web/advice/exception_advice.py`
maps domain exceptions to `{type, title, status, detail, code, ...}`
JSON bodies.

---

## 2. Synchronous extraction — `POST /api/v1/extract`

Blocks until the orchestrator finishes or `FLYDESK_IDP_SYNC_TIMEOUT_S`
elapses (default 60 s). On timeout the controller returns
`408 extraction_timeout`.

The request accepts **either** a single file (`document`) or a list
(`documents`) -- they're mutually exclusive. The legacy single-file
shape is unchanged for backwards compatibility; the multi-file shape
is documented in [§ 2a](#2a-multi-file-extraction).

### Request

```jsonc
{
  "intention": "KYC review for a Spanish power-of-attorney deed.",
  "document": {
    "filename": "deed.pdf",
    "content_base64": "JVBERi0xLjQK...",       // base64-encoded document bytes
    "content_type": "application/pdf"           // optional; sniffed if omitted
  },
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
  "document": {                                  // legacy single-file echo; null in multi-file mode
    "filename": "deed.pdf",
    "media_type": "application/pdf",
    "page_count": 21,
    "bytes": 384112,
    "document_type": null,                       // implicit in docs[0] for the legacy shape
    "classification": null                       // classifier didn't run in single-file mode
  },
  "files": [                                     // per-file summary; one entry per input file
    {
      "filename": "deed.pdf",
      "media_type": "application/pdf",
      "page_count": 21,
      "bytes": 384112,
      "document_type": null,
      "classification": null
    }
  ],
  "documents": [
    {
      "document_type": "escritura_poderes",
      "missing": false,
      "pages": [1, 2, /* ... */ 21],
      "description": "Escritura notarial de poderes",
      "confidence": 1.0,
      "source_file": null,                        // filename of the input file; set only in multi-file mode
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
| `cache_creation_tokens`  | Prompt tokens written to Anthropic's prompt cache (currently always 0 — caching not yet enabled).        |
| `cache_read_tokens`      | Prompt tokens served from cache (currently always 0).                                                    |
| `by_agent`               | Per-agent breakdown (extractor, classifier, splitter, judge, visual, content, rule-engine).             |
| `by_model`               | Per-model breakdown — useful when fallback or escalation switched models mid-request.                   |

`null` when cost tracking is disabled or no LLM call fired.

#### `trace` block

One entry per executed pipeline node, ordered as the DAG ran them. Each
entry has `node`, `started_at`, `completed_at`, `latency_ms`, and a
`status` of `success` | `failed` | `skipped`. Useful for spotting which
stages dominate a request's latency.

#### Operational notes

The cost number is an **estimate** based on a static price table; for
the Claude 4 family (`opus-4-*`, `sonnet-4-*`, `haiku-4-*`) we maintain
overrides in `core/observability/pricing.py`. Update there when prices
change. The same per-call data is also emitted on the
``outbound_call`` log lines (one per LLM call, with `correlation_id`,
`in_tokens`, `out_tokens`, `cost_usd`), so spend forensics work even
without parsing the response.

### Error responses

| Status | Code                   | When                                                               |
| -----: | ---------------------- | ------------------------------------------------------------------ |
|    400 | _various_              | Pydantic validation failed (RFC 7807 body with field errors).      |
|    408 | `extraction_timeout`   | Sync pipeline exceeded `FLYDESK_IDP_SYNC_TIMEOUT_S`.                |
|    413 | `document_too_large`   | Decoded document exceeds `FLYDESK_IDP_MAX_BYTES` (default 32 MiB).  |
|    422 | `invalid_base64`       | A `content_base64` field failed strict base64 parsing.             |
|    422 | `invalid_request`      | Semantic validator rejected the payload (e.g. a `document_type` pin references an undeclared docType, rule points at an unknown field). The body includes the full report so the caller can fix every issue at once. |

### 2a. Multi-file & sub-document discovery

The pipeline accepts two complementary shapes for "documents per
request":

1. **Multi-file**: submit several files by sending `documents` (a
   list) instead of `document` (a single object). The two shapes are
   mutually exclusive; the request validator rejects payloads that
   set both or neither.
2. **Sub-document discovery**: enable `options.stages.splitter` and a
   single uploaded PDF that contains several documents inside (deed
   + ID + utility bill, for example) is split into its sub-documents
   automatically. Each sub-document is then classified against the
   declared `DocSpec`s and extracted independently.

The two work in any combination -- you can submit five files and turn
on the splitter, and every file gets its own discover → classify →
extract sub-pipeline.

Each entry in `documents` carries the same fields as the legacy
`document`, plus an optional `document_type` pin:

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

The response shape is the same `ExtractionResult`, but:

- `document` is `null` (the legacy field).
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

> **Single-file only.** The async submit endpoint currently accepts
> the legacy `document` shape only. Multi-file workloads should use
> the sync endpoint, or open one job per file.

### Submit

```http
POST /api/v1/jobs
Content-Type: application/json
Idempotency-Key: 4b2e8c70-8d10-4f04-92ee-9d8...   ; optional, replays the response if reused

{
  "intention": "...",
  "document": { "filename": "...", "content_base64": "...", "content_type": "..." },
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

### Poll status

```http
GET /api/v1/jobs/01HEM2ZZ7M0Q8...
```

```jsonc
{
  "job_id": "01HEM2ZZ7M0Q8...",
  "status": "RUNNING",         // QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
  "attempts": 1,
  "submitted_at": "2026-05-14T10:42:00Z",
  "started_at":   "2026-05-14T10:42:03Z",
  "finished_at":  null,
  "error_code":   null,
  "error_message": null
}
```

### Fetch the result

Only valid once `status == SUCCEEDED`. While the job is still running,
the controller returns `409 job_not_ready`.

```http
GET /api/v1/jobs/01HEM2ZZ7M0Q8.../result
```

```jsonc
{
  "job_id": "01HEM2ZZ7M0Q8...",
  "result": { /* full ExtractionResult, same shape as /extract */ }
}
```

### Cancel

Only valid while `status == QUEUED`. After that the worker has started
on the job and there is no mid-flight cancellation hook.

```http
DELETE /api/v1/jobs/01HEM2ZZ7M0Q8...
→ 200 { "job_id": "...", "status": "CANCELLED", ... }
→ 409 { "code": "job_not_cancellable", ... }       // already RUNNING / done
```

### Webhook

When the job leaves a terminal state and `callback_url` is set, the
worker POSTs:

```http
POST <callback_url>
Content-Type: application/json
X-FLYDESK-Signature: sha256=<hex>

{
  "job_id": "01HEM2ZZ7M0Q8...",
  "status": "SUCCEEDED",
  "occurred_at": "2026-05-14T10:43:01Z",
  "metadata": { "tenant_id": "acme", ... },
  "result": { /* full ExtractionResult */ },
  "error_code": null,
  "error_message": null
}
```

`X-FLYDESK-Signature` is an HMAC-SHA256 of the raw body using
`FLYDESK_IDP_WEBHOOK_HMAC_SECRET`. The publisher retries on `5xx` and
`429` up to `FLYDESK_IDP_WEBHOOK_MAX_ATTEMPTS`; anything else `4xx` is
treated as permanent.

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

---

## 5. Authentication

Two layers, both optional.

- **API keys** — set `FLYDESK_IDP_API_KEYS` to a comma-separated list
  of secrets; pyfly enforces them via the
  `security-api-key` starter when the env var is present.
- **OIDC / OAuth2** — out of scope here; use pyfly's `security-jwt`
  starter and add an extra `@bean` for the JWT decoder.

For development the API is open. Production deployments should set at
least one of the two.
