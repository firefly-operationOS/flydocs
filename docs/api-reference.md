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
      "rule_engine": true
    }
  }
}
```

### Response — 200 OK

```jsonc
{
  "request_id": "8d6624d3-96b0-43e4-b99f-e03258a99b22",
  "document": {
    "filename": "deed.pdf",
    "media_type": "application/pdf",
    "page_count": 21,
    "bytes": 384112
  },
  "documents": [
    {
      "document_type": "escritura_poderes",
      "missing": false,
      "pages": [1, 2, /* ... */ 21],
      "description": "Escritura notarial de poderes",
      "confidence": 1.0,
      "fields": [
        {
          "fieldGroupName": "otorgamiento",
          "fieldGroupFields": [
            {
              "fieldName": "fecha",
              "fieldValueFound": "2025-05-15",
              "confidence": 0.98,
              "pagesFound": [1],
              "bbox": {"xmin": 0.15, "ymin": 0.26, "xmax": 0.85, "ymax": 0.30},
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
  "pipeline_errors": []
}
```

### Error responses

| Status | Code                   | When                                                               |
| -----: | ---------------------- | ------------------------------------------------------------------ |
|    400 | _various_              | Pydantic validation failed (RFC 7807 body with field errors).      |
|    408 | `extraction_timeout`   | Sync pipeline exceeded `FLYDESK_IDP_SYNC_TIMEOUT_S`.                |
|    413 | `document_too_large`   | Decoded document exceeds `FLYDESK_IDP_MAX_BYTES` (default 32 MiB).  |
|    422 | `invalid_base64`       | `document.content_base64` failed strict base64 parsing.            |

---

## 3. Async extraction — `POST /api/v1/jobs`

For documents that may take longer than the sync ceiling, or for
fire-and-forget workflows with a webhook callback. The submit endpoint
returns immediately; the worker drives the same orchestrator behind
the scenes.

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
  "field_validation": true,
  "visual_authenticity": false,
  "content_authenticity": false,
  "judge": true,
  "rule_engine": true
}
```

The extractor is always on; assemble/load are unconditional.

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
