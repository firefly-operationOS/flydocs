# flydocs Python SDK — Tutorial (v1 contract)

A complete, payload-composition-focused reference for the flydocs Python SDK. Every typed model is documented: **what it carries**, **what variants exist**, **what values are accepted**, **what the defaults are**, and **what the wire shape looks like**. Targets SDK `26.6.0` and the v1 server contract.

> **Audience.** Engineers integrating flydocs into a Python codebase who want to know exactly which knobs exist and how to compose them. For a 5-minute zero-to-first-extraction, see [QUICKSTART.md](./QUICKSTART.md).
>
> **Prerequisites.** Python ≥ 3.11. A flydocs service reachable at some base URL — locally via `task docker:up:test` on the repo root.

---

## Table of contents

1. [The mental model](#1-the-mental-model)
2. [`ExtractionRequest` — the top-level envelope](#2-extractionrequest--the-top-level-envelope)
3. [`FileInput` — input files](#3-fileinput--input-files)
4. [`DocumentTypeSpec` — what to extract](#4-documenttypespec--what-to-extract)
5. [`Field` & `FieldGroup` — recursive field schema](#5-field--fieldgroup--recursive-field-schema)
6. [`ValidatorSpec` — built-in validators (full catalogue)](#6-validatorspec--built-in-validators-full-catalogue)
7. [`ExtractionOptions` & `StageToggles` — pipeline configuration](#7-extractionoptions--stagetoggles--pipeline-configuration)
8. [`RuleSpec` — business rules over extracted fields](#8-rulespec--business-rules-over-extracted-fields)
9. [Transformations — post-extraction reshaping](#9-transformations--post-extraction-reshaping)
10. [Async extractions — `SubmitExtractionRequest`, callbacks, idempotency](#10-async-extractions--submitextractionrequest-callbacks-idempotency)
11. [Webhooks — receiving and verifying delivery](#11-webhooks--receiving-and-verifying-delivery)
12. [Errors — RFC 7807 problem-details](#12-errors--rfc-7807-problem-details)
13. [Production patterns](#13-production-patterns)
14. [The kitchen sink — full request with every feature](#14-the-kitchen-sink--full-request-with-every-feature)
15. [Synchronous facade (when async isn't an option)](#15-synchronous-facade-when-async-isnt-an-option)

---

## 1. The mental model

A flydocs request carries three things:

```
  ┌────────────────── ExtractionRequest ──────────────────┐
  │                                                       │
  │   files:           [FileInput, ...]   ← the bytes     │
  │   document_types:  [DocumentTypeSpec] ← the schema    │
  │   rules:           [RuleSpec, ...]    ← the predicates│
  │   options:         ExtractionOptions  ← the knobs     │
  │                                                       │
  └───────────────────────────────────────────────────────┘
```

The service runs a configurable pipeline:

```
  files → splitter? → classifier? → extract (always) → field_validation? →
        → visual_authenticity? → content_authenticity? → judge? →
        → judge_escalation? → bbox_refine? → transform? → rule_engine? → assemble
```

`extract` is mandatory; every other stage is opt-in via `StageToggles`. The response (`ExtractionResult`) carries one entry per resolved `DocumentTypeSpec` under `documents`, plus per-stage trace and (when enabled) rule results, transformation outputs, judge verdicts, etc.

Two integration modes share the same request shape:

| Mode               | Method                                                   | When to use                                                 |
|--------------------|----------------------------------------------------------|-------------------------------------------------------------|
| **Sync extract**   | `await flydocs.extract(req)`                              | Single document, sub-minute. Caller waits on the HTTP call. |
| **Async**          | `await flydocs.extractions.create(req)` + `wait_for_completion` | Long-running, batches, webhook-delivered results. |

---

## 2. `ExtractionRequest` — the top-level envelope

| Field             | Type                                            | Default                  | Required | Notes                                                              |
|-------------------|-------------------------------------------------|--------------------------|----------|--------------------------------------------------------------------|
| `intention`       | `str`                                           | `"Extract structured data from the document."` | no | Free-form guidance for every LLM node (extract, judge, rules, …). |
| `files`           | `list[FileInput]`                               | —                        | **yes**, min length 1 | Input files. A single file is a one-element list.    |
| `document_types`  | `list[DocumentTypeSpec \| dict]`                 | —                        | **yes**, min length 1 | One entry per **expected document type**.            |
| `rules`           | `list[RuleSpec \| dict]`                         | `[]`                     | no       | Business-rule DAG. See §8.                                         |
| `options`         | `ExtractionOptions \| dict`                     | `ExtractionOptions()`    | no       | Per-request knobs. See §7.                                         |

Every field that takes a typed model also accepts a plain `dict` — useful for forward-compatibility with server-side fields the SDK hasn't surfaced yet.

```python
from flydocs_sdk import (
    DocumentTypeSpec, ExtractionRequest, Field, FieldGroup, FieldType, FileInput,
)

invoice = DocumentTypeSpec(
    id="invoice",
    field_groups=[
        FieldGroup(name="totals", fields=[
            Field(name="total", type=FieldType.NUMBER, required=True),
        ]),
    ],
)

req = ExtractionRequest(
    files=[FileInput.from_path("invoice.pdf")],
    document_types=[invoice],
)
```

---

## 3. `FileInput` — input files

One entry per input file. Each file is processed independently by the pipeline. Replaces v0 `DocumentInput`.

| Field             | Type            | Default | Required | Notes                                                           |
|-------------------|-----------------|---------|----------|-----------------------------------------------------------------|
| `filename`        | `str`           | —       | **yes**, non-empty | Surfaced on the response so you know which file produced what. |
| `content_base64`  | `str \| None`   | `None`  | yes for JSON | Base64 of the raw bytes. `data:<media-type>;base64,...` URLs accepted; SDK strips the prefix client-side. Omit when posting multipart. |
| `content_type`    | `str \| None`   | `None`  | no       | MIME hint. Omit to let the service sniff magic bytes.           |
| `expected_type`   | `str \| None`   | `None`  | no       | When set, pins this file to one of the declared `DocumentTypeSpec.id` values; the classifier is skipped for this file. (Replaces v0 `document_type`.) |

### Three ways to build one

```python
# 1. From bytes you already have in memory
f = FileInput.from_bytes(b"%PDF-1.4...", filename="invoice.pdf",
                          content_type="application/pdf")

# 2. From a path on disk
f = FileInput.from_path("invoice.pdf")
f = FileInput.from_path("invoice.pdf", expected_type="invoice")  # caller-pinned

# 3. Hand-build (e.g. when you already have the base64)
f = FileInput(
    filename="invoice.pdf",
    content_base64="JVBERi0xLjQK...",
    content_type="application/pdf",
)
```

### Multipart upload

For very large files (or when you'd rather not encode to base64 first), pass `files=[...]` to the client method:

```python
with open("big.pdf", "rb") as buf:
    result = client.extract(req, files=[buf])
```

The SDK posts a multipart body with the binaries riding as `files` parts and the JSON envelope (minus `files`) under a `request` part. The `FileInput` entries you put in the JSON still carry `filename` / `content_type` / `expected_type` for the matching parts.

### Sizing

The service enforces `FLYDOCS_MAX_BYTES` per file. Going over yields `FlydocsHttpError(413, code="file_too_large")`.

---

## 4. `DocumentTypeSpec` — what to extract

One `DocumentTypeSpec` per **expected document type**. When you submit multiple files, the classifier matches each file to one of the declared types unless the caller pins `FileInput.expected_type`.

Replaces v0 `DocSpec` and the nested `DocType` envelope — the v0 `docs[i].docType.documentType` is now `document_types[i].id`.

```python
from flydocs_sdk import (
    DocumentTypeSpec, Field, FieldGroup, FieldType, VisualCheck,
)

invoice = DocumentTypeSpec(
    id="invoice",
    description="Vendor invoice (paper or PDF)",
    country="ES",
    field_groups=[ ... ],
    visual_checks=[
        VisualCheck(name="signature_present",
                    description="A handwritten or e-signature is visible"),
    ],
)
```

### Fields

| Field            | Type                       | Default       | Required | Notes                                                              |
|------------------|----------------------------|---------------|----------|--------------------------------------------------------------------|
| `id`             | `str`                      | —             | **yes**, non-empty | Stable identifier. Referenced by `RuleFieldParent.document_type`, `FileInput.expected_type`, and surfaced on the response as `Document.type`. |
| `description`    | `str \| None`              | `None`        | no       | Hints the classifier when multi-doc requests need disambiguation. |
| `country`        | `str \| None`              | `None`        | no       | ISO 3166-1 alpha-2. Hint for region-aware validators / formats.   |
| `field_groups`   | `list[FieldGroup]`         | —             | **yes**, min 1 | One or more named groups of fields the extractor should produce. |
| `visual_checks`  | `list[VisualCheck]`        | `[]`          | no       | Visual checks the service should run when `visual_authenticity` is on. (Replaces v0 `ValidatorsSpec.visual`.) |

### `FieldGroup`

A named bundle of `Field`s that the service should extract together.

| Field        | Type             | Default | Required | Notes                                                   |
|--------------|------------------|---------|----------|---------------------------------------------------------|
| `name`       | `str`            | —       | **yes**, non-empty | Group identifier (snake_case). Surfaced as `ExtractedFieldGroup.name`. |
| `description`| `str \| None`    | `None`  | no       | Free-form description shown to the LLM.                  |
| `fields`     | `list[Field]`    | —       | **yes**, min 1 | The fields the group carries.                            |

```python
totals = FieldGroup(
    name="totals",
    fields=[
        Field(name="subtotal",     type=FieldType.NUMBER, required=True),
        Field(name="tax_amount",   type=FieldType.NUMBER, required=True),
        Field(name="total_amount", type=FieldType.NUMBER, required=True),
        Field(name="currency",     type=FieldType.STRING, required=True),
    ],
    description="Top-of-invoice money block",
)
```

### `VisualCheck`

| Field         | Type    | Notes                                                       |
|---------------|---------|-------------------------------------------------------------|
| `name`        | `str`   | Short identifier the response carries back.                 |
| `description` | `str`   | What the LLM should look for.                                |

`visual_authenticity` must be enabled on `StageToggles` for these to fire.

---

## 5. `Field` & `FieldGroup` — recursive field schema

v1 collapses v0's `FieldSpec` + `FieldItem` into a **single recursive `Field`** type. Primitives, arrays, and objects all use the same model.

### `Field`

| Field         | Type                          | Default     | Notes                                                              |
|---------------|-------------------------------|-------------|--------------------------------------------------------------------|
| `name`        | `str`                         | —, required | The key under which the extracted value appears in the response.   |
| `description` | `str \| None`                 | `None`      | Free-form hint for the LLM.                                        |
| `type`        | `FieldType`                   | `STRING`    | One of `STRING` / `NUMBER` / `INTEGER` / `BOOLEAN` / `ARRAY` / `OBJECT`. |
| `required`    | `bool`                        | `False`     | When `True`, a missing field surfaces as a `field_validation` error. |
| `pattern`     | `str \| None`                 | `None`      | RFC-flavour regex applied by the field validator.                  |
| `format`      | `StandardFormat \| None`      | `None`      | One of `DATE`, `DATE_TIME`, `TIME`, `EMAIL`, `URI`, `UUID`, `CURRENCY`. |
| `enum`        | `list \| None`                | `None`      | Closed set of acceptable values.                                   |
| `minimum`     | `float \| None`               | `None`      | Numeric lower bound (inclusive).                                   |
| `maximum`     | `float \| None`               | `None`      | Numeric upper bound (inclusive).                                   |
| `items`       | `Field \| None`               | `None`      | **Only valid when `type == ARRAY`**; describes a single row shape. |
| `fields`      | `list[Field] \| None`         | `None`      | **Only valid when `type == OBJECT`**; describes the object's members. |
| `validators`  | `list[ValidatorSpec]`         | `[]`        | See §6.                                                            |

### `FieldType`

| Member               | Wire form  | Use for                                                          |
|----------------------|------------|------------------------------------------------------------------|
| `FieldType.STRING`   | `"string"` | Any free-form text, identifier, format-validated string.         |
| `FieldType.NUMBER`   | `"number"` | Floats / decimals.                                               |
| `FieldType.INTEGER`  | `"integer"`| Integral quantities.                                              |
| `FieldType.BOOLEAN`  | `"boolean"`| Yes/no / present/absent.                                          |
| `FieldType.ARRAY`    | `"array"`  | Repeating rows. **Requires** an `items` Field describing the row. |
| `FieldType.OBJECT`   | `"object"` | Nested object. **Requires** a non-empty `fields` list.            |

### Arrays + objects

```python
line_items = Field(
    name="line_items",
    type=FieldType.ARRAY,
    items=Field(
        name="row",
        type=FieldType.OBJECT,
        fields=[
            Field(name="description", type=FieldType.STRING),
            Field(name="quantity",    type=FieldType.NUMBER, minimum=0),
            Field(name="unit_price",  type=FieldType.NUMBER, minimum=0),
            Field(name="line_total",  type=FieldType.NUMBER, minimum=0),
        ],
    ),
)
```

### `StandardFormat`

| Member                          | Wire form    | Validation                  |
|---------------------------------|--------------|-----------------------------|
| `StandardFormat.DATE`            | `"date"`     | `YYYY-MM-DD`                |
| `StandardFormat.DATE_TIME`       | `"date-time"`| RFC 3339 / ISO 8601 with time |
| `StandardFormat.TIME`            | `"time"`     | `HH:MM:SS`                  |
| `StandardFormat.EMAIL`           | `"email"`    | RFC 5322                    |
| `StandardFormat.URI`             | `"uri"`      | Generic URI                 |
| `StandardFormat.UUID`            | `"uuid"`     | RFC 4122                    |
| `StandardFormat.CURRENCY`        | `"currency"` | ISO 4217 currency code      |

### Variant cheat sheet

| Goal                                | Recipe                                                                          |
|-------------------------------------|---------------------------------------------------------------------------------|
| Required scalar                     | `Field(name="x", type=FieldType.STRING, required=True)`                          |
| Optional with range                 | `Field(name="age", type=FieldType.INTEGER, minimum=0, maximum=120)`              |
| Closed enum                         | `Field(name="status", type=FieldType.STRING, enum=["active", "inactive"])`       |
| Date                                | `Field(name="dob", type=FieldType.STRING, format=StandardFormat.DATE)`           |
| Regex                               | `Field(name="ref", pattern=r"^[A-Z]{2}-\d{6}$")`                                |
| IBAN                                | `Field(name="iban", validators=[ValidatorSpec(name=ValidatorType.IBAN)])`        |
| Repeating rows                      | `Field(name="rows", type=FieldType.ARRAY, items=Field(...))`                     |
| Object value                        | `Field(name="address", type=FieldType.OBJECT, fields=[Field(...)])`              |
| Soft-warning validator              | `ValidatorSpec(name=..., severity="warning")`                                    |

---

## 6. `ValidatorSpec` — built-in validators (full catalogue)

Attach validators to a `Field`. The field validator stage runs them after extraction and folds the result into `ExtractedField.validation`. Replaces v0 `StandardValidatorSpec`; dispatch key is `name` (not `type`).

```python
from flydocs_sdk import ValidatorSpec, ValidatorType

iban_field = Field(
    name="iban",
    type=FieldType.STRING,
    required=True,
    validators=[ValidatorSpec(name=ValidatorType.IBAN)],
)

vat_es = Field(
    name="vat_id",
    validators=[
        ValidatorSpec(name=ValidatorType.VAT_ID, params={"country": "ES"}),
    ],
)

soft_warning = ValidatorSpec(
    name=ValidatorType.PHONE_E164,
    params={"country": "ES"},
    severity="warning",   # records the error but doesn't set valid=False
)
```

### Fields

| Field      | Type                                          | Default     | Notes                                              |
|------------|-----------------------------------------------|-------------|----------------------------------------------------|
| `name`     | `ValidatorType`                               | —, required | Use the enum; raw strings work too for forward compat. |
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
| **Finance**        | `IBAN`                               | `iban`           | none                              |
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
| **National IDs**   | `NIF`                                | `nif`            | none                              |
|                    | `NIE`                                | `nie`            | ES — foreign person tax id        |
|                    | `CIF`                                | `cif`            | ES — legacy company tax id        |
|                    | `VAT_ID`                             | `vat_id`         | `{"country": "ES"}` (EU VAT)      |
|                    | `SSN`                                | `ssn`            | US                                |
|                    | `PASSPORT_NUMBER`                    | `passport_number`| ICAO 9303 (length / charset only) |

---

## 7. `ExtractionOptions` & `StageToggles` — pipeline configuration

```python
from flydocs_sdk import (
    EscalationConfig, ExtractionOptions, StageToggles,
)

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
    escalation=EscalationConfig(threshold=0.25, model="anthropic:claude-opus-4-7"),
    transformations=[],
)
```

### `ExtractionOptions`

| Field                  | Type                       | Default                | Notes                                                              |
|------------------------|----------------------------|------------------------|--------------------------------------------------------------------|
| `model`                | `str \| None`              | `None` (env default)   | Per-request primary model id (`"anthropic:claude-sonnet-4-6"`, `"openai:gpt-4o"`, …). |
| `language_hint`        | `str \| None`              | `None`                 | ISO 639-1; guides multilingual OCR / extraction. ≤ 16 chars.       |
| `return_bboxes`        | `bool`                     | `True`                 | When `False`, the response strips bounding boxes (cheaper to ship). |
| `declared_media_type`  | `str \| None`              | `None`                 | Override sniffing. Rare; useful when the caller knows better than `magic`. |
| `stages`               | `StageToggles`             | `StageToggles()`       | See below.                                                         |
| `escalation`           | `EscalationConfig \| None` | `None`                 | Replaces v0 `escalation_threshold` + `escalation_model` (now nested). |
| `transformations`      | `list[Transformation \| dict]` | `[]`                | Post-extraction transformations. See §9.                            |

### `EscalationConfig`

| Field        | Type      | Default | Notes                                                                |
|--------------|-----------|---------|----------------------------------------------------------------------|
| `threshold`  | `float`   | —       | `0.0–1.0`. The judge fail-rate trigger for the escalation re-run.    |
| `model`      | `str`     | —       | Model id used by the escalation re-run.                              |

### `StageToggles` — all ten stages

| Stage                  | Default | What it does                                                                                                  |
|------------------------|---------|---------------------------------------------------------------------------------------------------------------|
| `splitter`             | `False` | LLM document splitter. Required when one upload mixes several document types.                                  |
| `classifier`           | **`True`** | LLM classifier mapping each input file to one of the declared document types. No-op when every file carries `expected_type`. |
| `field_validation`     | **`True`** | Pure-Python validation pass.                                                                                  |
| `visual_authenticity`  | `False` | LLM visual check using the `DocumentTypeSpec.visual_checks` declarations.                                      |
| `content_authenticity` | `False` | LLM cross-document content checks.                                                                              |
| `judge`                | `False` | Per-field LLM re-evaluation. Annotates every extracted field with `confidence`, `evidence`, `flag_for_review`. |
| `judge_escalation`     | `False` | When the judge's fail-rate exceeds `escalation.threshold`, re-runs extract + judge with `escalation.model`.    |
| `bbox_refine`          | `False` | Replaces LLM-estimated bboxes with grounded coordinates from the document's text layer (PyMuPDF) or OCR.       |
| `rule_engine`          | `False` | Evaluates the business-rule DAG. See §8.                                                                        |
| `transform`            | `False` | Runs the `transformations` list. See §9.                                                                       |

---

## 8. `RuleSpec` — business rules over extracted fields

Rules are **natural-language predicates** the LLM evaluates against extracted fields, validator outcomes, or other rules' outputs. They form a DAG; the engine sorts topologically. Cycles are rejected at request-validation time.

```python
from flydocs_sdk import (
    RuleFieldParent, RuleOutputSpec, RuleRuleParent, RuleSpec, RuleValidatorParent,
)

totals_consistent = RuleSpec(
    id="totals_consistent",
    predicate="subtotal + tax_amount equals total_amount within 0.01",
    parents=[RuleFieldParent(
        document_type="invoice",
        fields=["subtotal", "tax_amount", "total_amount"],
    )],
)

vat_id_valid = RuleSpec(
    id="vat_id_valid",
    predicate="The supplier_vat field passes the vat_id validator",
    parents=[RuleValidatorParent(document_type="invoice", validator="vat_id")],
)

acceptable = RuleSpec(
    id="invoice_acceptable",
    predicate="totals_consistent AND vat_id_valid",
    parents=[
        RuleRuleParent(rule="totals_consistent"),
        RuleRuleParent(rule="vat_id_valid"),
    ],
    output=RuleOutputSpec(type="boolean"),
)
```

### `RuleSpec`

| Field        | Type                                       | Default               | Notes                                                              |
|--------------|--------------------------------------------|-----------------------|--------------------------------------------------------------------|
| `id`         | `str`                                      | —, required           | Unique within the request. Referenced by `RuleRuleParent.rule`.    |
| `predicate`  | `str`                                      | —, required           | Natural-language statement evaluated by the LLM.                   |
| `parents`    | `list[RuleParent]`                         | `[]`                  | Discriminated union — see below.                                   |
| `output`     | `RuleOutputSpec`                           | `RuleOutputSpec()`    | Shape the response should carry.                                   |

### `RuleParent` — three variants (discriminator `kind`)

| Variant                  | `kind`           | Fields                                              | Use for                                          |
|--------------------------|------------------|-----------------------------------------------------|--------------------------------------------------|
| `RuleFieldParent`        | `"field"`        | `document_type` (str), `fields` (list[str], min 1)   | "This rule operates on these fields of this document type." |
| `RuleValidatorParent`    | `"validator"`    | `document_type` (str), `validator` (str)             | "This rule operates on the outcome of this validator." |
| `RuleRuleParent`         | `"rule"`         | `rule` (str)                                         | "This rule depends on another rule's output."     |

The v0 keys (`parentType`, `fieldNames`, `validatorName`, `ruleId`) are gone in v1.

### Response shape

The response carries `result.rule_results: list[RuleResult]` with one entry per rule:

```python
for rr in result.rule_results:
    print(rr.rule_id, rr.output, rr.summary, rr.human_revision)
```

`summary` and `human_revision` are both `str | None` in v1.

---

## 9. Transformations — post-extraction reshaping

Two transformation types ship in-tree. Both are passed through `ExtractionOptions.transformations`; the `transform` stage must be enabled in `StageToggles`.

### `EntityResolutionTransformation` — declarative, fast, free

Deduplicates rows of an array field group using accent-fold + token-subset matching.

```python
from flydocs_sdk import (
    EntityResolutionTransformation, ExtractionOptions, StageToggles, TransformationScope,
)

opts = ExtractionOptions(
    stages=StageToggles(transform=True),
    transformations=[
        EntityResolutionTransformation(
            target_group="personas",
            match_by=["dni", "nombre"],
            min_shared_tokens=2,
            scope=TransformationScope.REQUEST,
        ),
    ],
)
```

### `LlmTransformation` — free-form

```python
from flydocs_sdk import LlmTransformation, TransformationScope

LlmTransformation(
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
| `target_group`   | `str`                      | —, required              | Must match a `FieldGroup.name` the extractor produces.             |
| `output_group`   | `str \| None`              | `None`                   | When set, append the transformation output as a NEW group; the original stays. When `None`, replaces in place. |
| `scope`          | `TransformationScope`      | `TASK`                   | `TASK`: one pass per document. `REQUEST`: across documents.        |
| `id`             | `str`                      | random UUIDv4            | Used in logs and the trace.                                        |

### `EntityResolutionTransformation`-only

| Field               | Type        | Default | Notes                                                       |
|---------------------|-------------|---------|-------------------------------------------------------------|
| `match_by`          | `list[str]` | required, min length 1 | Priority-ordered field names.               |
| `min_shared_tokens` | `int`       | `2`     | Minimum shared name tokens for a name-variant match.        |

### `LlmTransformation`-only

| Field        | Type           | Default | Notes                                                |
|--------------|----------------|---------|------------------------------------------------------|
| `intention`  | `str`          | required, min length 10 | One-sentence goal in any language.  |
| `prompt_id`  | `str \| None`  | `None`  | Named template id from the server-side catalog.       |

---

## 10. Async extractions — `SubmitExtractionRequest`, callbacks, idempotency

For long documents, batches, or fire-and-forget workloads, use `extractions.create` and either poll with `wait_for_completion` or receive a webhook. Replaces v0 `submit_job` / `/api/v1/jobs`.

```python
from flydocs_sdk import (
    AsyncClient, ExtractionStatus, FileInput, SubmitExtractionRequest,
)

async with AsyncClient("http://localhost:8400") as flydocs:
    ext = await flydocs.extractions.create(
        SubmitExtractionRequest(
            files=[FileInput.from_path("big-batch.pdf")],
            document_types=[invoice],
            callback_url="https://your-app.example.com/flydocs/webhook",
            metadata={"caller": "ingest-pipeline", "batch_id": "b-42"},
        ),
        idempotency_key="ingest-pipeline:b-42",
        correlation_id="req-12345",
    )
    print(f"queued {ext.id} ({ext.status.value})")

    final = await flydocs.wait_for_completion(
        ext.id, poll_interval=2.0, timeout=900.0,
    )
    if final.status == ExtractionStatus.SUCCEEDED:
        envelope = await flydocs.extractions.get_result(ext.id)
        result = envelope.result
        ...
```

### `SubmitExtractionRequest`

A superset of `ExtractionRequest`:

| Field            | Type                            | Default                  | Notes                                                              |
|------------------|---------------------------------|--------------------------|--------------------------------------------------------------------|
| (all fields from `ExtractionRequest`) | — | — | The extraction's `id` plays the role of v0's `request_id`. |
| `callback_url`   | `str \| None`                   | `None`                   | When set, the service POSTs an `EventEnvelope` here on terminal status. |
| `metadata`       | `dict[str, Any]`                | `{}`                     | Echoed back on the envelope — use for caller-side correlation.    |

### Lifecycle states

v1 simplifies the state machine to a linear `queued → running → succeeded | failed | cancelled`. The intermediate `PARTIAL_SUCCEEDED` / `REFINING_BBOXES` states from v0 are gone — bbox refinement runs as additive post-processing under `Extraction.post_processing.bbox_refinement` without gating the main lifecycle.

| Status              | Wire form        | When                                                            |
|---------------------|------------------|------------------------------------------------------------------|
| `ExtractionStatus.QUEUED`     | `"queued"`     | Persisted, waiting for the worker.                              |
| `ExtractionStatus.RUNNING`    | `"running"`    | Worker claimed it.                                              |
| `ExtractionStatus.SUCCEEDED`  | `"succeeded"`  | Terminal: the main pipeline finished cleanly.                   |
| `ExtractionStatus.FAILED`     | `"failed"`     | Terminal: the worker hit an unrecoverable error.                |
| `ExtractionStatus.CANCELLED`  | `"cancelled"`  | Terminal: caller cancelled while queued.                        |

Post-processing has its own lifecycle (`PostProcessingStatus.PENDING/RUNNING/SUCCEEDED/FAILED`).

### Headers per call

| Header            | SDK kwarg          | Notes                                                              |
|-------------------|--------------------|--------------------------------------------------------------------|
| `Idempotency-Key` | `idempotency_key=` | Send the same key to replay an existing submission.                |
| `X-Correlation-Id`| `correlation_id=`  | Stamped on every internal log line and on the webhook envelope.    |

### Polling helper

```python
final = await flydocs.wait_for_completion(
    ext.id,
    poll_interval=2.0,   # seconds between GET /api/v1/extractions/{id}
    timeout=900.0,       # raises TimeoutError after this many seconds
)
```

Terminal statuses are `SUCCEEDED`, `FAILED`, `CANCELLED`. `wait_for_completion` returns the final `Extraction` in all three cases.

### Listing / cancelling

```python
listing = await flydocs.extractions.list(
    status=[ExtractionStatus.SUCCEEDED, ExtractionStatus.FAILED],
    post_processing_status=[PostProcessingStatus.PENDING, PostProcessingStatus.RUNNING],
    idempotency_key="ingest-pipeline:b-42",
    created_after=datetime(2026, 5, 1),
    created_before=datetime(2026, 5, 31, 23, 59),
    limit=25,
    offset=0,
)
for ext in listing.items:
    print(ext.id, ext.status, ext.submitted_at)

await flydocs.extractions.cancel("ext_abc")   # only valid while QUEUED
```

---

## 11. Webhooks — receiving and verifying delivery

When `callback_url` is set, the service POSTs an `EventEnvelope` on every lifecycle event. It signs the body with HMAC-SHA256 in `X-Flydocs-Signature` when `FLYDOCS_WEBHOOK_HMAC_SECRET` is configured on the service.

### Event types (string literals)

| Constant                                                | String value                                  |
|---------------------------------------------------------|-----------------------------------------------|
| `EVENT_TYPE_EXTRACTION_SUBMITTED`                       | `"extraction.submitted"`                      |
| `EVENT_TYPE_EXTRACTION_COMPLETED`                       | `"extraction.completed"`                      |
| `EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED`       | `"extraction.post_processing.requested"`      |
| `EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED`       | `"extraction.post_processing.completed"`      |

### `EventEnvelope` shape

| Field             | Type                       | Notes                                                                          |
|-------------------|----------------------------|--------------------------------------------------------------------------------|
| `event_id`        | `str`                      | Unique per delivery. Dedupe on this — the publisher retries on transient errors. |
| `event_type`      | `str`                      | One of the four constants above.                                                |
| `version`         | `str`                      | Semver of the envelope schema (`"1.0.0"`).                                      |
| `occurred_at`     | `datetime`                 | When the event happened.                                                        |
| `correlation_id`  | `str \| None`              | The `X-Correlation-Id` you passed at submit time, if any.                       |
| `tenant_id`       | `str \| None`              | When the service runs multi-tenant.                                              |
| `extraction`      | `Extraction`               | Current-state snapshot of the resource.                                          |
| `result`          | `ExtractionResult \| None` | Present on `extraction.completed` when terminal status is `succeeded`; `None` otherwise. |
| `metadata`        | `dict[str, Any]`           | The dict you passed in `SubmitExtractionRequest.metadata`.                       |

### Verifying — FastAPI example

```python
import os
from flydocs_sdk import (
    EVENT_TYPE_EXTRACTION_COMPLETED, ExtractionStatus,
    WebhookVerificationError, WebhookVerifier,
)
from fastapi import FastAPI, HTTPException, Request

verifier = WebhookVerifier(secret=os.environ["FLYDOCS_WEBHOOK_HMAC_SECRET"])
app = FastAPI()

@app.post("/flydocs/webhook")
async def on_webhook(request: Request) -> dict:
    body = await request.body()                                   # raw bytes
    signature = request.headers.get("X-Flydocs-Signature", "")
    try:
        envelope = verifier.verify(body, signature)               # typed EventEnvelope
    except WebhookVerificationError:
        raise HTTPException(status_code=403, detail="bad signature")
    if envelope.event_type == EVENT_TYPE_EXTRACTION_COMPLETED:
        ext = envelope.extraction
        if ext.status == ExtractionStatus.SUCCEEDED and envelope.result is not None:
            ...   # persist, fan out downstream work
    return {"ok": True}
```

> **Verify against the raw bytes.** If your framework deserialised the JSON before you got the bytes, re-encoding will change the digest. `verifier.sign(body)` exists for tests so you can pin parity with the service.

---

## 12. Errors — RFC 7807 problem-details

Every non-2xx response decodes into a typed `FlydocsHttpError` with `status_code`, `code`, `title`, `detail`, `type`, `instance`, `extensions`, and the raw `payload` dict:

| `code`                       | Status | Meaning                                                                                                   |
|------------------------------|--------|-----------------------------------------------------------------------------------------------------------|
| `timeout`                    | 408    | Sync pipeline exceeded `FLYDOCS_SYNC_TIMEOUT_S`. Retry via `extractions.create`.                          |
| `file_too_large`             | 413    | File over `FLYDOCS_MAX_BYTES`.                                                                            |
| `unsupported_file`           | 415    | The file's media type is unsupported.                                                                     |
| `invalid_base64`             | 422    | `content_base64` failed strict parsing.                                                                   |
| `validation_failed`          | 422    | Semantic validation found issues. `payload` carries every issue.                                          |
| `invalid_request`            | 422    | Generic request-shape problem.                                                                            |
| `encrypted_pdf`              | 422    | PDF carries an encryption header the service can't open.                                                  |
| `office_conversion_failed`   | 422    | Gotenberg/LibreOffice could not convert the Office document.                                              |
| `archive_extraction_failed`  | 422    | ZIP / 7z / TAR could not be unpacked.                                                                     |
| `image_conversion_failed`    | 422    | Pillow / cairosvg could not convert the image.                                                            |
| `not_ready`                  | 409    | `GET /extractions/{id}/result` called before the extraction succeeded.                                    |
| `not_cancellable`            | 409    | Worker already started; mid-flight cancellation isn't supported.                                          |
| `not_found`                  | 404    | Unknown extraction id.                                                                                    |
| `unauthorized`               | 401    | API key missing or invalid.                                                                                |

```python
from flydocs_sdk import (
    FlydocsClientError, FlydocsHttpError, FlydocsTimeoutError,
    SubmitExtractionRequest,
)

try:
    result = await flydocs.extract(req)
except FlydocsHttpError as exc:
    if exc.code == "timeout":
        await flydocs.extractions.create(SubmitExtractionRequest(**req.model_dump()))
    elif exc.code in ("validation_failed", "invalid_request"):
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

The error also carries the full RFC 7807 view via `exc.as_problem_details()` returning a typed `ProblemDetails`.

---

## 13. Production patterns

**Reuse a client.** Construct `AsyncClient` once per application and share it. The underlying httpx connection pool is the most expensive part to set up.

**API keys.** Pass `api_key="..."` to the constructor and the SDK adds the `Authorization: Bearer ...` header on every call.

**Correlation ids.** Pass `correlation_id="..."` on `extract` / `extractions.create`. The service stamps it on every internal log line and on the webhook envelope.

**Custom timeouts.** Default is 60 s. `AsyncClient("http://...", timeout=120.0)`.

**Default headers.** `AsyncClient(..., default_headers={"X-Tenant-Id": "tenant-42"})` adds the header to every outbound request.

**Bring your own httpx client.** `AsyncClient(..., http_client=existing)` shares your app's connection pool. The SDK never closes transports it didn't create.

**Health checks.** `await flydocs.health("readiness")` returns the actuator JSON.

**Cost tracking.** When the service has cost tracking enabled, `result.pipeline.usage` carries per-agent and per-model token + USD breakdowns; webhook envelopes carry the same.

---

## 14. The kitchen sink — full request with every feature

A realistic invoice extraction touching every feature: typed schema with array rows + validators, every applicable stage on, business rules, an entity-resolution transformation, idempotency, correlation id.

```python
import asyncio
from flydocs_sdk import (
    AsyncClient,
    DocumentTypeSpec,
    EntityResolutionTransformation,
    EscalationConfig,
    ExtractionOptions,
    ExtractionStatus,
    Field,
    FieldGroup,
    FieldType,
    FileInput,
    RuleFieldParent,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
    StageToggles,
    StandardFormat,
    SubmitExtractionRequest,
    TransformationScope,
    ValidatorSpec,
    ValidatorType,
)


invoice = DocumentTypeSpec(
    id="invoice",
    description="Vendor invoice",
    country="ES",
    field_groups=[
        FieldGroup(name="header", fields=[
            Field(name="invoice_number", type=FieldType.STRING, required=True),
            Field(name="invoice_date",   type=FieldType.STRING,
                  format=StandardFormat.DATE, required=True),
            Field(name="supplier_name",  type=FieldType.STRING, required=True),
            Field(
                name="supplier_vat",
                type=FieldType.STRING,
                required=True,
                validators=[ValidatorSpec(
                    name=ValidatorType.VAT_ID, params={"country": "ES"}
                )],
            ),
            Field(
                name="supplier_iban",
                type=FieldType.STRING,
                validators=[ValidatorSpec(name=ValidatorType.IBAN)],
            ),
        ]),
        FieldGroup(name="totals", fields=[
            Field(name="subtotal",     type=FieldType.NUMBER, required=True, minimum=0.0),
            Field(name="tax_amount",   type=FieldType.NUMBER, required=True, minimum=0.0),
            Field(name="total_amount", type=FieldType.NUMBER, required=True, minimum=0.0),
            Field(
                name="currency",
                type=FieldType.STRING,
                required=True,
                validators=[ValidatorSpec(name=ValidatorType.CURRENCY_CODE)],
            ),
        ]),
        FieldGroup(name="line_items_block", fields=[
            Field(
                name="line_items",
                type=FieldType.ARRAY,
                items=Field(
                    name="row",
                    type=FieldType.OBJECT,
                    fields=[
                        Field(name="description", type=FieldType.STRING),
                        Field(name="quantity",    type=FieldType.NUMBER, minimum=0),
                        Field(name="unit_price",  type=FieldType.NUMBER, minimum=0),
                        Field(name="line_total",  type=FieldType.NUMBER, minimum=0),
                    ],
                ),
            ),
        ]),
    ],
)

rules = [
    RuleSpec(
        id="totals_consistent",
        predicate="subtotal + tax_amount equals total_amount within 0.01",
        parents=[RuleFieldParent(
            document_type="invoice",
            fields=["subtotal", "tax_amount", "total_amount"],
        )],
    ),
    RuleSpec(
        id="vat_id_valid",
        predicate="The supplier_vat field passes the vat_id validator",
        parents=[RuleValidatorParent(document_type="invoice", validator="vat_id")],
    ),
    RuleSpec(
        id="invoice_acceptable",
        predicate="totals_consistent AND vat_id_valid",
        parents=[
            RuleRuleParent(rule="totals_consistent"),
            RuleRuleParent(rule="vat_id_valid"),
        ],
    ),
]


async def main(invoice_path: str) -> None:
    async with AsyncClient("http://localhost:8400") as flydocs:
        ext = await flydocs.extractions.create(
            SubmitExtractionRequest(
                files=[FileInput.from_path(invoice_path)],
                document_types=[invoice],
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
                    escalation=EscalationConfig(
                        threshold=0.25,
                        model="anthropic:claude-opus-4-7",
                    ),
                    transformations=[
                        EntityResolutionTransformation(
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

        final = await flydocs.wait_for_completion(ext.id, poll_interval=2.0, timeout=900.0)
        if final.status != ExtractionStatus.SUCCEEDED:
            err_msg = final.error.message if final.error else ""
            raise SystemExit(f"extraction did not succeed: {final.status.value} {err_msg}")

        envelope = await flydocs.extractions.get_result(ext.id)
        result = envelope.result
        for rr in result.rule_results:
            print(f"  rule {rr.rule_id}: {rr.output}")
        for group in result.documents[0].field_groups:
            print(group.name, "→", len(group.fields), "fields")


asyncio.run(main("invoice.pdf"))
```

---

## 15. Synchronous facade (when async isn't an option)

For scripts, batch tools, and callers that can't run an event loop, `Client` wraps `AsyncClient` on a dedicated background loop:

```python
from flydocs_sdk import Client

with Client("http://localhost:8400") as flydocs:
    result = flydocs.extract(req)
```

Method-for-method identical to `AsyncClient`, just without `await`. Prefer the async client whenever you can — the sync wrapper costs you one extra event loop per instance.

---

## Further reading

- [`QUICKSTART.md`](./QUICKSTART.md) — 5-minute zero-to-first-extraction.
- [`examples/`](./examples/) — six runnable scripts.
- [`docs/migration-v0-to-v1.md`](../../docs/migration-v0-to-v1.md) — complete rename / reshape table for v0 callers.
- [`docs/api-reference.md`](../../docs/api-reference.md) — full HTTP wire contract.
- [`docs/pipeline.md`](../../docs/pipeline.md) — stage DAG internals.
- [`docs/rule-engine.md`](../../docs/rule-engine.md) — rule engine semantics + DAG resolution.
- [`docs/transformations.md`](../../docs/transformations.md) — the `transform` stage internals.
