# flydocs — Payload Composition Reference

The complete reference for the `ExtractionRequest` / `SubmitJobRequest` JSON the service accepts. Every field, every variant, every option, every value — with constraints, defaults, and JSON examples. Language-neutral; applies to curl, Postman, the [Python SDK](../sdks/python/), the [Java SDK](../sdks/java/), and any future client.

> **Looking for…**
> - A 5-minute first-extraction: [`QUICKSTART.md`](../QUICKSTART.md).
> - The HTTP endpoint catalogue (paths, status codes, headers): [`api-reference.md`](api-reference.md).
> - The pipeline DAG internals: [`pipeline.md`](pipeline.md).

---

## Table of contents

1. [Mental model](#1-mental-model)
2. [The top-level envelope](#2-the-top-level-envelope)
3. [`documents[]` — input files](#3-documents--input-files)
4. [`docs[]` — what to extract](#4-docs--what-to-extract)
5. [`fieldGroupFields[]` — field-level shape and constraints](#5-fieldgroupfields--field-level-shape-and-constraints)
6. [`standard_validators[]` — built-in validators (full catalogue)](#6-standard_validators--built-in-validators-full-catalogue)
7. [`options` — pipeline configuration](#7-options--pipeline-configuration)
8. [`rules[]` — business rules over extracted fields](#8-rules--business-rules-over-extracted-fields)
9. [`options.transformations[]` — post-extraction reshaping](#9-optionstransformations--post-extraction-reshaping)
10. [Async jobs — `POST /jobs`, callbacks, idempotency](#10-async-jobs--post-jobs-callbacks-idempotency)
11. [Webhooks — payload shape and signature](#11-webhooks--payload-shape-and-signature)
12. [Errors — RFC 7807 problem-details](#12-errors--rfc-7807-problem-details)
13. [The kitchen sink — full request with every feature](#13-the-kitchen-sink--full-request-with-every-feature)

---

## 1. Mental model

A flydocs request carries three things:

```
  ┌────────────────────── ExtractionRequest ─────────────────────┐
  │                                                              │
  │   documents:  [ DocumentInput, … ]    ← the bytes            │
  │   docs:       [ DocSpec, … ]          ← the schema           │
  │   rules:      [ RuleSpec, … ]         ← the predicates       │
  │   options:    ExtractionOptions       ← the knobs            │
  │                                                              │
  └──────────────────────────────────────────────────────────────┘
```

The service runs a configurable pipeline:

```
  documents → splitter? → classifier? → extract (always) → field_validation? →
            → visual_authenticity? → content_authenticity? → judge? →
            → judge_escalation? → bbox_refine? → transform? → rule_engine? → assemble
```

`extract` is mandatory; every other stage is opt-in via `options.stages`. The response (`ExtractionResult`) carries one entry per resolved `DocSpec` under `documents`, plus per-stage trace and (when enabled) rule results, transformation outputs, judge verdicts, etc.

Two integration modes share **the exact same request body**:

| Mode            | Endpoint                                          | When to use                                                 |
|-----------------|---------------------------------------------------|-------------------------------------------------------------|
| **Sync extract**  | `POST /api/v1/extract`                          | Single document, sub-minute. Caller waits on the HTTP call. |
| **Async jobs**    | `POST /api/v1/jobs` + `GET /api/v1/jobs/{id}/result` | Long-running, batches, webhook-delivered results.   |

`SubmitJobRequest` adds `callback_url` + `metadata` on top of `ExtractionRequest`; everything else is identical.

---

## 2. The top-level envelope

```jsonc
POST /api/v1/extract
Content-Type: application/json
Idempotency-Key:  (optional)
X-Correlation-Id: (optional, propagated everywhere)
{
  "request_id":   "00000000-0000-0000-0000-000000000001",      // optional, server fills random UUIDv4
  "intention":    "Extract structured data from the document.", // optional
  "documents":    [ … ],                                        // REQUIRED, min 1
  "docs":         [ … ],                                        // REQUIRED, min 1
  "rules":        [ … ],                                        // optional, default []
  "options":      { … }                                          // optional, sensible defaults
}
```

| Field         | Type                       | Default                                          | Notes                                                              |
|---------------|----------------------------|--------------------------------------------------|--------------------------------------------------------------------|
| `request_id`  | UUID v4                    | random                                           | Correlates logs / replays.                                          |
| `intention`   | string                     | `"Extract structured data from the document."`   | Free-form guidance baked into every LLM call.                       |
| `documents`   | array of `DocumentInput`   | —, **required** (min 1)                          | One entry per file.                                                  |
| `docs`        | array of `DocSpec`         | —, **required** (min 1)                          | One entry per expected document type.                                |
| `rules`       | array of `RuleSpec`        | `[]`                                             | Business-rule DAG. See §8.                                          |
| `options`     | `ExtractionOptions`        | `{}` (server-default stage toggles + bbox=true)  | Per-request knobs. See §7.                                          |

---

## 3. `documents[]` — input files

```jsonc
{
  "filename":       "invoice.pdf",                  // REQUIRED, non-empty
  "content_base64": "JVBERi0xLjQK...",              // REQUIRED, strict base64 (data: URLs accepted)
  "content_type":   "application/pdf",              // optional MIME hint; omit to let the service sniff
  "document_type":  "invoice"                       // optional caller-pinned type; skips the classifier
}
```

### Fields

| Field             | Type    | Default | Required | Notes                                                                                                  |
|-------------------|---------|---------|----------|--------------------------------------------------------------------------------------------------------|
| `filename`        | string  | —       | yes      | Surfaced verbatim in `result.files[*].filename`.                                                        |
| `content_base64`  | string  | —       | yes      | Strict base64. `data:<media-type>;base64,...` data URLs are accepted; the prefix is stripped server-side. |
| `content_type`    | string? | `null`  | no       | MIME hint. Omit to let the service sniff magic bytes.                                                  |
| `document_type`   | string? | `null`  | no       | Must match a declared `docs[*].docType.documentType`. Skips the classifier for this file.              |

### Accepted formats

The service's binary normaliser converts anything non-native before reaching the LLM:

| Family            | Examples                                              | Conversion path                                |
|-------------------|-------------------------------------------------------|------------------------------------------------|
| PDF               | `application/pdf`                                     | pass-through                                   |
| Raster images     | PNG, JPEG, WebP, GIF                                  | pass-through                                   |
| Other images      | HEIC/HEIF, AVIF, multi-frame TIFF, SVG, BMP           | Pillow / pillow-heif / cairosvg                |
| Office docs       | DOCX, XLSX, PPTX, RTF, ODT, HTML                      | Configurable `OfficeConverter` (default: Gotenberg sidecar; LibreOffice fallback) |
| Archive / email   | ZIP, 7z, TAR, GZIP, EML, MSG                          | Fanned into multiple internal rows; one extraction per included file |

Encrypted / corrupt PDFs and other unreadable inputs return `422 invalid_request` with the reason in `detail`.

### Sizing

Per-file cap is `FLYDOCS_MAX_BYTES` (deployment-configurable). Going over yields `413 document_too_large`.

---

## 4. `docs[]` — what to extract

One entry per **expected document type**. When you submit multiple files, the classifier matches each file to a `DocSpec` unless the caller pins `documents[*].document_type`.

```jsonc
{
  "docType": {                                       // REQUIRED
    "documentType": "invoice",                       // REQUIRED, non-empty (use as stable id)
    "description":  "Vendor invoice (paper or PDF)", // optional
    "country":      "ES"                              // optional, ISO 3166-1 alpha-2
  },
  "fieldGroups": [                                   // REQUIRED, min 1
    { … }                                            // FieldGroup -- see below
  ],
  "validators": {                                    // optional
    "visual": [
      { "name": "signature_present",
        "description": "A handwritten or e-signature is visible" }
    ]
  }
}
```

### `DocType`

| Field            | Type    | Default | Notes                                                                                            |
|------------------|---------|---------|--------------------------------------------------------------------------------------------------|
| `documentType`   | string  | —, required (non-empty) | Stable id. Referenced by `RuleParent.documentType` and `DocumentInput.document_type`. snake_case lower-kebab convention: `invoice`, `purchase_order`, `id_card_es`, `passport_int`. |
| `description`    | string  | `""`    | Hints the classifier when the request is multi-doc.                                              |
| `country`        | string  | `""`    | ISO 3166-1 alpha-2 (`"ES"`, `"US"`, …). Hint for region-aware validators / formats.              |

### `FieldGroup`

A named bundle of fields the service should extract together. Use groups to partition the schema logically — `header`, `totals`, `line_items_block`, …

```jsonc
{
  "fieldGroupName":  "totals",                       // REQUIRED, non-empty
  "fieldGroupDesc":  "Money block at the top of the invoice", // optional
  "fieldGroupFields": [ … ]                          // REQUIRED, min 1 -- FieldSpec entries
}
```

| Field                 | Type                  | Default | Notes                                                |
|-----------------------|-----------------------|---------|------------------------------------------------------|
| `fieldGroupName`      | string                | —, required (non-empty) | snake_case. Surfaced verbatim in the response under `fields[*].fieldGroupName`. |
| `fieldGroupDesc`      | string                | `""`    | Free-form description shown to the LLM.              |
| `fieldGroupFields`    | array of `FieldSpec`  | —, required (min 1) | See §5.                                  |

### `ValidatorsSpec` + `VisualValidatorSpec`

Per-`DocSpec` validator declarations. Currently only `visual` is exposed publicly; future types (`audio`, `structural`) will plug in here.

| Field                              | Type                          | Notes                                                |
|------------------------------------|-------------------------------|------------------------------------------------------|
| `validators.visual[]`              | array of `VisualValidatorSpec`| One entry per visual check the LLM should run.       |
| `validators.visual[*].name`        | string                        | Short identifier the response carries back.           |
| `validators.visual[*].description` | string                        | What the LLM should look for.                        |

`options.stages.visual_authenticity` must be enabled for these to fire.

---

## 5. `fieldGroupFields[]` — field-level shape and constraints

### `FieldSpec`

One field the caller wants extracted. JSON keys use a mix of camelCase (`fieldName` aliases) and lowercase — see the table:

```jsonc
{
  "name":        "total_amount",                     // REQUIRED, non-empty (snake_case)
  "description": "Amount due in cents",              // optional
  "type":        "number",                           // optional, default "string"
  "required":    true,                               // optional, default false
  "pattern":     "^\\d+\\.\\d{2}$",                  // optional, RFC-flavour regex
  "format":      null,                               // optional, one of date|date-time|email|uri|uuid
  "enum":        null,                               // optional, closed value set
  "minimum":     0.0,                                // optional, numeric inclusive lower bound
  "maximum":     null,                               // optional, numeric inclusive upper bound
  "items":       null,                               // REQUIRED iff type == "array"
  "standard_validators": []                          // optional, see §6
}
```

| Field                  | Type                          | Default     | Notes                                                              |
|------------------------|-------------------------------|-------------|--------------------------------------------------------------------|
| `name`                 | string                        | —, required | The key under which the extracted value appears in the response.   |
| `description`          | string                        | `""`        | Free-form hint for the LLM.                                        |
| `type`                 | enum                          | `"string"`  | See below.                                                          |
| `required`             | boolean                       | `false`     | `true` ⇒ a missing field surfaces as a `field_validation` error.    |
| `pattern`              | string?                       | `null`      | Applied by the field validator stage.                              |
| `format`               | enum?                         | `null`      | JSON-Schema-style format hint.                                     |
| `enum`                 | array?                        | `null`      | Closed set of acceptable values.                                   |
| `minimum`              | number?                       | `null`      | Numeric lower bound (inclusive).                                   |
| `maximum`              | number?                       | `null`      | Numeric upper bound (inclusive).                                   |
| `items`                | array of `FieldItem`?         | `null`      | **Required** when `type == "array"`; ignored otherwise.            |
| `standard_validators`  | array of `StandardValidatorSpec` | `[]`     | See §6.                                                            |

### `type` enum — the five primitives

| Wire value | Use for                                                          |
|------------|------------------------------------------------------------------|
| `"string"` | Free-form text, identifier, format-validated string.             |
| `"number"` | Floats / decimals. Pair with `minimum` / `maximum` / `format`.   |
| `"integer"`| Integral quantities (counts, page numbers, quantities).          |
| `"boolean"`| Yes/no, present/absent, signed/unsigned.                          |
| `"array"`  | Repeating rows. **Requires** `items`.                             |

### `format` enum — JSON-Schema-style hints

| Wire value     | Validation                  |
|----------------|-----------------------------|
| `"date"`       | `YYYY-MM-DD`                |
| `"date-time"`  | RFC 3339 / ISO 8601 with time |
| `"email"`      | RFC 5322                    |
| `"uri"`        | Generic URI                 |
| `"uuid"`       | RFC 4122                    |

> **`format` vs `standard_validators`.** `format` is a single-shot JSON-Schema-style check baked onto the field; `standard_validators` is the extensible catalogue (IBAN, NIE, VAT_ID, …). Prefer `format` for format-only checks (cheaper, not surfaced as a validator hit); use validators for domain checks.

### `FieldItem` — sub-fields inside an array field

```jsonc
{
  "fieldName":         "description",                // REQUIRED (camelCase!)
  "fieldDescription":  "Free-text line description", // optional
  "fieldType":         "string",                     // optional, default "string"
  "pattern":           null,
  "format":            null,
  "enum":              null,
  "minimum":           null,
  "maximum":           null,
  "standard_validators": []
}
```

| Field                 | Type                          | Notes                                                              |
|-----------------------|-------------------------------|--------------------------------------------------------------------|
| `fieldName`           | string (camelCase!)           | Column name. Note: top-level fields use `name`, array sub-fields use `fieldName`. |
| `fieldDescription`    | string                        | Free-form hint per column.                                          |
| `fieldType`           | enum (see above)              | `"array"` is **not** supported here (no nested arrays).             |
| `pattern`, `format`, `enum`, `minimum`, `maximum`, `standard_validators` | (same as `FieldSpec`) | All constraints apply per row.                       |

### Worked example — invoice line items as a repeating row

```jsonc
{
  "name": "line_items",
  "description": "One row per line item",
  "type": "array",
  "items": [
    { "fieldName": "description", "fieldType": "string" },
    { "fieldName": "quantity",    "fieldType": "number", "minimum": 0 },
    { "fieldName": "unit_price",  "fieldType": "number", "minimum": 0 },
    { "fieldName": "line_total",  "fieldType": "number", "minimum": 0 }
  ]
}
```

---

## 6. `standard_validators[]` — built-in validators (full catalogue)

Attach validators to a `FieldSpec` (or a `FieldItem` for array columns). The field-validator stage runs them after extraction and folds the result into `ExtractedField.field_validation`.

```jsonc
"standard_validators": [
  { "type": "iban" },
  { "type": "phone_e164", "params": { "country": "ES" } },
  { "type": "vat_id",     "params": { "country": "ES" }, "severity": "warning" }
]
```

| Field      | Type                            | Default   | Notes                                              |
|------------|---------------------------------|-----------|----------------------------------------------------|
| `type`     | enum (see below)                | required  | Use raw strings; the SDKs ship typed enums.        |
| `params`   | object                          | `{}`      | Per-validator parameters (e.g. `{"country": "ES"}`). |
| `severity` | `"error"` \| `"warning"`        | `"error"` | `"warning"` records the issue but keeps `valid=true`. |

### Complete catalogue

| Category   | Wire value         | `params`                          | Notes                                  |
|------------|--------------------|-----------------------------------|----------------------------------------|
| **Network / web** | `email`         | none                              | RFC 5322                                |
|                   | `uri`           | none                              | Generic URI                             |
|                   | `url`           | none                              | HTTP(S) URL                             |
|                   | `ipv4`          | none                              |                                         |
|                   | `ipv6`          | none                              |                                         |
|                   | `domain`        | none                              | DNS-like                                |
|                   | `slug`          | none                              | URL slug                                |
| **Temporal**      | `date`          | none                              | `YYYY-MM-DD`                            |
|                   | `datetime`      | none                              | ISO 8601 with time                      |
|                   | `time`          | none                              | `HH:MM[:SS]`                            |
|                   | `iso_8601`      | none                              | Strict ISO 8601                         |
| **Identifiers**   | `uuid`          | none                              | RFC 4122                                |
|                   | `json`          | none                              | Parses as valid JSON                    |
|                   | `hex_color`     | none                              | `#RGB` or `#RRGGBB`                     |
| **Finance**       | `iban`          | none                              | ISO 13616 (country derived from prefix) |
|                   | `bic`           | none                              | ISO 9362 (SWIFT)                        |
|                   | `credit_card`   | none                              | Luhn-checked                            |
|                   | `currency_code` | none                              | ISO 4217 alpha-3                        |
|                   | `amount`        | none                              | Numeric > 0                             |
| **Telephony**     | `phone_e164`    | `{ "country": "ES" }` (optional)  | `+<country><number>`                    |
| **Geographic**    | `country_code`  | none                              | ISO 3166-1 alpha-2                      |
|                   | `language_code` | none                              | ISO 639-1                               |
|                   | `postal_code`   | `{ "country": "ES" }` (optional)  | Country-aware                           |
|                   | `latitude`      | none                              |                                         |
|                   | `longitude`     | none                              |                                         |
| **National IDs**  | `nif`           | none (ES implied)                 | Person tax id                            |
|                   | `nie`           | none                              | ES — foreign person tax id              |
|                   | `cif`           | none                              | ES — legacy company tax id              |
|                   | `vat_id`        | `{ "country": "ES" }` (optional)  | EU VAT                                  |
|                   | `ssn`           | none                              | US                                       |
|                   | `passport_number` | none                            | ICAO 9303 length / charset only         |

`options.stages.field_validation` must be enabled (it is, by default) for validators to fire.

---

## 7. `options` — pipeline configuration

```jsonc
"options": {
  "return_bboxes":         true,
  "language_hint":         "es",
  "model":                 "anthropic:claude-sonnet-4-6",
  "declared_media_type":   null,
  "stages": {
    "splitter":              false,
    "classifier":            true,
    "field_validation":      true,
    "visual_authenticity":   false,
    "content_authenticity":  false,
    "judge":                 false,
    "rule_engine":           false,
    "judge_escalation":      false,
    "bbox_refine":           false,
    "transform":             false
  },
  "escalation_threshold":  null,
  "escalation_model":      null,
  "transformations":       []
}
```

### `ExtractionOptions`

| Field                    | Type     | Default               | Notes                                                              |
|--------------------------|----------|-----------------------|--------------------------------------------------------------------|
| `return_bboxes`          | boolean  | `true`                | `false` strips bboxes from the response (cheaper to transfer).      |
| `language_hint`          | string?  | `null`                | ISO 639-1, ≤ 16 chars. Guides multilingual OCR / extraction.        |
| `model`                  | string?  | `null` (env default)  | Per-request primary model id (e.g. `anthropic:claude-sonnet-4-6`).  |
| `declared_media_type`    | string?  | `null`                | Override sniffing; rare.                                            |
| `stages`                 | object   | server defaults       | See `StageToggles` below.                                           |
| `escalation_threshold`   | number?  | `null` (env default)  | `0.0–1.0`. With `stages.judge_escalation=true`, fires the rerun when the judge's fail-rate crosses this. |
| `escalation_model`       | string?  | `null` (env default)  | Model id for the escalation rerun.                                  |
| `transformations`        | array    | `[]`                  | See §9.                                                             |

### `StageToggles` — all ten stages

| Stage                  | Default | Cost-ish | What it does                                                                                                  |
|------------------------|---------|----------|---------------------------------------------------------------------------------------------------------------|
| `splitter`             | `false` | one LLM call | LLM document splitter. Required when one upload mixes several document types and you need page ranges per type. |
| `classifier`           | **`true`**  | one LLM call when multi-file & not pinned | Maps each input file to one of the declared `DocSpec.docType.documentType` values. No-op when every file already carries `document_type`. |
| `field_validation`     | **`true`**  | ~free    | Pure-Python validation — `pattern`, `format`, `enum`, `min`/`max`, every `StandardValidatorSpec`.              |
| `visual_authenticity`  | `false` | one LLM call | LLM visual check using `validators.visual` declarations.                                                     |
| `content_authenticity` | `false` | one LLM call | LLM cross-document consistency checks.                                                                       |
| `judge`                | `false` | doubles extract spend | Per-field LLM re-evaluation. Annotates every extracted field with `confidence`, `evidence`, `flag_for_review`. |
| `judge_escalation`     | `false` | +1 extract+judge pass when triggered | Re-runs extract + judge with `escalation_model` when the first judge's fail-rate exceeds `escalation_threshold`. Requires `judge`. |
| `bbox_refine`          | `false` | ~50-200 ms / 30-page text-PDF; seconds / page for OCR | Replaces LLM-estimated bboxes with grounded coordinates from the document's real text layer. Multilingual-aware. On async jobs the SDK runs this out-of-band via a dedicated worker. |
| `rule_engine`          | `false` | one LLM call per rule | Evaluates the business-rule DAG. See §8.                                                                    |
| `transform`            | `false` | varies   | Runs `options.transformations`. See §9.                                                                       |

> **Picking stages.** Start with defaults (`classifier`, `field_validation`). Enable `bbox_refine` when downstream consumers need pixel-accurate boxes. Enable `judge` when you need per-field confidence + evidence (compliance-grade extractions). Enable `judge_escalation` on top of `judge` when you'd otherwise eat the cost of always running the stronger model. Enable `rule_engine` when the response should embed business decisions.

---

## 8. `rules[]` — business rules over extracted fields

Rules are **natural-language predicates** the LLM evaluates against extracted fields, validator outcomes, or other rules' outputs. They form a DAG; the engine sorts topologically and runs in dependency order. Cycles are rejected at request-validation time with `422 invalid_request`.

```jsonc
[
  {
    "id": "totals_consistent",
    "predicate": "subtotal + tax_amount equals total_amount within 0.01",
    "parents": [
      { "parentType": "field",
        "documentType": "invoice",
        "fieldNames": ["subtotal", "tax_amount", "total_amount"] }
    ],
    "output": { "type": "boolean" }
  },
  {
    "id": "vat_id_valid",
    "predicate": "The supplier_vat field passes the VAT_ID validator",
    "parents": [
      { "parentType": "validator", "documentType": "invoice", "validatorName": "vat_id" }
    ]
  },
  {
    "id": "invoice_acceptable",
    "predicate": "totals_consistent AND vat_id_valid",
    "parents": [
      { "parentType": "rule", "ruleId": "totals_consistent" },
      { "parentType": "rule", "ruleId": "vat_id_valid" }
    ]
  }
]
```

### `RuleSpec`

| Field        | Type                | Default       | Notes                                                              |
|--------------|---------------------|---------------|--------------------------------------------------------------------|
| `id`         | string              | —, required   | Unique within the request. Referenced by `RuleParent.ruleId`.       |
| `predicate`  | string              | —, required   | Natural-language statement.                                         |
| `parents`    | array of `RuleParent` | `[]`        | Discriminated union — see below.                                    |
| `output`     | `RuleOutputSpec`    | `{ "type": "boolean" }` | Shape the response should carry.                          |

### `RuleParent` — three variants (discriminator: `parentType`)

| `parentType`   | Fields                                                | Use for                                          |
|----------------|-------------------------------------------------------|--------------------------------------------------|
| `"field"`      | `documentType` (string), `fieldNames` (array, min 1)  | "This rule operates on these fields of this doc type." |
| `"validator"`  | `documentType` (string), `validatorName` (string)     | "This rule operates on a validator's outcome."   |
| `"rule"`       | `ruleId` (string)                                     | "This rule depends on another rule's output."    |

### `RuleOutputSpec`

| Field            | Type                       | Default       | Notes                                                                                |
|------------------|----------------------------|---------------|--------------------------------------------------------------------------------------|
| `type`           | string                     | `"boolean"`   | Also accepted: `"string"`, `"number"`. The engine coerces accordingly.                |
| `valid_outputs`  | array of string?           | `null`        | Closed set of acceptable string outputs. Anything else is treated as `flag_for_review`. |

### Response — `result.rule_results[]`

```jsonc
{
  "rule_id":         "invoice_acceptable",
  "predicate":       "totals_consistent AND vat_id_valid",
  "output":          "true",
  "summary":         "Both totals consistency and VAT validation passed.",
  "notes":           [],
  "human_revision":  ""
}
```

`human_revision` carries instructions for a human reviewer when the output didn't fit `valid_outputs`.

---

## 9. `options.transformations[]` — post-extraction reshaping

The `transform` stage runs **after** every other LLM stage and **before** `rule_engine`. Two types ship in-tree:

### `entity_resolution` — declarative, fast, free

Deduplicates rows of an array field group by normalised-key match + token-subset name match. Typical use: collapse `"Andrés Contreras"` and `"Andres Contreras Guillen"` into a single row across documents.

```jsonc
{
  "type":              "entity_resolution",        // discriminator
  "target_group":      "personas",                 // FieldGroup.fieldGroupName to operate on
  "match_by":          ["dni", "nombre"],          // priority order; first non-empty wins
  "min_shared_tokens": 2,                          // safe default for name-variant matching
  "scope":             "request",                  // "task" (per-doc) or "request" (across docs)
  "output_group":      "personas_canonical"        // optional: append a new group instead of replacing
}
```

### `llm` — free-form

A focused LLM call against the target group, driven by a one-sentence `intention`:

```jsonc
{
  "type":         "llm",
  "target_group": "cargos",
  "intention":    "Normaliza cada cargo a una taxonomía cerrada: {administrador_unico, consejero, apoderado, otros}.",
  "scope":        "task",
  "prompt_id":    null                              // optional named template; default uses ``intention`` only
}
```

### Common fields

| Field           | Type      | Default     | Notes                                                              |
|-----------------|-----------|-------------|--------------------------------------------------------------------|
| `type`          | enum      | required    | `"entity_resolution"` \| `"llm"`. New types may appear; unknown types are no-ops. |
| `target_group`  | string    | required    | Must match a `FieldGroup.fieldGroupName` the extractor produces.    |
| `output_group`  | string?   | `null`      | When set, the result appends as a new group; original is preserved. When `null`, replaces in place. |
| `scope`         | enum      | `"task"`    | `"task"`: one pass per `(segment, DocSpec)`. `"request"`: concatenate across documents, run once, emit under `result.request_transformations[]`. |
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

`options.stages.transform` must be enabled. `scope=request` outputs land in `result.request_transformations[]`; `scope=task` outputs replace (or augment) the per-document group in place.

---

## 10. Async jobs — `POST /jobs`, callbacks, idempotency

`SubmitJobRequest` is `ExtractionRequest` minus `request_id` plus two fields:

```jsonc
{
  "intention":   "Extract structured data from the document.",
  "documents":   [ … ],
  "docs":        [ … ],
  "rules":       [ … ],
  "options":     { … },
  "callback_url":"https://your-app.example.com/flydocs/webhook",   // optional
  "metadata":    { "caller": "ingest-v2", "batch_id": "b-42" }     // optional, echoed on the webhook
}
```

| Field          | Type    | Default | Notes                                                              |
|----------------|---------|---------|--------------------------------------------------------------------|
| `callback_url` | string? | `null`  | The service POSTs a `JobWebhookPayload` here on terminal status.   |
| `metadata`     | object  | `{}`    | Echoed back on the webhook payload — use for caller-side correlation. |

### Headers per call

| Header               | Notes                                                              |
|----------------------|--------------------------------------------------------------------|
| `Idempotency-Key`    | Send the same key to replay an existing submission instead of creating a duplicate job. The service indexes by key. |
| `X-Correlation-Id`   | Stamped on every internal log line and on the webhook payload (`correlation_id`). |

### Lifecycle

`QUEUED` → `RUNNING` → terminal (`SUCCEEDED` | `PARTIAL_SUCCEEDED` | `FAILED` | `CANCELLED`).
On terminal status the service POSTs the webhook (when `callback_url` is set) and the result becomes available at `GET /api/v1/jobs/{id}/result`.

### Bbox-refine sub-state

When `options.stages.bbox_refine=true`, async jobs publish an `IDPBboxRefineRequested` event on success; a dedicated `BboxRefineWorker` grounds boxes out-of-band. The job's `bbox_refine_status` cycles `pending → running → succeeded|failed` independently of the main status. The full result with grounded boxes is available once `bbox_refine_status == "succeeded"`; long-poll with `?wait_for_bboxes=true&timeout=60` on `GET /jobs/{id}/result` to block on it.

### Listing

`GET /api/v1/jobs?status=SUCCEEDED,PARTIAL_SUCCEEDED&limit=25&offset=0` — comma-separated CSV filters for `status` and `bbox_refine_status`; exact match on `idempotency_key`; `created_after` / `created_before` are RFC 3339 timestamps inclusive.

### Cancellation

`DELETE /api/v1/jobs/{id}` — only valid while `status == QUEUED`. Once the worker has started, `409 job_not_cancellable` is returned.

---

## 11. Webhooks — payload shape and signature

When `callback_url` is set, the service POSTs a `JobWebhookPayload` on terminal status. It signs the body with HMAC-SHA256 in `X-Flydocs-Signature` when `FLYDOCS_WEBHOOK_HMAC_SECRET` is configured on the service.

### Payload shape — `JobWebhookPayload`

```jsonc
{
  "event_id":      "5dc2e9c4-…-…-…-…",        // unique per delivery -- dedupe on this
  "event_type":    "IDPJobCompleted",
  "version":       "1.0.0",
  "job_id":        "job-abc",
  "status":        "SUCCEEDED",                // or PARTIAL_SUCCEEDED, FAILED, CANCELLED
  "occurred_at":   "2026-05-17T12:00:00Z",
  "started_at":    "2026-05-17T11:59:30Z",
  "finished_at":   "2026-05-17T12:00:00Z",
  "attempts":      1,
  "correlation_id":"req-12345",                // the X-Correlation-Id you sent at submit time
  "tenant_id":     null,
  "metadata":      { "caller": "ingest-v2" },  // echoed from SubmitJobRequest.metadata
  "result":        { … ExtractionResult … },   // null on FAILED / CANCELLED
  "error_code":    null,
  "error_message": null
}
```

### Header

```
X-Flydocs-Signature: sha256=<hex-digest-of-raw-body>
```

### Verification (HTTP-layer reference)

```python
import hmac, hashlib

def verify(body: bytes, header: str, secret: str) -> bool:
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)
```

> **Verify against the raw bytes.** If your framework deserialised the JSON before you got the bytes, re-encoding will change the digest. The Python and Java SDKs ship `WebhookVerifier` helpers that wrap this correctly.

---

## 12. Errors — RFC 7807 problem-details

Every non-2xx response is RFC 7807-ish:

```jsonc
{
  "type":   "https://flydocs.dev/problems/extraction_timeout",
  "title":  "Extraction timed out",
  "status": 408,
  "code":   "extraction_timeout",
  "detail": "Pipeline exceeded 60s sync ceiling"
}
```

### Common codes

| `code`                  | Status | Meaning                                                                                                   |
|-------------------------|--------|-----------------------------------------------------------------------------------------------------------|
| `extraction_timeout`    | 408    | Pipeline exceeded `FLYDOCS_SYNC_TIMEOUT_S`. Retry via `POST /api/v1/jobs`.                                |
| `document_too_large`    | 413    | Document over `FLYDOCS_MAX_BYTES`.                                                                         |
| `invalid_base64`        | 422    | `content_base64` failed strict parsing.                                                                    |
| `invalid_request`       | 422    | Semantic validator caught an issue (rule references unknown field, duplicate ids, cycles, …). The body carries the full list of issues in `errors[]` / `warnings[]`. |
| `job_not_ready`         | 409    | `GET /jobs/{id}/result` called before the worker finished.                                                  |
| `job_not_cancellable`   | 409    | Worker already started; mid-flight cancellation isn't supported.                                            |
| `JOB_NOT_FOUND`         | 404    | Unknown `job_id`.                                                                                          |

---

## 13. The kitchen sink — full request with every feature

A realistic invoice extraction touching every field-level feature: typed schema with array rows, multiple validators, every applicable stage on, business rules, an entity-resolution transformation, idempotency, correlation id, webhook callback.

```jsonc
POST /api/v1/jobs
Content-Type: application/json
Idempotency-Key: ingest-v2:b-42
X-Correlation-Id: req-12345

{
  "intention": "Extract structured data from the document.",
  "documents": [
    { "filename": "invoice.pdf", "content_base64": "JVBERi0xLjQK..." }
  ],
  "docs": [
    {
      "docType": { "documentType": "invoice", "description": "Vendor invoice", "country": "ES" },
      "fieldGroups": [
        {
          "fieldGroupName": "header",
          "fieldGroupFields": [
            { "name": "invoice_number", "type": "string", "required": true },
            { "name": "invoice_date",   "type": "string", "format": "date", "required": true },
            { "name": "supplier_name",  "type": "string", "required": true },
            { "name": "supplier_vat",   "type": "string", "required": true,
              "standard_validators": [
                { "type": "vat_id", "params": { "country": "ES" } }
              ]
            },
            { "name": "supplier_iban", "type": "string",
              "standard_validators": [{ "type": "iban" }]
            }
          ]
        },
        {
          "fieldGroupName": "totals",
          "fieldGroupFields": [
            { "name": "subtotal",     "type": "number", "required": true, "minimum": 0.0 },
            { "name": "tax_amount",   "type": "number", "required": true, "minimum": 0.0 },
            { "name": "total_amount", "type": "number", "required": true, "minimum": 0.0 },
            { "name": "currency",     "type": "string", "required": true,
              "standard_validators": [{ "type": "currency_code" }]
            }
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
                { "fieldName": "unit_price",  "fieldType": "number", "minimum": 0 },
                { "fieldName": "line_total",  "fieldType": "number", "minimum": 0 }
              ]
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
      "parents": [{ "parentType": "field", "documentType": "invoice",
                    "fieldNames": ["subtotal", "tax_amount", "total_amount"] }]
    },
    {
      "id": "vat_id_valid",
      "predicate": "The supplier_vat field passes the VAT_ID validator",
      "parents": [{ "parentType": "validator",
                    "documentType": "invoice", "validatorName": "vat_id" }]
    },
    {
      "id": "invoice_acceptable",
      "predicate": "totals_consistent AND vat_id_valid",
      "parents": [
        { "parentType": "rule", "ruleId": "totals_consistent" },
        { "parentType": "rule", "ruleId": "vat_id_valid" }
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
    "escalation_threshold":   0.25,
    "escalation_model":       "anthropic:claude-opus-4-7",
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

The webhook receiver gets the full `ExtractionResult` under `result`. Verify with the SDK's `WebhookVerifier` (see §11).

---

## Further reading

- [`QUICKSTART.md`](../QUICKSTART.md) — 5-minute zero-to-first-extraction.
- [`api-reference.md`](api-reference.md) — endpoint catalogue (paths, status codes, headers).
- [`pipeline.md`](pipeline.md) — stage DAG internals (timeouts, concurrency, cost telemetry).
- [`rule-engine.md`](rule-engine.md) — rule engine semantics + DAG resolution.
- [`standard-validators.md`](standard-validators.md) — per-validator algorithm references.
- [`transformations.md`](transformations.md) — the `transform` stage internals.
- [`sdks/python/TUTORIAL.md`](../sdks/python/TUTORIAL.md) — Python typed-models view of the same surface.
- [`sdks/java/TUTORIAL.md`](../sdks/java/TUTORIAL.md) — Java records view of the same surface.
