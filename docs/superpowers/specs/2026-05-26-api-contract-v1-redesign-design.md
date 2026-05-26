# flydocs — API contract v1 redesign

**Status:** Draft
**Date:** 2026-05-26
**Authors:** andres.contreras@soon.es (with Claude)
**Scope:** Public HTTP API, EDA event envelopes, webhook payloads, Python SDK, Java SDK, Spring Boot starter, examples, all docs.

---

## 1. Why

The current `/api/v1` contract has accreted several semantic and naming inconsistencies that hurt developer experience:

1. **`documents[]` vs `docs[]` collision.** The request body has two near-synonyms for completely different concepts: `documents[]` carries the binary input files, `docs[]` carries the schema templates. Every onboarding developer asks which is which.
2. **Triple-stutter inside `DocSpec`.** Reading `docs[0].docType.documentType` to get the type id is three layers of "doc" for what is logically one identifier.
3. **Mixed JSON casing in the same payload.** Top-level keys are snake (`documents`, `options`), `DocSpec` internals are camel (`docType`, `fieldGroups`, `fieldGroupName`), stage toggles are snake (`field_validation`, `bbox_refine`), rule parents are camel (`parentType`, `documentType`, `fieldNames`), rule results are snake (`rule_id`, `human_revision`). Enum values mix UPPER (`SUCCEEDED`, `PASS`), lower (`pdf_text`), and even SCREAMING (`JOB_NOT_FOUND`).
4. **`FieldGroup` stutter.** `fieldGroupName`, `fieldGroupDesc`, `fieldGroupFields` — every member prefixed with the parent name.
5. **Two field shapes for the same concept.** `FieldSpec` (top-level) supports the `name`/`type`/`description` aliases, but `FieldItem` (array sub-fields) only supports the prefixed `fieldName`/`fieldType`/`fieldDescription`. Same concept, two wire shapes, different at different depths.
6. **`fieldValueFound` / `pagesFound`.** Past-tense verbose names on the response side, with cleaner `value`/`pages` aliases that exist but are not canonical.
7. **Three different "document-ish" arrays in the response.** `files[]` (per input file), `documents[]` (extracted instances), `additional_documents[]` (unexpected discoveries) — same noun, three different meanings.
8. **Two parallel job state machines.** `JobStatus.PARTIAL_SUCCEEDED → REFINING_BBOXES → SUCCEEDED` interacts with a separate `bbox_refine_status` column (`pending → running → succeeded → failed`). Understanding when a job's result is readable, when post-processing is done, and what happens on refinement failure requires reading three pages of docs.
9. **`document_type` is overloaded.** Appears on `DocumentInput`, `DocType`, `DocumentInfo`, `ExtractedDocument`, `RuleFieldParent` — sometimes camelCase, sometimes snake_case, sometimes meaning "caller's pin", sometimes meaning "final assignment", sometimes meaning "type id".
10. **Webhook ≠ EDA event shape** despite the docstring claiming parity. Operators reading both sides have to keep two mental models.
11. **No multipart upload path.** Every file is base64-encoded inside the JSON body, paying ~33% bandwidth + memory overhead even for large files.

This document defines the v1 contract that replaces the current one in a single clean break. Old shapes are not preserved.

---

## 2. Goals

- **One naming convention end-to-end.** snake_case JSON keys, snake_case enum values, snake_case error codes — no exceptions.
- **One word per concept.** `file` for binary inputs, `document_type` for schema templates, `document` for extracted instances. Never reuse a word across layers.
- **One field shape at every depth.** A `Field` is recursive: primitives, arrays, and objects share the same model.
- **One status field with one state machine.** The async lifecycle is `queued → running → succeeded | failed | cancelled`. Post-processing (bbox refinement today, more tomorrow) lives in its own block with its own lifecycle, not inside the main status.
- **One envelope shape for events and webhooks.** Operators see the same payload over Kafka, Redis, Postgres LISTEN/NOTIFY, and HTTP webhook calls.
- **Two transport modes for files.** JSON+base64 for the simple curl case, multipart/form-data for large files. Same endpoint, content-negotiated.

## 3. Non-goals

- **No backwards compatibility.** The current contract is replaced wholesale. We do not ship aliases, deprecation shims, or dual-shape responses.
- **No new pipeline stages.** This change is purely about the public surface; the internal `core/services/extraction` DAG is unaffected.
- **No new validators.** The `validators` catalogue keeps its current set of built-ins (`iban`, `nif`, `phone_e164`, `vat_id`, …). Only the dispatch key name on the request side changes from `type` to `name`.
- **No two-step file upload.** We do not introduce `POST /files` returning a `file_id` reference. Considered and rejected for v1 — adds an extra request to every workflow. Revisit later if very-large or resumable uploads become a need.
- **No GraphQL / gRPC surface.** REST + JSON / multipart only.
- **No URL versioning bump.** We keep `/api/v1/...` as the path prefix. The old v1 just goes away in a single release.

---

## 4. Universal conventions

These rules govern every wire-level decision below.

| Concern | Rule |
|---|---|
| **JSON keys** | `snake_case`. No camelCase aliases. One canonical name per concept. |
| **Enum values** | `snake_case`. `succeeded` / `pass` / `pdf_text` / `entity_resolution` / `not_found`. |
| **IDs** | Prefixed ULIDs: `ext_01HEM2ZZ7M0Q8…` for extractions. (File ids reserved for a future two-step upload.) |
| **Timestamps** | UTC ISO-8601 (RFC 3339) strings. Field names ending in `_at` are timestamps; `_ms` are duration in milliseconds. |
| **Nullability** | Optional fields are `null` when absent. No `""` or `0` defaults to signal "unset". This rule covers `description`, `country`, `notes`, `human_revision`, `evidence` — every string that could legitimately be missing serialises as `null`, not `""`. |
| **Singular vs plural** | Arrays are plural (`files`, `documents`, `field_groups`). Each element's id/name/type is singular. |
| **No verbose past tense** | `value` not `fieldValueFound`; `pages` not `pagesFound`. |
| **Discriminators** | The discriminator key is `type` by default (`Transformation`). It changes to `kind` when `type` would collide with another semantic field in the same request envelope — `RuleParent` uses `kind` because `Field.type` and `RuleOutputSpec.type` live in the same `RuleSpec`-rooted tree and JSON-Schema-walking tools key on the literal field name. |
| **Event type strings** | Event types are dotted snake_case (`extraction.submitted`, `extraction.post_processing.completed`) — a deliberate exception to the "flat snake_case enums" rule. Dots are the de-facto routing convention for Kafka topics, EventBridge buses, and CloudEvents. They appear only as `event_type` values; everywhere else (HTTP `code`, `status`, `kind`, …) stays flat. |
| **Path prefix** | `/api/v1/...` for every endpoint. |
| **MIME** | Request bodies are `application/json` or `multipart/form-data`. Error responses are `application/problem+json` (RFC 7807). |

---

## 5. Endpoint surface

### 5.1 Sync extraction

```
POST   /api/v1/extract                  -> 200 ExtractionResult
POST   /api/v1/extract:validate         -> 200 ValidationResponse   (dry-run, no LLM)
```

Both endpoints accept either `application/json` (with `files[].content_base64`) or `multipart/form-data` (with file parts + a `request` JSON part). The orchestrator path is identical after parse.

### 5.2 Async extractions

```
POST   /api/v1/extractions              -> 202 Extraction
GET    /api/v1/extractions              -> 200 ExtractionList
GET    /api/v1/extractions/{id}         -> 200 Extraction
GET    /api/v1/extractions/{id}/result  -> 200 ExtractionResultEnvelope | 409 not_ready
DELETE /api/v1/extractions/{id}         -> 200 Extraction (cancelled) | 409 not_cancellable
```

### 5.3 Meta

```
GET    /api/v1/version                  -> VersionInfo
GET    /actuator/health                 -> composite
GET    /actuator/health/liveness
GET    /actuator/health/readiness
GET    /actuator/metrics                -> Prometheus
GET    /admin                           -> PyFly Admin
GET    /docs                            -> Swagger UI
GET    /openapi.json                    -> OpenAPI 3.1 spec
```

### 5.4 Request headers honoured

| Header | Endpoints | Meaning |
|---|---|---|
| `Idempotency-Key` | `POST /api/v1/extractions` | Replay the original `Extraction` response when the same key is seen twice. |
| `X-Correlation-Id` | every endpoint | Propagated through pipeline stages, EDA events, webhooks. Generated when absent. |
| `X-Request-Id` | every endpoint | Echoed back in the response. Generated when absent. |
| `X-Tenant-Id` | every endpoint | Copied into EDA events / webhooks as `tenant_id`. |
| `traceparent`, `tracestate` | every endpoint | W3C trace context. Propagated to OTLP spans + downstream HTTP. |
| `Authorization` | every endpoint (when API keys enabled) | `Bearer <key>` or configured scheme. |

---

## 6. Request body

Sync (`POST /extract`) and async (`POST /extractions`) share the same envelope. Async adds two fields.

```jsonc
{
  "intention":       "KYC review for a Spanish power-of-attorney deed.",
  "files":           [ FileInput,         ... ],   // required, min 1
  "document_types":  [ DocumentTypeSpec,  ... ],   // required, min 1
  "rules":           [ RuleSpec,          ... ],   // optional, default []
  "options":         ExtractionOptions,            // optional, sensible defaults

  // POST /extractions only:
  "callback_url":    "https://workflow.example.com/idp/webhook",
  "metadata":        { "tenant_id": "acme", "external_id": "..." }
}
```

Renames from current contract:
- `documents` → `files`
- `docs` → `document_types`
- `request_id` (request side) → removed; server generates `ext_…`

### 6.1 `FileInput`

```jsonc
{
  "filename":       "deed.pdf",                    // required, non-empty
  "content_base64": "JVBERi0xLjQK...",             // required in JSON mode; absent in multipart mode
  "content_type":   "application/pdf",             // optional; sniffed when omitted
  "expected_type":  "escritura_poderes"            // optional; must reference document_types[].id
}
```

Renames:
- `document_type` (the caller's pin) → `expected_type` (the caller's *hint*; the classifier may still override when not present).

In multipart mode, `filename` + `content_type` come from the part headers; `content_base64` is absent (the part body is the binary). `expected_type` rides in the multipart `request` JSON part keyed by filename, e.g.:

```jsonc
// `request` part
{
  "document_types": [...],
  "rules": [...],
  "options": {...},
  "file_options": {
    "deed.pdf":     { "expected_type": "escritura_poderes" },
    "id_front.jpg": { "expected_type": "dni" }
  }
}
```

### 6.2 `DocumentTypeSpec`

```jsonc
{
  "id":             "escritura_poderes",
  "description":    "Spanish notarial power of attorney",
  "country":        "ES",                          // optional, ISO 3166-1 alpha-2 or null
  "field_groups":   [ FieldGroup, ... ],           // required, min 1
  "visual_checks":  [ VisualCheck, ... ]           // optional, default []
}
```

Renames:
- `docType.documentType` → `id`
- `fieldGroups` → `field_groups`
- `validators.visual[]` → top-level `visual_checks[]` (future kinds add new top-level keys; no nested `validators` envelope)

### 6.3 `FieldGroup`

```jsonc
{
  "name":         "totals",                        // was: fieldGroupName
  "description":  "Money block at the top",        // was: fieldGroupDesc
  "fields":       [ Field, ... ]                    // was: fieldGroupFields -- required, min 1
}
```

### 6.4 `Field` (recursive)

```jsonc
{
  "name":         "line_items",
  "description":  "One row per line item",
  "type":         "array",                          // string | number | integer | boolean | array | object
  "required":     true,
  "pattern":      null,
  "format":       null,                             // "date" | "date-time" | "time" | "email" | "uri" | "uuid" | "currency"
  "enum":         null,
  "minimum":      null,
  "maximum":      null,

  // Recursive composition:
  "items":        Field | null,                     // required when type == "array"
  "fields":       [ Field, ... ] | null,            // required when type == "object"

  "validators":   [ ValidatorSpec, ... ]            // was: standard_validators
}
```

Constraints:
- `type == "array"` requires `items` (the row shape) and forbids `fields`.
- `type == "object"` requires `fields` (the member shape) and forbids `items`.
- Primitive types forbid both `items` and `fields`.
- `minimum <= maximum` when both set.

Renames:
- `fieldName` / `name` alias → `name` only (single canonical key).
- `fieldDescription` / `description` alias → `description` only.
- `fieldType` / `type` alias → `type` only.
- `standard_validators` → `validators` (the "standard" prefix carried no semantic; there is only one validator catalogue).
- New: `object` type with `fields[]` (closes the "nested struct" gap the current contract has).
- Removed: `FieldItem` separate type. One `Field` shape at every depth.

**Array-row shape change.** Today `items` is a list of `FieldItem` describing each column of a row. In v1 `items` is a single `Field` describing the whole row (typically `type: "object"` with its own `fields`). Worked example:

```jsonc
// v0
{
  "name": "line_items",
  "type": "array",
  "items": [
    { "fieldName": "description", "fieldType": "string" },
    { "fieldName": "quantity",    "fieldType": "number", "minimum": 0 },
    { "fieldName": "unit_price",  "fieldType": "number", "minimum": 0 }
  ]
}

// v1
{
  "name": "line_items",
  "type": "array",
  "items": {
    "type":   "object",
    "fields": [
      { "name": "description", "type": "string" },
      { "name": "quantity",    "type": "number", "minimum": 0 },
      { "name": "unit_price",  "type": "number", "minimum": 0 }
    ]
  }
}
```

Benefit: arrays of primitives (`type: "array"` with `items: { "type": "string" }`) are now expressible, and nested object members compose without a special case.

### 6.5 `ValidatorSpec`

```jsonc
{
  "name":     "iban",                               // was: type
  "params":   { "country": "ES" },
  "severity": "error"                               // "error" | "warning"
}
```

The catalogue is unchanged (`email`, `uri`, `url`, `domain`, `slug`, `ipv4`, `ipv6`, `date`, `datetime`, `time`, `iso_8601`, `uuid`, `json`, `hex_color`, `iban`, `bic`, `credit_card`, `currency_code`, `amount`, `phone_e164`, `country_code`, `language_code`, `postal_code`, `latitude`, `longitude`, `nif`, `nie`, `cif`, `vat_id`, `ssn`, `passport_number`). Only the request-side dispatch key renames from `type` to `name`.

### 6.6 `VisualCheck`

```jsonc
{
  "name":         "firma_notario",
  "description":  "The notary's signature is present."
}
```

Unchanged semantically; just the home moves from `validators.visual[]` to top-level `visual_checks[]`.

### 6.7 `RuleSpec`

```jsonc
{
  "id":         "kyc_complete",
  "predicate":  "Both DNI/NIE fields are populated AND fecha is populated.",
  "parents":    [ RuleParent, ... ],
  "output":     RuleOutputSpec
}
```

### 6.8 `RuleParent` (discriminated union, discriminator: `kind`)

```jsonc
// field parent
{ "kind": "field",     "document_type": "escritura_poderes", "fields": ["otorgante_dni_nie", "fecha"] }

// validator parent
{ "kind": "validator", "document_type": "escritura_poderes", "validator": "vat_id" }

// rule parent
{ "kind": "rule",      "rule":          "totals_consistent" }
```

Renames:
- `parentType` → `kind` (avoids collision with `Field.type` and `RuleOutputSpec.type` in tools that walk by key name).
- `documentType` → `document_type`
- `fieldNames` → `fields`
- `validatorName` → `validator`
- `ruleId` → `rule`

### 6.9 `RuleOutputSpec`

```jsonc
{
  "type":          "boolean",                       // "boolean" | "string" | "number"
  "valid_outputs": ["true", "false"]                // optional, null when open-ended
}
```

Unchanged semantically.

### 6.10 `ExtractionOptions`

```jsonc
{
  "model":               "anthropic:claude-opus-4-7",
  "language_hint":       "es",
  "return_bboxes":       true,
  "declared_media_type": null,

  "stages": {
    "splitter":              false,
    "classifier":            true,
    "field_validation":      true,
    "visual_authenticity":   false,
    "content_authenticity":  false,
    "judge":                 false,
    "judge_escalation":      false,
    "bbox_refine":           false,
    "transform":             false,
    "rule_engine":           false
  },

  "escalation": {                                    // null when judge_escalation is off
    "threshold": 0.25,
    "model":     "anthropic:claude-opus-4-7"
  },

  "transformations": [ Transformation, ... ]
}
```

Reshape:
- `escalation_threshold` + `escalation_model` collapsed into an `escalation` sub-object (mirrors the toggle).

### 6.11 `Transformation` (discriminated union, discriminator: `type`)

```jsonc
// Entity resolution
{
  "type":              "entity_resolution",
  "target_group":      "personas",
  "output_group":      null,                        // null = replace in place
  "scope":             "request",                    // "task" | "request"
  "match_by":          ["dni", "nombre"],
  "min_shared_tokens": 2
}

// Free-form LLM transformation
{
  "type":         "llm",
  "target_group": "cargos",
  "output_group": null,
  "scope":        "task",
  "intention":    "Normalize each cargo to a closed taxonomy.",
  "prompt_id":    null
}
```

Unchanged semantically; only the JSON keys it inherits (snake_case) and enum value casing change.

---

## 7. Sync response — `ExtractionResult`

```jsonc
{
  "id":     "ext_01HEM...",                         // was: request_id
  "status": "success",                               // "success" | "partial"

  "files":  [ FileSummary,        ... ],            // per-input-file summary, one entry per file
  "documents":            [ Document, ... ],         // matched against declared types
  "discovered_documents": [ Document, ... ],         // was: additional_documents

  "rule_results":            [ RuleResult,                  ... ],
  "request_transformations": [ ExtractedFieldGroup,         ... ],  // response-side FieldGroup (§7.4) — name + ExtractedField[]

  "pipeline": {
    "model":      "anthropic:claude-opus-4-7",
    "latency_ms": 43580,
    "trace":      [ TraceEntry,    ... ],
    "errors":     [ PipelineError, ... ],            // was: pipeline_errors
    "escalation": EscalationInfo | null,
    "usage":      UsageBreakdown | null
  }
}
```

Reshape rationale:
- Caller-facing data stays at top level (`files`, `documents`, `discovered_documents`, `rule_results`, `request_transformations`).
- Pipeline meta (model, latency, trace, errors, escalation, usage) groups under one `pipeline` block so business data isn't drowned in instrumentation.
- `status: "partial"` is the new flag for "result returned but at least one non-fatal stage failed and surfaced under `pipeline.errors`". Replaces today's implicit "look at `pipeline_errors[]`" convention.

### 7.1 `FileSummary`

```jsonc
{
  "filename":      "deed.pdf",
  "media_type":    "application/pdf",
  "page_count":    21,
  "bytes":         384112,
  "matched_type":  "escritura_poderes",             // caller's expected_type OR classifier verdict; null when neither resolved
  "classification": ClassificationInfo | null
}
```

Renames:
- `document_type` → `matched_type` (this is the *final assignment*, not the caller's input).

### 7.2 `ClassificationInfo`

```jsonc
{
  "document_type": "escritura_poderes",
  "matched":       true,
  "confidence":    0.97,
  "description":   "Spanish notarial power of attorney",
  "notes":         null
}
```

`null` when the classifier was skipped (either pinned via `expected_type`, or `stages.classifier == false`).

### 7.3 `Document`

```jsonc
{
  "type":         "escritura_poderes",              // was: document_type
  "source_file":  "deed.pdf",
  "missing":      false,
  "pages":        [1, 2, 3],
  "confidence":   1.0,
  "description":  "Spanish notarial power of attorney",
  "notes":        null,

  "field_groups": [ FieldGroup,        ... ],       // was: fields  (which contained groups, confusingly)
  "authenticity": DocumentAuthenticity
}
```

`discovered_documents[]` entries carry the same shape with `type: "unmatched"` and `field_groups: []` (the discoverer cannot extract fields without a schema).

### 7.4 `FieldGroup` (response-side)

```jsonc
{
  "name":   "otorgamiento",                          // was: fieldGroupName
  "fields": [ ExtractedField, ... ]                  // was: fieldGroupFields
}
```

### 7.5 `ExtractedField` (recursive)

```jsonc
{
  "name":       "fecha",                             // was: fieldName
  "value":      "2025-05-15",                        // was: fieldValueFound  -- string | int | float | bool | [ExtractedField] | null
  "pages":      [1],                                 // was: pagesFound
  "confidence": 0.98,
  "bbox":       BoundingBox | null,                  // null when no bbox was produced
  "validation": FieldValidation,                      // was: field_validation
  "judge":      JudgeOutcome,
  "notes":      null
}
```

For `type == "array"`, `value` is a list of `ExtractedField` rows whose `name`s mirror the schema-side row shape. For `type == "object"`, `value` is itself a list of `ExtractedField` members whose `name`s mirror the schema-side member shape. Recursion is unbounded.

### 7.6 `BoundingBox`

```jsonc
{
  "xmin": 0.15, "ymin": 0.26, "xmax": 0.85, "ymax": 0.30,
  "source":  "pdf_text",                             // "llm" | "pdf_text" | "ocr"
  "quality": "good",                                  // "good" | "poor" | "suspicious" | "invalid"
  "quality_score": 0.94,
  "refinement_confidence": 0.91
}
```

Or `null` when no bbox was produced. The "empty" placeholder (zero-area box with `quality: "empty"`, `source: "none"`) is gone — `null` is the canonical signal for absence.

### 7.7 `FieldValidation`

```jsonc
{
  "valid":  true,
  "errors": [ FieldValidationError, ... ]
}
```

`FieldValidationError`:

```jsonc
{
  "rule":    "pattern",                              // "type" | "pattern" | "format" | "enum" | "minimum" | "maximum" | "validator"
  "message": "Value does not match the expected pattern."
}
```

Renames:
- `"standard"` rule kind → `"validator"` (since `standard_validators` → `validators`).

### 7.8 `JudgeOutcome`

```jsonc
{
  "status":          "pass",                         // "pass" | "fail" | "uncertain"
  "confidence":      0.99,
  "evidence":        "15 May 2025",
  "notes":           "Date matches the otorgamiento date.",
  "flag_for_review": false
}
```

`evidence` and `notes` are `null` (not `""`) when the judge didn't produce text. Values lowercase (was `PASS` / `FAIL` / `UNCERTAIN`).

### 7.9 `DocumentAuthenticity`

```jsonc
{
  "visual":  [ VisualCheckResult, ... ],
  "content": ContentAuthenticity | null
}
```

`VisualCheckResult`:

```jsonc
{
  "name":       "firma_notario",
  "passed":     true,
  "confidence": 0.85,
  "notes":      null
}
```

`ContentAuthenticity`:

```jsonc
{
  "overall_integrity_status": "valid",               // "valid" | "invalid" | "uncertain"
  "checks": [
    { "name": "...", "description": "...", "status": "pass", "evidence": "...", "reasoning": "..." }
  ]
}
```

All enum values lowercase.

### 7.10 `RuleResult`

```jsonc
{
  "rule_id":        "kyc_complete",
  "predicate":      "Both DNI/NIE fields are populated AND fecha is populated.",
  "output":         "true",
  "summary":        "All required identity fields are present.",
  "notes":          [],
  "human_revision": null
}
```

`notes` is an empty array when there are no notes (a list of strings; `[]` is the canonical empty-list value, not `null`). `human_revision` is a single optional string — `null` when not set.

### 7.11 `TraceEntry`

```jsonc
{
  "node":         "extract",                         // load | discover | classify | plan_tasks | extract | bbox_validation | bbox_refine | field_validation | visual_authenticity | content_authenticity | judge | judge_escalation | transform | rules | assemble
  "started_at":   "2026-05-15T16:42:03.140Z",
  "completed_at": "2026-05-15T16:42:24.493Z",
  "latency_ms":   21352.88,
  "status":       "success"                          // "success" | "failed" | "skipped"
}
```

Unchanged.

### 7.12 `PipelineError`

```jsonc
{
  "node":    "judge",
  "code":    "stage_timeout",
  "message": "Judge stage exceeded its per-call timeout."
}
```

Cleaner than today's untyped `dict[str, Any]`. The node + code combination is callable from rule expressions and from monitoring dashboards.

### 7.13 `EscalationInfo`

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

Unchanged (already snake_case).

### 7.14 `UsageBreakdown`

```jsonc
{
  "total_input_tokens":    162109,
  "total_output_tokens":   22218,
  "total_tokens":          184327,
  "total_cost_usd":        3.0651,
  "total_requests":        0,
  "total_latency_ms":      96739.0,
  "record_count":          27,
  "cache_creation_tokens": 0,
  "cache_read_tokens":     0,
  "by_agent": {
    "flydocs-extractor":  { "input_tokens": 78936, "output_tokens": 6057, "total_tokens": 84993, "cost_usd": 1.638 },
    "flydocs-judge":      { "input_tokens": 73023, "output_tokens": 5719, "total_tokens": 78742, "cost_usd": 1.524 }
  },
  "by_model": {
    "anthropic:claude-opus-4-7": { "input_tokens": 318338, "output_tokens": 17797, "total_tokens": 336135, "cost_usd": 6.110 }
  }
}
```

Unchanged.

---

## 8. Async lifecycle (`/extractions`)

### 8.1 `POST /api/v1/extractions` → 202 `Extraction`

```jsonc
{
  "id":           "ext_01HEM2ZZ7M0Q8...",
  "status":       "queued",
  "submitted_at": "2026-05-14T10:42:00Z",
  "started_at":   null,
  "finished_at":  null,
  "attempts":     0,
  "error":        null,
  "post_processing": null                            // null until and unless post-processing is requested
}
```

### 8.2 `GET /api/v1/extractions/{id}` → 200 `Extraction`

When `options.stages.bbox_refine == true`:

```jsonc
{
  "id":           "ext_01HEM...",
  "status":       "succeeded",                       // "queued" | "running" | "succeeded" | "failed" | "cancelled"
  "submitted_at": "2026-05-14T10:42:00Z",
  "started_at":   "2026-05-14T10:42:03Z",
  "finished_at":  "2026-05-14T10:42:48Z",
  "attempts":     1,
  "error":        null,
  "post_processing": {
    "bbox_refinement": {
      "status":       "running",                     // "pending" | "running" | "succeeded" | "failed"
      "started_at":   "2026-05-14T10:42:49Z",
      "finished_at":  null,
      "attempts":     1,
      "error":        null
    }
  }
}
```

Removed:
- `JobStatus.PARTIAL_SUCCEEDED` (subsumed into `status: "succeeded"` + `post_processing.bbox_refinement.status`).
- `JobStatus.REFINING_BBOXES` (ditto).
- Top-level `bbox_refine_status` / `bbox_refine_attempts` / `bbox_refine_started_at` / `bbox_refine_finished_at` / `bbox_refine_error_code` / `bbox_refine_error_message` columns (collapsed into `post_processing.bbox_refinement`).

Result-readability rule:
- `GET /extractions/{id}/result` returns 200 as soon as `status == "succeeded"`.
- Bboxes carry `source: "llm"` until refinement completes. When `post_processing.bbox_refinement.status` transitions to `"succeeded"`, the persisted result is updated in place and subsequent `GET /result` calls return refined bboxes (`source: "pdf_text"` or `"ocr"`).
- Long-polling: `GET /result?wait_for_bboxes=true&timeout=120` blocks until refinement settles or `timeout` elapses (current semantics, just simpler to describe with the new state model).

### 8.3 `GET /api/v1/extractions/{id}/result` → 200 `ExtractionResultEnvelope`

```jsonc
{
  "id":     "ext_01HEM...",
  "result": ExtractionResult                          // same shape as sync /extract response (§7)
}
```

| Query param | Type | Default | Meaning |
|---|---|---:|---|
| `wait_for_bboxes` | bool | `false` | Block until `post_processing.bbox_refinement.status` is terminal. |
| `timeout` | float (s) | `60.0` | Long-poll ceiling. On timeout the unrefined result is returned. |

Returns `409 not_ready` while `status ∈ {queued, running, failed, cancelled}`.

### 8.4 `DELETE /api/v1/extractions/{id}` → 200 `Extraction`

Only valid while `status == "queued"`. After that the worker has started; the response is `409 not_cancellable` with the current `Extraction` under `extensions`.

### 8.5 `GET /api/v1/extractions` → 200 `ExtractionList`

```jsonc
// Query: ?status=succeeded,failed&post_processing_status=running&limit=25
{
  "items":  [ Extraction, ... ],
  "total":  187,
  "limit":  25,
  "offset": 0
}
```

| Query param | Type | Default | Meaning |
|---|---|---:|---|
| `status` | CSV of statuses | `""` | Filter by main status (`queued`, `running`, `succeeded`, `failed`, `cancelled`). |
| `post_processing_status` | CSV | `""` | Filter by `post_processing.bbox_refinement.status`. |
| `idempotency_key` | string | `""` | Exact match on submit-time idempotency key. |
| `created_after` | RFC 3339 | `null` | Inclusive lower bound on `submitted_at`. |
| `created_before` | RFC 3339 | `null` | Inclusive upper bound on `submitted_at`. |
| `limit` | int (1–500) | `50` | Page size. |
| `offset` | int ≥ 0 | `0` | Skip count. |

---

## 9. Events & webhooks (unified envelope)

EDA events and webhook deliveries share **one** envelope shape.

### 9.1 Envelope

```jsonc
{
  "event_id":       "f0c7b3aa-2f43-4d34-bf6c-3b09e6efbb19",   // UUID v4 — dedupe by this on the client
  "event_type":     "extraction.completed",                    // dotted snake_case
  "version":        "1.0.0",
  "occurred_at":    "2026-05-14T10:43:01Z",
  "correlation_id": "req-...",
  "tenant_id":      "acme",

  "extraction": {                                              // current state snapshot of the resource
    "id":           "ext_01HEM...",
    "status":       "succeeded",
    "submitted_at": "...",
    "started_at":   "...",
    "finished_at":  "...",
    "attempts":     1,
    "error":        null,
    "post_processing": { ... }
  },

  "result":   ExtractionResult | null,                         // populated on extraction.completed (status == succeeded)
  "metadata": { "external_id": "..." }                          // verbatim echo of submit-time metadata
}
```

### 9.2 Event types

| `event_type` | Triggered by | `result` |
|---|---|---|
| `extraction.submitted` | `SubmitExtractionHandler` persists the row | `null` |
| `extraction.completed` | Main pipeline reaches a terminal `extraction.status` | `ExtractionResult` if status==`succeeded`, else `null` |
| `extraction.post_processing.requested` | Main pipeline emits bbox-refine fan-out | `null` |
| `extraction.post_processing.completed` | `BboxRefineWorker` finishes | `null` (the updated result is fetched via `/result`) |

Renames:
- `IDPJobSubmitted` → `extraction.submitted`
- `IDPJobCompleted` → `extraction.completed`
- `IDPBboxRefineRequested` → `extraction.post_processing.requested`
- `IDPBboxRefineCompleted` → `extraction.post_processing.completed`

### 9.3 Webhook delivery

Same envelope, posted to `callback_url` on the events that mark a user-visible lifecycle transition. Specifically:

| Event | Webhook fires? |
|---|---|
| `extraction.submitted` | No — server-internal acknowledgement. Use the 202 response to learn the id. |
| `extraction.completed` | **Yes** — main pipeline reached a terminal status. `result` is populated when status==`succeeded`. |
| `extraction.post_processing.requested` | No — internal fan-out. |
| `extraction.post_processing.completed` | **Yes** — when there is a `callback_url`, refined-bbox availability is delivered too. Same envelope shape with `result == null`; the caller refetches `/result` to read the updated bboxes. |

All deliveries signed via:

```
X-Flydocs-Signature: sha256=<hex-digest-of-raw-body>
```

Default retry policy unchanged: 5xx / 429 trigger exponential back-off + jitter up to `FLYDOCS_WEBHOOK_MAX_ATTEMPTS`; other 4xx is permanent. Dedupe by `event_id` on the client.

---

## 10. Errors (RFC 7807)

```jsonc
{
  "type":     "https://flydocs.dev/problems/not_found",
  "title":    "Resource not found",
  "status":   404,
  "code":     "not_found",
  "detail":   "No extraction with id 'ext_xyz'.",
  "instance": null,
  "extensions": { "extraction_id": "ext_xyz" }
}
```

### 10.1 Code catalogue

| Status | `code` | Endpoint(s) | When |
|---:|---|---|---|
| 400 | `invalid_request` | every | Pydantic schema validation failed. Body lists offending paths under `extensions.errors`. |
| 401 | `unauthorized` | every (when API keys enabled) | Missing or invalid `Authorization`. |
| 404 | `not_found` | `/extractions/{id}*` | Unknown extraction id. |
| 408 | `timeout` | `POST /extract` | Sync pipeline exceeded `FLYDOCS_SYNC_TIMEOUT_S`. |
| 409 | `not_ready` | `GET /extractions/{id}/result` | Status is `queued` / `running` / `failed` / `cancelled`. Current `Extraction` under `extensions`. |
| 409 | `not_cancellable` | `DELETE /extractions/{id}` | Already running or terminated. |
| 413 | `file_too_large` | `POST /extract`, `POST /extractions` | Decoded per-file size exceeds `FLYDOCS_MAX_BYTES`. Body names the offending file under `extensions.filename`. |
| 422 | `invalid_base64` | `POST /extract`, `POST /extractions` | `content_base64` failed strict parsing. |
| 422 | `validation_failed` | `POST /extract`, `POST /extractions` | Semantic validator rejected the payload (rule references unknown field, duplicate ids, cycles, …). Full report under `extensions`. |
| 422 | `encrypted_pdf` | `POST /extract`, `POST /extractions` | Password-protected PDF. |
| 422 | `unsupported_file` | `POST /extract`, `POST /extractions` | MIME not on supported list and could not be sniffed. |
| 422 | `office_conversion_failed` | `POST /extract`, `POST /extractions` | Gotenberg / LibreOffice refused conversion. |
| 422 | `archive_extraction_failed` | `POST /extract`, `POST /extractions` | Bundle (ZIP / 7z / TAR / GZIP / EML / MSG) failed to unpack. |
| 422 | `image_conversion_failed` | `POST /extract`, `POST /extractions` | Pillow / pillow-heif / cairosvg failed to normalise the image. |
| 503 | _composite_ | `GET /actuator/health/readiness` | At least one health indicator reported `down`. |

Renames from current catalogue:
- `JOB_NOT_FOUND` → `not_found`
- `job_not_ready` → `not_ready`
- `job_not_cancellable` → `not_cancellable`
- `extraction_timeout` → `timeout`
- `document_too_large` → `file_too_large`
- `unsupported_binary` → `unsupported_file`
- `invalid_request` (422) → `validation_failed` (kept distinct from 400 `invalid_request`)

Non-fatal stage failures continue to surface as `pipeline.errors[]` on the 200 response, not as HTTP errors.

---

## 11. Dry-run validator — `POST /api/v1/extract:validate`

```jsonc
// Request body: same shape as POST /extract
// Response body:
{
  "ok":            false,
  "error_count":   2,
  "warning_count": 1,
  "errors": [
    { "severity": "error", "code": "document_type_unknown",
      "message": "expected_type 'utility_bill' is not declared in document_types[].",
      "path":    "files[2].expected_type" }
  ],
  "warnings": [
    { "severity": "warning", "code": "no_field_groups",
      "message": "DocumentType 'cover_page' has only one field group; consider grouping.",
      "path":    "document_types[1]" }
  ]
}
```

Always 200. The same shape is embedded under `extensions` of the 422 `validation_failed` response from the real `/extract` and `/extractions` endpoints.

Renames in semantic codes:
- `document_type_unknown` keeps its name (still references `document_types[].id`).
- Anything referencing `docs[...]` paths is rewritten to `document_types[...]`.

---

## 12. SDK shape

Both SDKs reflect the new contract one-to-one. Same renames, same recursion, same enums.

### 12.1 Python SDK (`flydocs-sdk`)

Module layout:

```
flydocs_sdk/
  __init__.py
  client.py            # sync Client
  async_client.py      # async Client
  models.py            # wire-level Pydantic models
  request.py           # request-builder helpers (DocumentTypeSpec, Field, FieldGroup, RuleSpec)
  webhooks.py          # WebhookEnvelope, WebhookVerifier
  errors.py            # FlydocsError, FlydocsHttpError, FlydocsTimeoutError, ProblemDetails
  _transport.py        # httpx wiring
  _version.py
```

Renamed exports:
| Old | New |
|---|---|
| `DocumentInput` | `FileInput` |
| `DocSpec` | `DocumentTypeSpec` |
| `DocType` | (removed; collapsed into `DocumentTypeSpec.id` / `description` / `country`) |
| `FieldSpec`, `FieldItem` | `Field` (single recursive type) |
| `FieldGroup` (request) | `FieldGroup` (unchanged name, snake-cased members) |
| `StandardValidatorSpec` | `ValidatorSpec` |
| `VisualValidatorSpec` | `VisualCheck` |
| `ValidatorsSpec` (envelope) | (removed; flat `visual_checks` on `DocumentTypeSpec`) |
| `ExtractionRequest`, `SubmitJobRequest` | `ExtractionRequest`, `SubmitExtractionRequest` (both names cover sync + async) |
| `JobStatus` | `ExtractionStatus` |
| `BboxRefineStatus` | `PostProcessingStatus` |
| `JobStatusResponse`, `SubmitJobResponse`, `JobResult`, `JobListQuery`, `JobListResponse` | `Extraction`, `ExtractionResultEnvelope`, `ExtractionListQuery`, `ExtractionList` |
| `JobWebhookPayload` | `WebhookEnvelope` |

Client API:

```python
client = Client(base_url="...", api_key="...")

# Sync
result      = client.extract(request)                # POST /extract
validation  = client.validate(request)               # POST /extract:validate

# Async
extraction  = client.extractions.create(request, idempotency_key="...")  # POST /extractions
extraction  = client.extractions.get(id)                                  # GET /extractions/{id}
envelope    = client.extractions.get_result(id, wait_for_bboxes=True)     # GET /extractions/{id}/result
extraction  = client.extractions.cancel(id)                               # DELETE /extractions/{id}
page        = client.extractions.list(status=["succeeded"], limit=25)     # GET /extractions
```

`client.extract` is a method (not an attribute), so `validate` lives at top level rather than as `client.extract.validate(...)`. The `client.extractions` accessor is a sub-resource handle (`ExtractionsResource`) carrying the five CRUD-ish methods.

Webhook verifier:

```python
from flydocs_sdk.webhooks import WebhookVerifier

verifier = WebhookVerifier(secret="...")
envelope = verifier.verify(raw_body=body_bytes, signature_header=header_value)
# -> WebhookEnvelope (pydantic model)
```

### 12.2 Java SDK (`flydocs-sdk`)

Package layout:

```
com.firefly.flydocs.sdk
  ├── FlydocsClient            # sync
  ├── FlydocsClientAsync       # async (CompletableFuture)
  ├── model/                   # Jackson-serialisable records
  │     ├── FileInput
  │     ├── DocumentTypeSpec
  │     ├── Field
  │     ├── FieldGroup
  │     ├── ValidatorSpec
  │     ├── VisualCheck
  │     ├── RuleSpec
  │     ├── RuleParent (sealed interface, 3 records)
  │     ├── RuleOutputSpec
  │     ├── ExtractionOptions
  │     ├── ExtractionRequest
  │     ├── SubmitExtractionRequest
  │     ├── ExtractionResult
  │     ├── Document
  │     ├── FileSummary
  │     ├── ExtractedField
  │     ├── BoundingBox
  │     ├── FieldValidation
  │     ├── JudgeOutcome
  │     ├── DocumentAuthenticity
  │     ├── RuleResult
  │     ├── Extraction
  │     ├── ExtractionStatus (enum)
  │     ├── PostProcessingStatus (enum)
  │     └── WebhookEnvelope
  ├── webhook/
  │     ├── WebhookVerifier
  │     └── WebhookVerificationException
  └── error/
        ├── FlydocsException
        ├── FlydocsClientException
        ├── FlydocsHttpException
        ├── FlydocsTimeoutException
        └── ProblemDetails (record)
```

Java identifiers stay idiomatic camelCase (`fileInput.contentBase64()`, `documentType.fieldGroups()`); Jackson `@JsonProperty` annotations stamp the canonical snake_case names on the wire. Records throughout (Java 21+); no Lombok dependency.

Client API mirrors Python:

```java
FlydocsClient client = FlydocsClient.builder()
    .baseUrl("...")
    .apiKey("...")
    .build();

ExtractionResult result   = client.extract(request);
ValidationResponse v      = client.extractValidate(request);

Extraction extraction     = client.extractions().create(request, "idem-key");
Extraction current        = client.extractions().get(id);
ExtractionResultEnvelope e = client.extractions().getResult(id, /* waitForBboxes */ true, Duration.ofSeconds(60));
Extraction cancelled      = client.extractions().cancel(id);
ExtractionList page       = client.extractions().list(ListQuery.builder().status(SUCCEEDED).limit(25).build());
```

### 12.3 Spring Boot starter (`flydocs-spring-boot-starter`)

Auto-configures a `FlydocsClient` bean wired from `flydocs.*` properties:

```yaml
flydocs:
  base-url: https://flydocs.example.com
  api-key: ${FLYDOCS_API_KEY}
  timeout: 60s
  webhook:
    secret: ${FLYDOCS_WEBHOOK_SECRET}
```

`@FlydocsWebhook` annotated handler signatures take `WebhookEnvelope` directly (auto-verified upstream by a Spring HandlerMethodArgumentResolver).

### 12.4 Examples module

The `flydocs-examples` Maven module and `sdks/python/examples/` directory both rewrite to:
- Use the new identifiers everywhere.
- Show JSON-mode and multipart-mode usage side-by-side.
- Show the new webhook handler signature.
- Drop any reference to `PARTIAL_SUCCEEDED` / `REFINING_BBOXES` / `bbox_refine_status` columns.

---

## 13. Files to touch

This is informational — the implementation plan (next step, via `writing-plans`) will sequence the work.

### 13.1 Service runtime

- `src/flydocs/interfaces/dtos/extract.py` — rewrite all top-level request/response DTOs.
- `src/flydocs/interfaces/dtos/doc.py` — collapse `DocType` into `DocumentTypeSpec`, rename validators block.
- `src/flydocs/interfaces/dtos/field.py` — single recursive `Field`; drop `FieldItem`; rename response-side fields.
- `src/flydocs/interfaces/dtos/job.py` — rename to `extraction.py`; new `Extraction` shape with `post_processing`.
- `src/flydocs/interfaces/dtos/rule.py` — snake_case all keys; `parentType` → `kind`; rename sub-fields.
- `src/flydocs/interfaces/dtos/transformation.py` — snake_case keys (most already are).
- `src/flydocs/interfaces/dtos/bbox.py` — drop the empty-placeholder helper.
- `src/flydocs/interfaces/dtos/authenticity.py` — lowercase enum values.
- `src/flydocs/interfaces/dtos/standard_validator.py` — rename to `validator.py`; rename `StandardValidatorSpec` → `ValidatorSpec`, `type` → `name`.
- `src/flydocs/interfaces/dtos/event.py` — single `WebhookEnvelope`/`EventEnvelope` shape; dotted event types.
- `src/flydocs/interfaces/dtos/webhook.py` — collapse into `event.py` envelope.
- `src/flydocs/interfaces/dtos/error.py` — no shape changes (RFC 7807 already snake_case).
- `src/flydocs/interfaces/enums/job_status.py` → `extraction_status.py`; lowercase values; drop `PARTIAL_SUCCEEDED` / `REFINING_BBOXES`; add `PostProcessingStatus`.
- `src/flydocs/interfaces/enums/status.py` — lowercase `JudgeStatus`, `ContentIntegrityStatus`, `CheckStatus`.
- `src/flydocs/interfaces/enums/standard_validator.py` — rename module `validator.py`, enum unchanged values.
- `src/flydocs/interfaces/enums/field_type.py` — add `OBJECT`.
- `src/flydocs/web/controllers/extract_controller.py` — rewrite around new DTOs; add multipart parsing.
- `src/flydocs/web/controllers/jobs_controller.py` → `extractions_controller.py`; new endpoints.
- `src/flydocs/web/controllers/version_controller.py` — minor (status enums).
- `src/flydocs/web/advice/exception_advice.py` — rename codes per §10.
- `src/flydocs/core/services/extract/` — DTO references; no orchestration change.
- `src/flydocs/core/services/jobs/` → `extractions/`; rename throughout; new `post_processing` column model.
- `src/flydocs/core/services/validation/` — rename codes (`document_type_unknown` paths now point at `document_types[...]`).
- `src/flydocs/models/` — SQLAlchemy models for `ExtractionJob` → `Extraction`; add `post_processing` JSONB column; drop the bbox_refine_* columns (migrate data).
- `migrations/` — new Alembic migration renaming the table, columns, and re-shaping the `post_processing` JSONB.

### 13.2 SDKs

- `sdks/python/src/flydocs_sdk/models.py` — full rewrite.
- `sdks/python/src/flydocs_sdk/request.py` — full rewrite.
- `sdks/python/src/flydocs_sdk/client.py` — new method names, multipart support.
- `sdks/python/src/flydocs_sdk/async_client.py` — same.
- `sdks/python/src/flydocs_sdk/errors.py` — rename codes.
- `sdks/python/src/flydocs_sdk/webhooks.py` — single envelope.
- `sdks/python/tests/` — rewrite around new shapes.
- `sdks/python/examples/` — rewrite all examples.
- `sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/model/*.java` — rename + rewrite every record.
- `sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/FlydocsClient*.java` — new client APIs.
- `sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/webhook/*.java` — single envelope.
- `sdks/java/flydocs-spring-boot-starter/` — update auto-configuration and `@FlydocsWebhook` resolver.
- `sdks/java/flydocs-examples/` — rewrite.

### 13.3 Tests

- `tests/unit/` — broad rewrite around new identifiers; structure preserved.
- `tests/integration/` — rewrite request/response fixtures.
- `tests/unit/test_standard_validators.py` → `test_validators.py`.

### 13.4 Documentation

- `docs/api-reference.md` — full rewrite.
- `docs/payload-reference.md` — full rewrite.
- `docs/pipeline.md` — rename stage references where relevant.
- `docs/rule-engine.md` — rule parent kinds.
- `docs/standard-validators.md` → `docs/validators.md`.
- `docs/concurrency.md` — drop `PARTIAL_SUCCEEDED` / `REFINING_BBOXES` references; describe `post_processing` lifecycle.
- `docs/transformations.md` — snake_case key audit.
- `docs/overview.md`, `docs/architecture.md`, `docs/deployment.md`, `docs/troubleshooting.md` — naming audit pass.
- `docs/cicd.md` — naming audit pass.
- `docs/docling.md` — naming audit pass.
- `QUICKSTART.md` — rewrite the worked examples.
- `README.md` — rewrite the snippets.
- `CLAUDE.md` — update the worked snippets, state machine notes, and naming guidance.
- `sdks/python/QUICKSTART.md`, `TUTORIAL.md`, `README.md` — rewrite.
- `sdks/java/QUICKSTART.md`, `TUTORIAL.md`, `README.md` — rewrite.
- **New:** `docs/migration-v0-to-v1.md` — every old key → new key with worked examples (use cases: KYC pack, invoice extraction, async with webhooks).
- **New:** `docs/whitepaper-updates.md` — diff against `flydocs-whitepaper.pdf` and queue a regeneration if needed.

### 13.5 Configuration / runtime

- `pyfly.yaml` — naming audit pass (no shape changes expected; spot-check for any `pyfly_eda_outbox` references that surface event-type strings).
- `env_template` — no changes expected.
- `Taskfile.yml` — task names unchanged; verify nothing references `JobStatus` literals.

---

## 14. Concurrency-invariants impact

The v1 changes do **not** alter the four concurrency guarantees called out in `CLAUDE.md`:

1. **Atomic state transitions** — `ExtractionJobRepository._atomic_update` (rename to `ExtractionRepository._atomic_update`) keeps the `UPDATE … WHERE … RETURNING` pattern. The transition graph is *smaller* (no PARTIAL_SUCCEEDED / REFINING_BBOXES intermediate hops), so the predicate set shrinks — strictly simpler, not riskier.
2. **Per-group advisory lock on EDA drain** — unchanged. Event type strings change but the lock key derives from the consumer group, not the event type.
3. **Idempotency-key collision recovery** — unchanged. The partial unique index keeps its semantics; `SubmitExtractionHandler` mirrors `SubmitJobHandler`'s collision-recovery path.
4. **Reapers** — `JobReaper` → `ExtractionReaper`; `BboxReaper` keeps its name (it's still bbox-specific). Reapers republish events with the new dotted event types; sweep logic unchanged.

Lease defaults (`run_lease_s = sync_timeout_s + 60s`, etc.) keep their current values.

---

## 15. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Existing integrations break in lockstep — every internal caller, every external pilot, every test fixture rewrites at once. | Land in a feature branch behind a release-candidate tag. Run the new SDKs against the new server in a dedicated stack before cutover. Provide `docs/migration-v0-to-v1.md` ahead of merge with copy-paste snippets. |
| Database migration is destructive on the `ExtractionJob.bbox_refine_*` columns when collapsing them into `post_processing` JSONB. | The Alembic migration writes the JSONB *before* dropping the columns. Pre-migration backup is required (call out in release notes). On rollback the migration can re-construct the columns from the JSONB (down-migration script). |
| Webhook consumers built against `JobWebhookPayload` break. | The `docs/migration-v0-to-v1.md` includes a side-by-side webhook payload diff; the Java SDK ships an `@FlydocsWebhook` resolver that supports `WebhookEnvelope` only (no `JobWebhookPayload`). Pilots that consume webhooks get a heads-up window from the RC tag. |
| `documents` in *responses* still means "extracted instances" — a remaining colloquial collision with the user's plain-English use of "documents". | Mitigated by sharply scoping the word's meaning per layer: file = input, document_type = schema, document = extracted instance. The migration guide spells this triangle out front and centre. |
| OpenAPI generator clients (external consumers) regenerate against new spec; auto-gen names may drift. | `task openapi` produces the canonical spec from the new DTOs; we ship a checked-in copy under `docs/openapi.v1.json` so consumers can diff and pin. |

---

## 16. Open questions for the implementation plan

None affecting the contract itself. The implementation-planning phase needs to settle:

- Database migration direction (single combined migration vs. two-step write-and-cut).
- Whether `core/services/jobs` becomes `core/services/extractions` in one rename or via a Python alias shim during the migration commit window.
- Multipart parser choice for FastAPI (Starlette built-in vs. python-multipart explicit configuration for large parts).

These are work-sequencing decisions, not contract decisions.

---

## 17. Migration cheat-sheet

The single most important artefact for callers. Lives at `docs/migration-v0-to-v1.md`; this section is the canonical seed list of renames.

### 17.1 Request keys

| v0 | v1 |
|---|---|
| `documents` | `files` |
| `documents[].document_type` | `files[].expected_type` |
| `docs` | `document_types` |
| `docs[].docType.documentType` | `document_types[].id` |
| `docs[].docType.description` | `document_types[].description` |
| `docs[].docType.country` | `document_types[].country` |
| `docs[].fieldGroups` | `document_types[].field_groups` |
| `docs[].fieldGroups[].fieldGroupName` | `document_types[].field_groups[].name` |
| `docs[].fieldGroups[].fieldGroupDesc` | `document_types[].field_groups[].description` |
| `docs[].fieldGroups[].fieldGroupFields` | `document_types[].field_groups[].fields` |
| `…fieldGroupFields[].fieldName` (or `.name`) | `…fields[].name` |
| `…fieldGroupFields[].fieldDescription` | `…fields[].description` |
| `…fieldGroupFields[].fieldType` (or `.type`) | `…fields[].type` |
| `…fieldGroupFields[].standard_validators` | `…fields[].validators` |
| `…fieldGroupFields[].standard_validators[].type` | `…fields[].validators[].name` |
| `…fieldGroupFields[].items[]` (FieldItem array) | `…fields[].items` (single recursive Field) |
| `docs[].validators.visual` | `document_types[].visual_checks` |
| `rules[].parents[].parentType` | `rules[].parents[].kind` |
| `rules[].parents[].documentType` | `rules[].parents[].document_type` |
| `rules[].parents[].fieldNames` | `rules[].parents[].fields` |
| `rules[].parents[].validatorName` | `rules[].parents[].validator` |
| `rules[].parents[].ruleId` | `rules[].parents[].rule` |
| `options.escalation_threshold` | `options.escalation.threshold` |
| `options.escalation_model` | `options.escalation.model` |

### 17.2 Response keys

| v0 | v1 |
|---|---|
| `request_id` | `id` |
| `files[].document_type` | `files[].matched_type` |
| `documents[].document_type` | `documents[].type` |
| `documents[].fields` (which contained groups) | `documents[].field_groups` |
| `…fieldGroupName` | `…name` |
| `…fieldGroupFields` | `…fields` |
| `…fieldName` (or `.name`) | `…name` |
| `…fieldValueFound` (or `.value`) | `…value` |
| `…pagesFound` | `…pages` |
| `…field_validation` | `…validation` |
| `additional_documents` | `discovered_documents` |
| `model`, `latency_ms`, `trace`, `pipeline_errors`, `escalation`, `usage` (top-level) | nested under `pipeline.{model, latency_ms, trace, errors, escalation, usage}` |

### 17.3 Async / lifecycle

| v0 | v1 |
|---|---|
| `POST /api/v1/jobs` | `POST /api/v1/extractions` |
| `GET /api/v1/jobs` | `GET /api/v1/extractions` |
| `GET /api/v1/jobs/{id}` | `GET /api/v1/extractions/{id}` |
| `GET /api/v1/jobs/{id}/result` | `GET /api/v1/extractions/{id}/result` |
| `DELETE /api/v1/jobs/{id}` | `DELETE /api/v1/extractions/{id}` |
| `JobStatus.QUEUED` | `extraction.status = "queued"` |
| `JobStatus.RUNNING` | `extraction.status = "running"` |
| `JobStatus.PARTIAL_SUCCEEDED` | `extraction.status = "succeeded"` + `post_processing.bbox_refinement.status ∈ {pending, running}` |
| `JobStatus.REFINING_BBOXES` | `extraction.status = "succeeded"` + `post_processing.bbox_refinement.status = "running"` |
| `JobStatus.SUCCEEDED` | `extraction.status = "succeeded"` + `post_processing == null` (or terminal) |
| `JobStatus.FAILED` | `extraction.status = "failed"` |
| `JobStatus.CANCELLED` | `extraction.status = "cancelled"` |
| `bbox_refine_status`, `bbox_refine_*` columns | `post_processing.bbox_refinement.*` |

### 17.4 Events / webhook

| v0 | v1 |
|---|---|
| `IDPJobSubmitted` | `extraction.submitted` |
| `IDPJobCompleted` | `extraction.completed` |
| `IDPBboxRefineRequested` | `extraction.post_processing.requested` |
| `IDPBboxRefineCompleted` | `extraction.post_processing.completed` |
| `JobWebhookPayload` | `WebhookEnvelope` (same shape as EDA event) |

### 17.5 Error codes

| v0 | v1 |
|---|---|
| `JOB_NOT_FOUND` | `not_found` |
| `job_not_ready` | `not_ready` |
| `job_not_cancellable` | `not_cancellable` |
| `extraction_timeout` | `timeout` |
| `document_too_large` | `file_too_large` |
| `unsupported_binary` | `unsupported_file` |
| `invalid_request` (422) | `validation_failed` |

### 17.6 Enum values

| v0 | v1 |
|---|---|
| `PASS` / `FAIL` / `UNCERTAIN` (judge) | `pass` / `fail` / `uncertain` |
| `VALID` / `INVALID` / `UNCERTAIN` (content integrity) | `valid` / `invalid` / `uncertain` |
| `GOOD` / `POOR` / `SUSPICIOUS` / `INVALID` / `EMPTY` (bbox quality) | `good` / `poor` / `suspicious` / `invalid` + `null` for "no bbox" |

---

## 18. Acceptance criteria

The redesign is "done" when, all in one release:

1. `task test`, `task test:llm`, and `task docker:up:test` integration suite all pass against the new contracts.
2. `task openapi` produces an OpenAPI 3.1 spec that mentions zero v0 names (audited via grep of the spec output against the v0 vocabulary).
3. The Python SDK and Java SDK both compile clean, their READMEs/QUICKSTART/TUTORIAL examples run end-to-end against a fresh `docker compose up` stack, and the published WebhookVerifier round-trip works in both languages.
4. `docs/migration-v0-to-v1.md` covers every row in §17 with worked before/after JSON snippets, and is linked from the project root README + CHANGELOG.
5. The CHANGELOG entry for the release explicitly calls out: snake_case everywhere, removed `documents`/`docs`, single Field type, simplified Extraction state machine, unified webhook envelope, and points at the migration doc.
6. `grep -r "JobStatus\.PARTIAL_SUCCEEDED\|REFINING_BBOXES\|bbox_refine_status\|fieldGroupName\|fieldGroupFields\|fieldValueFound\|JOB_NOT_FOUND"` over the repo returns zero hits outside the migration doc and the CHANGELOG.

---

## 19. Out-of-scope follow-ups

These were considered and consciously deferred:

- **Two-step `POST /files` upload + reference.** Worth adding when very-large or resumable uploads become a need; the design intentionally leaves room (the `FileInput` shape can grow a `file_id` field that mutually excludes `content_base64`).
- **Streaming responses for sync `/extract`.** Could surface stage-by-stage progress over SSE. Not yet warranted.
- **Per-document `rules[]` (rules attached to `document_types[]`).** Today rules are top-level and reference document types by string. Moving them inside the type is a real ergonomic win for callers writing one schema per type, but it's a bigger semantic change than this redesign tackles.
- **GraphQL surface.** Out of scope; REST + JSON remains the only public surface.
