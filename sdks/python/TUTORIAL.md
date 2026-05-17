# flydocs Python SDK — Tutorial

A complete, payload-composition-focused reference for the flydocs Python SDK. Every typed model is documented: **what it carries**, **what variants exist**, **what values are accepted**, **what the defaults are**, and **what the wire shape looks like**.

> **Audience.** Engineers integrating flydocs into a Python codebase who want to know exactly which knobs exist and how to compose them. For a 5-minute zero-to-first-extraction, see [QUICKSTART.md](./QUICKSTART.md).
>
> **Prerequisites.** Python ≥ 3.11. A flydocs service reachable at some base URL — locally via `task docker:up:test` on the repo root.

---

## Table of contents

1. [The mental model](#1-the-mental-model)
2. [`ExtractionRequest` — the top-level envelope](#2-extractionrequest--the-top-level-envelope)
3. [`DocumentInput` — input files](#3-documentinput--input-files)
4. [`DocSpec` — what to extract](#4-docspec--what-to-extract)
5. [`FieldSpec` & `FieldItem` — field-level shape and constraints](#5-fieldspec--fielditem--field-level-shape-and-constraints)
6. [`StandardValidatorSpec` — built-in validators (full catalogue)](#6-standardvalidatorspec--built-in-validators-full-catalogue)
7. [`ExtractionOptions` & `StageToggles` — pipeline configuration](#7-extractionoptions--stagetoggles--pipeline-configuration)
8. [`RuleSpec` — business rules over extracted fields](#8-rulespec--business-rules-over-extracted-fields)
9. [Transformations — post-extraction reshaping](#9-transformations--post-extraction-reshaping)
10. [Async jobs — `SubmitJobRequest`, callbacks, idempotency](#10-async-jobs--submitjobrequest-callbacks-idempotency)
11. [Webhooks — receiving and verifying delivery](#11-webhooks--receiving-and-verifying-delivery)
12. [Errors — RFC 7807 problem-details](#12-errors--rfc-7807-problem-details)
13. [Production patterns](#13-production-patterns)
14. [The kitchen sink — full request with every feature](#14-the-kitchen-sink--full-request-with-every-feature)
15. [Synchronous facade (when async isn't an option)](#15-synchronous-facade-when-async-isnt-an-option)

---

## 1. The mental model

A flydocs request carries three things:

```
  ┌─────────────────── ExtractionRequest ─────────────────┐
  │                                                       │
  │   documents:  [DocumentInput, ...]   ← the bytes      │
  │   docs:       [DocSpec, ...]         ← the schema     │
  │   rules:      [RuleSpec, ...]        ← the predicates │
  │   options:    ExtractionOptions      ← the knobs      │
  │                                                       │
  └───────────────────────────────────────────────────────┘
```

The service runs a configurable pipeline:

```
  documents → splitter? → classifier? → extract (always) → field_validation? →
            → visual_authenticity? → content_authenticity? → judge? →
            → judge_escalation? → bbox_refine? → transform? → rule_engine? → assemble
```

`extract` is mandatory; every other stage is opt-in via `StageToggles`. The response (`ExtractionResult`) carries one entry per resolved `DocSpec` under `documents`, plus per-stage trace and (when enabled) rule results, transformation outputs, judge verdicts, etc.

Two integration modes share the same request shape:

| Mode            | Method                            | When to use                                                 |
|-----------------|-----------------------------------|-------------------------------------------------------------|
| **Sync extract**  | `await flydocs.extract(req)`     | Single document, sub-minute. Caller waits on the HTTP call. |
| **Async jobs**    | `await flydocs.submit_job(req)` + `await flydocs.wait_for_completion(job_id)` | Long-running, batches, webhook-delivered results. |

---

## 2. `ExtractionRequest` — the top-level envelope

| Field           | Type                                    | Default                  | Required | Notes                                                              |
|-----------------|-----------------------------------------|--------------------------|----------|--------------------------------------------------------------------|
| `request_id`    | `UUID`                                  | random UUIDv4            | no       | Use it to correlate logs / re-fetch by id later.                   |
| `intention`     | `str`                                   | `"Extract structured data from the document."` | no | Free-form guidance for every LLM node (extract, judge, rules, …). |
| `documents`     | `list[DocumentInput]`                   | —                        | **yes**, min length 1 | Input files. A single file is a one-element list.    |
| `docs`          | `list[DocSpec \| dict]`                  | —                        | **yes**, min length 1 | One entry per **expected document type**.            |
| `rules`         | `list[RuleSpec \| dict]`                 | `[]`                     | no       | Business-rule DAG. See §8.                                         |
| `options`       | `ExtractionOptions \| dict`             | `ExtractionOptions()`    | no       | Per-request knobs. See §7.                                         |

Every field that takes a typed model also accepts a plain `dict` — useful for forward-compatibility with server-side fields the SDK hasn't surfaced yet.

```python
from flydocs_sdk import (
    DocSpec, DocumentInput, ExtractionRequest,
    FieldGroup, FieldSpec, FieldType,
)

invoice = DocSpec(
    doc_type={"documentType": "invoice"},
    field_groups=[
        FieldGroup.of("totals",
            FieldSpec(field_name="total", field_type=FieldType.NUMBER, required=True)),
    ],
)

req = ExtractionRequest(
    documents=[DocumentInput.from_path("invoice.pdf")],
    docs=[invoice],
)
```

---

## 3. `DocumentInput` — input files

One entry per input file. Each file is processed independently by the pipeline.

| Field             | Type            | Default | Required | Notes                                                           |
|-------------------|-----------------|---------|----------|-----------------------------------------------------------------|
| `filename`        | `str`           | —       | **yes**, non-empty | Surfaced on the response so you know which file produced what. |
| `content_base64`  | `str`           | —       | **yes**  | Base64 of the raw bytes. `data:<media-type>;base64,...` URLs are accepted; the SDK strips the prefix client-side. |
| `content_type`    | `str \| None`   | `None`  | no       | MIME hint. Omit to let the service sniff magic bytes.           |
| `document_type`   | `str \| None`   | `None`  | no       | When set, pins this file to one of the declared `DocSpec.doc_type.documentType` values; the classifier is skipped for this file. |

### Three ways to build one

```python
# 1. From bytes you already have in memory
doc = DocumentInput.from_bytes(b"%PDF-1.4...", filename="invoice.pdf",
                                content_type="application/pdf")

# 2. From a path on disk
doc = DocumentInput.from_path("invoice.pdf")
doc = DocumentInput.from_path("invoice.pdf", document_type="invoice")  # caller-pinned

# 3. Hand-build (e.g. when you already have the base64)
doc = DocumentInput(
    filename="invoice.pdf",
    content_base64="JVBERi0xLjQK...",
    content_type="application/pdf",
)
```

### Accepted formats

flydocs runs binary normalisation upstream of the extractor, so any of these reach the LLM cleanly:

| Family           | Examples                                              | Native to provider? |
|------------------|-------------------------------------------------------|---------------------|
| PDF              | `application/pdf`                                     | yes (pass-through)  |
| Raster image     | PNG, JPEG, WebP, GIF                                  | yes (pass-through)  |
| Other image      | HEIC/HEIF, AVIF, multi-frame TIFF, SVG, BMP           | no — converted via Pillow / pillow-heif / cairosvg |
| Office docs      | DOCX, XLSX, PPTX, RTF, ODT, HTML                      | no — converted via the configured `OfficeConverter` (default Gotenberg HTTP sidecar) |
| Archive / email  | ZIP, 7z, TAR, GZIP, EML, MSG                          | no — fanned out into multiple internal rows by the normalizer |

Encrypted or corrupt PDFs raise a typed `FlydocsHTTPError(422, code="invalid_request")` with the underlying reason in `detail`.

### Sizing

The service enforces `FLYDOCS_MAX_BYTES` per file. Going over yields `FlydocsHTTPError(413, code="document_too_large")`. Defaults vary by deployment — call `flydocs.version()` for instance identity, or split the file before submitting.

---

## 4. `DocSpec` — what to extract

One `DocSpec` per **expected document type**. When you submit multiple files, the classifier matches each file to one of the declared specs unless the caller pins `DocumentInput.document_type`.

```python
from flydocs_sdk import (
    DocSpec, DocType, FieldGroup, FieldSpec, FieldType,
    ValidatorsSpec, VisualValidatorSpec,
)

invoice = DocSpec(
    doc_type=DocType(
        document_type="invoice",
        description="Vendor invoice (paper or PDF)",
        country="ES",
    ),
    field_groups=[ ... ],
    validators=ValidatorsSpec(visual=[
        VisualValidatorSpec(name="signature_present",
                            description="A handwritten or e-signature is visible"),
    ]),
)
```

### `DocType`

| Field             | Type    | Default | Required | Notes                                                              |
|-------------------|---------|---------|----------|--------------------------------------------------------------------|
| `document_type`   | `str`   | —       | **yes**, non-empty | Stable id. Used by `RuleParent.document_type`, by `DocumentInput.document_type`, and surfaced verbatim on the response under `ExtractedDocument.document_type`. Snake_case lower-kebab works well: `invoice`, `purchase_order`, `id_card_es`, `passport_int`. |
| `description`     | `str`   | `""`    | no       | Hints the classifier when multi-doc requests need disambiguation.  |
| `country`         | `str`   | `""`    | no       | ISO 3166-1 alpha-2. Hint for region-aware validators / formats.    |

### `FieldGroup`

A named bundle of `FieldSpec`s that the service should extract together. Groups are how you partition the schema visually and logically — `header`, `totals`, `line_items_block`, …

| Field                | Type                 | Default | Required | Notes                                                   |
|----------------------|----------------------|---------|----------|---------------------------------------------------------|
| `field_group_name`   | `str`                | —       | **yes**, non-empty | JSON alias `fieldGroupName`. Use snake_case.            |
| `field_group_desc`   | `str`                | `""`    | no       | Free-form description shown to the LLM.                  |
| `field_group_fields` | `list[FieldSpec]`    | —       | **yes**, min length 1 | JSON alias `fieldGroupFields`.                    |

```python
totals = FieldGroup.of(
    "totals",                                                  # name
    FieldSpec(field_name="subtotal",     field_type=FieldType.NUMBER, required=True),
    FieldSpec(field_name="tax_amount",   field_type=FieldType.NUMBER, required=True),
    FieldSpec(field_name="total_amount", field_type=FieldType.NUMBER, required=True),
    FieldSpec(field_name="currency",     field_type=FieldType.STRING, required=True),
    description="Top-of-invoice money block",
)
```

`FieldGroup.of(name, *fields, description="")` is the recommended factory — it folds the variadic fields into the list. Use the explicit constructor when you need to programmatically build the list.

### `ValidatorsSpec` + `VisualValidatorSpec`

Per-`DocSpec` validator definitions. Currently only `visual` is exposed publicly; future additions (`audio`, `structural`) plug in here.

| Field                    | Type                          | Notes                                                |
|--------------------------|-------------------------------|------------------------------------------------------|
| `ValidatorsSpec.visual`  | `list[VisualValidatorSpec]`   | One entry per visual check the LLM should run.       |
| `VisualValidatorSpec.name`        | `str`                | Short identifier the response carries back.           |
| `VisualValidatorSpec.description` | `str`                | What the LLM should look for (`"a handwritten or e-signature is visible"`). |

`visual_authenticity` must be enabled on `StageToggles` for these to fire.

---

## 5. `FieldSpec` & `FieldItem` — field-level shape and constraints

### `FieldSpec`

The unit of "one thing the caller wants extracted".

| Field                  | Type                          | Default     | Notes                                                              |
|------------------------|-------------------------------|-------------|--------------------------------------------------------------------|
| `field_name`           | `str` (alias `name`)          | —, **required** | The key under which the extracted value appears in the response. Snake_case lower-case is conventional. |
| `field_description`    | `str` (alias `description`)   | `""`        | Free-form hint for the LLM. The more specific, the better the recall on lookalikes. |
| `field_type`           | `FieldType` (alias `type`)    | `STRING`    | See enum below.                                                    |
| `required`             | `bool`                        | `False`     | When `True`, a missing field surfaces as a `field_validation` error. |
| `pattern`              | `str \| None`                 | `None`      | RFC-flavour regex applied by the field validator.                  |
| `format`               | `StandardFormat \| None`      | `None`      | One of `DATE` / `DATE_TIME` / `EMAIL` / `URI` / `UUID`.            |
| `enum`                 | `list \| None`                | `None`      | Closed set of acceptable values.                                   |
| `minimum`              | `float \| None`               | `None`      | Numeric lower bound (inclusive).                                   |
| `maximum`              | `float \| None`               | `None`      | Numeric upper bound (inclusive).                                   |
| `items`                | `list[FieldItem] \| None`     | `None`      | **Only valid when `field_type == FieldType.ARRAY`**; describes the columns of each repeating row. |
| `standard_validators`  | `list[StandardValidatorSpec]` | `[]`        | See §6.                                                            |

### `FieldType` — the five primitives

| Member                   | Wire form  | Use for                                                          |
|--------------------------|------------|------------------------------------------------------------------|
| `FieldType.STRING`       | `"string"` | Any free-form text, identifier, format-validated string.         |
| `FieldType.NUMBER`       | `"number"` | Floats / decimals. Pair with `minimum` / `maximum` / `format=AMOUNT`. |
| `FieldType.INTEGER`      | `"integer"`| Integral quantities (counts, page numbers, quantities).          |
| `FieldType.BOOLEAN`      | `"boolean"`| Yes/no / present/absent / signed/unsigned.                       |
| `FieldType.ARRAY`        | `"array"`  | Repeating rows. **Requires** a non-empty `items` list.            |

### `StandardFormat` — JSON-Schema-style format hints

| Member                   | Wire form    | Validation                  |
|--------------------------|--------------|-----------------------------|
| `StandardFormat.DATE`        | `"date"`        | `YYYY-MM-DD`                |
| `StandardFormat.DATE_TIME`   | `"date-time"`   | RFC 3339 / ISO 8601 with time |
| `StandardFormat.EMAIL`       | `"email"`       | RFC 5322                    |
| `StandardFormat.URI`         | `"uri"`         | Generic URI                 |
| `StandardFormat.UUID`        | `"uuid"`        | RFC 4122                    |

> **`format` vs `standard_validators`.** `format` is a single-shot JSON-Schema-style check baked into `FieldSpec`; `standard_validators` is the extensible catalogue (IBAN, NIE, VAT_ID, …). For format checks that have an equivalent validator, prefer `format` (cheaper, doesn't show up as a validator hit). For domain checks, use validators.

### `FieldItem` — sub-fields inside an array

`field_type == FieldType.ARRAY` makes a field a repeating row; `items` declares the columns:

| Field                | Type                          | Notes                                                              |
|----------------------|-------------------------------|--------------------------------------------------------------------|
| `field_name`         | `str` (alias `fieldName`)     | Column name (camelCase on the wire).                               |
| `field_description`  | `str` (alias `fieldDescription`) | Free-form hint per column.                                       |
| `field_type`         | `FieldType` (alias `fieldType`) | One of the primitives. `FieldItem` does NOT support nested arrays — flatten or split into two field groups. |
| `pattern`, `format`, `enum`, `minimum`, `maximum`, `standard_validators` | (same as `FieldSpec`) | All the field-level constraints apply per row. |

```python
line_items = FieldSpec(
    field_name="line_items",
    field_type=FieldType.ARRAY,
    field_description="One row per line item on the invoice",
    items=[
        FieldItem(field_name="description", field_type=FieldType.STRING),
        FieldItem(field_name="quantity",    field_type=FieldType.NUMBER, minimum=0),
        FieldItem(field_name="unit_price",  field_type=FieldType.NUMBER, minimum=0),
        FieldItem(field_name="line_total",  field_type=FieldType.NUMBER, minimum=0),
    ],
)
```

### Variant cheat sheet

| Goal                                | Recipe                                                                          |
|-------------------------------------|---------------------------------------------------------------------------------|
| Required scalar                     | `FieldSpec(field_name="x", field_type=FieldType.STRING, required=True)`         |
| Optional with range                 | `FieldSpec(field_name="age", field_type=FieldType.INTEGER, minimum=0, maximum=120)` |
| Closed enum                         | `FieldSpec(field_name="status", field_type=FieldType.STRING, enum=["active", "inactive"])` |
| Date                                | `FieldSpec(field_name="dob", field_type=FieldType.STRING, format=StandardFormat.DATE)` |
| Regex                               | `FieldSpec(field_name="ref", pattern=r"^[A-Z]{2}-\d{6}$")`                       |
| IBAN                                | `FieldSpec(field_name="iban", standard_validators=[StandardValidatorSpec(type=StandardValidatorType.IBAN)])` |
| Repeating rows                      | `FieldSpec(field_name="rows", field_type=FieldType.ARRAY, items=[FieldItem(...)])` |
| Soft-warning validator              | `StandardValidatorSpec(type=..., severity="warning")` — recorded, but `valid` stays `True`. |

---

## 6. `StandardValidatorSpec` — built-in validators (full catalogue)

Attach validators to a `FieldSpec` (or a `FieldItem` for array columns). The field validator stage runs them after extraction and folds the result into `ExtractedField.field_validation`.

```python
from flydocs_sdk import StandardValidatorSpec, StandardValidatorType

iban_field = FieldSpec(
    field_name="iban",
    field_type=FieldType.STRING,
    required=True,
    standard_validators=[StandardValidatorSpec(type=StandardValidatorType.IBAN)],
)

vat_es = FieldSpec(
    field_name="vat_id",
    standard_validators=[
        StandardValidatorSpec(type=StandardValidatorType.VAT_ID,
                              params={"country": "ES"}),
    ],
)

soft_warning = StandardValidatorSpec(
    type=StandardValidatorType.PHONE_E164,
    params={"country": "ES"},
    severity="warning",   # records the error but doesn't set valid=False
)
```

### Fields

| Field      | Type                                          | Default     | Notes                                              |
|------------|-----------------------------------------------|-------------|----------------------------------------------------|
| `type`     | `StandardValidatorType`                       | —, required | Use the enum; raw strings work too for forward compat. |
| `params`   | `dict[str, Any]`                              | `{}`        | Per-validator parameters (e.g. `{"country": "ES"}`). |
| `severity` | `Literal["error", "warning"]`                 | `"error"`   | `"warning"` records the issue but keeps `valid=True`. |

### Complete catalogue

| Category   | Member                                       | Wire form        | `params`                          |
|------------|----------------------------------------------|------------------|-----------------------------------|
| **Network / web**  | `EMAIL`                              | `email`          | none                              |
|                    | `URI`                                | `uri`            | none                              |
|                    | `URL`                                | `url`            | none                              |
|                    | `IPV4`                               | `ipv4`           | none                              |
|                    | `IPV6`                               | `ipv6`           | none                              |
|                    | `DOMAIN`                             | `domain`         | none                              |
|                    | `SLUG`                               | `slug`           | none                              |
| **Temporal**       | `DATE`                               | `date`           | none                              |
|                    | `DATETIME`                           | `datetime`       | none                              |
|                    | `TIME`                               | `time`           | none                              |
|                    | `ISO_8601`                           | `iso_8601`       | none                              |
| **Identifiers**    | `UUID`                               | `uuid`           | none                              |
|                    | `JSON`                               | `json`           | none                              |
|                    | `HEX_COLOR`                          | `hex_color`      | none                              |
| **Finance**        | `IBAN`                               | `iban`           | none (country derived from prefix) |
|                    | `BIC`                                | `bic`            | none                              |
|                    | `CREDIT_CARD`                        | `credit_card`    | none (Luhn-checked)               |
|                    | `CURRENCY_CODE`                      | `currency_code`  | none (ISO 4217)                   |
|                    | `AMOUNT`                             | `amount`         | none (numeric > 0)                |
| **Telephony**      | `PHONE_E164`                         | `phone_e164`     | `{"country": "ES"}` (optional)    |
| **Geographic**     | `COUNTRY_CODE`                       | `country_code`   | none (ISO 3166-1 alpha-2)         |
|                    | `LANGUAGE_CODE`                      | `language_code`  | none (ISO 639-1)                  |
|                    | `POSTAL_CODE`                        | `postal_code`    | `{"country": "ES"}` (optional)    |
|                    | `LATITUDE`                           | `latitude`       | none                              |
|                    | `LONGITUDE`                          | `longitude`      | none                              |
| **National IDs**   | `NIF`                                | `nif`            | `{"country": "ES"}` implied       |
|                    | `NIE`                                | `nie`            | ES — foreign person tax id        |
|                    | `CIF`                                | `cif`            | ES — legacy company tax id        |
|                    | `VAT_ID`                             | `vat_id`         | `{"country": "ES"}` (EU VAT)      |
|                    | `SSN`                                | `ssn`            | US                                |
|                    | `PASSPORT_NUMBER`                    | `passport_number`| ICAO 9303 (length / charset only) |

> **Soft vs hard.** Use `severity="warning"` for "extra signal" checks where you want the issue logged but still want the row to be `valid=True` (e.g. a non-canonical date format). Use the default `"error"` for "this is a contract violation" checks (e.g. malformed IBAN).

---

## 7. `ExtractionOptions` & `StageToggles` — pipeline configuration

```python
from flydocs_sdk import ExtractionOptions, StageToggles

options = ExtractionOptions(
    return_bboxes=True,
    language_hint="es",
    model="anthropic:claude-sonnet-4-6",
    declared_media_type=None,
    stages=StageToggles(
        classifier=True,
        field_validation=True,
        judge=True,
        bbox_refine=True,
        rule_engine=True,
    ),
    escalation_threshold=0.25,
    escalation_model="anthropic:claude-opus-4-7",
    transformations=[],
)
```

### `ExtractionOptions`

| Field                    | Type                       | Default                | Notes                                                              |
|--------------------------|----------------------------|------------------------|--------------------------------------------------------------------|
| `return_bboxes`          | `bool`                     | `True`                 | When `False`, the response strips bounding boxes (cheaper to ship). |
| `language_hint`          | `str \| None`              | `None`                 | ISO 639-1 (`"en"`, `"es"`, `"zh"`, …) — guides multilingual OCR / extraction. ≤ 16 chars. |
| `model`                  | `str \| None`              | `None` (uses env default) | Per-request primary model id (`"anthropic:claude-sonnet-4-6"`, `"openai:gpt-4o"`, …). |
| `declared_media_type`    | `str \| None`              | `None`                 | Override sniffing; rare. Useful when callers know better than `magic`. |
| `stages`                 | `StageToggles`             | `StageToggles()`       | See below.                                                         |
| `escalation_threshold`   | `float \| None`            | `None` (env default)   | `0.0–1.0`. When `stages.judge_escalation=True`, re-runs the request with `escalation_model` once the judge's fail-rate crosses this. |
| `escalation_model`       | `str \| None`              | `None` (env default)   | Model id used by the escalation re-run.                            |
| `transformations`        | `list[dict]`               | `[]`                   | Post-extraction transformations. See §9.                            |

### `StageToggles` — all ten stages

| Stage                  | Default | What it does                                                                                                  |
|------------------------|---------|---------------------------------------------------------------------------------------------------------------|
| `splitter`             | `False` | LLM document splitter. Required when one upload mixes several document types and you need page ranges per type. |
| `classifier`           | **`True`** | LLM classifier that maps each input file to one of the declared `DocSpec.doc_type.documentType` values. No-op when every file already carries `document_type`. |
| `field_validation`     | **`True`** | Pure-Python validation pass — runs `pattern`, `format`, `enum`, `min`/`max`, every `StandardValidatorSpec`.   |
| `visual_authenticity`  | `False` | LLM visual check using the `ValidatorsSpec.visual` declarations (signature, watermark, …).                    |
| `content_authenticity` | `False` | LLM cross-document content checks (consistency across pages / files).                                          |
| `judge`                | `False` | Per-field LLM re-evaluation. Annotates every extracted field with `confidence`, `evidence`, `flag_for_review`. |
| `judge_escalation`     | `False` | When the judge's fail-rate exceeds `escalation_threshold`, re-runs extract + judge with `escalation_model`; the lower-fail-rate run wins. Requires `judge`. |
| `bbox_refine`          | `False` | Replaces LLM-estimated bboxes with grounded coordinates from the document's real text layer (PyMuPDF for born-digital PDFs, OCR for rasters). Multilingual-aware. |
| `rule_engine`          | `False` | Evaluates the business-rule DAG against extracted fields + validator outcomes. See §8.                         |
| `transform`            | `False` | Runs the `transformations` list. See §9.                                                                       |

> **Cost & latency.** `extract` is mandatory. `classifier` and `field_validation` are cheap (cheap LLM call + pure Python). `judge` doubles your LLM spend per field. `judge_escalation` adds a third pass when triggered. `bbox_refine` adds ~50–200 ms per 30-page PDF (text-layer) or seconds-per-page for image-only PDFs (OCR). `visual_authenticity`, `content_authenticity` each add one LLM call.

---

## 8. `RuleSpec` — business rules over extracted fields

Rules are **natural-language predicates** the LLM evaluates against extracted fields, validator outcomes, or other rules' outputs. They form a DAG; the engine sorts topologically and runs in dependency order. Cycles are rejected at request-validation time.

```python
from flydocs_sdk import (
    RuleFieldParent, RuleOutputSpec, RuleRuleParent, RuleSpec, RuleValidatorParent,
)

totals_consistent = RuleSpec(
    id="totals_consistent",
    predicate="subtotal + tax_amount equals total_amount within 0.01",
    parents=[RuleFieldParent(
        document_type="invoice",
        field_names=["subtotal", "tax_amount", "total_amount"],
    )],
)

vat_id_valid = RuleSpec(
    id="vat_id_valid",
    predicate="The supplier_vat field passes the VAT_ID validator",
    parents=[RuleValidatorParent(document_type="invoice", validator_name="vat_id")],
)

acceptable = RuleSpec(
    id="invoice_acceptable",
    predicate="totals_consistent AND vat_id_valid",
    parents=[
        RuleRuleParent(rule_id="totals_consistent"),
        RuleRuleParent(rule_id="vat_id_valid"),
    ],
    output=RuleOutputSpec(type="boolean"),
)
```

### `RuleSpec`

| Field        | Type                                       | Default               | Notes                                                              |
|--------------|--------------------------------------------|-----------------------|--------------------------------------------------------------------|
| `id`         | `str`                                      | —, required           | Unique within the request. Referenced by `RuleRuleParent.rule_id`. |
| `predicate`  | `str`                                      | —, required           | Natural-language statement evaluated by the LLM.                   |
| `parents`    | `list[RuleParent]`                         | `[]`                  | Discriminated union — see below.                                   |
| `output`     | `RuleOutputSpec`                           | `RuleOutputSpec()` (`type="boolean"`) | Shape the response should carry.                  |

### `RuleParent` — three variants

| Variant                  | Discriminator   | Fields                                              | Use for                                          |
|--------------------------|-----------------|-----------------------------------------------------|--------------------------------------------------|
| `RuleFieldParent`        | `"field"`       | `document_type` (str), `field_names` (list[str], min 1) | "This rule operates on these fields of this document type." |
| `RuleValidatorParent`    | `"validator"`   | `document_type` (str), `validator_name` (str)        | "This rule operates on the outcome of this validator." |
| `RuleRuleParent`         | `"rule"`        | `rule_id` (str)                                      | "This rule depends on another rule's output."     |

### `RuleOutputSpec`

| Field             | Type                       | Default       | Notes                                                                                |
|-------------------|----------------------------|---------------|--------------------------------------------------------------------------------------|
| `type`            | `str`                      | `"boolean"`   | Other supported types: `"string"`, `"number"`. The rule engine coerces accordingly.   |
| `valid_outputs`   | `list[str] \| None`        | `None`        | Closed set of acceptable string outputs. Anything else is treated as `flag_for_review`. |

### Response shape

The response carries `result.rule_results: list[RuleResult]` with one entry per rule:

```python
for rr in result.rule_results:
    print(rr["rule_id"], rr["output"], rr.get("summary"), rr.get("human_revision"))
```

`output` is the resolved value (string form: `"true"` / `"false"` / your custom strings). `human_revision` carries instructions for a human reviewer when the rule's output didn't fit `valid_outputs`.

---

## 9. Transformations — post-extraction reshaping

Two transformation types ship in-tree. Both are passed through `ExtractionOptions.transformations`; the `transform` stage must be enabled in `StageToggles`.

### `entity_resolution` — declarative, fast, free

Deduplicates rows of an array field group using accent-fold + token-subset matching. Typical use: collapse `"Andrés Contreras"` and `"Andres Contreras Guillen"` into a single row across N documents.

```python
from flydocs_sdk import (
    ExtractionOptions, StageToggles, TransformationScope,
    entity_resolution,
)

opts = ExtractionOptions(
    stages=StageToggles(transform=True),
    transformations=[
        entity_resolution(
            target_group="personas",        # array field group to dedupe
            match_by=["dni", "nombre"],     # priority: DNI first, then name
            min_shared_tokens=2,            # default; lower = more aggressive merging
            scope=TransformationScope.REQUEST,  # dedupe ACROSS documents
            # output_group="personas_canonical",  # keep both views (omit to replace)
        ),
    ],
)
```

### `llm_transformation` — free-form

A focused LLM call against a target group, driven by a one-sentence `intention`:

```python
from flydocs_sdk import llm_transformation

llm_transformation(
    target_group="cargos",
    intention=(
        "Normaliza cada cargo a una taxonomía cerrada: "
        "{administrador_unico, consejero, apoderado, otros}."
    ),
    scope=TransformationScope.TASK,
)
```

### Common fields (both variants)

| Field            | Type                       | Default                  | Notes                                                              |
|------------------|----------------------------|--------------------------|--------------------------------------------------------------------|
| `target_group`   | `str`                      | —, required              | Must match a `FieldGroup.field_group_name` the extractor produces. |
| `output_group`   | `str \| None`              | `None`                   | When set, the transformation output is appended as a NEW group; the original stays. When `None`, replaces in place. |
| `scope`          | `TransformationScope`      | `TASK`                   | `TASK`: one pass per document. `REQUEST`: concatenates across documents, runs once, emits under `result.request_transformations`. |
| `id`             | `str`                      | random UUIDv4            | Used in logs and the trace.                                        |

### `entity_resolution`-only

| Field               | Type        | Default | Notes                                                       |
|---------------------|-------------|---------|-------------------------------------------------------------|
| `match_by`          | `list[str]` | required, min length 1 | Priority-ordered field names. First non-empty wins as the matching key. |
| `min_shared_tokens` | `int`       | `2`     | Minimum shared name tokens for a name-variant match.        |

### `llm_transformation`-only

| Field        | Type           | Default | Notes                                                |
|--------------|----------------|---------|------------------------------------------------------|
| `intention`  | `str`          | required, min length 10 | One-sentence goal in any language.  |
| `prompt_id`  | `str \| None`  | `None`  | Named template id from the server-side catalog. Omit to use the default transform prompt with `intention` interpolated. |

---

## 10. Async jobs — `SubmitJobRequest`, callbacks, idempotency

For long documents, batches, or fire-and-forget workloads, use `submit_job` and either poll with `wait_for_completion` or receive a webhook.

```python
from flydocs_sdk import (
    AsyncFlydocsClient, DocumentInput, JobStatus, SubmitJobRequest,
)

async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
    submit = await flydocs.submit_job(
        SubmitJobRequest(
            documents=[DocumentInput.from_path("big-batch.pdf")],
            docs=[invoice],
            callback_url="https://your-app.example.com/flydocs/webhook",
            metadata={"caller": "ingest-pipeline", "batch_id": "b-42"},
        ),
        idempotency_key="ingest-pipeline:b-42",   # safe to retry
        correlation_id="req-12345",
    )
    print(f"queued {submit.job_id} ({submit.status})")

    final = await flydocs.wait_for_completion(
        submit.job_id,
        poll_interval=2.0,
        timeout=900.0,
    )
    if final.status == JobStatus.SUCCEEDED:
        result = (await flydocs.get_job_result(submit.job_id)).result
        ...
```

### `SubmitJobRequest`

A superset of `ExtractionRequest`:

| Field            | Type                            | Default                  | Notes                                                              |
|------------------|---------------------------------|--------------------------|--------------------------------------------------------------------|
| (all fields from `ExtractionRequest` minus `request_id`) | — | —                | The job's `job_id` plays the role of `request_id`.                 |
| `callback_url`   | `str \| None`                   | `None`                   | When set, the service POSTs a `JobWebhookPayload` here on terminal status (see §11). |
| `metadata`       | `dict[str, Any]`                | `{}`                     | Echoed back on the webhook payload — use for caller-side correlation. |

### Headers per call

| Header            | SDK kwarg          | Notes                                                              |
|-------------------|--------------------|--------------------------------------------------------------------|
| `Idempotency-Key` | `idempotency_key=` | Send the same key to replay an existing submission instead of creating a duplicate. The service indexes by key. |
| `X-Correlation-Id`| `correlation_id=`  | Stamped on every internal log line and on the webhook payload (`correlation_id` field). |

### Polling helper

```python
final = await flydocs.wait_for_completion(
    submit.job_id,
    poll_interval=2.0,   # seconds between GET /api/v1/jobs/{id}
    timeout=900.0,       # raises TimeoutError after this many seconds
)
```

Terminal statuses are `SUCCEEDED`, `PARTIAL_SUCCEEDED`, `FAILED`, `CANCELLED`. `wait_for_completion` returns the final `JobStatusResponse` in all four cases — it only raises `TimeoutError` when the deadline elapses while the worker is still in flight.

### Listing / cancelling

```python
listing = await flydocs.list_jobs(
    status=["SUCCEEDED", "PARTIAL_SUCCEEDED"],   # CSV filter
    bbox_refine_status=["pending", "running"],   # CSV filter
    idempotency_key="ingest-pipeline:b-42",      # exact match
    created_after=datetime(2026, 5, 1),
    created_before=datetime(2026, 5, 31, 23, 59),
    limit=25,
    offset=0,
)
for job in listing.items:
    print(job.job_id, job.status, job.submitted_at)

await flydocs.cancel_job("job-abc")              # only valid while QUEUED
```

---

## 11. Webhooks — receiving and verifying delivery

When `callback_url` is set, the service POSTs a `JobWebhookPayload` on terminal status. It signs the body with HMAC-SHA256 in `X-Flydocs-Signature` when `FLYDOCS_WEBHOOK_HMAC_SECRET` is configured on the service.

### Payload shape — `JobWebhookPayload`

| Field             | Type                       | Notes                                                                          |
|-------------------|----------------------------|--------------------------------------------------------------------------------|
| `event_id`        | `str`                      | Unique per delivery. Dedupe on this — the publisher retries on transient errors. |
| `event_type`      | `str`                      | `"IDPJobCompleted"`.                                                            |
| `version`         | `str`                      | Semver of the payload schema (`"1.0.0"`).                                       |
| `job_id`          | `str`                      | The submitted job.                                                              |
| `status`          | `JobStatus`                | Terminal: `SUCCEEDED` / `PARTIAL_SUCCEEDED` / `FAILED` / `CANCELLED`.            |
| `occurred_at` / `started_at` / `finished_at` | `datetime`  | Lifecycle timestamps.                                          |
| `attempts`        | `int`                      | Worker attempts consumed.                                                       |
| `correlation_id`  | `str \| None`              | The `X-Correlation-Id` you passed at submit time, if any.                       |
| `tenant_id`       | `str \| None`              | When the service runs multi-tenant.                                              |
| `metadata`        | `dict[str, Any]`           | The dict you passed in `SubmitJobRequest.metadata`.                              |
| `result`          | `ExtractionResult \| None` | Present on `SUCCEEDED` / `PARTIAL_SUCCEEDED`; `None` on `FAILED` / `CANCELLED`.  |
| `error_code` / `error_message` | `str \| None`  | Populated when the job failed.                                                  |

### Verifying — FastAPI example

```python
import os
from flydocs_sdk import (
    JobStatus, JobWebhookPayload,
    WebhookVerificationError, WebhookVerifier,
)
from fastapi import FastAPI, Header, HTTPException, Request

verifier = WebhookVerifier(secret=os.environ["FLYDOCS_WEBHOOK_HMAC_SECRET"])
app = FastAPI()

@app.post("/flydocs/webhook")
async def on_webhook(
    request: Request,
    x_flydocs_signature: str = Header(...),
) -> dict:
    body = await request.body()                                   # raw bytes
    try:
        verifier.verify(body, x_flydocs_signature)
    except WebhookVerificationError:
        raise HTTPException(status_code=403, detail="bad signature")
    payload = JobWebhookPayload.model_validate_json(body)
    if payload.status == JobStatus.SUCCEEDED and payload.result is not None:
        ...   # persist, fan out downstream work
    return {"ok": True}
```

> **Verify against the raw bytes.** If your framework deserialised the JSON before you got the bytes, re-encoding will change the digest. `verifier.sign(body)` exists for tests so you can pin parity with the service.

---

## 12. Errors — RFC 7807 problem-details

Every non-2xx response decodes into a typed `FlydocsHTTPError` with `status_code`, `code`, `title`, `detail`, and the raw `payload` dict:

| `code`                  | Status | Meaning                                                                                                   |
|-------------------------|--------|-----------------------------------------------------------------------------------------------------------|
| `extraction_timeout`    | 408    | Pipeline exceeded the sync ceiling (`FLYDOCS_SYNC_TIMEOUT_S`). Retry via `submit_job`.                    |
| `document_too_large`    | 413    | Document over `FLYDOCS_MAX_BYTES`.                                                                         |
| `invalid_base64`        | 422    | `content_base64` failed strict parsing.                                                                    |
| `invalid_request`       | 422    | Semantic validation found issues (rule references unknown field, duplicate ids, cycles, …). `payload` carries every issue. |
| `job_not_ready`         | 409    | `GET /jobs/{id}/result` called before the worker finished.                                                  |
| `job_not_cancellable`   | 409    | Worker already started; mid-flight cancellation isn't supported.                                            |
| `JOB_NOT_FOUND`         | 404    | Unknown `job_id`.                                                                                          |

```python
from flydocs_sdk import (
    FlydocsClientError, FlydocsHTTPError, FlydocsTimeoutError,
)

try:
    result = await flydocs.extract(req)
except FlydocsHTTPError as exc:
    if exc.code == "extraction_timeout":
        submit = await flydocs.submit_job(SubmitJobRequest(**req.model_dump()))
    elif exc.code == "invalid_request":
        for issue in exc.payload.get("errors", []):
            print(issue)
        raise
    else:
        raise
except FlydocsTimeoutError:
    raise   # SDK's own HTTP timeout (no service response)
except FlydocsClientError:
    raise   # transport failure (DNS, connect, TLS, …)
```

---

## 13. Production patterns

**Reuse a client.** Construct `AsyncFlydocsClient` once per application and share it. The underlying httpx connection pool is the most expensive part to set up.

**Correlation ids.** Pass `correlation_id="..."` on `extract` / `submit_job`. The service stamps it on every internal log line and on the webhook payload.

**Custom timeouts.** Default is 60 s. `AsyncFlydocsClient("http://...", timeout=120.0)`.

**Default headers.** `AsyncFlydocsClient(..., default_headers={"X-Tenant-Id": "tenant-42"})` adds the header to every outbound request.

**Bring your own httpx client.** `AsyncFlydocsClient(..., http_client=existing)` shares your app's connection pool. The SDK never closes transports it didn't create.

**Health checks.** `await flydocs.health("readiness")` returns the actuator JSON — wire it into your deploy verification.

**Cost tracking.** When the service has cost tracking enabled, `result.usage` carries per-agent and per-model token + USD breakdowns; webhook payloads carry the same.

---

## 14. The kitchen sink — full request with every feature

A realistic invoice extraction touching every feature: typed schema with array rows + validators, every applicable stage on, business rules, an entity-resolution transformation, idempotency, correlation id.

```python
import asyncio
from flydocs_sdk import (
    AsyncFlydocsClient,
    DocSpec, DocType, DocumentInput,
    ExtractionOptions, ExtractionRequest,
    FieldGroup, FieldItem, FieldSpec, FieldType,
    JobStatus,
    RuleFieldParent, RuleRuleParent, RuleSpec, RuleValidatorParent,
    StageToggles, StandardFormat,
    StandardValidatorSpec, StandardValidatorType,
    SubmitJobRequest,
    TransformationScope, entity_resolution,
)


invoice = DocSpec(
    doc_type=DocType(document_type="invoice", description="Vendor invoice", country="ES"),
    field_groups=[
        FieldGroup.of("header",
            FieldSpec(field_name="invoice_number", field_type=FieldType.STRING, required=True),
            FieldSpec(field_name="invoice_date",   field_type=FieldType.STRING,
                      format=StandardFormat.DATE, required=True),
            FieldSpec(field_name="supplier_name",  field_type=FieldType.STRING, required=True),
            FieldSpec(
                field_name="supplier_vat",
                field_type=FieldType.STRING,
                required=True,
                standard_validators=[
                    StandardValidatorSpec(
                        type=StandardValidatorType.VAT_ID, params={"country": "ES"},
                    ),
                ],
            ),
            FieldSpec(field_name="supplier_iban", field_type=FieldType.STRING,
                      standard_validators=[
                          StandardValidatorSpec(type=StandardValidatorType.IBAN),
                      ]),
        ),
        FieldGroup.of("totals",
            FieldSpec(field_name="subtotal",     field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="tax_amount",   field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="total_amount", field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="currency",     field_type=FieldType.STRING, required=True,
                      standard_validators=[
                          StandardValidatorSpec(type=StandardValidatorType.CURRENCY_CODE),
                      ]),
        ),
        FieldGroup.of("line_items_block",
            FieldSpec(
                field_name="line_items",
                field_type=FieldType.ARRAY,
                items=[
                    FieldItem(field_name="description", field_type=FieldType.STRING),
                    FieldItem(field_name="quantity",    field_type=FieldType.NUMBER, minimum=0),
                    FieldItem(field_name="unit_price",  field_type=FieldType.NUMBER, minimum=0),
                    FieldItem(field_name="line_total",  field_type=FieldType.NUMBER, minimum=0),
                ],
            ),
        ),
    ],
)

rules = [
    RuleSpec(
        id="totals_consistent",
        predicate="subtotal + tax_amount equals total_amount within 0.01",
        parents=[RuleFieldParent(document_type="invoice",
                                 field_names=["subtotal", "tax_amount", "total_amount"])],
    ),
    RuleSpec(
        id="vat_id_valid",
        predicate="The supplier_vat field passes the VAT_ID validator",
        parents=[RuleValidatorParent(document_type="invoice", validator_name="vat_id")],
    ),
    RuleSpec(
        id="invoice_acceptable",
        predicate="totals_consistent AND vat_id_valid",
        parents=[
            RuleRuleParent(rule_id="totals_consistent"),
            RuleRuleParent(rule_id="vat_id_valid"),
        ],
    ),
]


async def main(invoice_path: str) -> None:
    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        submit = await flydocs.submit_job(
            SubmitJobRequest(
                documents=[DocumentInput.from_path(invoice_path)],
                docs=[invoice],
                rules=rules,
                options=ExtractionOptions(
                    language_hint="es",
                    model="anthropic:claude-sonnet-4-6",
                    stages=StageToggles(
                        classifier=True,
                        field_validation=True,
                        judge=True,
                        judge_escalation=True,
                        bbox_refine=True,
                        rule_engine=True,
                        transform=True,
                    ),
                    escalation_threshold=0.25,
                    escalation_model="anthropic:claude-opus-4-7",
                    transformations=[
                        entity_resolution(
                            target_group="line_items",
                            match_by=["description"],
                            scope=TransformationScope.TASK,
                            output_group="line_items_dedup",
                        ),
                    ],
                ),
                callback_url="https://your-app.example.com/flydocs/webhook",
                metadata={"caller": "ingest-v2", "batch_id": "b-42"},
            ),
            idempotency_key="ingest-v2:b-42",
            correlation_id="req-12345",
        )

        final = await flydocs.wait_for_completion(submit.job_id, poll_interval=2.0, timeout=900.0)
        if final.status != JobStatus.SUCCEEDED:
            raise SystemExit(f"job did not succeed: {final.status} {final.error_message}")

        result = (await flydocs.get_job_result(submit.job_id)).result
        for rr in result.rule_results:
            print(f"  rule {rr['rule_id']}: {rr['output']}")
        for line in result.documents[0]["fields"]:
            print(line["fieldGroupName"], "→", len(line["fieldGroupFields"]), "fields")


asyncio.run(main("invoice.pdf"))
```

---

## 15. Synchronous facade (when async isn't an option)

For scripts, batch tools, and callers that can't run an event loop, `FlydocsClient` wraps `AsyncFlydocsClient` on a dedicated background loop:

```python
from flydocs_sdk import FlydocsClient

with FlydocsClient("http://localhost:8400") as flydocs:
    result = flydocs.extract(req)
```

Method-for-method identical to `AsyncFlydocsClient`, just without `await`. Prefer the async client whenever you can — the sync wrapper costs you one extra event loop per instance.

---

## Further reading

- [`QUICKSTART.md`](./QUICKSTART.md) — 5-minute zero-to-first-extraction.
- [`examples/`](./examples/) — six runnable scripts mirroring each section above.
- [`docs/api-reference.md`](../../docs/api-reference.md) — full HTTP wire contract.
- [`docs/pipeline.md`](../../docs/pipeline.md) — stage DAG internals.
- [`docs/rule-engine.md`](../../docs/rule-engine.md) — rule engine semantics + DAG resolution.
- [`docs/standard-validators.md`](../../docs/standard-validators.md) — per-validator algorithm references.
- [`docs/transformations.md`](../../docs/transformations.md) — the `transform` stage internals.
