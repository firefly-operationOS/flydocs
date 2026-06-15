# flydocs — Payload Composition Reference

The complete reference for the v1 `ExtractionRequest` /
`SubmitExtractionRequest` JSON the service accepts. Every field, every
variant, every option, every value — with constraints, defaults, and
JSON examples. Language-neutral; applies to curl, Postman, the
[Python SDK](../sdks/python/), the [Java SDK](../sdks/java/), and any
future client.

> **What this doc covers:** every request and response shape, with
> worked examples for the common scenarios. **When to read it:** while
> composing a request payload, deserialising a response, or building a
> webhook receiver. **Where else to look:**
> - 5-minute first extraction: [`QUICKSTART.md`](../QUICKSTART.md).
> - HTTP endpoint catalogue (paths, status codes, headers): [`api-reference.md`](api-reference.md).
> - Rule engine semantics: [`rule-engine.md`](rule-engine.md).
> - Validator catalogue: [`validators.md`](validators.md).
> - Migrating from v0: [`migration-v0-to-v1.md`](migration-v0-to-v1.md).
> - Pipeline DAG internals: [`pipeline.md`](pipeline.md).

---

## Table of contents

1. [Mental model](#1-mental-model)
2. [The top-level envelope](#2-the-top-level-envelope)
3. [`files[]` — input files](#3-files--input-files)
4. [`document_types[]` — what to extract](#4-document_types--what-to-extract)
5. [`Field` — field-level shape and constraints](#5-field--field-level-shape-and-constraints)
6. [`validators[]` — built-in validators](#6-validators--built-in-validators)
7. [`options` — pipeline configuration](#7-options--pipeline-configuration)
8. [`rules[]` — business rules](#8-rules--business-rules-over-extracted-fields)
9. [`options.transformations[]` — post-extraction reshaping](#9-optionstransformations--post-extraction-reshaping)
10. [Async — `POST /api/v1/extractions`, callbacks, idempotency](#10-async--post-apiv1extractions-callbacks-idempotency)
11. [Webhooks — envelope shape and signature](#11-webhooks--envelope-shape-and-signature)
12. [The `ExtractionResult` response in detail](#12-the-extractionresult-response-in-detail)
13. [Errors — RFC 7807 problem-details](#13-errors--rfc-7807-problem-details)
14. [The kitchen sink — full request with every feature](#14-the-kitchen-sink--full-request-with-every-feature)

---

## 1. Mental model

A flydocs request carries three things:

```
  ┌────────────────────── ExtractionRequest ─────────────────────┐
  │                                                              │
  │   files:           [ FileInput,         … ]   ← the bytes    │
  │   document_types:  [ DocumentTypeSpec,  … ]   ← the schemas  │
  │   rules:           [ RuleSpec,          … ]   ← the predicates│
  │   options:         ExtractionOptions          ← the knobs    │
  │                                                              │
  └──────────────────────────────────────────────────────────────┘
```

Three layers, three precise words — never confused:

| Word            | Meaning                                                       | Lives at                                                       |
|-----------------|---------------------------------------------------------------|----------------------------------------------------------------|
| **file**        | Binary input.                                                  | `files[]` (request) · `files[]` (response, as `FileSummary`).  |
| **document_type** | Schema template.                                              | `document_types[]` (request).                                  |
| **document**    | Extracted instance.                                            | `documents[]` (response) · `discovered_documents[]` (unmatched). |

The service runs a configurable pipeline:

```
  files → splitter? → classifier? → extract (always) → field_validation? →
        → visual_authenticity? → content_authenticity? → judge? →
        → judge_escalation? → bbox_refine? → transform? → rule_engine? → assemble
```

`extract` is mandatory; every other stage is opt-in via
`options.stages`. The response (`ExtractionResult`) carries one entry
per resolved document under `documents[]`, plus per-stage trace and
(when enabled) rule results, transformation outputs, judge verdicts,
etc.

Two integration modes share **the exact same request body**:

| Mode            | Endpoint                                                           | When to use                                                 |
|-----------------|--------------------------------------------------------------------|-------------------------------------------------------------|
| **Sync extract**  | `POST /api/v1/extract`                                           | Single document, sub-minute. Caller waits on the HTTP call. |
| **Async**       | `POST /api/v1/extractions` + `GET /api/v1/extractions/{id}/result` | Long-running, batches, webhook-delivered results.           |

`SubmitExtractionRequest` adds `callback_url` + `metadata` on top of
`ExtractionRequest`; everything else is identical. Both endpoints
accept `application/json` and `multipart/form-data`.

---

## 2. The top-level envelope

```jsonc
POST /api/v1/extract
Content-Type: application/json
Idempotency-Key:  (optional, applies to /extractions only)
X-Correlation-Id: (optional, propagated everywhere)

{
  "intention":      "Extract structured data from the document.",  // optional
  "files":          [ … ],                                          // REQUIRED, min 1
  "document_types": [ … ],                                          // REQUIRED, min 1
  "rules":          [ … ],                                          // optional, default []
  "options":        { … }                                            // optional, sensible defaults
}
```

| Field            | Type                       | Default                                          | Notes                                                              |
|------------------|----------------------------|--------------------------------------------------|--------------------------------------------------------------------|
| `intention`      | string                     | `"Extract structured data from the document."`   | Free-form guidance baked into every LLM call.                       |
| `files`          | array of `FileInput`       | —, **required** (min 1)                          | One entry per file.                                                  |
| `document_types` | array of `DocumentTypeSpec`| —, **required** (min 1)                          | One entry per expected document type.                                |
| `rules`          | array of `RuleSpec`        | `[]`                                             | Business-rule DAG. See [§ 8](#8-rules--business-rules-over-extracted-fields). |
| `options`        | `ExtractionOptions`        | `{}` (server-default stage toggles)              | Per-request knobs. See [§ 7](#7-options--pipeline-configuration).   |

The response carries the server-generated `id` (`ext_…` prefixed
ULID); callers do not set it on the request.

---

## 3. `files[]` — input files

```jsonc
{
  "filename":       "invoice.pdf",                  // REQUIRED, non-empty
  "content_base64": "JVBERi0xLjQK...",              // REQUIRED in JSON mode; absent in multipart mode
  "content_type":   "application/pdf",              // optional MIME hint; sniffed when omitted
  "expected_type":  "invoice"                       // optional caller pin; skips the classifier
}
```

### Fields

| Field            | Type    | Default | Required (JSON mode) | Notes                                                                                                  |
|------------------|---------|---------|----------------------|--------------------------------------------------------------------------------------------------------|
| `filename`       | string  | —       | yes                  | Surfaced verbatim in `files[*].filename` on the response.                                              |
| `content_base64` | string  | —       | yes (JSON), absent (multipart) | Strict base64. `data:<media-type>;base64,...` data URLs are accepted; the prefix is stripped server-side. |
| `content_type`   | string? | `null`  | no                   | MIME hint. Omit to let the service sniff magic bytes.                                                  |
| `expected_type`  | string? | `null`  | no                   | Must reference a declared `document_types[].id`. Skips the classifier for this file.                  |

### Multipart mode

```http
POST /api/v1/extract HTTP/1.1
Content-Type: multipart/form-data; boundary=---xyz

-----xyz
Content-Disposition: form-data; name="request"
Content-Type: application/json

{
  "document_types": [...],
  "rules":          [...],
  "options":        {...},
  "file_options": {
    "deed.pdf":     { "expected_type": "escritura_poderes" },
    "id_front.jpg": { "expected_type": "dni" }
  }
}
-----xyz
Content-Disposition: form-data; name="files"; filename="deed.pdf"
Content-Type: application/pdf

<binary bytes>
-----xyz--
```

`filename` + `content_type` come from the part headers; the binary is
the part body (no base64). `expected_type` rides in the `request`
JSON's `file_options`, keyed by filename.

### Accepted formats

The service's binary normaliser converts anything non-native before reaching the LLM:

| Family            | Examples                                              | Conversion path                                |
|-------------------|-------------------------------------------------------|------------------------------------------------|
| PDF               | `application/pdf`                                     | pass-through                                   |
| Raster images     | PNG, JPEG, WebP, GIF                                  | pass-through                                   |
| Other images      | HEIC/HEIF, AVIF, multi-frame TIFF, SVG, BMP           | Pillow / pillow-heif / cairosvg                |
| Office docs       | DOCX, XLSX, PPTX, RTF, ODT, HTML                      | Configurable `OfficeConverter` (default: Gotenberg sidecar; LibreOffice fallback) |
| Archive / email   | ZIP, 7z, TAR, GZIP, EML, MSG                          | Fanned into multiple internal rows; one extraction per included file |

Encrypted / corrupt PDFs and other unreadable inputs return RFC 7807
errors — typically `422 encrypted_pdf` or `422 unsupported_file`.

### Sizing

Per-file cap is `FLYDOCS_MAX_BYTES` (deployment-configurable). Going
over yields `413 file_too_large` with the offending filename under
`extensions.filename`.

---

## 4. `document_types[]` — what to extract

One entry per **expected document type**. When you submit multiple
files, the classifier matches each file to a `DocumentTypeSpec` unless
the caller pins `files[*].expected_type`.

```jsonc
{
  "id":          "invoice",                          // REQUIRED, non-empty
  "description": "Vendor invoice (paper or PDF)",   // optional
  "country":     "ES",                                // optional, ISO 3166-1 alpha-2
  "field_groups": [                                  // REQUIRED, min 1
    { /* FieldGroup -- see below */ }
  ],
  "visual_checks": [                                 // optional, default []
    { "name": "signature_present",
      "description": "A handwritten or e-signature is visible" }
  ]
}
```

### `DocumentTypeSpec` fields

| Field           | Type    | Default | Notes                                                                                            |
|-----------------|---------|---------|--------------------------------------------------------------------------------------------------|
| `id`            | string  | —, required (non-empty) | Stable identifier. Referenced by `rules[*].parents[*].document_type` and `files[*].expected_type`. Convention: `invoice`, `purchase_order`, `id_card_es`, `passport_int`. |
| `description`   | string? | `null`  | Hints the classifier when the request is multi-document.                                          |
| `country`       | string? | `null`  | ISO 3166-1 alpha-2 (`"ES"`, `"US"`, …). Hint for region-aware validators / formats.              |
| `field_groups`  | array   | —, required (min 1) | One per logical section of the document.                                              |
| `visual_checks` | array   | `[]`    | Caller-defined visual checks. See below.                                                          |

### `FieldGroup` (request side)

A named bundle of fields the service should extract together. Use
groups to partition the schema logically — `header`, `totals`,
`line_items_block`, …

```jsonc
{
  "name":        "totals",                       // REQUIRED, non-empty
  "description": "Money block at the top of the invoice", // optional
  "fields":      [ /* Field entries, see § 5 */ ] // REQUIRED, min 1
}
```

| Field         | Type             | Default | Notes                                                |
|---------------|------------------|---------|------------------------------------------------------|
| `name`        | string           | —, required (non-empty) | snake_case. Surfaced verbatim in `documents[*].field_groups[*].name`. |
| `description` | string?          | `null`  | Free-form description shown to the LLM.              |
| `fields`      | array of `Field` | —, required (min 1) | See [§ 5](#5-field--field-level-shape-and-constraints). |

### `VisualCheck`

Per-`DocumentTypeSpec` visual-check declarations.

```jsonc
{
  "name":        "firma_notario",
  "description": "The notary's signature is present."
}
```

`options.stages.visual_authenticity` must be enabled for these to fire.

---

## 5. `Field` — field-level shape and constraints

One recursive `Field` shape handles primitives, arrays, and objects.
No more separate `FieldSpec` + `FieldItem` types from v0.

```jsonc
{
  "name":        "total_amount",                  // REQUIRED, non-empty (snake_case)
  "description": "Amount due",                    // optional
  "type":        "number",                        // string | number | integer | boolean | array | object
  "required":    true,                            // optional, default false
  "pattern":     "^\\d+\\.\\d{2}$",               // optional, RFC-flavour regex
  "format":      null,                            // optional, see below
  "enum":        null,                            // optional, closed value set
  "minimum":     0.0,                             // optional, numeric inclusive lower bound
  "maximum":     null,                            // optional, numeric inclusive upper bound
  "items":       null,                            // REQUIRED iff type == "array"; single Field
  "fields":      null,                            // REQUIRED iff type == "object"; list of Field
  "validators":  []                               // optional, see § 6
}
```

| Field          | Type                  | Default     | Notes                                                              |
|----------------|-----------------------|-------------|--------------------------------------------------------------------|
| `name`         | string                | —, required | The key under which the extracted value appears in the response.   |
| `description`  | string?               | `null`      | Free-form hint for the LLM.                                        |
| `type`         | enum                  | `"string"`  | See below.                                                          |
| `required`     | boolean               | `false`     | `true` ⇒ a missing field surfaces as a `validation` error.         |
| `pattern`      | string?               | `null`      | Applied by the field validator stage.                              |
| `format`       | enum?                 | `null`      | JSON-Schema-style format hint.                                     |
| `enum`         | array?                | `null`      | Closed set of acceptable values.                                   |
| `minimum`      | number?               | `null`      | Numeric lower bound (inclusive).                                   |
| `maximum`      | number?               | `null`      | Numeric upper bound (inclusive).                                   |
| `items`        | `Field?`              | `null`      | **Required** when `type == "array"`; describes the row shape.       |
| `fields`       | array of `Field`?     | `null`      | **Required** when `type == "object"`; describes the members.        |
| `validators`   | array of `ValidatorSpec` | `[]`     | See [§ 6](#6-validators--built-in-validators).                     |

### `type` enum

| Wire value | Use for                                                          |
|------------|------------------------------------------------------------------|
| `"string"` | Free-form text, identifier, format-validated string.             |
| `"number"` | Floats / decimals. Pair with `minimum` / `maximum` / `format`.   |
| `"integer"`| Integral quantities (counts, page numbers, quantities).          |
| `"boolean"`| Yes/no, present/absent, signed/unsigned.                          |
| `"array"`  | Repeating rows. **Requires** `items` (a single `Field`).          |
| `"object"` | Structured sub-object. **Requires** `fields` (list of `Field`).   |

### `format` enum

| Wire value     | Validation                  |
|----------------|-----------------------------|
| `"date"`       | `YYYY-MM-DD`                |
| `"date-time"`  | RFC 3339 / ISO 8601 with time |
| `"time"`       | `HH:MM[:SS]`                |
| `"email"`      | RFC 5322                    |
| `"uri"`        | Generic URI                 |
| `"uuid"`       | RFC 4122                    |
| `"currency"`   | ISO 4217 alpha-3            |

> **`format` vs `validators`.** `format` is a single-shot
> JSON-Schema-style check baked onto the field; `validators` is the
> extensible catalogue (IBAN, NIE, VAT_ID, …). Prefer `format` for
> simple checks; use `validators[]` for domain checks.

### Worked example — invoice line items as an array of objects

```jsonc
{
  "name": "line_items",
  "description": "One row per line item",
  "type": "array",
  "items": {
    "type":   "object",
    "name":   "line_item",
    "fields": [
      { "name": "description", "type": "string" },
      { "name": "quantity",    "type": "number",  "minimum": 0 },
      { "name": "unit_price",  "type": "number",  "minimum": 0 },
      { "name": "line_total",  "type": "number",  "minimum": 0 }
    ]
  }
}
```

The response side mirrors the shape — `value` becomes a list of
`ExtractedField` rows, each whose `value` is itself a list of member
`ExtractedField`s. Recursion is unbounded.

### Worked example — array of primitives

```jsonc
{
  "name": "tags",
  "type": "array",
  "items": { "name": "tag", "type": "string" }
}
```

### Worked example — nested object

```jsonc
{
  "name": "supplier",
  "type": "object",
  "fields": [
    { "name": "name", "type": "string", "required": true },
    { "name": "vat",  "type": "string", "validators": [{"name": "vat_id", "params": {"country": "ES"}}] }
  ]
}
```

---

## 6. `validators[]` — built-in validators

Attach validators to a `Field` (for any depth — top-level fields,
array rows, object members). The field-validator stage runs them
after extraction and folds the result into
`ExtractedField.validation`.

```jsonc
"validators": [
  { "name": "iban" },
  { "name": "phone_e164", "params": { "country": "ES" } },
  { "name": "vat_id",     "params": { "country": "ES" }, "severity": "warning" }
]
```

| Field      | Type                            | Default   | Notes                                              |
|------------|---------------------------------|-----------|----------------------------------------------------|
| `name`     | enum (see [validators.md](validators.md)) | required  | Use raw strings; the SDKs ship typed enums. |
| `params`   | object                          | `{}`      | Per-validator parameters (e.g. `{"country": "ES"}`). |
| `severity` | `"error"` \| `"warning"`        | `"error"` | `"warning"` records the issue but keeps `valid=true`. |

The full catalogue (`email`, `uri`, `url`, `domain`, `slug`, `ipv4`,
`ipv6`, `date`, `datetime`, `time`, `iso_8601`, `uuid`, `json`,
`hex_color`, `iban`, `bic`, `credit_card`, `currency_code`, `amount`,
`phone_e164`, `country_code`, `language_code`, `postal_code`,
`latitude`, `longitude`, `nif`, `nie`, `cif`, `vat_id`, `ssn`,
`passport_number`) is documented per-entry in [validators.md](validators.md).

`options.stages.field_validation` must be enabled (it is by default)
for validators to fire.

---

## 7. `options` — pipeline configuration

```jsonc
"options": {
  "return_bboxes":       true,
  "language_hint":       "es",
  "model":               "anthropic:claude-sonnet-4-6",
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
  "escalation": null,
  "transformations": []
}
```

### `ExtractionOptions`

| Field                  | Type     | Default               | Notes                                                              |
|------------------------|----------|-----------------------|--------------------------------------------------------------------|
| `return_bboxes`        | boolean  | `true`                | `false` strips bboxes from the response (cheaper to transfer).      |
| `language_hint`        | string?  | `null`                | ISO 639-1, ≤ 16 chars. Guides multilingual OCR / extraction.        |
| `model`                | string?  | `null` (env default)  | Per-request primary model id (`anthropic:claude-sonnet-4-6`, …).    |
| `declared_media_type`  | string?  | `null`                | Override sniffing; rare.                                            |
| `stages`               | object   | server defaults       | See `StageToggles` below.                                           |
| `escalation`           | object?  | `null`                | `{ threshold, model }`. Required when `stages.judge_escalation=true`. |
| `transformations`      | array    | `[]`                  | See [§ 9](#9-optionstransformations--post-extraction-reshaping).    |

### `EscalationConfig`

```jsonc
{
  "threshold": 0.25,                               // judge fail-rate trigger, [0.0, 1.0]
  "model":     "anthropic:claude-opus-4-7"         // model used for the re-run
}
```

### `StageToggles` — every stage

| Stage                  | Default | Cost-ish | What it does                                                                                                  |
|------------------------|---------|----------|---------------------------------------------------------------------------------------------------------------|
| `splitter`             | `false` | one LLM call | Splits a single uploaded file into its sub-documents (deed + ID + utility bill, …).                       |
| `classifier`           | **`true`**  | one LLM call when multi-file & not pinned | Maps each input file to one of the declared `document_types[].id` values. No-op when every file already carries `expected_type`. |
| `field_validation`     | **`true`**  | ~free    | Pure-Python validation — `pattern`, `format`, `enum`, `min`/`max`, every `ValidatorSpec`.                     |
| `visual_authenticity`  | `false` | one LLM call | LLM visual check using `visual_checks` declarations.                                                         |
| `content_authenticity` | `false` | one LLM call | LLM cross-document consistency checks.                                                                       |
| `judge`                | `false` | doubles extract spend | Per-field LLM re-evaluation. Annotates every extracted field with `confidence`, `evidence`, `flag_for_review`. |
| `judge_escalation`     | `false` | +1 extract+judge pass when triggered | Re-runs extract + judge with `escalation.model` when the first judge's fail-rate exceeds `escalation.threshold`. Requires `judge`. |
| `bbox_refine`          | `false` | ~50–200 ms / 30-page text-PDF; seconds / page for OCR | Replaces LLM-estimated bboxes with grounded coordinates from the document's real text layer. On async requests this runs out-of-band via a dedicated worker. |
| `rule_engine`          | `false` | one LLM call per DAG level | Evaluates the business-rule DAG. See [§ 8](#8-rules--business-rules-over-extracted-fields). |
| `transform`            | `false` | varies   | Runs `options.transformations`. See [§ 9](#9-optionstransformations--post-extraction-reshaping).             |

> **Picking stages.** Start with defaults (`classifier`,
> `field_validation`). Enable `bbox_refine` when downstream consumers
> need pixel-accurate boxes. Enable `judge` when you need per-field
> confidence + evidence (compliance-grade extractions). Enable
> `judge_escalation` on top of `judge` when you'd otherwise eat the
> cost of always running the stronger model. Enable `rule_engine`
> when the response should embed business decisions.

---

## 8. `rules[]` — business rules over extracted fields

Rules are **natural-language predicates** the LLM evaluates against
extracted fields, validator outcomes, or other rules' outputs. They
form a DAG; the engine sorts topologically and runs in dependency
order. Cycles are rejected at request-validation time with
`422 validation_failed`.

```jsonc
[
  {
    "id": "totals_consistent",
    "predicate": "subtotal + tax_amount equals total_amount within 0.01",
    "parents": [
      { "kind": "field",
        "document_type": "invoice",
        "fields": ["subtotal", "tax_amount", "total_amount"] }
    ],
    "output": { "type": "boolean" }
  },
  {
    "id": "vat_id_valid",
    "predicate": "The supplier_vat field passes the vat_id validator",
    "parents": [
      { "kind": "validator", "document_type": "invoice", "validator": "vat_id" }
    ]
  },
  {
    "id": "invoice_acceptable",
    "predicate": "totals_consistent AND vat_id_valid",
    "parents": [
      { "kind": "rule", "rule": "totals_consistent" },
      { "kind": "rule", "rule": "vat_id_valid" }
    ]
  }
]
```

### `RuleSpec`

| Field        | Type                | Default       | Notes                                                              |
|--------------|---------------------|---------------|--------------------------------------------------------------------|
| `id`         | string              | —, required   | Unique within the request. Referenced by `parents[].rule`.         |
| `predicate`  | string              | —, required   | Natural-language statement.                                         |
| `parents`    | array of `RuleParent` | `[]`        | Discriminated union — see below.                                    |
| `output`     | `RuleOutputSpec`    | `{ "type": "boolean" }` | Shape the response should carry.                          |

### `RuleParent` — three variants (discriminator: `kind`)

| `kind`         | Other fields                                            | Use for                                          |
|----------------|---------------------------------------------------------|--------------------------------------------------|
| `"field"`      | `document_type` (string), `fields` (array, min 1)       | "This rule operates on these fields of this document type." |
| `"validator"`  | `document_type` (string), `validator` (string)          | "This rule operates on a validator's outcome."   |
| `"rule"`       | `rule` (string)                                         | "This rule depends on another rule's output."    |

### `RuleOutputSpec`

| Field           | Type                       | Default       | Notes                                                                                |
|-----------------|----------------------------|---------------|--------------------------------------------------------------------------------------|
| `type`          | string                     | `"boolean"`   | Also accepted: `"string"`, `"number"`.                                                |
| `valid_outputs` | array of string?           | `null`        | Closed set of acceptable string outputs. Anything else flags `human_revision`.       |

### Response — `result.rule_results[]`

```jsonc
{
  "rule_id":        "invoice_acceptable",
  "predicate":      "totals_consistent AND vat_id_valid",
  "output":         "true",
  "summary":        "Both totals consistency and VAT validation passed.",
  "notes":          [],
  "human_revision": null
}
```

`human_revision` carries instructions for a human reviewer when the
output didn't fit `valid_outputs`; `null` otherwise. See
[rule-engine.md](rule-engine.md) for the DAG mechanics.

---

## 9. `options.transformations[]` — post-extraction reshaping

The `transform` stage runs **after** every other LLM stage and
**before** `rule_engine`. Two types ship in-tree:

### `entity_resolution` — declarative, fast, free

Deduplicates rows of an array field group by normalised-key match +
token-subset name match. Typical use: collapse `"Andrés Contreras"`
and `"Andres Contreras Guillen"` into a single row across documents.

```jsonc
{
  "type":              "entity_resolution",        // discriminator
  "target_group":      "personas",                 // FieldGroup.name to operate on
  "match_by":          ["dni", "nombre"],          // priority order; first non-empty wins
  "min_shared_tokens": 2,                          // safe default for name-variant matching
  "scope":             "request",                  // "task" (per-doc) or "request" (across docs)
  "output_group":      "personas_canonical"        // optional: append new group instead of replacing
}
```

### `llm` — free-form

A focused LLM call against the target group, driven by a one-sentence
`intention`:

```jsonc
{
  "type":         "llm",
  "target_group": "cargos",
  "intention":    "Normaliza cada cargo a una taxonomía cerrada: {administrador_unico, consejero, apoderado, otros}.",
  "scope":        "task",
  "prompt_id":    null                              // optional named template
}
```

### Common fields

| Field           | Type      | Default     | Notes                                                              |
|-----------------|-----------|-------------|--------------------------------------------------------------------|
| `type`          | enum      | required    | `"entity_resolution"` \| `"llm"`. Unknown types are rejected.       |
| `target_group`  | string    | required    | Must match a `FieldGroup.name` the extractor produces.              |
| `output_group`  | string?   | `null`      | When set, the result appends as a new group; original is preserved. When `null`, replaces in place. |
| `scope`         | enum      | `"task"`    | `"task"`: one pass per `(segment, document_type)`. `"request"`: concatenate across documents, run once, emit under `result.request_transformations[]`. |
| `id`            | string    | random UUID | Used in logs and the per-stage trace.                              |

### `entity_resolution`-only fields

| Field               | Type        | Default | Notes                                                       |
|---------------------|-------------|---------|-------------------------------------------------------------|
| `match_by`          | array       | required (min 1) | Priority-ordered field names. First non-empty wins as the matching key. |
| `min_shared_tokens` | integer     | `2`     | Minimum shared name tokens for a name-variant match.        |

### `llm`-only fields

| Field        | Type    | Default | Notes                                                |
|--------------|---------|---------|------------------------------------------------------|
| `intention`  | string  | required (min length 10) | One-sentence goal in any language.    |
| `prompt_id`  | string? | `null`  | Named template id from the catalog; default uses the generic transform prompt with `intention` interpolated. |

`options.stages.transform` must be enabled. `scope=request` outputs
land in `result.request_transformations[]`; `scope=task` outputs
replace (or augment) the per-document group in place. See
[transformations.md](transformations.md) for the full story.

---

## 10. Async — `POST /api/v1/extractions`, callbacks, idempotency

`SubmitExtractionRequest` is `ExtractionRequest` plus two fields:

```jsonc
{
  "intention":     "Extract structured data from the document.",
  "files":         [ … ],
  "document_types":[ … ],
  "rules":         [ … ],
  "options":       { … },
  "callback_url":  "https://your-app.example.com/flydocs/webhook",   // optional
  "metadata":      { "caller": "ingest-v2", "batch_id": "b-42" }     // optional, echoed on the webhook
}
```

| Field          | Type    | Default | Notes                                                              |
|----------------|---------|---------|--------------------------------------------------------------------|
| `callback_url` | string? | `null`  | The service POSTs an `EventEnvelope` here on `extraction.completed`. |
| `metadata`     | object  | `{}`    | Echoed back on the webhook payload — use for caller-side correlation. |

### Headers per call

| Header               | Notes                                                              |
|----------------------|--------------------------------------------------------------------|
| `Idempotency-Key`    | Send the same key to replay an existing submission. The service indexes by key (partial unique index). |
| `X-Correlation-Id`   | Stamped on every internal log line and on every event delivered. |

### Lifecycle

```text
main pipeline:
  queued ─▶ running ─▶ succeeded | failed
  queued ─▶ cancelled            (only while still queued)

post-processing (bbox refinement, when stages.bbox_refine=true):
  null ─▶ pending ─▶ running ─▶ succeeded | failed
```

The webhook fires on `extraction.completed` (and on
`extraction.post_processing.completed` when bbox refinement is
requested). The 202 response carries `id` so the caller knows what to
match on.

### Listing

`GET /api/v1/extractions?status=succeeded&limit=25&offset=0` —
comma-separated CSV filters for `status` and `post_processing_status`;
exact match on `idempotency_key`; `created_after` / `created_before`
are RFC 3339 timestamps inclusive on `submitted_at`.

### Cancellation

`DELETE /api/v1/extractions/{id}` — only valid while `status ==
"queued"`. Once the worker has started, `409 not_cancellable` is
returned.

### Bbox-refine sub-state

When `options.stages.bbox_refine=true`, async submissions publish
`extraction.post_processing.requested` after the main pipeline
finishes; the `BboxRefineWorker` grounds boxes out-of-band. The
`post_processing.bbox_refinement.status` cycles `pending → running
→ succeeded | failed` independently of `extraction.status`. The full
result with grounded boxes is available once
`post_processing.bbox_refinement.status == "succeeded"`; long-poll
with `?wait_for_bboxes=true&timeout=60` on
`GET /api/v1/extractions/{id}/result` to block on it.

### Worked example — async submit + poll + result

```bash
# 1. Submit
curl -sS http://localhost:8080/api/v1/extractions \
  -H 'content-type: application/json' \
  -H 'idempotency-key: '"$(uuidgen)" \
  -d @request.json
# → 202 {"id": "ext_01HEM...", "status": "queued", ...}

# 2. Poll state
curl -sS http://localhost:8080/api/v1/extractions/ext_01HEM...
# → 200 {"id":"ext_01HEM...","status":"running",...}

# 3. Fetch result (long-poll for grounded bboxes)
curl -sS 'http://localhost:8080/api/v1/extractions/ext_01HEM.../result?wait_for_bboxes=true&timeout=120'
# → 200 {"id":"ext_01HEM...","result":{...ExtractionResult...}}
```

---

## 11. Webhooks — envelope shape and signature

When `callback_url` is set, the service POSTs an `EventEnvelope` on
relevant lifecycle events. The body is signed with HMAC-SHA256 in
`X-Flydocs-Signature` when `FLYDOCS_WEBHOOK_HMAC_SECRET` is configured.

### Envelope — `EventEnvelope`

```jsonc
{
  "event_id":       "5dc2e9c4-…-…-…-…",            // unique per delivery — dedupe on this
  "event_type":     "extraction.completed",         // dotted snake_case
  "version":        "1.0.0",
  "occurred_at":    "2026-05-17T12:00:00Z",
  "correlation_id": "req-12345",                    // echoes the X-Correlation-Id you sent at submit time
  "tenant_id":      "acme",                         // echoes X-Tenant-Id when set

  "extraction": {                                   // current state snapshot of the resource
    "id":              "ext_01HEM...",
    "status":          "succeeded",                  // queued | running | succeeded | failed | cancelled
    "submitted_at":    "2026-05-17T11:59:30Z",
    "started_at":      "2026-05-17T11:59:32Z",
    "finished_at":     "2026-05-17T12:00:00Z",
    "attempts":        1,
    "error":           null,
    "post_processing": null
  },

  "result":   { /* ExtractionResult, populated on extraction.completed when status==succeeded */ },
  "metadata": { "caller": "ingest-v2" }              // verbatim copy of submit-time metadata
}
```

### Event types

| `event_type`                            | Triggered by                                   | Webhook fires?                                                   |
|-----------------------------------------|------------------------------------------------|------------------------------------------------------------------|
| `extraction.submitted`                  | Submit handler persists the row                | No — use the 202 response.                                       |
| `extraction.completed`                  | Main pipeline reaches a terminal status        | **Yes**. `result` is populated when status==`succeeded`.         |
| `extraction.post_processing.requested`  | Main pipeline emits bbox-refine fan-out        | No — internal.                                                   |
| `extraction.post_processing.completed`  | `BboxRefineWorker` finishes                    | **Yes** when `callback_url` was set. `result == null`; refetch via `/result`. |

### Header

```
X-Flydocs-Signature: sha256=<hex-digest-of-raw-body>
```

### Verification reference

```python
import hmac, hashlib

def verify(body: bytes, header: str, secret: str) -> bool:
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)
```

> **Verify against the raw bytes.** If your framework deserialised
> the JSON before you got the bytes, re-encoding will change the
> digest. The Python and Java SDKs ship `WebhookVerifier` helpers
> that wrap this correctly.

### Retry semantics

`5xx` and `429` trigger exponential back-off + jitter up to
`FLYDOCS_WEBHOOK_MAX_ATTEMPTS`; other 4xx is permanent. **Dedupe by
`event_id` on the client** — at-least-once delivery means the same
`event_id` may arrive more than once.

---

## 12. The `ExtractionResult` response in detail

```jsonc
{
  "id":     "ext_01HEM...",
  "status": "success",                              // "success" | "partial"

  "files":                  [ FileSummary, ... ],
  "documents":              [ Document,    ... ],
  "discovered_documents":   [ Document,    ... ],

  "rule_results":             [ RuleResult,          ... ],
  "request_transformations":  [ ExtractedFieldGroup, ... ],

  "pipeline": {
    "model":      "anthropic:claude-opus-4-7",
    "latency_ms": 43580,
    "trace":      [ TraceEntry, ...    ],
    "errors":     [ PipelineError, ... ],
    "escalation": EscalationInfo | null,
    "usage":      UsageBreakdown   | null
  }
}
```

### `FileSummary`

```jsonc
{
  "filename":       "deed.pdf",
  "media_type":     "application/pdf",
  "page_count":     21,
  "bytes":          384112,
  "matched_type":   "escritura_poderes",            // expected_type OR classifier verdict; null when neither
  "classification": {
    "document_type": "escritura_poderes",
    "matched":       true,
    "confidence":    0.97,
    "description":   "Spanish notarial power of attorney.",
    "notes":         null
  }                                                  // null when classifier skipped (pin set OR stage off)
}
```

### `Document`

```jsonc
{
  "type":         "escritura_poderes",              // matched document_type id (or "unmatched" in discovered_documents[])
  "source_file":  "deed.pdf",                        // filename of the input file
  "missing":      false,
  "pages":        [1, 2, 3],
  "confidence":   1.0,
  "description":  "Spanish notarial power of attorney",
  "notes":        null,
  "field_groups": [ ExtractedFieldGroup, ... ],
  "authenticity": DocumentAuthenticity
}
```

`discovered_documents[]` entries carry `type: "unmatched"` and
`field_groups: []`.

### `ExtractedFieldGroup`

```jsonc
{
  "name":   "otorgamiento",
  "fields": [ ExtractedField, ... ]
}
```

### `ExtractedField` (recursive)

```jsonc
{
  "name":       "fecha",
  "value":      "2025-05-15",                       // string | int | float | bool | ExtractedField[] | null
  "pages":      [1],
  "confidence": 0.98,
  "bbox":       BoundingBox | null,                  // null when no bbox was produced
  "validation": {
    "valid":  true,
    "errors": []                                     // [{rule, message}]
  },
  "judge": {
    "status":          "pass",                       // "pass" | "fail" | "uncertain"
    "confidence":      0.99,
    "evidence":        "15 May 2025",
    "notes":           "Date matches the otorgamiento date.",
    "flag_for_review": false
  },
  "notes":      null
}
```

For `array` schema fields, `value` is a list of `ExtractedField` rows;
for `object` schema fields, `value` is itself a list of `ExtractedField`
members. Names mirror the schema side.

### `BoundingBox`

```jsonc
{
  "xmin": 0.15, "ymin": 0.26, "xmax": 0.85, "ymax": 0.30,
  "quality":               "good",                   // "good" | "poor" | "suspicious" | "invalid"
  "quality_score":         0.94,                     // continuous in [0, 1]
  "source":                "pdf_text",               // "llm" | "pdf_text" | "ocr"
  "refinement_confidence": 0.91                      // null for source == "llm"
}
```

`null` at the field site signals absence. There is no synthetic
"empty" box.

### `DocumentAuthenticity`

```jsonc
{
  "visual": [
    {"name": "firma_notario", "passed": true, "confidence": 0.85, "notes": null}
  ],
  "content": {
    "overall_integrity_status": "valid",             // "valid" | "invalid" | "uncertain"
    "checks": [
      {"name": "dates_consistent", "description": "All dates are mutually consistent.",
       "status": "pass", "evidence": "...", "reasoning": "..."}
    ]
  }                                                  // null when content_authenticity stage is off
}
```

### `pipeline.trace`

```jsonc
[
  {"node": "extract", "started_at": "...", "completed_at": "...", "latency_ms": 21352.88, "status": "success"}
]
```

`node` ∈ `load` · `discover` · `classify` · `plan_tasks` · `extract` ·
`bbox_validation` · `bbox_refine` · `field_validation` ·
`visual_authenticity` · `content_authenticity` · `judge` ·
`judge_escalation` · `transform` · `rules` · `assemble`.

`status` ∈ `success` · `failed` · `skipped`.

### `pipeline.errors`

```jsonc
[
  {"node": "judge", "code": "stage_timeout", "message": "Judge stage exceeded its per-call timeout."}
]
```

Cleaner than v0's untyped dicts. The node + code combination is
callable from rule expressions and from monitoring dashboards.

### `pipeline.escalation`

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

### `pipeline.usage`

```jsonc
{
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
    "flydocs-extractor":  {"input_tokens": 78936, "output_tokens": 6057, "total_tokens": 84993, "cost_usd": 1.638},
    "flydocs-judge":      {"input_tokens": 73023, "output_tokens": 5719, "total_tokens": 78742, "cost_usd": 1.524}
  },
  "by_model": {
    "anthropic:claude-opus-4-7": {"input_tokens": 318338, "output_tokens": 17797, "total_tokens": 336135, "cost_usd": 6.110}
  }
}
```

---

## 13. Errors — RFC 7807 problem-details

Every non-2xx response is RFC 7807:

```jsonc
{
  "type":     "https://flydocs.dev/problems/timeout",
  "title":    "Extraction timed out",
  "status":   408,
  "code":     "timeout",
  "detail":   "Pipeline exceeded 60s sync ceiling.",
  "instance": null,
  "extensions": { /* arbitrary extra context */ }
}
```

### Common codes

| `code`                      | Status | Meaning                                                                                                   |
|-----------------------------|--------|-----------------------------------------------------------------------------------------------------------|
| `invalid_request`           | 400    | Pydantic validation failed (offending paths under `extensions.errors`).                                   |
| `unauthorized`              | 401    | Missing / invalid `Authorization`.                                                                         |
| `not_found`                 | 404    | Unknown extraction id.                                                                                    |
| `timeout`                   | 408    | Pipeline exceeded `FLYDOCS_SYNC_TIMEOUT_S`. Retry via `POST /api/v1/extractions`.                         |
| `not_ready`                 | 409    | `GET /api/v1/extractions/{id}/result` called before the worker finished.                                  |
| `not_cancellable`           | 409    | Worker already started; mid-flight cancellation isn't supported.                                          |
| `file_too_large`            | 413    | File over `FLYDOCS_MAX_BYTES`. Body names the file under `extensions.filename`.                            |
| `invalid_base64`            | 422    | `content_base64` failed strict parsing.                                                                    |
| `validation_failed`         | 422    | Semantic validator caught an issue (rule references unknown field, duplicate ids, cycles, …). Full report in `extensions`. |
| `encrypted_pdf`             | 422    | Password-protected PDF.                                                                                    |
| `unsupported_file`          | 422    | MIME not on supported list.                                                                                |
| `office_conversion_failed`  | 422    | Office adapter rejected the conversion.                                                                    |
| `archive_extraction_failed` | 422    | Archive bundle could not be unpacked.                                                                      |
| `image_conversion_failed`   | 422    | Image normaliser could not produce a provider-readable raster.                                             |

See [api-reference.md § 8](api-reference.md#8-error-codes) for the
full per-endpoint table.

---

## 14. The kitchen sink — full request with every feature

A realistic invoice extraction touching every field-level feature:
typed schema with array-of-objects, multiple validators, every
applicable stage on, business rules, an entity-resolution
transformation, idempotency, correlation id, webhook callback.

```jsonc
POST /api/v1/extractions
Content-Type: application/json
Idempotency-Key: ingest-v2:b-42
X-Correlation-Id: req-12345

{
  "intention": "Extract structured data from the document.",
  "files": [
    { "filename": "invoice.pdf", "content_base64": "JVBERi0xLjQK..." }
  ],
  "document_types": [
    {
      "id":          "invoice",
      "description": "Vendor invoice",
      "country":     "ES",
      "field_groups": [
        {
          "name": "header",
          "fields": [
            { "name": "invoice_number", "type": "string", "required": true },
            { "name": "invoice_date",   "type": "string", "format": "date", "required": true },
            { "name": "supplier_name",  "type": "string", "required": true },
            { "name": "supplier_vat",   "type": "string", "required": true,
              "validators": [
                { "name": "vat_id", "params": { "country": "ES" } }
              ]
            },
            { "name": "supplier_iban", "type": "string",
              "validators": [{ "name": "iban" }]
            }
          ]
        },
        {
          "name": "totals",
          "fields": [
            { "name": "subtotal",     "type": "number", "required": true, "minimum": 0.0 },
            { "name": "tax_amount",   "type": "number", "required": true, "minimum": 0.0 },
            { "name": "total_amount", "type": "number", "required": true, "minimum": 0.0 },
            { "name": "currency",     "type": "string", "required": true,
              "validators": [{ "name": "currency_code" }]
            }
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
                  { "name": "unit_price",  "type": "number", "minimum": 0 },
                  { "name": "line_total",  "type": "number", "minimum": 0 }
                ]
              }
            }
          ]
        }
      ]
    }
  ],
  "rules": [
    {
      "id": "totals_consistent",
      "predicate": "subtotal + tax_amount equals total_amount within 0.01",
      "parents": [{ "kind": "field", "document_type": "invoice",
                    "fields": ["subtotal", "tax_amount", "total_amount"] }]
    },
    {
      "id": "vat_id_valid",
      "predicate": "The supplier_vat field passes the vat_id validator",
      "parents": [{ "kind": "validator",
                    "document_type": "invoice", "validator": "vat_id" }]
    },
    {
      "id": "invoice_acceptable",
      "predicate": "totals_consistent AND vat_id_valid",
      "parents": [
        { "kind": "rule", "rule": "totals_consistent" },
        { "kind": "rule", "rule": "vat_id_valid" }
      ]
    }
  ],
  "options": {
    "language_hint":          "es",
    "model":                  "anthropic:claude-sonnet-4-6",
    "stages": {
      "classifier":         true,
      "field_validation":   true,
      "judge":              true,
      "judge_escalation":   true,
      "bbox_refine":        true,
      "rule_engine":        true,
      "transform":          true
    },
    "escalation": {
      "threshold": 0.25,
      "model":     "anthropic:claude-opus-4-7"
    },
    "transformations": [
      {
        "type":         "entity_resolution",
        "target_group": "line_items",
        "match_by":     ["description"],
        "scope":        "task",
        "output_group": "line_items_dedup"
      }
    ]
  },
  "callback_url": "https://your-app.example.com/flydocs/webhook",
  "metadata":     { "caller": "ingest-v2", "batch_id": "b-42" }
}
```

The webhook receiver gets the full `ExtractionResult` under `result`
on `extraction.completed`. Verify with the SDK's `WebhookVerifier`
(see [§ 11](#11-webhooks--envelope-shape-and-signature)).

---

## Further reading

- [`QUICKSTART.md`](../QUICKSTART.md) — 5-minute zero-to-first-extraction.
- [`api-reference.md`](api-reference.md) — endpoint catalogue (paths, status codes, headers).
- [`migration-v0-to-v1.md`](migration-v0-to-v1.md) — every old key → new key.
- [`pipeline.md`](pipeline.md) — stage DAG internals (timeouts, concurrency, cost telemetry).
- [`rule-engine.md`](rule-engine.md) — rule engine semantics + DAG resolution.
- [`validators.md`](validators.md) — per-validator algorithm references.
- [`transformations.md`](transformations.md) — the `transform` stage internals.
- [`sdks/python/TUTORIAL.md`](../sdks/python/TUTORIAL.md) — Python typed-models view of the same surface.
- [`sdks/java/TUTORIAL.md`](../sdks/java/TUTORIAL.md) — Java records view of the same surface.
