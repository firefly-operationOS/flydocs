# Migrating from v0 to v1

The flydocs API contract was redesigned end-to-end in release
`26.6.0`. This is the **only** migration guide you need — the v0
contract is gone in a single clean break, with no backwards-compatible
shim.

> **What this doc covers:** every old key and how it maps to its v1
> equivalent, with side-by-side worked examples for the common
> scenarios. **When to read it:** any time you're porting a v0
> integration to v1. **Where else to look:**
> - Full v1 reference: [`api-reference.md`](api-reference.md) +
>   [`payload-reference.md`](payload-reference.md).
> - Validator catalogue: [`validators.md`](validators.md).
> - Rule engine semantics: [`rule-engine.md`](rule-engine.md).

---

## Glossary — three precise words

The v1 contract uses three precise words for layers that v0 routinely
conflated:

- **`file`** — a binary input. Lives in `files[]` on the request, and
  in `files[]` (as `FileSummary`) on the response. Was called
  `documents[]` on the request in v0 — the most confusing v0 collision.
- **`document_type`** — a schema template. Lives in
  `document_types[]` on the request, identified by `id`. Was called
  `docs[]` in v0, with the id buried under `docs[].docType.documentType`.
- **`document`** — an extracted instance, one per `(file_or_segment,
  document_type)` pair the orchestrator resolved. Lives in
  `documents[]` on the response (or `discovered_documents[]` when
  unmatched). Was the same name in v0.

The v1 rule: **never use one of these words for another layer's
concept**. When the migration guide says "renamed `documents[]` to
`files[]`", it always means *on the request side*.

---

## §1. Request key renames

| v0 (request)                                              | v1 (request)                                            |
|-----------------------------------------------------------|---------------------------------------------------------|
| `documents`                                               | `files`                                                 |
| `documents[].document_type`                               | `files[].expected_type`                                 |
| `docs`                                                    | `document_types`                                        |
| `docs[].docType.documentType`                             | `document_types[].id`                                   |
| `docs[].docType.description`                              | `document_types[].description`                          |
| `docs[].docType.country`                                  | `document_types[].country`                              |
| `docs[].fieldGroups`                                      | `document_types[].field_groups`                         |
| `docs[].fieldGroups[].fieldGroupName`                     | `document_types[].field_groups[].name`                  |
| `docs[].fieldGroups[].fieldGroupDesc`                     | `document_types[].field_groups[].description`           |
| `docs[].fieldGroups[].fieldGroupFields`                   | `document_types[].field_groups[].fields`                |
| `…fieldGroupFields[].fieldName` (or `.name`)              | `…fields[].name`                                        |
| `…fieldGroupFields[].fieldDescription`                    | `…fields[].description`                                 |
| `…fieldGroupFields[].fieldType` (or `.type`)              | `…fields[].type`                                        |
| `…fieldGroupFields[].standard_validators`                 | `…fields[].validators`                                  |
| `…fieldGroupFields[].standard_validators[].type`          | `…fields[].validators[].name`                           |
| `…fieldGroupFields[].items[]` (list of `FieldItem`)       | `…fields[].items` (single recursive `Field`)             |
| `docs[].validators.visual`                                | `document_types[].visual_checks`                        |
| `rules[].parents[].parentType`                            | `rules[].parents[].kind`                                |
| `rules[].parents[].documentType`                          | `rules[].parents[].document_type`                       |
| `rules[].parents[].fieldNames`                            | `rules[].parents[].fields`                              |
| `rules[].parents[].validatorName`                         | `rules[].parents[].validator`                           |
| `rules[].parents[].ruleId`                                | `rules[].parents[].rule`                                |
| `options.escalation_threshold`                            | `options.escalation.threshold`                          |
| `options.escalation_model`                                | `options.escalation.model`                              |

### Worked example — invoice request, before / after

A complete same-payload diff. Notice in v1: `documents` → `files`,
`docs` → `document_types`, the camelCase inside `DocSpec` collapses to
snake_case throughout, `standard_validators` → `validators` with
`name` (not `type`), `parentType` → `kind`, and the
array-of-`FieldItem` collapses to a single recursive `Field`.

**v0:**

```jsonc
{
  "intention": "Extract structured data from the invoice.",
  "documents": [
    {
      "filename":       "invoice.pdf",
      "content_base64": "JVBERi0xLjQK...",
      "content_type":   "application/pdf",
      "document_type":  "invoice"
    }
  ],
  "docs": [
    {
      "docType": {
        "documentType": "invoice",
        "description":  "Vendor invoice",
        "country":      "ES"
      },
      "fieldGroups": [
        {
          "fieldGroupName": "header",
          "fieldGroupDesc": "Header block",
          "fieldGroupFields": [
            { "name": "invoice_number", "type": "string", "required": true },
            { "name": "supplier_vat",   "type": "string", "required": true,
              "standard_validators": [{ "type": "vat_id", "params": { "country": "ES" } }] }
          ]
        },
        {
          "fieldGroupName": "line_items_block",
          "fieldGroupFields": [
            {
              "name": "line_items",
              "type": "array",
              "items": [
                { "fieldName": "description", "fieldType": "string" },
                { "fieldName": "quantity",    "fieldType": "number", "minimum": 0 },
                { "fieldName": "unit_price",  "fieldType": "number", "minimum": 0 }
              ]
            }
          ]
        }
      ],
      "validators": {
        "visual": [
          { "name": "signature_present", "description": "A signature is visible." }
        ]
      }
    }
  ],
  "rules": [
    {
      "id": "totals_consistent",
      "predicate": "subtotal + tax_amount equals total_amount within 0.01",
      "parents": [
        { "parentType": "field", "documentType": "invoice",
          "fieldNames": ["subtotal", "tax_amount", "total_amount"] }
      ]
    }
  ],
  "options": {
    "stages": { "judge": true, "judge_escalation": true },
    "escalation_threshold": 0.25,
    "escalation_model":     "anthropic:claude-opus-4-7"
  }
}
```

**v1:**

```jsonc
{
  "intention": "Extract structured data from the invoice.",
  "files": [
    {
      "filename":       "invoice.pdf",
      "content_base64": "JVBERi0xLjQK...",
      "content_type":   "application/pdf",
      "expected_type":  "invoice"
    }
  ],
  "document_types": [
    {
      "id":          "invoice",
      "description": "Vendor invoice",
      "country":     "ES",
      "field_groups": [
        {
          "name":        "header",
          "description": "Header block",
          "fields": [
            { "name": "invoice_number", "type": "string", "required": true },
            { "name": "supplier_vat",   "type": "string", "required": true,
              "validators": [{ "name": "vat_id", "params": { "country": "ES" } }] }
          ]
        },
        {
          "name": "line_items_block",
          "fields": [
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
          ]
        }
      ],
      "visual_checks": [
        { "name": "signature_present", "description": "A signature is visible." }
      ]
    }
  ],
  "rules": [
    {
      "id": "totals_consistent",
      "predicate": "subtotal + tax_amount equals total_amount within 0.01",
      "parents": [
        { "kind": "field", "document_type": "invoice",
          "fields": ["subtotal", "tax_amount", "total_amount"] }
      ]
    }
  ],
  "options": {
    "stages": { "judge": true, "judge_escalation": true },
    "escalation": {
      "threshold": 0.25,
      "model":     "anthropic:claude-opus-4-7"
    }
  }
}
```

Watch the `line_items` shape carefully: v0 took an **array of
`FieldItem`** as `items`; v1 takes **one `Field` describing the whole
row** (typically `type: "object"` with its own `fields`). The new
shape lets you nest objects, declare arrays-of-primitives, and recurse
arbitrarily — none of which v0 could express.

---

## §2. Response key renames

| v0 (response)                                                | v1 (response)                                              |
|--------------------------------------------------------------|------------------------------------------------------------|
| `request_id`                                                 | `id`                                                       |
| `files[].document_type`                                      | `files[].matched_type`                                     |
| `documents[].document_type`                                  | `documents[].type`                                         |
| `documents[].fields` (which held groups)                     | `documents[].field_groups`                                 |
| `…fieldGroupName`                                            | `…name`                                                    |
| `…fieldGroupFields`                                          | `…fields`                                                  |
| `…fieldName` (or `.name`)                                    | `…name`                                                    |
| `…fieldValueFound` (or `.value`)                             | `…value`                                                   |
| `…pagesFound`                                                | `…pages`                                                   |
| `…field_validation`                                          | `…validation`                                              |
| `additional_documents`                                       | `discovered_documents`                                     |
| top-level `model` / `latency_ms` / `trace` / `pipeline_errors` / `escalation` / `usage` | nested under `pipeline.{model, latency_ms, trace, errors, escalation, usage}` |

### Worked example — invoice response, before / after

**v0:**

```jsonc
{
  "request_id": "8d6624d3-96b0-43e4-b99f-e03258a99b22",
  "files": [
    {
      "filename":      "invoice.pdf",
      "media_type":    "application/pdf",
      "page_count":    1,
      "bytes":         12345,
      "document_type": "invoice"
    }
  ],
  "documents": [
    {
      "document_type": "invoice",
      "missing":       false,
      "pages":         [1],
      "confidence":    1.0,
      "source_file":   "invoice.pdf",
      "fields": [
        {
          "fieldGroupName": "header",
          "fieldGroupFields": [
            {
              "fieldName":       "invoice_number",
              "fieldValueFound": "INV-0042",
              "pagesFound":      [1],
              "confidence":      0.97,
              "bbox":            { "xmin": 0.6, "ymin": 0.1, "xmax": 0.9, "ymax": 0.13, "quality": "GOOD" },
              "field_validation": { "valid": true, "errors": [] },
              "judge":           { "status": "PASS", "confidence": 0.98 }
            }
          ]
        }
      ]
    }
  ],
  "additional_documents": [],
  "rule_results":         [],
  "model":           "anthropic:claude-sonnet-4-6",
  "latency_ms":      4280,
  "pipeline_errors": [],
  "usage":           { "total_tokens": 1234, "total_cost_usd": 0.018 },
  "trace":           [ /* per-stage entries */ ]
}
```

**v1:**

```jsonc
{
  "id":     "ext_01HEM2ZZ7M0Q8...",
  "status": "success",
  "files": [
    {
      "filename":     "invoice.pdf",
      "media_type":   "application/pdf",
      "page_count":   1,
      "bytes":        12345,
      "matched_type": "invoice",
      "classification": null
    }
  ],
  "documents": [
    {
      "type":         "invoice",
      "source_file":  "invoice.pdf",
      "missing":      false,
      "pages":        [1],
      "confidence":   1.0,
      "description":  "Vendor invoice",
      "notes":        null,
      "field_groups": [
        {
          "name": "header",
          "fields": [
            {
              "name":       "invoice_number",
              "value":      "INV-0042",
              "pages":      [1],
              "confidence": 0.97,
              "bbox":       {
                "xmin": 0.6, "ymin": 0.1, "xmax": 0.9, "ymax": 0.13,
                "source": "llm", "quality": "good", "quality_score": 0.92, "refinement_confidence": null
              },
              "validation": { "valid": true, "errors": [] },
              "judge":      { "status": "pass", "confidence": 0.98, "evidence": null, "notes": null, "flag_for_review": false },
              "notes":      null
            }
          ]
        }
      ],
      "authenticity": { "visual": [], "content": null }
    }
  ],
  "discovered_documents":   [],
  "rule_results":             [],
  "request_transformations":  [],
  "pipeline": {
    "model":      "anthropic:claude-sonnet-4-6",
    "latency_ms": 4280,
    "trace":      [ /* per-stage entries */ ],
    "errors":     [],
    "escalation": null,
    "usage":      { "total_tokens": 1234, "total_cost_usd": 0.018 }
  }
}
```

Notice the layer separation: top-level keys carry caller-visible data;
the `pipeline` object groups everything instrumentation-related.

---

## §3. Async lifecycle changes

Endpoint moves:

| v0 endpoint                                  | v1 endpoint                                            |
|----------------------------------------------|--------------------------------------------------------|
| `POST /api/v1/jobs`                          | `POST /api/v1/extractions`                             |
| `GET /api/v1/jobs`                           | `GET /api/v1/extractions`                              |
| `GET /api/v1/jobs/{id}`                      | `GET /api/v1/extractions/{id}`                         |
| `GET /api/v1/jobs/{id}/result`               | `GET /api/v1/extractions/{id}/result`                  |
| `DELETE /api/v1/jobs/{id}`                   | `DELETE /api/v1/extractions/{id}`                      |

Status renames:

| v0 `JobStatus`                          | v1 `extraction.status` (and `post_processing`)                                       |
|-----------------------------------------|---------------------------------------------------------------------------------------|
| `QUEUED`                                | `queued`                                                                              |
| `RUNNING`                               | `running`                                                                             |
| `PARTIAL_SUCCEEDED`                     | `succeeded` + `post_processing.bbox_refinement.status ∈ {pending, running}`           |
| `REFINING_BBOXES`                       | `succeeded` + `post_processing.bbox_refinement.status = "running"`                    |
| `SUCCEEDED`                             | `succeeded` + `post_processing == null` (or terminal)                                 |
| `FAILED`                                | `failed`                                                                              |
| `CANCELLED`                             | `cancelled`                                                                           |

Column collapse:

| v0 column                                  | v1                                                       |
|--------------------------------------------|----------------------------------------------------------|
| `bbox_refine_status`                       | `post_processing.bbox_refinement.status`                 |
| `bbox_refine_attempts`                     | `post_processing.bbox_refinement.attempts`               |
| `bbox_refine_started_at`                   | `post_processing.bbox_refinement.started_at`             |
| `bbox_refine_finished_at`                  | `post_processing.bbox_refinement.finished_at`            |
| `bbox_refine_error_code` / `_error_message`| `post_processing.bbox_refinement.error`                  |

**`PARTIAL_SUCCEEDED` and `REFINING_BBOXES` are gone.** The main
status reaches `succeeded` as soon as the main pipeline ends; refining
progress lives in the `post_processing` block with its own lifecycle.
Result-readability: `GET /result` returns 200 as soon as `status ==
"succeeded"`. Bboxes carry `source: "llm"` until refinement completes;
the persisted result is updated in place when
`post_processing.bbox_refinement.status` transitions to `succeeded`.

### Worked example — async submit + state polling

**v0:**

```jsonc
// POST /api/v1/jobs → 202
{
  "job_id":       "01HEM2ZZ7M0Q8...",
  "status":       "QUEUED",
  "submitted_at": "2026-05-14T10:42:00Z"
}

// GET /api/v1/jobs/01HEM2ZZ7M0Q8... → 200
{
  "job_id":        "01HEM2ZZ7M0Q8...",
  "status":        "PARTIAL_SUCCEEDED",
  "submitted_at":  "2026-05-14T10:42:00Z",
  "started_at":    "2026-05-14T10:42:03Z",
  "finished_at":   "2026-05-14T10:42:48Z",
  "attempts":      1,
  "error_code":    null,
  "error_message": null,

  "bbox_refine_status":         "running",
  "bbox_refine_attempts":       1,
  "bbox_refine_started_at":     "2026-05-14T10:42:49Z",
  "bbox_refine_finished_at":    null,
  "bbox_refine_error_code":     null,
  "bbox_refine_error_message":  null
}
```

**v1:**

```jsonc
// POST /api/v1/extractions → 202
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

// GET /api/v1/extractions/ext_01HEM2ZZ7M0Q8... → 200
{
  "id":           "ext_01HEM2ZZ7M0Q8...",
  "status":       "succeeded",
  "submitted_at": "2026-05-14T10:42:00Z",
  "started_at":   "2026-05-14T10:42:03Z",
  "finished_at":  "2026-05-14T10:42:48Z",
  "attempts":     1,
  "error":        null,
  "post_processing": {
    "bbox_refinement": {
      "status":      "running",
      "started_at":  "2026-05-14T10:42:49Z",
      "finished_at": null,
      "attempts":    1,
      "error":       null
    }
  }
}
```

The result is readable via `GET /result` as soon as `status ==
"succeeded"`, even if `post_processing.bbox_refinement.status` is
still `running`. Long-poll for grounded bboxes with
`?wait_for_bboxes=true&timeout=120` on `/result`.

---

## §4. Events & webhook envelope

v0 published two different shapes — `JobWebhookPayload` over HTTP and
the EDA event envelopes over Kafka / Redis / Postgres LISTEN/NOTIFY.
v1 unifies them into one `EventEnvelope` shape with a dotted event
type.

### Event type strings

| v0 EDA event type            | v1 event type                              |
|------------------------------|--------------------------------------------|
| `IDPJobSubmitted`            | `extraction.submitted`                     |
| `IDPJobCompleted`            | `extraction.completed`                     |
| `IDPBboxRefineRequested`     | `extraction.post_processing.requested`     |
| `IDPBboxRefineCompleted`     | `extraction.post_processing.completed`     |

DTO name: `JobWebhookPayload` → `EventEnvelope`. Both the EDA bus and
the HTTP webhook deliver the same envelope.

### Worked example — webhook envelope, before / after

**v0:**

```jsonc
{
  "event_id":      "5dc2e9c4-…-…-…-…",
  "event_type":    "IDPJobCompleted",
  "version":       "1.0.0",
  "job_id":        "job-abc",
  "status":        "SUCCEEDED",
  "occurred_at":   "2026-05-17T12:00:00Z",
  "started_at":    "2026-05-17T11:59:30Z",
  "finished_at":   "2026-05-17T12:00:00Z",
  "attempts":      1,
  "correlation_id":"req-12345",
  "tenant_id":     null,
  "metadata":      { "caller": "ingest-v2" },
  "result":        { /* … ExtractionResult … */ },
  "error_code":    null,
  "error_message": null
}
```

**v1:**

```jsonc
{
  "event_id":       "5dc2e9c4-…-…-…-…",
  "event_type":     "extraction.completed",
  "version":        "1.0.0",
  "occurred_at":    "2026-05-17T12:00:00Z",
  "correlation_id": "req-12345",
  "tenant_id":      null,

  "extraction": {
    "id":              "ext_01HEM...",
    "status":          "succeeded",
    "submitted_at":    "2026-05-17T11:59:30Z",
    "started_at":      "2026-05-17T11:59:32Z",
    "finished_at":     "2026-05-17T12:00:00Z",
    "attempts":        1,
    "error":           null,
    "post_processing": null
  },

  "result":   { /* … ExtractionResult … */ },
  "metadata": { "caller": "ingest-v2" }
}
```

Key reshape: state snapshot lives under `extraction` instead of being
flattened into the envelope; `error_code` + `error_message` collapse
into `extraction.error`; the event type is dotted snake_case.

### Webhook firing rules

| Event                                   | Webhook fires? |
|-----------------------------------------|----------------|
| `extraction.submitted`                  | No — use the 202 response to learn the id. |
| `extraction.completed`                  | **Yes**. `result` is populated when status==`succeeded`. |
| `extraction.post_processing.requested`  | No — internal. |
| `extraction.post_processing.completed`  | **Yes** when `callback_url` was set. `result == null`; refetch via `/result`. |

---

## §5. Error codes

| v0 `code`                | HTTP status | v1 `code`              |
|--------------------------|-------------|------------------------|
| `JOB_NOT_FOUND`          | 404         | `not_found`            |
| `job_not_ready`          | 409         | `not_ready`            |
| `job_not_cancellable`    | 409         | `not_cancellable`      |
| `extraction_timeout`     | 408         | `timeout`              |
| `document_too_large`     | 413         | `file_too_large`       |
| `unsupported_binary`     | 422         | `unsupported_file`     |
| `invalid_request` (422)  | 422         | `validation_failed`    |

The 400 `invalid_request` code is preserved for pre-handler pydantic
errors; the 422 catch-all (semantic validator output) is now
`validation_failed` so it doesn't collide with the 400.

Other codes (`invalid_base64`, `encrypted_pdf`, `office_conversion_failed`,
`archive_extraction_failed`, `image_conversion_failed`,
`unauthorized`) keep their v0 names.

### Worked example — 404 problem-details

**v0:**

```jsonc
{
  "type":   "https://flydocs.dev/problems/JOB_NOT_FOUND",
  "title":  "Job not found",
  "status": 404,
  "code":   "JOB_NOT_FOUND",
  "detail": "No job with id 'job-xyz'.",
  "extensions": { "job_id": "job-xyz" }
}
```

**v1:**

```jsonc
{
  "type":   "https://flydocs.dev/problems/not_found",
  "title":  "Resource not found",
  "status": 404,
  "code":   "not_found",
  "detail": "No extraction with id 'ext_xyz'.",
  "instance": null,
  "extensions": { "extraction_id": "ext_xyz" }
}
```

### Worked example — 422 validation_failed

**v0:**

```jsonc
{
  "type":   "https://flydocs.dev/problems/invalid_request",
  "title":  "Invalid request",
  "status": 422,
  "code":   "invalid_request",
  "detail": "DocSpec 'utility_bill' is referenced by documents[2] but not declared in docs[].",
  "extensions": {
    "errors": [
      {"severity": "error", "code": "document_type_unknown",
       "message": "Pin 'utility_bill' is not declared in docs[].",
       "path":    "documents[2].document_type"}
    ]
  }
}
```

**v1:**

```jsonc
{
  "type":   "https://flydocs.dev/problems/validation_failed",
  "title":  "Validation failed",
  "status": 422,
  "code":   "validation_failed",
  "detail": "DocumentType 'utility_bill' is referenced by files[2] but not declared in document_types[].",
  "extensions": {
    "errors": [
      {"severity": "error", "code": "document_type_unknown",
       "message": "expected_type 'utility_bill' is not declared in document_types[].",
       "path":    "files[2].expected_type"}
    ]
  }
}
```

---

## §6. Enum values

All enum values are lowercase snake_case in v1.

| Category               | v0 values                                            | v1 values                                                   |
|------------------------|------------------------------------------------------|-------------------------------------------------------------|
| Judge status           | `PASS` / `FAIL` / `UNCERTAIN`                        | `pass` / `fail` / `uncertain`                               |
| Content integrity      | `VALID` / `INVALID` / `UNCERTAIN`                    | `valid` / `invalid` / `uncertain`                           |
| Check status           | `PASS` / `FAIL` / `UNCERTAIN`                        | `pass` / `fail` / `uncertain`                               |
| Bbox quality           | `GOOD` / `POOR` / `SUSPICIOUS` / `INVALID` / `EMPTY` | `good` / `poor` / `suspicious` / `invalid` + `null` (no bbox) |
| Bbox source            | `llm` / `pdf_text` / `ocr` / `none`                  | `llm` / `pdf_text` / `ocr` + `null` (no bbox)               |
| Field type             | (unchanged) plus new `object`                        | `string` · `number` · `integer` · `boolean` · `array` · `object` |
| Job / extraction status| `QUEUED` / `RUNNING` / `PARTIAL_SUCCEEDED` / `REFINING_BBOXES` / `SUCCEEDED` / `FAILED` / `CANCELLED` | `queued` / `running` / `succeeded` / `failed` / `cancelled` (no PARTIAL_SUCCEEDED, no REFINING_BBOXES) |
| Post-processing status | n/a                                                  | `pending` / `running` / `succeeded` / `failed`              |

Validation rule names: `"standard"` rule kind on
`FieldValidationError.rule` is renamed to `"validator"`, mirroring
`standard_validators` → `validators`.

---

## §7. Database schema changes

| v0                                            | v1                                                                       |
|-----------------------------------------------|--------------------------------------------------------------------------|
| Table `extraction_jobs`                       | Table `extractions`                                                      |
| Column `created_at`                           | Column `submitted_at`                                                    |
| Columns `bbox_refine_status` / `_attempts` / `_started_at` / `_finished_at` / `_error_code` / `_error_message` | Single JSONB column `post_processing` (`{ "bbox_refinement": { ... } }`) |
| Column `error_code` + `error_message`         | Single JSONB column `error` (`{ "code": "...", "message": "..." }`)      |
| Repository `ExtractionJobRepository`           | Repository `ExtractionRepository`                                        |
| Entity `ExtractionJob`                        | Entity `Extraction`                                                       |
| Commands `SubmitJobCommand` / `GetJobQuery` / `ListJobsQuery` / `CancelJobCommand` / `GetJobResultQuery` | `SubmitExtractionCommand` / `GetExtractionQuery` / `ListExtractionsQuery` / `CancelExtractionCommand` / `GetExtractionResultQuery` |
| Handlers `SubmitJobHandler` etc.              | Handlers `SubmitExtractionHandler` etc.                                  |
| Directory `core/services/jobs/`               | Directory `core/services/extractions/`                                   |
| Worker `JobWorker` + `JobReaper`              | Worker `ExtractionWorker` + `ExtractionReaper` (`BboxReaper` keeps its name) |

The Alembic migration writes the JSONB column **before** dropping the
old per-column fields, so callers running mixed-version replicas
during the rollout window can still read the row. A down-migration
script re-constructs the columns from the JSONB on rollback.

---

## §8. SDK upgrade quick-reference

Both official SDKs are released in lockstep with the server in
`26.6.0`. The shape changes mirror the wire shape one-to-one.

### Imports

**Python (v0):**

```python
from flydocs_sdk import (
    FlydocsClient,
    DocumentInput,
    DocSpec,
    DocType,
    FieldSpec,
    FieldItem,
    FieldGroup,
    StandardValidatorSpec,
    ExtractionRequest,
    SubmitJobRequest,
    JobStatus,
    BboxRefineStatus,
    JobWebhookPayload,
)
```

**Python (v1):**

```python
from flydocs_sdk import (
    FlydocsClient,
    FileInput,
    DocumentTypeSpec,
    Field,
    FieldGroup,
    ValidatorSpec,
    VisualCheck,
    ExtractionRequest,
    SubmitExtractionRequest,
    ExtractionStatus,
    PostProcessingStatus,
    WebhookEnvelope,
)
```

**Java (v0):**

```java
import com.firefly.flydocs.sdk.model.DocumentInput;
import com.firefly.flydocs.sdk.model.DocSpec;
import com.firefly.flydocs.sdk.model.DocType;
import com.firefly.flydocs.sdk.model.FieldSpec;
import com.firefly.flydocs.sdk.model.FieldItem;
import com.firefly.flydocs.sdk.model.StandardValidatorSpec;
import com.firefly.flydocs.sdk.model.JobStatus;
import com.firefly.flydocs.sdk.model.SubmitJobRequest;
import com.firefly.flydocs.sdk.model.JobWebhookPayload;
```

**Java (v1):**

```java
import com.firefly.flydocs.sdk.model.FileInput;
import com.firefly.flydocs.sdk.model.DocumentTypeSpec;
import com.firefly.flydocs.sdk.model.Field;
import com.firefly.flydocs.sdk.model.ValidatorSpec;
import com.firefly.flydocs.sdk.model.VisualCheck;
import com.firefly.flydocs.sdk.model.ExtractionStatus;
import com.firefly.flydocs.sdk.model.SubmitExtractionRequest;
import com.firefly.flydocs.sdk.model.WebhookEnvelope;
```

### Sync extraction

**Python (v0):**

```python
client = FlydocsClient(base_url="http://localhost:8400")
result = client.extract(ExtractionRequest(
    documents=[DocumentInput.from_path("invoice.pdf")],
    docs=[DocSpec(
        docType=DocType(documentType="invoice", description="Invoice", country="ES"),
        fieldGroups=[...],
    )],
))
print(result.request_id, result.model)
```

**Python (v1):**

```python
client = FlydocsClient(base_url="http://localhost:8400")
result = client.extract(ExtractionRequest(
    files=[FileInput.from_path("invoice.pdf")],
    document_types=[DocumentTypeSpec(
        id="invoice", description="Invoice", country="ES",
        field_groups=[...],
    )],
))
print(result.id, result.pipeline.model)
```

**Java (v0):**

```java
FlydocsClient client = FlydocsClient.builder().baseUrl("http://localhost:8400").build();
ExtractionResult result = client.extract(ExtractionRequest.of(
        List.of(DocumentInput.ofPath(Path.of("invoice.pdf"))),
        List.of(DocSpec.builder()
                .docType(DocType.of("invoice", "Invoice", "ES"))
                .fieldGroups(List.of(...))
                .build())));
System.out.println(result.requestId() + " " + result.model());
```

**Java (v1):**

```java
FlydocsClient client = FlydocsClient.builder().baseUrl("http://localhost:8400").build();
ExtractionResult result = client.extract(ExtractionRequest.of(
        List.of(FileInput.ofPath(Path.of("invoice.pdf"))),
        List.of(DocumentTypeSpec.builder()
                .id("invoice")
                .description("Invoice")
                .country("ES")
                .fieldGroups(List.of(...))
                .build())));
System.out.println(result.id() + " " + result.pipeline().model());
```

### Async submit + result

**Python (v0):**

```python
submit_resp = client.submit_job(SubmitJobRequest(
    documents=[DocumentInput.from_path("deed.pdf")],
    docs=[...],
    callback_url="https://example.com/webhook",
))
print(submit_resp.job_id, submit_resp.status)            # "01HEM...", JobStatus.QUEUED

status = client.get_job(submit_resp.job_id)
result = client.get_job_result(submit_resp.job_id, wait_for_bboxes=True).result
```

**Python (v1):**

```python
extraction = client.extractions.create(SubmitExtractionRequest(
    files=[FileInput.from_path("deed.pdf")],
    document_types=[...],
    callback_url="https://example.com/webhook",
))
print(extraction.id, extraction.status)                  # "ext_01HEM...", ExtractionStatus.QUEUED

current = client.extractions.get(extraction.id)
envelope = client.extractions.get_result(extraction.id, wait_for_bboxes=True)
result = envelope.result
```

**Java (v0):**

```java
SubmitJobResponse submit = client.submitJob(SubmitJobRequest.builder()
        .documents(List.of(DocumentInput.ofPath(Path.of("deed.pdf"))))
        .docs(List.of(...))
        .callbackUrl("https://example.com/webhook")
        .build());

JobStatusResponse status = client.getJob(submit.jobId());
JobResult result        = client.getJobResult(submit.jobId(), /* waitForBboxes */ true, Duration.ofSeconds(60));
```

**Java (v1):**

```java
Extraction extraction = client.extractions().create(SubmitExtractionRequest.builder()
        .files(List.of(FileInput.ofPath(Path.of("deed.pdf"))))
        .documentTypes(List.of(...))
        .callbackUrl("https://example.com/webhook")
        .build());

Extraction current                 = client.extractions().get(extraction.id());
ExtractionResultEnvelope envelope  = client.extractions().getResult(extraction.id(), true, Duration.ofSeconds(60));
```

### Webhook handler

**Python (v0):**

```python
from flydocs_sdk.webhooks import WebhookVerifier, JobWebhookPayload

verifier = WebhookVerifier(secret="...")

def handle(raw_body: bytes, signature_header: str) -> None:
    payload: JobWebhookPayload = verifier.verify(raw_body=raw_body, signature_header=signature_header)
    if payload.status == "SUCCEEDED":
        process(payload.result)
```

**Python (v1):**

```python
from flydocs_sdk.webhooks import WebhookVerifier, WebhookEnvelope

verifier = WebhookVerifier(secret="...")

def handle(raw_body: bytes, signature_header: str) -> None:
    envelope: WebhookEnvelope = verifier.verify(raw_body=raw_body, signature_header=signature_header)
    if envelope.event_type == "extraction.completed" and envelope.extraction.status == "succeeded":
        process(envelope.result)
```

**Java (v0):**

```java
WebhookVerifier verifier = new WebhookVerifier("...");
JobWebhookPayload payload = verifier.verify(rawBody, signatureHeader);
if ("SUCCEEDED".equals(payload.status())) {
    process(payload.result());
}
```

**Java (v1):**

```java
WebhookVerifier verifier = new WebhookVerifier("...");
WebhookEnvelope envelope = verifier.verify(rawBody, signatureHeader);
if ("extraction.completed".equals(envelope.eventType())
        && "succeeded".equals(envelope.extraction().status())) {
    process(envelope.result());
}
```

### Spring Boot `@FlydocsWebhook` handler

**v0:**

```java
@FlydocsWebhook
public void onJobCompleted(JobWebhookPayload payload) {
    if (payload.status() == JobStatus.SUCCEEDED) {
        process(payload.result());
    }
}
```

**v1:**

```java
@FlydocsWebhook
public void onExtractionCompleted(WebhookEnvelope envelope) {
    if ("extraction.completed".equals(envelope.eventType())
            && envelope.extraction().status() == ExtractionStatus.SUCCEEDED) {
        process(envelope.result());
    }
}
```

---

## Cheat-sheet — top-of-mind rule of thumb

When in doubt:

1. **snake_case everywhere** on the wire — JSON keys, enum values,
   error codes (except `event_type` strings which are dotted
   snake_case like `extraction.completed`).
2. **One word per layer:** `file` for binary input,
   `document_type` for schema, `document` for extracted instance.
3. **One `Field` recurses to any depth.** No more `FieldItem`.
4. **One `EventEnvelope`** for EDA + webhook.
5. **`status: "succeeded"`** is reached the moment the main pipeline
   ends. Refining lives in `post_processing.bbox_refinement.status`.
6. **`null` means absent.** No more zero-area `bbox.quality == "empty"`
   placeholders, no more `""`-as-unset on optional strings.
