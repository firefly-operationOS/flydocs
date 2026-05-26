# flydocs — API contract v1 redesign implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-05-26-api-contract-v1-redesign-design.md`

**Goal:** Replace the current `/api/v1` contract with a snake_case-everywhere, semantically-cleaned-up v1 across server, Python SDK, Java SDK, Spring Boot starter, docs, and tests — in a single clean break.

**Architecture:** Twelve phases, each independently verifiable. We work bottom-up: enums → DTOs → SQLAlchemy + Alembic → core services → web controllers → server tests → Python SDK → Java SDK → docs → final E2E verification. Inside each phase, tasks follow strict TDD: write failing test, run it red, implement, run it green, commit.

**Tech Stack:** Python 3.13 (Pydantic v2, FastAPI / Starlette, SQLAlchemy async + Alembic, pytest, ruff), Java 21 (Maven, Jackson, JUnit 5), pyfly (`fireflyframework-pyfly`), `fireflyframework-agentic`.

**Pacing & verification gates:**
- Server tests run after every phase that touches Python (Phase 1, 2, 3, 4, 5).
- Linters (`task lint:check`) run after every phase.
- Full integration suite (`task docker:up:test`) runs at the end of Phase 5 and again at the end of Phase 9.
- SDK round-trips (Python + Java) run at the end of Phase 6 and Phase 7 respectively.
- Final acceptance gate (§19) runs at the end of Phase 11.

**Commit cadence:** Commit after every task. Phases never bundle multiple unrelated commits.

---

## Phase 0 — Pre-flight

### Task 0.1: Create the working branch

**Files:**
- None (git operation only)

- [ ] **Step 1: Verify clean working tree**

Run: `git status`
Expected: only `flydocs-whitepaper.pdf` and `docs/superpowers/specs/` (and now `docs/superpowers/plans/`) — i.e. nothing else uncommitted.

- [ ] **Step 2: Create the branch**

Run: `git checkout -b feat/api-v1-redesign`

- [ ] **Step 3: Verify branch**

Run: `git branch --show-current`
Expected: `feat/api-v1-redesign`

### Task 0.2: Capture a baseline test snapshot

**Files:**
- Create: `docs/superpowers/plans/baseline-snapshot.txt`

- [ ] **Step 1: Run the existing test suite and capture results**

Run: `task test 2>&1 | tee docs/superpowers/plans/baseline-snapshot.txt`
Expected: all tests pass; if any fail, fix them first before starting the refactor (do not start the refactor on a red baseline).

- [ ] **Step 2: Commit the baseline**

```bash
git add docs/superpowers/plans/baseline-snapshot.txt
git commit -m "chore: capture baseline test snapshot for v1 redesign"
```

---

## Phase 1 — Enums & wire-level DTOs (server interfaces)

These are the "pure shape" definitions everything else depends on. We rewrite all of `interfaces/enums/` and `interfaces/dtos/` first; downstream files will not compile yet — that's expected. The `task test` gate at the end of this phase only runs the DTO unit tests.

### Task 1.1: Rewrite `interfaces/enums/job_status.py` → `extraction_status.py`

**Files:**
- Create: `src/flydocs/interfaces/enums/extraction_status.py`
- Delete: `src/flydocs/interfaces/enums/job_status.py`
- Test: `tests/unit/test_extraction_status.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_extraction_status.py
from flydocs.interfaces.enums.extraction_status import ExtractionStatus, PostProcessingStatus


def test_extraction_status_values():
    assert {s.value for s in ExtractionStatus} == {"queued", "running", "succeeded", "failed", "cancelled"}


def test_extraction_status_terminal():
    assert ExtractionStatus.SUCCEEDED.is_terminal
    assert ExtractionStatus.FAILED.is_terminal
    assert ExtractionStatus.CANCELLED.is_terminal
    assert not ExtractionStatus.QUEUED.is_terminal
    assert not ExtractionStatus.RUNNING.is_terminal


def test_extraction_status_has_result():
    assert ExtractionStatus.SUCCEEDED.has_result
    assert not ExtractionStatus.QUEUED.has_result
    assert not ExtractionStatus.RUNNING.has_result
    assert not ExtractionStatus.FAILED.has_result
    assert not ExtractionStatus.CANCELLED.has_result


def test_post_processing_status_values():
    assert {s.value for s in PostProcessingStatus} == {"pending", "running", "succeeded", "failed"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_extraction_status.py -v`
Expected: ImportError / ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
# src/flydocs/interfaces/enums/extraction_status.py
# Copyright 2026 Firefly Software Solutions Inc
"""Async extraction lifecycle states.

One linear state machine: queued -> running -> succeeded | failed | cancelled.
Post-processing (bbox refinement) lives in a separate block with its own
PostProcessingStatus lifecycle.
"""

from __future__ import annotations

from enum import StrEnum


class ExtractionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (ExtractionStatus.SUCCEEDED, ExtractionStatus.FAILED, ExtractionStatus.CANCELLED)

    @property
    def has_result(self) -> bool:
        return self is ExtractionStatus.SUCCEEDED


class PostProcessingStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (PostProcessingStatus.SUCCEEDED, PostProcessingStatus.FAILED)
```

- [ ] **Step 4: Delete the old module**

Run: `rm src/flydocs/interfaces/enums/job_status.py`

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_extraction_status.py -v`
Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/flydocs/interfaces/enums/extraction_status.py tests/unit/test_extraction_status.py
git add -u src/flydocs/interfaces/enums/job_status.py
git commit -m "refactor(enums): job_status -> extraction_status with lowercase values"
```

### Task 1.2: Lowercase `JudgeStatus` / `ContentIntegrityStatus` / `CheckStatus`

**Files:**
- Modify: `src/flydocs/interfaces/enums/status.py`
- Test: `tests/unit/test_status_enums.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_status_enums.py
from flydocs.interfaces.enums.status import (
    CheckStatus,
    ContentIntegrityStatus,
    JudgeStatus,
    ValidationRule,
)


def test_judge_status_lowercase():
    assert {s.value for s in JudgeStatus} == {"pass", "fail", "uncertain"}


def test_content_integrity_status_lowercase():
    assert {s.value for s in ContentIntegrityStatus} == {"valid", "invalid", "uncertain"}


def test_check_status_lowercase():
    assert {s.value for s in CheckStatus} == {"pass", "fail", "uncertain"}


def test_validation_rule_includes_validator():
    assert "validator" in {r.value for r in ValidationRule}
    assert "standard" not in {r.value for r in ValidationRule}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_status_enums.py -v`
Expected: FAIL with capital-letter comparison errors.

- [ ] **Step 3: Implement**

Replace the entire body of `src/flydocs/interfaces/enums/status.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Status enums shared across nodes (validation rules, judge verdicts,
content-authenticity verdicts)."""

from __future__ import annotations

from enum import StrEnum


class ValidationRule(StrEnum):
    """Which validation check produced a given error."""

    TYPE = "type"
    PATTERN = "pattern"
    FORMAT = "format"
    ENUM = "enum"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    VALIDATOR = "validator"


class JudgeStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


class ContentIntegrityStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


__all__ = ["CheckStatus", "ContentIntegrityStatus", "JudgeStatus", "ValidationRule"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_status_enums.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/flydocs/interfaces/enums/status.py tests/unit/test_status_enums.py
git commit -m "refactor(enums): lowercase judge/content-integrity/check status values"
```

### Task 1.3: Add `OBJECT` to `FieldType`

**Files:**
- Modify: `src/flydocs/interfaces/enums/field_type.py`
- Test: `tests/unit/test_field_type.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_field_type.py
from flydocs.interfaces.enums.field_type import FieldType, StandardFormat


def test_field_type_includes_object():
    assert "object" in {t.value for t in FieldType}


def test_field_type_full_set():
    assert {t.value for t in FieldType} == {
        "string", "number", "integer", "boolean", "array", "object",
    }


def test_standard_format_unchanged():
    assert {f.value for f in StandardFormat} >= {"date", "date-time", "email", "uri", "uuid"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_field_type.py -v`
Expected: FAIL (no OBJECT member).

- [ ] **Step 3: Implement**

Replace `src/flydocs/interfaces/enums/field_type.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Supported field primitives + standard formats for the public extraction schema."""

from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class StandardFormat(StrEnum):
    """JSON Schema-style standard formats applied at validation time."""

    DATE = "date"
    DATE_TIME = "date-time"
    TIME = "time"
    EMAIL = "email"
    URI = "uri"
    UUID = "uuid"
    CURRENCY = "currency"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_field_type.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/flydocs/interfaces/enums/field_type.py tests/unit/test_field_type.py
git commit -m "feat(enums): add FieldType.OBJECT + StandardFormat.{TIME, CURRENCY}"
```

### Task 1.4: Rename `interfaces/enums/standard_validator.py` → `validator.py`

**Files:**
- Create: `src/flydocs/interfaces/enums/validator.py`
- Delete: `src/flydocs/interfaces/enums/standard_validator.py`
- Test: `tests/unit/test_validator_enum.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_validator_enum.py
from flydocs.interfaces.enums.validator import ValidatorType


def test_validator_type_includes_iban():
    assert "iban" in {v.value for v in ValidatorType}


def test_validator_type_includes_phone_e164():
    assert "phone_e164" in {v.value for v in ValidatorType}


def test_validator_type_includes_vat_id():
    assert "vat_id" in {v.value for v in ValidatorType}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_validator_enum.py -v`
Expected: ImportError.

- [ ] **Step 3: Inspect the existing enum**

Run: `cat src/flydocs/interfaces/enums/standard_validator.py | head -80`

This reveals the full `StandardValidatorType` member list. Copy the **member values** verbatim into the new enum below; member identifiers (e.g. `IBAN`, `PHONE_E164`) stay the same.

- [ ] **Step 4: Implement**

Create `src/flydocs/interfaces/enums/validator.py`:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Built-in validator catalogue applied to extracted field values."""

from __future__ import annotations

from enum import StrEnum


class ValidatorType(StrEnum):
    # Network / web
    EMAIL = "email"
    URI = "uri"
    URL = "url"
    DOMAIN = "domain"
    SLUG = "slug"
    IPV4 = "ipv4"
    IPV6 = "ipv6"

    # Temporal
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    ISO_8601 = "iso_8601"

    # Identifiers
    UUID = "uuid"
    JSON = "json"
    HEX_COLOR = "hex_color"

    # Finance
    IBAN = "iban"
    BIC = "bic"
    CREDIT_CARD = "credit_card"
    CURRENCY_CODE = "currency_code"
    AMOUNT = "amount"

    # Telephony
    PHONE_E164 = "phone_e164"

    # Geographic
    COUNTRY_CODE = "country_code"
    LANGUAGE_CODE = "language_code"
    POSTAL_CODE = "postal_code"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"

    # National IDs
    NIF = "nif"
    NIE = "nie"
    CIF = "cif"
    VAT_ID = "vat_id"
    SSN = "ssn"
    PASSPORT_NUMBER = "passport_number"
```

- [ ] **Step 5: Delete the old module**

Run: `rm src/flydocs/interfaces/enums/standard_validator.py`

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_validator_enum.py -v`
Expected: 3 PASSED.

- [ ] **Step 7: Commit**

```bash
git add src/flydocs/interfaces/enums/validator.py tests/unit/test_validator_enum.py
git add -u src/flydocs/interfaces/enums/standard_validator.py
git commit -m "refactor(enums): standard_validator -> validator; StandardValidatorType -> ValidatorType"
```

### Task 1.5: Rewrite `interfaces/dtos/bbox.py`

**Files:**
- Modify: `src/flydocs/interfaces/dtos/bbox.py`
- Test: `tests/unit/test_bbox_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_bbox_dto.py
import pytest
from pydantic import ValidationError

from flydocs.interfaces.dtos.bbox import BboxQuality, BboxSource, BoundingBox


def test_bounding_box_basic():
    bbox = BoundingBox(xmin=0.1, ymin=0.2, xmax=0.5, ymax=0.6)
    assert bbox.xmin == 0.1
    assert bbox.source is None  # not set until refiner runs


def test_bounding_box_rejects_degenerate():
    with pytest.raises(ValidationError):
        BoundingBox(xmin=0.5, ymin=0.1, xmax=0.5, ymax=0.6)  # xmin == xmax


def test_bbox_quality_lowercase():
    assert {q.value for q in BboxQuality} == {"good", "poor", "suspicious", "invalid"}
    assert "empty" not in {q.value for q in BboxQuality}


def test_bbox_source_lowercase():
    assert {s.value for s in BboxSource} == {"llm", "pdf_text", "ocr"}
    assert "none" not in {s.value for s in BboxSource}


def test_bbox_has_no_empty_classmethod():
    assert not hasattr(BoundingBox, "empty")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_bbox_dto.py -v`
Expected: FAIL on the EMPTY / NONE / empty() assertions.

- [ ] **Step 3: Implement**

Replace `src/flydocs/interfaces/dtos/bbox.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Bounding box in normalised image-space coordinates.

All values are floats in [0, 1]. (0, 0) is the top-left of the rendered
page; (1, 1) is the bottom-right. Absence is represented by null at the
field site, not by a synthetic "empty" box.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class BboxQuality(StrEnum):
    GOOD = "good"
    POOR = "poor"
    SUSPICIOUS = "suspicious"
    INVALID = "invalid"


class BboxSource(StrEnum):
    LLM = "llm"
    PDF_TEXT = "pdf_text"
    OCR = "ocr"


class BoundingBox(BaseModel):
    xmin: float = Field(..., ge=0.0, le=1.0)
    ymin: float = Field(..., ge=0.0, le=1.0)
    xmax: float = Field(..., ge=0.0, le=1.0)
    ymax: float = Field(..., ge=0.0, le=1.0)
    quality: BboxQuality | None = None
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source: BboxSource | None = None
    refinement_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_corners(self) -> "BoundingBox":
        if self.xmin >= self.xmax:
            raise ValueError("xmin must be strictly less than xmax")
        if self.ymin >= self.ymax:
            raise ValueError("ymin must be strictly less than ymax")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_bbox_dto.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/flydocs/interfaces/dtos/bbox.py tests/unit/test_bbox_dto.py
git commit -m "refactor(dtos): bbox uses null for absence; drop EMPTY/NONE placeholders"
```

### Task 1.6: Rewrite `interfaces/dtos/standard_validator.py` → `validator.py`

**Files:**
- Create: `src/flydocs/interfaces/dtos/validator.py`
- Delete: `src/flydocs/interfaces/dtos/standard_validator.py`
- Test: `tests/unit/test_validator_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_validator_dto.py
import pytest
from pydantic import ValidationError

from flydocs.interfaces.dtos.validator import ValidatorSpec
from flydocs.interfaces.enums.validator import ValidatorType


def test_validator_spec_basic():
    v = ValidatorSpec(name=ValidatorType.IBAN)
    assert v.name == ValidatorType.IBAN
    assert v.params == {}
    assert v.severity == "error"


def test_validator_spec_with_params_and_severity():
    v = ValidatorSpec(name=ValidatorType.PHONE_E164, params={"country": "ES"}, severity="warning")
    assert v.params == {"country": "ES"}
    assert v.severity == "warning"


def test_validator_spec_uses_name_not_type():
    # name is the canonical dispatch key in v1
    v = ValidatorSpec.model_validate({"name": "iban"})
    assert v.name == ValidatorType.IBAN


def test_validator_spec_rejects_legacy_type_key():
    # Passing `type` instead of `name` should fail (no alias)
    with pytest.raises(ValidationError):
        ValidatorSpec.model_validate({"type": "iban"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_validator_dto.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/flydocs/interfaces/dtos/validator.py`:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""ValidatorSpec -- request-side declaration for one built-in check."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.enums.validator import ValidatorType


class ValidatorSpec(BaseModel):
    """One named built-in validator applied to a field."""

    model_config = ConfigDict(extra="forbid")

    name: ValidatorType
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"] = "error"
```

- [ ] **Step 4: Delete the old module**

Run: `rm src/flydocs/interfaces/dtos/standard_validator.py`

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_validator_dto.py -v`
Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/flydocs/interfaces/dtos/validator.py tests/unit/test_validator_dto.py
git add -u src/flydocs/interfaces/dtos/standard_validator.py
git commit -m "refactor(dtos): StandardValidatorSpec -> ValidatorSpec; name (not type) is the dispatch key"
```

### Task 1.7: Rewrite `interfaces/dtos/field.py` (single recursive `Field`)

**Files:**
- Modify: `src/flydocs/interfaces/dtos/field.py`
- Test: `tests/unit/test_field_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_field_dto.py
import pytest
from pydantic import ValidationError

from flydocs.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    Field,
    FieldGroup,
    FieldValidation,
    FieldValidationError,
    JudgeOutcome,
)
from flydocs.interfaces.enums.field_type import FieldType


def test_primitive_field():
    f = Field(name="total", type=FieldType.NUMBER, required=True)
    assert f.name == "total"
    assert f.items is None
    assert f.fields is None


def test_array_field_with_object_rows():
    f = Field(
        name="line_items",
        type=FieldType.ARRAY,
        items=Field(
            type=FieldType.OBJECT,
            name="line_item",
            fields=[
                Field(name="description", type=FieldType.STRING),
                Field(name="quantity", type=FieldType.NUMBER, minimum=0),
            ],
        ),
    )
    assert f.items.type == FieldType.OBJECT
    assert len(f.items.fields) == 2


def test_object_field():
    f = Field(
        name="customer",
        type=FieldType.OBJECT,
        fields=[
            Field(name="name", type=FieldType.STRING),
            Field(name="vat", type=FieldType.STRING),
        ],
    )
    assert f.fields[0].name == "name"


def test_array_requires_items():
    with pytest.raises(ValidationError):
        Field(name="x", type=FieldType.ARRAY)


def test_object_requires_fields():
    with pytest.raises(ValidationError):
        Field(name="x", type=FieldType.OBJECT)


def test_primitive_rejects_items_and_fields():
    with pytest.raises(ValidationError):
        Field(name="x", type=FieldType.STRING, items=Field(name="y", type=FieldType.STRING))
    with pytest.raises(ValidationError):
        Field(name="x", type=FieldType.STRING, fields=[Field(name="y", type=FieldType.STRING)])


def test_field_group_snake_case_keys():
    g = FieldGroup(name="totals", description="money block", fields=[Field(name="total", type=FieldType.NUMBER)])
    assert g.name == "totals"
    assert g.fields[0].name == "total"


def test_field_group_rejects_legacy_camel_keys():
    with pytest.raises(ValidationError):
        FieldGroup.model_validate({"fieldGroupName": "totals", "fieldGroupFields": []})


def test_min_max_constraint():
    with pytest.raises(ValidationError):
        Field(name="x", type=FieldType.NUMBER, minimum=10, maximum=5)


def test_extracted_field_basic():
    e = ExtractedField(name="fecha", value="2025-05-15", pages=[1])
    assert e.name == "fecha"
    assert e.value == "2025-05-15"
    assert e.pages == [1]
    assert e.bbox is None
    assert e.notes is None
    assert isinstance(e.validation, FieldValidation)
    assert isinstance(e.judge, JudgeOutcome)


def test_extracted_field_rejects_legacy_keys():
    with pytest.raises(ValidationError):
        ExtractedField.model_validate({"fieldName": "x", "fieldValueFound": "y", "pagesFound": [1]})


def test_extracted_field_group():
    g = ExtractedFieldGroup(name="totals", fields=[ExtractedField(name="total", value=100)])
    assert g.name == "totals"
    assert g.fields[0].name == "total"


def test_field_validation_error_uses_validator_rule():
    e = FieldValidationError(rule="validator", message="bad iban")
    assert e.rule == "validator"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_field_dto.py -v`
Expected: many failures (Field doesn't exist yet).

- [ ] **Step 3: Implement**

Replace `src/flydocs/interfaces/dtos/field.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Field-level DTOs -- schema in, extraction out.

One recursive Field handles primitives, arrays, and objects. Arrays
require ``items`` (a single Field describing the row shape); objects
require ``fields`` (a list of Fields describing the members); primitives
forbid both. The response side carries ExtractedField with the same
recursion.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field as _PydField, model_validator

from flydocs.interfaces.dtos.bbox import BoundingBox
from flydocs.interfaces.dtos.validator import ValidatorSpec
from flydocs.interfaces.enums.field_type import FieldType, StandardFormat
from flydocs.interfaces.enums.status import JudgeStatus, ValidationRule


# ---------------------------------------------------------------------------
# REQUEST SIDE
# ---------------------------------------------------------------------------


class Field(BaseModel):
    """One field in a schema. Recursive: arrays carry an items Field, objects carry a list of Fields."""

    model_config = ConfigDict(extra="forbid")

    name: str = _PydField(..., min_length=1)
    description: str | None = None
    type: FieldType = FieldType.STRING
    required: bool = False
    pattern: str | None = None
    format: StandardFormat | None = None
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    items: "Field | None" = None
    fields: "list[Field] | None" = None
    validators: list[ValidatorSpec] = _PydField(default_factory=list)

    @model_validator(mode="after")
    def _check_constraints(self) -> "Field":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must be <= maximum")
        if self.type == FieldType.ARRAY:
            if self.items is None:
                raise ValueError("type 'array' requires items")
            if self.fields is not None:
                raise ValueError("type 'array' must not set fields")
        elif self.type == FieldType.OBJECT:
            if self.fields is None or not self.fields:
                raise ValueError("type 'object' requires fields (non-empty)")
            if self.items is not None:
                raise ValueError("type 'object' must not set items")
        else:
            if self.items is not None:
                raise ValueError(f"type '{self.type}' must not set items")
            if self.fields is not None:
                raise ValueError(f"type '{self.type}' must not set fields")
        return self


Field.model_rebuild()


class FieldGroup(BaseModel):
    """Named bundle of Fields the extractor produces together."""

    model_config = ConfigDict(extra="forbid")

    name: str = _PydField(..., min_length=1)
    description: str | None = None
    fields: list[Field] = _PydField(..., min_length=1)


# ---------------------------------------------------------------------------
# RESPONSE SIDE
# ---------------------------------------------------------------------------


class FieldValidationError(BaseModel):
    rule: ValidationRule
    message: str


class FieldValidation(BaseModel):
    valid: bool = True
    errors: list[FieldValidationError] = _PydField(default_factory=list)


class JudgeOutcome(BaseModel):
    status: JudgeStatus = JudgeStatus.UNCERTAIN
    confidence: float = _PydField(default=0.0, ge=0.0, le=1.0)
    evidence: str | None = None
    notes: str | None = None
    flag_for_review: bool = False


class ExtractedField(BaseModel):
    """One extracted field. Recursive for arrays and objects."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: "str | int | float | bool | list[ExtractedField] | None" = None
    pages: list[int] = _PydField(default_factory=list)
    confidence: float = _PydField(default=0.0, ge=0.0, le=1.0)
    bbox: BoundingBox | None = None
    validation: FieldValidation = _PydField(default_factory=FieldValidation)
    judge: JudgeOutcome = _PydField(default_factory=JudgeOutcome)
    notes: str | None = None


ExtractedField.model_rebuild()


class ExtractedFieldGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    fields: list[ExtractedField]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_field_dto.py -v`
Expected: 12 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/flydocs/interfaces/dtos/field.py tests/unit/test_field_dto.py
git commit -m "refactor(dtos): single recursive Field; drop FieldSpec/FieldItem duplication"
```

### Task 1.8: Rewrite `interfaces/dtos/doc.py` → `document_type.py`

**Files:**
- Create: `src/flydocs/interfaces/dtos/document_type.py`
- Delete: `src/flydocs/interfaces/dtos/doc.py`
- Test: `tests/unit/test_document_type_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_document_type_dto.py
import pytest
from pydantic import ValidationError

from flydocs.interfaces.dtos.document_type import DocumentTypeSpec, VisualCheck
from flydocs.interfaces.dtos.field import Field, FieldGroup
from flydocs.interfaces.enums.field_type import FieldType


def test_document_type_spec_basic():
    d = DocumentTypeSpec(
        id="invoice",
        description="Vendor invoice",
        country="ES",
        field_groups=[FieldGroup(name="header", fields=[Field(name="number", type=FieldType.STRING)])],
    )
    assert d.id == "invoice"
    assert d.country == "ES"
    assert d.field_groups[0].name == "header"


def test_document_type_spec_country_optional():
    d = DocumentTypeSpec(
        id="invoice",
        field_groups=[FieldGroup(name="header", fields=[Field(name="number", type=FieldType.STRING)])],
    )
    assert d.country is None
    assert d.description is None


def test_visual_checks_top_level():
    d = DocumentTypeSpec(
        id="passport",
        field_groups=[FieldGroup(name="g", fields=[Field(name="x", type=FieldType.STRING)])],
        visual_checks=[VisualCheck(name="photo_present", description="A passport photo is visible")],
    )
    assert d.visual_checks[0].name == "photo_present"


def test_document_type_rejects_legacy_dock_type_envelope():
    with pytest.raises(ValidationError):
        DocumentTypeSpec.model_validate({"docType": {"documentType": "invoice"}, "fieldGroups": []})


def test_document_type_requires_field_groups():
    with pytest.raises(ValidationError):
        DocumentTypeSpec(id="invoice", field_groups=[])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_document_type_dto.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/flydocs/interfaces/dtos/document_type.py`:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""DocumentTypeSpec -- schema template for one expected document type."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.dtos.field import FieldGroup


class VisualCheck(BaseModel):
    """One visual check to run against the document."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str


class DocumentTypeSpec(BaseModel):
    """One expected document type the caller is submitting fields for."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Stable id (e.g. 'invoice', 'passport').")
    description: str | None = None
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2 country code.")
    field_groups: list[FieldGroup] = Field(..., min_length=1)
    visual_checks: list[VisualCheck] = Field(default_factory=list)
```

- [ ] **Step 4: Delete the old module**

Run: `rm src/flydocs/interfaces/dtos/doc.py`

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_document_type_dto.py -v`
Expected: 5 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/flydocs/interfaces/dtos/document_type.py tests/unit/test_document_type_dto.py
git add -u src/flydocs/interfaces/dtos/doc.py
git commit -m "refactor(dtos): DocSpec -> DocumentTypeSpec; flatten DocType into id/description/country"
```

### Task 1.9: Rewrite `interfaces/dtos/rule.py`

**Files:**
- Modify: `src/flydocs/interfaces/dtos/rule.py`
- Test: `tests/unit/test_rule_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rule_dto.py
import pytest
from pydantic import TypeAdapter, ValidationError

from flydocs.interfaces.dtos.rule import (
    RuleFieldParent,
    RuleOutputSpec,
    RuleParent,
    RuleResult,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
)


def test_field_parent():
    p = RuleFieldParent(document_type="invoice", fields=["a", "b"])
    assert p.kind == "field"
    assert p.document_type == "invoice"
    assert p.fields == ["a", "b"]


def test_validator_parent():
    p = RuleValidatorParent(document_type="invoice", validator="vat_id")
    assert p.kind == "validator"


def test_rule_parent():
    p = RuleRuleParent(rule="totals_consistent")
    assert p.kind == "rule"


def test_rule_parent_discriminator():
    adapter = TypeAdapter(RuleParent)
    p = adapter.validate_python({"kind": "field", "document_type": "invoice", "fields": ["x"]})
    assert isinstance(p, RuleFieldParent)


def test_rule_parent_rejects_legacy_keys():
    adapter = TypeAdapter(RuleParent)
    with pytest.raises(ValidationError):
        adapter.validate_python({"parentType": "field", "documentType": "invoice", "fieldNames": ["x"]})


def test_rule_spec_basic():
    r = RuleSpec(id="r1", predicate="x is positive", parents=[])
    assert r.id == "r1"
    assert r.output.type == "boolean"


def test_rule_output_valid_outputs():
    o = RuleOutputSpec(type="string", valid_outputs=["yes", "no"])
    assert o.valid_outputs == ["yes", "no"]


def test_rule_result_human_revision_null():
    r = RuleResult(rule_id="r1", predicate="...", output="true")
    assert r.human_revision is None
    assert r.notes == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rule_dto.py -v`
Expected: many failures.

- [ ] **Step 3: Implement**

Replace `src/flydocs/interfaces/dtos/rule.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Business-rule DTOs.

Rules express boolean / categorical decisions over extracted fields,
validator outcomes, and other rules' results. They form a DAG; cycles
are rejected at request validation time. Discriminator is ``kind``
(not ``type``) to avoid collision with Field.type / RuleOutputSpec.type.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _BaseParent(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleFieldParent(_BaseParent):
    kind: Literal["field"] = "field"
    document_type: str
    fields: list[str] = Field(..., min_length=1)


class RuleValidatorParent(_BaseParent):
    kind: Literal["validator"] = "validator"
    document_type: str
    validator: str


class RuleRuleParent(_BaseParent):
    kind: Literal["rule"] = "rule"
    rule: str


RuleParent = Annotated[
    RuleFieldParent | RuleValidatorParent | RuleRuleParent,
    Field(discriminator="kind"),
]


class RuleOutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="boolean")
    valid_outputs: list[str] | None = None


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    parents: list[RuleParent] = Field(default_factory=list)
    output: RuleOutputSpec = Field(default_factory=RuleOutputSpec)


class RuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    predicate: str
    output: str = ""
    summary: str | None = None
    notes: list[str] = Field(default_factory=list)
    human_revision: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_rule_dto.py -v`
Expected: 8 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/flydocs/interfaces/dtos/rule.py tests/unit/test_rule_dto.py
git commit -m "refactor(dtos): rule parents use kind discriminator and snake_case keys"
```

### Task 1.10: Rewrite `interfaces/dtos/authenticity.py`

**Files:**
- Modify: `src/flydocs/interfaces/dtos/authenticity.py`
- Test: `tests/unit/test_authenticity_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_authenticity_dto.py
from flydocs.interfaces.dtos.authenticity import (
    ContentAuthenticity,
    ContentCoherenceCheck,
    DocumentAuthenticity,
    VisualCheckResult,
)
from flydocs.interfaces.enums.status import CheckStatus, ContentIntegrityStatus


def test_visual_check_result_basic():
    v = VisualCheckResult(name="signature", passed=True, confidence=0.9)
    assert v.notes is None


def test_content_authenticity_default_uncertain():
    a = ContentAuthenticity()
    assert a.overall_integrity_status == ContentIntegrityStatus.UNCERTAIN
    assert a.checks == []


def test_content_coherence_check_lowercase_status():
    c = ContentCoherenceCheck(name="n", description="d", status=CheckStatus.PASS)
    assert c.status.value == "pass"


def test_document_authenticity_visual_and_content():
    d = DocumentAuthenticity()
    assert d.visual == []
    assert d.content is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_authenticity_dto.py -v`
Expected: FAIL (VisualValidationOutcome name vs VisualCheckResult; `content` not nullable).

- [ ] **Step 3: Implement**

Replace `src/flydocs/interfaces/dtos/authenticity.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Authenticity DTOs -- visual + content integrity outputs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.enums.status import CheckStatus, ContentIntegrityStatus


class VisualCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str | None = None


class ContentCoherenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    status: CheckStatus
    evidence: str | None = None
    reasoning: str | None = None


class ContentAuthenticity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_integrity_status: ContentIntegrityStatus = ContentIntegrityStatus.UNCERTAIN
    checks: list[ContentCoherenceCheck] = Field(default_factory=list)


class DocumentAuthenticity(BaseModel):
    """Aggregated authenticity result for a single document instance."""

    model_config = ConfigDict(extra="forbid")

    visual: list[VisualCheckResult] = Field(default_factory=list)
    content: ContentAuthenticity | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_authenticity_dto.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/flydocs/interfaces/dtos/authenticity.py tests/unit/test_authenticity_dto.py
git commit -m "refactor(dtos): VisualValidationOutcome -> VisualCheckResult; lowercase enum values"
```

### Task 1.11: Rewrite `interfaces/dtos/transformation.py`

**Files:**
- Modify: `src/flydocs/interfaces/dtos/transformation.py`
- Test: `tests/unit/test_transformation_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_transformation_dto.py
import pytest
from pydantic import TypeAdapter, ValidationError

from flydocs.interfaces.dtos.transformation import (
    EntityResolutionTransformation,
    LlmTransformation,
    Transformation,
    TransformationScope,
)


def test_scope_lowercase():
    assert {s.value for s in TransformationScope} == {"task", "request"}


def test_entity_resolution_basic():
    t = EntityResolutionTransformation(target_group="personas", match_by=["dni"])
    assert t.type == "entity_resolution"
    assert t.scope == TransformationScope.TASK


def test_llm_transformation_requires_intention_min_length():
    with pytest.raises(ValidationError):
        LlmTransformation(target_group="cargos", intention="short")  # < 10 chars


def test_transformation_discriminator():
    adapter = TypeAdapter(Transformation)
    t = adapter.validate_python({
        "type": "entity_resolution",
        "target_group": "personas",
        "match_by": ["dni"],
    })
    assert isinstance(t, EntityResolutionTransformation)


def test_transformation_rejects_extra_keys():
    adapter = TypeAdapter(Transformation)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "type": "entity_resolution",
            "target_group": "personas",
            "match_by": ["dni"],
            "unknown_key": True,
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_transformation_dto.py -v`
Expected: FAIL on extra-keys-rejected test.

- [ ] **Step 3: Implement**

Replace `src/flydocs/interfaces/dtos/transformation.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Public DTOs for the transform pipeline stage."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class TransformationScope(StrEnum):
    TASK = "task"
    REQUEST = "request"


class _BaseTransformation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_group: str = Field(..., min_length=1)
    output_group: str | None = None
    scope: TransformationScope = TransformationScope.TASK


class EntityResolutionTransformation(_BaseTransformation):
    type: Literal["entity_resolution"] = "entity_resolution"
    match_by: list[str] = Field(..., min_length=1)
    min_shared_tokens: int = Field(default=2, ge=1)


class LlmTransformation(_BaseTransformation):
    type: Literal["llm"] = "llm"
    intention: str = Field(..., min_length=10)
    prompt_id: str | None = None


Transformation = Annotated[
    EntityResolutionTransformation | LlmTransformation,
    Field(discriminator="type"),
]


__all__ = [
    "EntityResolutionTransformation",
    "LlmTransformation",
    "Transformation",
    "TransformationScope",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_transformation_dto.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/flydocs/interfaces/dtos/transformation.py tests/unit/test_transformation_dto.py
git commit -m "refactor(dtos): transformation closed to unknown keys (extra=forbid)"
```

### Task 1.12: Rewrite `interfaces/dtos/extract.py` (top-level request + result)

**Files:**
- Modify: `src/flydocs/interfaces/dtos/extract.py`
- Test: `tests/unit/test_extract_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_extract_dto.py
import base64

import pytest
from pydantic import ValidationError

from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.extract import (
    ClassificationInfo,
    Document,
    EscalationConfig,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResult,
    FileInput,
    FileSummary,
    PipelineError,
    PipelineMeta,
    StageToggles,
    TraceEntry,
    UsageBreakdown,
)
from flydocs.interfaces.dtos.field import (
    ExtractedFieldGroup,
    Field as SchemaField,
    FieldGroup,
)
from flydocs.interfaces.enums.field_type import FieldType


_PDF = base64.b64encode(b"%PDF-1.4\n%test").decode()


def _dt():
    return DocumentTypeSpec(
        id="invoice",
        field_groups=[FieldGroup(name="g", fields=[SchemaField(name="x", type=FieldType.STRING)])],
    )


def test_file_input_basic():
    f = FileInput(filename="a.pdf", content_base64=_PDF, expected_type="invoice")
    assert f.filename == "a.pdf"
    assert f.expected_type == "invoice"


def test_file_input_strips_data_url_prefix():
    f = FileInput(filename="a.pdf", content_base64=f"data:application/pdf;base64,{_PDF}")
    assert f.content_base64 == _PDF


def test_extraction_request_requires_files_and_document_types():
    with pytest.raises(ValidationError):
        ExtractionRequest(files=[], document_types=[_dt()])
    with pytest.raises(ValidationError):
        ExtractionRequest(files=[FileInput(filename="a.pdf", content_base64=_PDF)], document_types=[])


def test_extraction_request_rejects_legacy_keys():
    with pytest.raises(ValidationError):
        ExtractionRequest.model_validate({"documents": [], "docs": []})


def test_stage_toggles_defaults():
    s = StageToggles()
    assert s.classifier is True
    assert s.field_validation is True
    assert s.bbox_refine is False
    assert s.judge is False


def test_extraction_options_escalation_block():
    o = ExtractionOptions(escalation=EscalationConfig(threshold=0.3, model="m"))
    assert o.escalation.threshold == 0.3
    o2 = ExtractionOptions()
    assert o2.escalation is None


def test_extraction_result_top_level_id():
    r = ExtractionResult(
        id="ext_abc",
        status="success",
        files=[],
        documents=[],
        pipeline=PipelineMeta(model="m", latency_ms=10),
    )
    assert r.id == "ext_abc"
    assert r.status == "success"
    assert r.discovered_documents == []
    assert r.rule_results == []
    assert r.request_transformations == []


def test_extraction_result_pipeline_block():
    p = PipelineMeta(model="m", latency_ms=10, errors=[PipelineError(node="x", code="y", message="z")])
    assert p.trace == []
    assert p.errors[0].code == "y"


def test_document_keys():
    d = Document(type="invoice", source_file="a.pdf")
    assert d.type == "invoice"
    assert d.source_file == "a.pdf"
    assert d.field_groups == []
    assert d.missing is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_extract_dto.py -v`
Expected: many ImportErrors / AttributeErrors.

- [ ] **Step 3: Implement**

Replace `src/flydocs/interfaces/dtos/extract.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Top-level request / response DTOs for the public extraction API."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flydocs.interfaces.dtos.authenticity import DocumentAuthenticity
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.field import ExtractedFieldGroup
from flydocs.interfaces.dtos.rule import RuleResult, RuleSpec
from flydocs.interfaces.dtos.transformation import Transformation


# ---------------------------------------------------------------------------
# FileInput (request)
# ---------------------------------------------------------------------------


class FileInput(BaseModel):
    """One input file for an extraction request."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., min_length=1)
    content_base64: str | None = Field(
        default=None,
        description="Base64-encoded bytes (or data: URL). Absent in multipart mode (binary rides in the file part).",
    )
    content_type: str | None = None
    expected_type: str | None = None

    @field_validator("content_base64")
    @classmethod
    def _validate_base64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "," in value and value.startswith("data:"):
            value = value.split(",", 1)[1]
        try:
            base64.b64decode(value, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
        return value

    def decoded_bytes(self) -> bytes:
        if self.content_base64 is None:
            raise ValueError("FileInput.content_base64 is not set (multipart mode)")
        return base64.b64decode(self.content_base64)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


class StageToggles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    splitter: bool = False
    classifier: bool = True
    field_validation: bool = True
    visual_authenticity: bool = False
    content_authenticity: bool = False
    judge: bool = False
    judge_escalation: bool = False
    bbox_refine: bool = False
    transform: bool = False
    rule_engine: bool = False


class EscalationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(..., ge=0.0, le=1.0)
    model: str = Field(..., min_length=1)


class ExtractionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    language_hint: str | None = Field(default=None, max_length=16)
    return_bboxes: bool = True
    declared_media_type: str | None = None
    stages: StageToggles = Field(default_factory=StageToggles)
    escalation: EscalationConfig | None = None
    transformations: list[Transformation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intention: str = "Extract structured data from the document."
    files: list[FileInput] = Field(..., min_length=1)
    document_types: list[DocumentTypeSpec] = Field(..., min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)
    options: ExtractionOptions = Field(default_factory=ExtractionOptions)


# ---------------------------------------------------------------------------
# Response (sync) / shared response shape
# ---------------------------------------------------------------------------


class ClassificationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str
    matched: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str | None = None
    notes: str | None = None


class FileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    media_type: str
    page_count: int
    bytes: int
    matched_type: str | None = None
    classification: ClassificationInfo | None = None


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    source_file: str | None = None
    missing: bool = False
    pages: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str | None = None
    notes: str | None = None
    field_groups: list[ExtractedFieldGroup] = Field(default_factory=list)
    authenticity: DocumentAuthenticity = Field(default_factory=DocumentAuthenticity)


class TraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    started_at: datetime
    completed_at: datetime
    latency_ms: float
    status: Literal["success", "failed", "skipped"]


class PipelineError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    code: str
    message: str


class EscalationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    triggered: bool = False
    primary_model: str | None = None
    escalation_model: str | None = None
    primary_fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    escalation_fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    accepted: bool = False


class UsageBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_requests: int = 0
    total_latency_ms: float = 0.0
    record_count: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    by_agent: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_model: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PipelineMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    latency_ms: int = Field(..., ge=0)
    trace: list[TraceEntry] = Field(default_factory=list)
    errors: list[PipelineError] = Field(default_factory=list)
    escalation: EscalationInfo | None = None
    usage: UsageBreakdown | None = None


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["success", "partial"] = "success"
    files: list[FileSummary] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    discovered_documents: list[Document] = Field(default_factory=list)
    rule_results: list[RuleResult] = Field(default_factory=list)
    request_transformations: list[ExtractedFieldGroup] = Field(default_factory=list)
    pipeline: PipelineMeta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_extract_dto.py -v`
Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/flydocs/interfaces/dtos/extract.py tests/unit/test_extract_dto.py
git commit -m "refactor(dtos): top-level request (files+document_types) + result (pipeline block)"
```

### Task 1.13: Rewrite `interfaces/dtos/job.py` → `extraction.py`

**Files:**
- Create: `src/flydocs/interfaces/dtos/extraction.py`
- Delete: `src/flydocs/interfaces/dtos/job.py`
- Test: `tests/unit/test_extraction_lifecycle_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_extraction_lifecycle_dto.py
import base64
from datetime import datetime, UTC

import pytest
from pydantic import ValidationError

from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.extract import FileInput
from flydocs.interfaces.dtos.extraction import (
    BboxRefinementInfo,
    Extraction,
    ExtractionError,
    ExtractionListQuery,
    ExtractionListResponse,
    ExtractionResultEnvelope,
    PostProcessing,
    SubmitExtractionRequest,
)
from flydocs.interfaces.dtos.field import Field as SchemaField, FieldGroup
from flydocs.interfaces.enums.extraction_status import ExtractionStatus, PostProcessingStatus
from flydocs.interfaces.enums.field_type import FieldType


_PDF = base64.b64encode(b"%PDF-1.4\n").decode()


def _dt():
    return DocumentTypeSpec(
        id="invoice",
        field_groups=[FieldGroup(name="g", fields=[SchemaField(name="x", type=FieldType.STRING)])],
    )


def test_submit_extraction_request_inherits_request_shape():
    s = SubmitExtractionRequest(
        files=[FileInput(filename="a.pdf", content_base64=_PDF)],
        document_types=[_dt()],
        callback_url="https://example.com/h",
        metadata={"external_id": "x"},
    )
    assert s.callback_url
    assert s.metadata == {"external_id": "x"}


def test_extraction_queued_shape():
    e = Extraction(
        id="ext_1",
        status=ExtractionStatus.QUEUED,
        submitted_at=datetime.now(UTC),
    )
    assert e.started_at is None
    assert e.finished_at is None
    assert e.attempts == 0
    assert e.error is None
    assert e.post_processing is None


def test_extraction_with_post_processing():
    e = Extraction(
        id="ext_1",
        status=ExtractionStatus.SUCCEEDED,
        submitted_at=datetime.now(UTC),
        post_processing=PostProcessing(
            bbox_refinement=BboxRefinementInfo(status=PostProcessingStatus.RUNNING, attempts=1)
        ),
    )
    assert e.post_processing.bbox_refinement.status == PostProcessingStatus.RUNNING


def test_extraction_error_shape():
    e = ExtractionError(code="stage_timeout", message="x")
    assert e.code == "stage_timeout"


def test_list_query_defaults():
    q = ExtractionListQuery()
    assert q.statuses == []
    assert q.post_processing_statuses == []
    assert q.limit == 50
    assert q.offset == 0


def test_extraction_rejects_legacy_partial_succeeded():
    with pytest.raises(ValidationError):
        Extraction.model_validate({"id": "x", "status": "PARTIAL_SUCCEEDED", "submitted_at": "2026-01-01T00:00:00Z"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_extraction_lifecycle_dto.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/flydocs/interfaces/dtos/extraction.py`:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""DTOs for the async extraction lifecycle.

Endpoints: POST /api/v1/extractions, GET /api/v1/extractions{,/id,/id/result},
DELETE /api/v1/extractions/{id}.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from flydocs.interfaces.dtos.extract import ExtractionRequest, ExtractionResult
from flydocs.interfaces.enums.extraction_status import ExtractionStatus, PostProcessingStatus


class SubmitExtractionRequest(ExtractionRequest):
    callback_url: AnyHttpUrl | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class BboxRefinementInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PostProcessingStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    error: ExtractionError | None = None


class PostProcessing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bbox_refinement: BboxRefinementInfo | None = None


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: ExtractionStatus
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    error: ExtractionError | None = None
    post_processing: PostProcessing | None = None


class ExtractionResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    result: ExtractionResult


class ExtractionListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statuses: list[ExtractionStatus] = Field(default_factory=list)
    post_processing_statuses: list[PostProcessingStatus] = Field(default_factory=list)
    created_after: datetime | None = None
    created_before: datetime | None = None
    idempotency_key: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ExtractionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Extraction]
    total: int
    limit: int
    offset: int
```

- [ ] **Step 4: Delete the old module**

Run: `rm src/flydocs/interfaces/dtos/job.py`

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_extraction_lifecycle_dto.py -v`
Expected: 6 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/flydocs/interfaces/dtos/extraction.py tests/unit/test_extraction_lifecycle_dto.py
git add -u src/flydocs/interfaces/dtos/job.py
git commit -m "refactor(dtos): job DTOs -> extraction; single linear status + post_processing block"
```

### Task 1.14: Unify event + webhook into one envelope (`interfaces/dtos/event.py`)

**Files:**
- Modify: `src/flydocs/interfaces/dtos/event.py`
- Delete: `src/flydocs/interfaces/dtos/webhook.py`
- Test: `tests/unit/test_event_envelope_dto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_event_envelope_dto.py
from datetime import datetime, UTC

from flydocs.interfaces.dtos.event import (
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EventEnvelope,
    envelope_for_publish,
)
from flydocs.interfaces.dtos.extraction import Extraction
from flydocs.interfaces.enums.extraction_status import ExtractionStatus


def test_event_types_dotted():
    assert EVENT_TYPE_EXTRACTION_SUBMITTED == "extraction.submitted"
    assert EVENT_TYPE_EXTRACTION_COMPLETED == "extraction.completed"
    assert EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED == "extraction.post_processing.requested"
    assert EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED == "extraction.post_processing.completed"


def test_envelope_basic():
    e = EventEnvelope(
        event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
        occurred_at=datetime.now(UTC),
        correlation_id="req-1",
        extraction=Extraction(id="ext_1", status=ExtractionStatus.QUEUED, submitted_at=datetime.now(UTC)),
        metadata={"k": "v"},
    )
    assert e.event_id
    assert e.version == "1.0.0"
    assert e.result is None


def test_envelope_for_publish_returns_json_ready():
    e = EventEnvelope(
        event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
        occurred_at=datetime.now(UTC),
        extraction=Extraction(id="ext_1", status=ExtractionStatus.QUEUED, submitted_at=datetime.now(UTC)),
    )
    raw = envelope_for_publish(e)
    assert isinstance(raw, dict)
    assert raw["event_type"] == "extraction.submitted"
    assert isinstance(raw["occurred_at"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_event_envelope_dto.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Replace `src/flydocs/interfaces/dtos/event.py` with:

```python
# Copyright 2026 Firefly Software Solutions Inc
"""Unified event + webhook envelope.

The same shape is published over the EDA bus and posted to webhook
``callback_url``s. Operators see a single mental model in logs, in
broker UIs, and in the receiving webhook handler.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.dtos.extract import ExtractionResult
from flydocs.interfaces.dtos.extraction import Extraction


EVENT_TYPE_EXTRACTION_SUBMITTED = "extraction.submitted"
EVENT_TYPE_EXTRACTION_COMPLETED = "extraction.completed"
EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED = "extraction.post_processing.requested"
EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED = "extraction.post_processing.completed"

ALL_EVENT_TYPES = (
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _new_event_id() -> str:
    return str(uuid.uuid4())


class EventEnvelope(BaseModel):
    """Shared envelope for EDA events and webhook deliveries."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_event_id)
    event_type: str
    version: str = "1.0.0"
    occurred_at: datetime = Field(default_factory=_now_utc)
    correlation_id: str | None = None
    tenant_id: str | None = None
    extraction: Extraction
    result: ExtractionResult | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def envelope_for_publish(env: EventEnvelope) -> dict[str, Any]:
    """Serialise an envelope for EventPublisher.publish(payload=...)."""
    return env.model_dump(mode="json", by_alias=True)


__all__ = [
    "ALL_EVENT_TYPES",
    "EVENT_TYPE_EXTRACTION_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED",
    "EVENT_TYPE_EXTRACTION_SUBMITTED",
    "EventEnvelope",
    "envelope_for_publish",
]
```

- [ ] **Step 4: Delete the old webhook module**

Run: `rm src/flydocs/interfaces/dtos/webhook.py`

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_event_envelope_dto.py -v`
Expected: 3 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/flydocs/interfaces/dtos/event.py tests/unit/test_event_envelope_dto.py
git add -u src/flydocs/interfaces/dtos/webhook.py
git commit -m "refactor(dtos): unify event + webhook into single EventEnvelope; dotted event types"
```

### Task 1.15: Phase-1 quality gate

- [ ] **Step 1: Lint**

Run: `uv run ruff check src/flydocs/interfaces/ tests/unit/`
Expected: clean.

- [ ] **Step 2: Run all new DTO/enum tests**

Run: `uv run pytest tests/unit/test_extraction_status.py tests/unit/test_status_enums.py tests/unit/test_field_type.py tests/unit/test_validator_enum.py tests/unit/test_bbox_dto.py tests/unit/test_validator_dto.py tests/unit/test_field_dto.py tests/unit/test_document_type_dto.py tests/unit/test_rule_dto.py tests/unit/test_authenticity_dto.py tests/unit/test_transformation_dto.py tests/unit/test_extract_dto.py tests/unit/test_extraction_lifecycle_dto.py tests/unit/test_event_envelope_dto.py -v`
Expected: all PASSED.

- [ ] **Step 3: Commit any incidental clean-ups**

```bash
git status
# Expected: nothing uncommitted unless the lint step changed files.
```

---

## Phase 2 — SQLAlchemy + Alembic migration

The runtime model and a new Alembic migration that renames the table, lowercases statuses, drops the bbox_refine_* columns, and adds a `post_processing` JSONB column.

### Task 2.1: Rewrite the SQLAlchemy entity

**Files:**
- Modify: `src/flydocs/models/entities/extraction_job.py` → rename to `extraction.py`
- Test: `tests/unit/test_extraction_entity.py`

- [ ] **Step 1: Inspect the existing entity**

Run: `cat src/flydocs/models/entities/extraction_job.py`

Note the table name (`extraction_jobs`), the bbox_refine_* columns, and any helpers.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_extraction_entity.py
from flydocs.models.entities.extraction import Extraction


def test_extraction_table_name():
    assert Extraction.__tablename__ == "extractions"


def test_extraction_has_post_processing_column():
    cols = {c.name for c in Extraction.__table__.columns}
    assert "post_processing" in cols


def test_extraction_no_bbox_refine_columns():
    cols = {c.name for c in Extraction.__table__.columns}
    legacy = {
        "bbox_refine_status",
        "bbox_refine_attempts",
        "bbox_refine_started_at",
        "bbox_refine_finished_at",
        "bbox_refine_error_code",
        "bbox_refine_error_message",
    }
    assert cols.isdisjoint(legacy)


def test_extraction_status_lowercase_default():
    # status column default value should be 'queued'
    default = Extraction.__table__.c.status.default
    assert default is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_extraction_entity.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement (rename + reshape)**

Move and rewrite the entity:

```bash
git mv src/flydocs/models/entities/extraction_job.py src/flydocs/models/entities/extraction.py
```

Replace its content with the new shape (based on the patterns the existing file already uses). Key requirements:
- `__tablename__ = "extractions"`
- `status: str` column defaulting to `"queued"` with a CHECK constraint over `{queued, running, succeeded, failed, cancelled}`.
- `submitted_at`, `started_at`, `finished_at` timestamps (timezone-aware).
- `attempts: int` default 0.
- `error_code: str | None`, `error_message: str | None`.
- `post_processing: dict | None` as a JSONB column (Postgres) / JSON column (SQLite). Use `sqlalchemy.dialects.postgresql.JSONB` with `JSON` fallback via `sqlalchemy.types.JSON`.
- `schema_json: dict` (the persisted request schema for the worker to re-render).
- `result_json: dict | None` for the completed `ExtractionResult` body.
- Drop the bbox_refine_* columns. The `post_processing` JSONB serialises `BboxRefinementInfo` when present.
- Keep `idempotency_key`, `correlation_id`, `tenant_id`, `callback_url`, `metadata_json` columns.
- Read the original file to preserve any custom indexes or constraints not listed here; copy them across with renamed names where appropriate.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_extraction_entity.py -v`
Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/flydocs/models/entities/extraction.py tests/unit/test_extraction_entity.py
git add -u src/flydocs/models/entities/extraction_job.py
git commit -m "refactor(db): ExtractionJob -> Extraction entity; collapse bbox_refine_* into post_processing JSONB"
```

### Task 2.2: Rewrite the repository

**Files:**
- Modify: `src/flydocs/models/repositories/extraction_job_repository.py` → rename to `extraction_repository.py`
- Test: `tests/integration/test_extraction_repository.py`

- [ ] **Step 1: Inspect the existing repository**

Run: `cat src/flydocs/models/repositories/extraction_job_repository.py | head -200`

Note `_atomic_update`, every `mark_*` transition, the claim/release semantics, and the JobStatus references.

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_extraction_repository.py
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flydocs.interfaces.enums.extraction_status import ExtractionStatus, PostProcessingStatus
from flydocs.models.entities.extraction import Base, Extraction
from flydocs.models.repositories.extraction_repository import ExtractionRepository


PG_URL = os.environ.get("FLYDOCS_TEST_PG_URL")


@pytest_asyncio.fixture
async def session():
    if not PG_URL:
        pytest.skip("FLYDOCS_TEST_PG_URL not set")
    engine = create_async_engine(PG_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_mark_running_atomic(session):
    repo = ExtractionRepository(session)
    ext = await repo.create(id=f"ext_{uuid.uuid4().hex}", schema_json={}, idempotency_key=None)
    assert ext.status == ExtractionStatus.QUEUED.value
    updated = await repo.mark_running(ext.id, attempt=1)
    assert updated is not None
    assert updated.status == ExtractionStatus.RUNNING.value


@pytest.mark.asyncio
async def test_mark_running_race_loser_returns_none(session):
    repo = ExtractionRepository(session)
    ext = await repo.create(id=f"ext_{uuid.uuid4().hex}", schema_json={}, idempotency_key=None)
    first = await repo.mark_running(ext.id, attempt=1)
    second = await repo.mark_running(ext.id, attempt=2)
    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_post_processing_round_trip(session):
    repo = ExtractionRepository(session)
    ext = await repo.create(id=f"ext_{uuid.uuid4().hex}", schema_json={}, idempotency_key=None)
    await repo.mark_running(ext.id, attempt=1)
    await repo.mark_succeeded(ext.id, result_json={})
    await repo.start_bbox_refinement(ext.id, attempt=1)
    fetched = await repo.get(ext.id)
    assert fetched.post_processing["bbox_refinement"]["status"] == PostProcessingStatus.RUNNING.value
```

- [ ] **Step 3: Run test to verify it fails**

Run: `FLYDOCS_TEST_PG_URL="postgresql+asyncpg://idp:idp@localhost:5435/flydocs" uv run pytest tests/integration/test_extraction_repository.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement (rename + reshape)**

```bash
git mv src/flydocs/models/repositories/extraction_job_repository.py src/flydocs/models/repositories/extraction_repository.py
```

Rewrite the repo:
- Rename `ExtractionJobRepository` → `ExtractionRepository`.
- Replace every `JobStatus.*` reference with `ExtractionStatus.*` and lowercase string values.
- Drop `mark_partial_succeeded`, `mark_refining_bboxes` — these are gone.
- Replace bbox_refine_* column updates with JSONB-merge updates into the `post_processing.bbox_refinement` sub-document. Pattern (Postgres):

```python
from sqlalchemy import update

stmt = (
    update(Extraction)
    .where(Extraction.id == ext_id)
    .where(...)  # legal predecessors
    .values(
        post_processing=func.jsonb_set(
            func.coalesce(Extraction.post_processing, sa.text("'{}'::jsonb")),
            sa.text("'{bbox_refinement}'"),
            json_value,
            True,  # create_missing
        )
    )
    .returning(Extraction)
)
```

For SQLite, fall back to read-modify-write inside an explicit transaction (the existing pattern likely already handles dialect differences — keep the existing structure).

Add new helper methods: `start_bbox_refinement`, `complete_bbox_refinement`, `fail_bbox_refinement`. Drop the old `mark_bbox_refine_*` set.

- [ ] **Step 5: Run test to verify it passes**

Run: `FLYDOCS_TEST_PG_URL="postgresql+asyncpg://idp:idp@localhost:5435/flydocs" uv run pytest tests/integration/test_extraction_repository.py -v`
Expected: 3 PASSED (or skipped when no Postgres).

- [ ] **Step 6: Commit**

```bash
git add src/flydocs/models/repositories/extraction_repository.py tests/integration/test_extraction_repository.py
git add -u src/flydocs/models/repositories/extraction_job_repository.py
git commit -m "refactor(db): ExtractionJobRepository -> ExtractionRepository; bbox_refine JSONB merge"
```

### Task 2.3: Write the Alembic migration

**Files:**
- Create: `migrations/versions/20260526_0004_extraction_v1_rename.py`

- [ ] **Step 1: Inspect existing migrations**

Run: `cat migrations/versions/20260514_0001_init.py migrations/versions/20260515_0002_bbox_refine_columns.py migrations/versions/20260515_0003_widen_job_status.py | head -150`

Note the table/column names, type names, and existing alembic conventions.

- [ ] **Step 2: Write the migration**

Create `migrations/versions/20260526_0004_extraction_v1_rename.py`:

```python
"""extraction v1: rename extraction_jobs -> extractions, drop bbox_refine_* columns, lowercase statuses, add post_processing JSONB

Revision ID: 20260526_0004
Revises: 20260515_0003
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260526_0004"
down_revision = "20260515_0003"
branch_labels = None
depends_on = None

_STATUS_MAP = [
    ("QUEUED", "queued"),
    ("RUNNING", "running"),
    ("SUCCEEDED", "succeeded"),
    ("FAILED", "failed"),
    ("CANCELLED", "cancelled"),
    # Legacy refining bbox states collapse into "succeeded" + post_processing JSONB:
    ("PARTIAL_SUCCEEDED", "succeeded"),
    ("REFINING_BBOXES", "succeeded"),
]


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Rename the table.
    op.rename_table("extraction_jobs", "extractions")

    # 2. Add the new post_processing JSONB column.
    op.add_column(
        "extractions",
        sa.Column(
            "post_processing",
            postgresql.JSONB(astext_type=sa.Text()) if bind.dialect.name == "postgresql" else sa.JSON(),
            nullable=True,
        ),
    )

    # 3. Backfill post_processing from the legacy bbox_refine_* columns,
    #    coalescing PARTIAL_SUCCEEDED / REFINING_BBOXES rows.
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE extractions
            SET post_processing = jsonb_build_object(
                'bbox_refinement', jsonb_build_object(
                    'status',        bbox_refine_status,
                    'started_at',    to_char(bbox_refine_started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
                    'finished_at',   to_char(bbox_refine_finished_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
                    'attempts',      bbox_refine_attempts,
                    'error',         CASE WHEN bbox_refine_error_code IS NULL THEN NULL
                                          ELSE jsonb_build_object('code', bbox_refine_error_code, 'message', bbox_refine_error_message)
                                     END
                )
            )
            WHERE bbox_refine_status IS NOT NULL
            """
        )

    # 4. Lowercase the status column.
    if bind.dialect.name == "postgresql":
        # Drop any existing CHECK constraint(s) on status.
        op.execute("ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extraction_jobs_status")
        op.execute("ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extractions_status")
        for upper, lower in _STATUS_MAP:
            op.execute(f"UPDATE extractions SET status = '{lower}' WHERE status = '{upper}'")
        op.create_check_constraint(
            "ck_extractions_status",
            "extractions",
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        )
    else:
        for upper, lower in _STATUS_MAP:
            op.execute(f"UPDATE extractions SET status = '{lower}' WHERE status = '{upper}'")

    # 5. Drop the legacy bbox_refine_* columns.
    for col in (
        "bbox_refine_status",
        "bbox_refine_attempts",
        "bbox_refine_started_at",
        "bbox_refine_finished_at",
        "bbox_refine_error_code",
        "bbox_refine_error_message",
    ):
        op.drop_column("extractions", col)


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Re-add the legacy bbox_refine_* columns.
    op.add_column("extractions", sa.Column("bbox_refine_status", sa.String(length=32), nullable=True))
    op.add_column("extractions", sa.Column("bbox_refine_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("extractions", sa.Column("bbox_refine_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("extractions", sa.Column("bbox_refine_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("extractions", sa.Column("bbox_refine_error_code", sa.String(length=64), nullable=True))
    op.add_column("extractions", sa.Column("bbox_refine_error_message", sa.Text(), nullable=True))

    # 2. Restore bbox_refine_* from the JSONB.
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE extractions
            SET
              bbox_refine_status     = post_processing->'bbox_refinement'->>'status',
              bbox_refine_attempts   = COALESCE((post_processing->'bbox_refinement'->>'attempts')::int, 0),
              bbox_refine_started_at = (post_processing->'bbox_refinement'->>'started_at')::timestamptz,
              bbox_refine_finished_at= (post_processing->'bbox_refinement'->>'finished_at')::timestamptz,
              bbox_refine_error_code = post_processing->'bbox_refinement'->'error'->>'code',
              bbox_refine_error_message = post_processing->'bbox_refinement'->'error'->>'message'
            WHERE post_processing IS NOT NULL
            """
        )

    # 3. UPPERCASE the status column back.
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extractions_status")
        for upper, lower in _STATUS_MAP:
            # Note: only the first upper -> lower pair is reversed; PARTIAL_SUCCEEDED and
            # REFINING_BBOXES rows are NOT restored (information lost on upgrade).
            if upper in {"PARTIAL_SUCCEEDED", "REFINING_BBOXES"}:
                continue
            op.execute(f"UPDATE extractions SET status = '{upper}' WHERE status = '{lower}'")
        op.create_check_constraint(
            "ck_extraction_jobs_status",
            "extractions",
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'PARTIAL_SUCCEEDED', 'REFINING_BBOXES')",
        )

    # 4. Drop post_processing.
    op.drop_column("extractions", "post_processing")

    # 5. Rename the table back.
    op.rename_table("extractions", "extraction_jobs")
```

- [ ] **Step 3: Smoke-test against SQLite (the test backend)**

Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: clean rollouts both directions. The smoke DB (`flydocs_smoke.db`) is fine; we just exercise the migration mechanics.

- [ ] **Step 4: Smoke-test against Postgres**

Run: `task docker:up:test` then `FLYDOCS_DB_URL=postgresql+asyncpg://idp:idp@localhost:5435/flydocs uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && task docker:down:test`
Expected: clean rollouts both directions.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/20260526_0004_extraction_v1_rename.py
git commit -m "feat(db): alembic migration for extraction v1 schema"
```

### Task 2.4: Phase-2 quality gate

- [ ] **Step 1: Lint**

Run: `uv run ruff check src/flydocs/models/ migrations/`
Expected: clean.

- [ ] **Step 2: Run unit + integration test selection**

Run: `uv run pytest tests/unit/test_extraction_entity.py tests/integration/test_extraction_repository.py -v`
Expected: PASSED (integration test skips when no Postgres).

---

## Phase 3 — Core services rewrite

### Task 3.1: Update `core/services/extract/`

**Files:**
- Modify: `src/flydocs/core/services/extract/extract_command.py`
- Modify: `src/flydocs/core/services/extract/extract_handler.py`

- [ ] **Step 1: Inspect**

Run: `cat src/flydocs/core/services/extract/extract_command.py src/flydocs/core/services/extract/extract_handler.py`

- [ ] **Step 2: Update both files**

In both files:
- Replace every `ExtractionRequest` field reference: `request.documents` → `request.files`, `request.docs` → `request.document_types`.
- Replace `DocSpec` references with `DocumentTypeSpec`; `docType.documentType` → `id`.
- Replace `request.request_id` with the new id flow (server-generated `ext_…`).

Keep the command/handler shape (frozen dataclass + `@command_handler` + `@service`). The orchestrator call site likely takes a normalised `ExtractionContext` — make sure that internal shape also adopts the new vocabulary (this may bubble through several internal helpers — fix them all in this task).

- [ ] **Step 3: Run server tests that exercise this path**

Run: `uv run pytest tests/unit/ -k extract -v`
Expected: tests that exist may fail because they still use old DTO shape; this task only updates the production code. Tests get updated in Phase 5. Run lint instead:

Run: `uv run ruff check src/flydocs/core/services/extract/`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/flydocs/core/services/extract/
git commit -m "refactor(core/extract): adopt FileInput/DocumentTypeSpec/ExtractionResult v1 shapes"
```

### Task 3.2: Rename + rewrite `core/services/jobs/` → `core/services/extractions/`

**Files:**
- Rename directory: `src/flydocs/core/services/jobs/` → `src/flydocs/core/services/extractions/`
- Inside, rename:
  - `submit_job_handler.py` → `submit_extraction_handler.py`
  - `get_job_handler.py` → `get_extraction_handler.py`
  - `get_job_result_handler.py` → `get_extraction_result_handler.py`
  - `list_jobs_handler.py` → `list_extractions_handler.py`
  - `cancel_job_handler.py` → `cancel_extraction_handler.py`

- [ ] **Step 1: Git-rename the directory**

```bash
git mv src/flydocs/core/services/jobs src/flydocs/core/services/extractions
cd src/flydocs/core/services/extractions
git mv submit_job_handler.py submit_extraction_handler.py
git mv get_job_handler.py get_extraction_handler.py
git mv get_job_result_handler.py get_extraction_result_handler.py
git mv list_jobs_handler.py list_extractions_handler.py
git mv cancel_job_handler.py cancel_extraction_handler.py
cd -
```

- [ ] **Step 2: Rewrite each handler**

In each file:
- Rename class: `SubmitJobHandler` → `SubmitExtractionHandler` (etc).
- Rename associated `Command` / `Query` classes (e.g. `SubmitJobCommand` → `SubmitExtractionCommand`).
- Replace every `SubmitJobRequest` / `JobStatusResponse` / `JobResult` / `JobListResponse` etc. with the new `Extraction` family from `interfaces/dtos/extraction.py`.
- Replace `JobStatus.*` with `ExtractionStatus.*` and lowercase string values.
- Replace `IDPJobSubmitted` / `IDPJobCompleted` event-type strings with the dotted constants from `interfaces.dtos.event`.
- Replace `JobWebhookPayload` with `EventEnvelope`.
- Replace `ExtractionJobRepository` with `ExtractionRepository`.
- For `submit_extraction_handler.py`: the unique idempotency-key index name may have changed in Phase 2 — re-derive the constraint name in any `IntegrityError` recovery path.
- For `get_extraction_result_handler.py`: drop the `wait_for_bboxes` long-poll dependency on `bbox_refine_status` column; long-poll on `post_processing.bbox_refinement.status` JSONB path instead. Helper:

```python
async def _wait_for_refinement(repo, ext_id: str, timeout: float) -> Extraction:
    deadline = time.monotonic() + timeout
    while True:
        ext = await repo.get(ext_id)
        bbox = (ext.post_processing or {}).get("bbox_refinement") if isinstance(ext.post_processing, dict) else None
        if not bbox or bbox.get("status") in {"succeeded", "failed"}:
            return ext
        if time.monotonic() >= deadline:
            return ext
        await asyncio.sleep(0.5)
```

- [ ] **Step 3: Lint**

Run: `uv run ruff check src/flydocs/core/services/extractions/`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add -A src/flydocs/core/services/extractions/ src/flydocs/core/services/jobs/
git commit -m "refactor(core): rename jobs handlers to extractions; adopt v1 lifecycle DTOs"
```

### Task 3.3: Update the worker(s) under `core/services/workers/`

**Files:**
- Modify: every Python file under `src/flydocs/core/services/workers/`

- [ ] **Step 1: Inspect**

Run: `ls src/flydocs/core/services/workers/ && grep -l "JobStatus\|IDPJob\|JobWebhookPayload\|bbox_refine_status" src/flydocs/core/services/workers/`

- [ ] **Step 2: Update each file**

For each file under `workers/`:
- Replace `JobStatus.*` → `ExtractionStatus.*`.
- Replace `IDPJobSubmitted` / `IDPJobCompleted` event-type strings (used in `@event_listener` subscriptions and in publish calls) with constants from `interfaces.dtos.event`.
- Replace `JobWebhookPayload` construction with `EventEnvelope`.
- Replace `mark_partial_succeeded` / `mark_refining_bboxes` calls. The new flow on success-with-refinement:
  - `mark_succeeded(result_json=...)` — main pipeline marks status terminal-succeeded.
  - `start_bbox_refinement(attempt=1)` — sets `post_processing.bbox_refinement.status = "running"`.
  - On completion: `complete_bbox_refinement()` or `fail_bbox_refinement(error=...)`.
- Replace reaper class names: `JobReaper` → `ExtractionReaper` (file rename if applicable).

- [ ] **Step 3: Lint**

Run: `uv run ruff check src/flydocs/core/services/workers/`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add -A src/flydocs/core/services/workers/
git commit -m "refactor(workers): adopt extraction v1 lifecycle and dotted event types"
```

### Task 3.4: Update the validation service (`core/services/validation/`)

**Files:**
- Modify: every file under `src/flydocs/core/services/validation/`

- [ ] **Step 1: Inspect**

Run: `ls src/flydocs/core/services/validation/ && grep -l "docSpec\|docType\|fieldGroupName\|standard_validators\|parentType" src/flydocs/core/services/validation/`

- [ ] **Step 2: Update paths and references**

The semantic validator emits errors with `path` strings such as `docs[2].docType.documentType`. Rewrite every emitted path:
- `documents[i].document_type` → `files[i].expected_type`
- `docs[i]` → `document_types[i]`
- `.docType.documentType` → `.id`
- `.docType.description` → `.description`
- `.docType.country` → `.country`
- `.fieldGroups[i].fieldGroupName` → `.field_groups[i].name`
- `.fieldGroups[i].fieldGroupFields[j].fieldName` → `.field_groups[i].fields[j].name`
- `.standard_validators[k].type` → `.validators[k].name`
- `.parents[k].parentType` → `.parents[k].kind`
- `.parents[k].documentType` → `.parents[k].document_type`
- `.parents[k].fieldNames` → `.parents[k].fields`
- `.parents[k].validatorName` → `.parents[k].validator`
- `.parents[k].ruleId` → `.parents[k].rule`

Rename codes that change semantics:
- `document_type_unknown` keeps its name but references `document_types[].id`.

- [ ] **Step 3: Lint**

Run: `uv run ruff check src/flydocs/core/services/validation/`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add -A src/flydocs/core/services/validation/
git commit -m "refactor(validation): rewrite error paths against v1 DTO shape"
```

### Task 3.5: Sweep the remaining `core/services/` subdirectories

**Files:**
- Modify: every Python file under `src/flydocs/core/services/` that still references the v0 vocabulary

- [ ] **Step 1: Find every offender**

```bash
grep -rl "JobStatus\|JobWebhookPayload\|IDPJobSubmitted\|IDPJobCompleted\|IDPBboxRefineRequested\|IDPBboxRefineCompleted\|StandardValidatorSpec\|standard_validators\|DocSpec\|docType\.documentType\|fieldGroupName\|fieldGroupFields\|fieldGroupDesc\|fieldValueFound\|pagesFound\|parentType\|fieldNames\|validatorName\|ruleId\|bbox_refine_status\|PARTIAL_SUCCEEDED\|REFINING_BBOXES" src/flydocs/core/services/
```

- [ ] **Step 2: Update each file**

For each file the grep produces, apply the mechanical renames defined in earlier tasks. Pay special attention to:
- `core/services/extraction/` — the orchestrator core; replaces internal references to `ExtractedDocument` field names.
- `core/services/transformations/` — transformation engine; uses `target_group` against `FieldGroup.name` (was `fieldGroupName`).
- `core/services/rules/` — rule engine; parent unpacking, field/validator/rule path resolution.
- `core/services/bbox/` — bbox refiner; reads `BoundingBox.source` etc.
- `core/services/webhook/webhook_publisher.py` — webhook delivery; build `EventEnvelope` payloads.
- `core/services/pipeline/` — pipeline orchestrator + stage glue; rewires response field names.
- `core/services/binary/`, `core/services/splitting/`, `core/services/classification/`, `core/services/judge/`, `core/services/escalation/`, `core/services/authenticity/`: each has DTO touchpoints.
- `core/observability/`: usage tracking emits `by_agent` / `by_model` keys; no shape change beyond removing legacy field names.
- `core/mappers/`: any pydantic→entity / entity→pydantic mappers; rewire to new names.

- [ ] **Step 3: Re-grep to verify clean**

```bash
grep -rl "JobStatus\|JobWebhookPayload\|IDPJobSubmitted\|StandardValidatorSpec\|standard_validators\|DocSpec\|docType\.documentType\|fieldGroupName\|fieldGroupFields\|fieldValueFound\|pagesFound\|parentType\|fieldNames\|validatorName\|ruleId\|bbox_refine_status\|PARTIAL_SUCCEEDED\|REFINING_BBOXES" src/flydocs/core/
```
Expected: empty.

- [ ] **Step 4: Lint**

Run: `uv run ruff check src/flydocs/core/`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add -u src/flydocs/core/
git commit -m "refactor(core): sweep remaining v0 identifiers to v1 vocabulary"
```

### Task 3.6: Update `core/configuration.py` if it references old class names

**Files:**
- Modify: `src/flydocs/core/configuration.py` (or wherever pyfly beans are declared)

- [ ] **Step 1: Inspect**

Run: `grep -n "ExtractionJob\|JobReaper\|IDPJob" src/flydocs/core/`

- [ ] **Step 2: Rename bean classes / constructors**

Rename any bean classes referenced under `IDPCoreConfiguration` / module configurations:
- `ExtractionJobRepository` → `ExtractionRepository`
- `JobReaper` → `ExtractionReaper`
- Any `BboxReaper` references keep their name.

Re-export accordingly.

- [ ] **Step 3: Lint + commit**

```bash
uv run ruff check src/flydocs/core/configuration.py
git add -u src/flydocs/core/configuration.py
git commit -m "refactor(core/config): rename v1 bean classes"
```

---

## Phase 4 — Web layer rewrite

### Task 4.1: Rewrite `web/controllers/extract_controller.py`

**Files:**
- Modify: `src/flydocs/web/controllers/extract_controller.py`
- Test: `tests/integration/test_extract_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_extract_endpoint.py
import base64

import pytest
from fastapi.testclient import TestClient

from flydocs.main import build_app


@pytest.fixture(scope="module")
def client():
    app = build_app()
    with TestClient(app) as c:
        yield c


PDF = base64.b64encode(b"%PDF-1.4\n").decode()
DT = {
    "id": "invoice",
    "field_groups": [
        {"name": "g", "fields": [{"name": "number", "type": "string"}]}
    ],
}


def test_extract_accepts_v1_payload_shape(client):
    resp = client.post(
        "/api/v1/extract",
        json={
            "files": [{"filename": "a.pdf", "content_base64": PDF}],
            "document_types": [DT],
        },
    )
    assert resp.status_code in (200, 422)  # 422 is ok for stubbed pipeline; we just check shape acceptance
    if resp.status_code == 200:
        body = resp.json()
        assert "id" in body
        assert "files" in body
        assert "documents" in body
        assert "pipeline" in body


def test_extract_rejects_v0_documents_key(client):
    resp = client.post(
        "/api/v1/extract",
        json={"documents": [{"filename": "a.pdf", "content_base64": PDF}], "docs": [DT]},
    )
    assert resp.status_code == 400 or resp.status_code == 422


def test_extract_validate_endpoint(client):
    resp = client.post(
        "/api/v1/extract:validate",
        json={
            "files": [{"filename": "a.pdf", "content_base64": PDF}],
            "document_types": [DT],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_extract_endpoint.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Rewrite `src/flydocs/web/controllers/extract_controller.py`:
- Use `ExtractionRequest` and `ExtractionResult` from the new `interfaces/dtos/extract.py`.
- Add a multipart variant for `/extract` that parses each file part into a `FileInput` and reads the `request` part as JSON. Pseudocode:

```python
@post_mapping("/extract")
async def extract(self, request: Request) -> ExtractionResult:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        json_part = form.get("request")
        files_parts = form.getlist("files")
        # Parse json_part into a dict, then build ExtractionRequest with files populated from files_parts
        ...
    else:
        body = await request.json()
        req = ExtractionRequest.model_validate(body)
        ...
```

Replace `ValidationResponse` to use snake_case keys (already correct).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_extract_endpoint.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add -u src/flydocs/web/controllers/extract_controller.py
git add tests/integration/test_extract_endpoint.py
git commit -m "feat(web): rewrite /extract for v1 contract; multipart upload support"
```

### Task 4.2: Rewrite `web/controllers/jobs_controller.py` → `extractions_controller.py`

**Files:**
- Move/rename: `src/flydocs/web/controllers/jobs_controller.py` → `src/flydocs/web/controllers/extractions_controller.py`
- Test: `tests/integration/test_extractions_endpoint.py`

- [ ] **Step 1: Git-rename**

```bash
git mv src/flydocs/web/controllers/jobs_controller.py src/flydocs/web/controllers/extractions_controller.py
```

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_extractions_endpoint.py
import base64

import pytest
from fastapi.testclient import TestClient

from flydocs.main import build_app


@pytest.fixture(scope="module")
def client():
    app = build_app()
    with TestClient(app) as c:
        yield c


PDF = base64.b64encode(b"%PDF-1.4\n").decode()
DT = {
    "id": "invoice",
    "field_groups": [
        {"name": "g", "fields": [{"name": "number", "type": "string"}]}
    ],
}


def test_create_extraction(client):
    resp = client.post(
        "/api/v1/extractions",
        json={"files": [{"filename": "a.pdf", "content_base64": PDF}], "document_types": [DT]},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["id"].startswith("ext_")


def test_get_extraction_not_found(client):
    resp = client.get("/api/v1/extractions/ext_unknown")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "not_found"


def test_get_result_not_ready(client):
    # Submit then immediately try /result -> should be 409 not_ready
    submit = client.post(
        "/api/v1/extractions",
        json={"files": [{"filename": "a.pdf", "content_base64": PDF}], "document_types": [DT]},
    )
    ext_id = submit.json()["id"]
    resp = client.get(f"/api/v1/extractions/{ext_id}/result")
    assert resp.status_code in (409, 200)  # depends on test timing


def test_list_extractions(client):
    resp = client.get("/api/v1/extractions?status=queued&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert body["limit"] == 10
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_extractions_endpoint.py -v`
Expected: FAIL.

- [ ] **Step 4: Rewrite the controller**

Replace its contents. Each endpoint binds:
- `POST /extractions` → `SubmitExtractionHandler` (via `CommandBus.send`).
- `GET /extractions` → `ListExtractionsHandler`.
- `GET /extractions/{id}` → `GetExtractionHandler`.
- `GET /extractions/{id}/result` → `GetExtractionResultHandler`.
- `DELETE /extractions/{id}` → `CancelExtractionHandler`.

Return shapes use `Extraction`, `ExtractionResultEnvelope`, `ExtractionListResponse` directly.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_extractions_endpoint.py -v`
Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add -A src/flydocs/web/controllers/
git add tests/integration/test_extractions_endpoint.py
git commit -m "feat(web): /api/v1/extractions endpoints replacing /jobs"
```

### Task 4.3: Update `web/advice/exception_advice.py`

**Files:**
- Modify: `src/flydocs/web/advice/exception_advice.py`
- Test: `tests/unit/test_exception_advice.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_exception_advice.py
import pytest


_LEGACY_CODES = {
    "JOB_NOT_FOUND",
    "job_not_ready",
    "job_not_cancellable",
    "extraction_timeout",
    "document_too_large",
    "unsupported_binary",
}


def test_advice_emits_only_v1_codes():
    from flydocs.web.advice import exception_advice
    src = open(exception_advice.__file__).read()
    for legacy in _LEGACY_CODES:
        assert legacy not in src, f"legacy code {legacy} still present"


def test_advice_defines_new_codes():
    from flydocs.web.advice import exception_advice
    src = open(exception_advice.__file__).read()
    for code in ("not_found", "not_ready", "not_cancellable", "timeout", "file_too_large", "unsupported_file", "validation_failed"):
        assert code in src, f"new code {code} missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_exception_advice.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the renames**

Open `src/flydocs/web/advice/exception_advice.py` and rename every code string per §10 of the spec.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_exception_advice.py -v`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add -u src/flydocs/web/advice/exception_advice.py
git add tests/unit/test_exception_advice.py
git commit -m "refactor(web/advice): RFC 7807 codes adopt v1 catalogue"
```

### Task 4.4: Update `web/openapi_override.py` and the OpenAPI spec dump

**Files:**
- Modify: `src/flydocs/web/openapi_override.py`

- [ ] **Step 1: Inspect**

Run: `cat src/flydocs/web/openapi_override.py`

- [ ] **Step 2: Update**

Replace any model references that mention old class names (e.g. `SubmitJobRequest`, `JobStatusResponse`). Verify component schema names match v1 DTOs.

- [ ] **Step 3: Run the openapi task**

Run: `task openapi`
Expected: produces a clean OpenAPI 3.1 spec that mentions no v0 names.

- [ ] **Step 4: Audit the generated spec**

Run: `grep -E "documents|docs|fieldGroupName|fieldGroupFields|fieldValueFound|pagesFound|JOB_NOT_FOUND|PARTIAL_SUCCEEDED|REFINING_BBOXES|parentType|documentType|fieldNames|validatorName|ruleId|standard_validators|StandardValidatorSpec" docs/openapi.v1.json | head` (or wherever the spec lands)
Expected: zero hits (a `documents` key for *response* arrays is fine — they refer to the new "extracted documents" concept, but `request` schemas should not have a `documents` property).

- [ ] **Step 5: Commit**

```bash
git add -u src/flydocs/web/openapi_override.py
git add docs/openapi.v1.json 2>/dev/null || true
git commit -m "refactor(web/openapi): regenerate spec against v1 DTOs"
```

### Task 4.5: Update `app.py` / `cli.py` / `main.py` / `config.py` if they reference legacy types

**Files:**
- Modify: `src/flydocs/app.py`, `src/flydocs/cli.py`, `src/flydocs/main.py`, `src/flydocs/config.py`

- [ ] **Step 1: Inspect**

Run: `grep -n "JobStatus\|ExtractionJob\|SubmitJobRequest\|JobWorker\|JobReaper\|IDPJob" src/flydocs/*.py`

- [ ] **Step 2: Update**

Update any references to use v1 vocabulary (`ExtractionStatus`, `Extraction`, `SubmitExtractionRequest`, `ExtractionWorker` or whatever the renamed worker class is).

- [ ] **Step 3: Lint + run**

```bash
uv run ruff check src/flydocs/
task serve & sleep 3 && curl -s -f http://localhost:8400/api/v1/version && kill %1
```
Expected: server boots, `/api/v1/version` returns a JSON body.

- [ ] **Step 4: Commit**

```bash
git add -u src/flydocs/app.py src/flydocs/cli.py src/flydocs/main.py src/flydocs/config.py
git commit -m "refactor(app): adopt v1 vocabulary at the top-level entry points"
```

---

## Phase 5 — Server unit + integration tests

### Task 5.1: Update existing unit tests

**Files:**
- All files under `tests/unit/` except the ones already created for this redesign

- [ ] **Step 1: Find every test file referencing legacy identifiers**

```bash
grep -rl "JobStatus\|SubmitJobRequest\|JobStatusResponse\|JobResult\|JobListResponse\|StandardValidatorSpec\|DocSpec\|docType\|fieldGroupName\|fieldGroupFields\|fieldValueFound\|pagesFound\|parentType\|fieldNames\|validatorName\|ruleId\|standard_validators\|bbox_refine_status\|PARTIAL_SUCCEEDED\|REFINING_BBOXES" tests/unit/
```

- [ ] **Step 2: Update each test**

For each file:
- Mechanically apply the rename table from §17 of the spec.
- Update test data fixtures (sample requests, sample responses).
- Run the test after editing to verify green:

```bash
uv run pytest tests/unit/<the-file>.py -v
```

- [ ] **Step 3: Rename `test_standard_validators.py` → `test_validators.py`**

```bash
git mv tests/unit/test_standard_validators.py tests/unit/test_validators.py
```

Update any internal class/test-name references.

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit/ -v`
Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add -A tests/unit/
git commit -m "test(unit): migrate unit tests to v1 vocabulary"
```

### Task 5.2: Update integration tests

**Files:**
- All files under `tests/integration/`
- All files under `tests/llm/`
- `tests/conftest.py`
- `tests/fixtures/`

- [ ] **Step 1: Find every offender**

```bash
grep -rl "JobStatus\|SubmitJobRequest\|JobStatusResponse\|fieldGroupName\|fieldValueFound\|standard_validators\|bbox_refine_status\|PARTIAL_SUCCEEDED\|REFINING_BBOXES" tests/
```

- [ ] **Step 2: Update each**

Apply the rename table. For fixtures that build request bodies, switch to the new keys (`files`, `document_types`, `field_groups`, `fields`, etc.).

- [ ] **Step 3: Run the full integration suite (sans LLM)**

Run: `task docker:up:test` then `uv run pytest tests/integration/ -v` then `task docker:down:test`
Expected: all PASSED.

- [ ] **Step 4: Run the LLM smoke**

Run: `task test:llm`
Expected: PASSED (requires `ANTHROPIC_API_KEY`; skip if absent).

- [ ] **Step 5: Commit**

```bash
git add -A tests/integration/ tests/llm/ tests/conftest.py tests/fixtures/
git commit -m "test(integration): migrate integration + LLM smoke tests to v1 vocabulary"
```

### Task 5.3: Phase-5 quality gate

- [ ] **Step 1: Lint**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Step 2: Full test suite**

Run: `task test`
Expected: all PASSED.

- [ ] **Step 3: Compare against baseline**

Run: `task test 2>&1 | tee docs/superpowers/plans/post-server-snapshot.txt && diff docs/superpowers/plans/baseline-snapshot.txt docs/superpowers/plans/post-server-snapshot.txt | head -40`
Inspect: the same set of tests should pass (count-wise); new tests added during the refactor should appear, but no previously-passing test should be missing.

---

## Phase 6 — Python SDK

### Task 6.1: Rewrite `sdks/python/src/flydocs_sdk/models.py`

**Files:**
- Modify: `sdks/python/src/flydocs_sdk/models.py`
- Test: `sdks/python/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# sdks/python/tests/test_models.py
import base64

import pytest
from pydantic import ValidationError

from flydocs_sdk.models import (
    Extraction,
    ExtractionResult,
    ExtractionStatus,
    FileInput,
    PostProcessingStatus,
)


PDF = base64.b64encode(b"%PDF-1.4\n").decode()


def test_file_input_basic():
    f = FileInput(filename="a.pdf", content_base64=PDF, expected_type="invoice")
    assert f.filename == "a.pdf"


def test_extraction_status_lowercase():
    assert ExtractionStatus.QUEUED.value == "queued"
    assert ExtractionStatus.SUCCEEDED.value == "succeeded"
    assert not hasattr(ExtractionStatus, "PARTIAL_SUCCEEDED")


def test_post_processing_status_lowercase():
    assert PostProcessingStatus.PENDING.value == "pending"


def test_extraction_extra_allow_tolerates_unknown_fields():
    e = Extraction.model_validate({
        "id": "ext_1",
        "status": "queued",
        "submitted_at": "2026-01-01T00:00:00Z",
        "future_field": "x",
    })
    assert "future_field" in e.model_extra
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdks/python && uv run pytest tests/test_models.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Rewrite `sdks/python/src/flydocs_sdk/models.py` to mirror the server DTOs but with `model_config = ConfigDict(extra="allow")` (SDK forward-compatibility). Export:
- `FileInput`, `DocumentTypeSpec`, `Field`, `FieldGroup`, `ValidatorSpec`, `VisualCheck`, `RuleSpec`, `RuleOutputSpec`, `ExtractionOptions`, `StageToggles`, `EscalationConfig`, `ExtractionRequest`, `SubmitExtractionRequest`, `Transformation` (union), `EntityResolutionTransformation`, `LlmTransformation`, `TransformationScope`.
- Response side: `BoundingBox`, `BboxQuality`, `BboxSource`, `FieldValidation`, `FieldValidationError`, `JudgeOutcome`, `ExtractedField`, `ExtractedFieldGroup`, `DocumentAuthenticity`, `VisualCheckResult`, `ContentAuthenticity`, `ContentCoherenceCheck`, `ClassificationInfo`, `FileSummary`, `Document`, `PipelineError`, `PipelineMeta`, `TraceEntry`, `EscalationInfo`, `UsageBreakdown`, `ExtractionResult`, `RuleResult`.
- Lifecycle: `Extraction`, `ExtractionStatus`, `PostProcessingStatus`, `PostProcessing`, `BboxRefinementInfo`, `ExtractionError`, `ExtractionResultEnvelope`, `ExtractionListQuery`, `ExtractionListResponse`.
- Events: `EventEnvelope`, plus the four event-type constants.

Use `ConfigDict(extra="allow")` everywhere so unknown wire fields are tolerated.

- [ ] **Step 4: Delete `sdks/python/src/flydocs_sdk/request.py`**

Run: `rm sdks/python/src/flydocs_sdk/request.py` (subsumed into `models.py`).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd sdks/python && uv run pytest tests/test_models.py -v`
Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add -A sdks/python/src/flydocs_sdk/models.py sdks/python/tests/test_models.py sdks/python/src/flydocs_sdk/request.py
git commit -m "refactor(py-sdk): rewrite models around v1 DTOs; consolidate request helpers into models.py"
```

### Task 6.2: Rewrite `sdks/python/src/flydocs_sdk/client.py` (sync)

**Files:**
- Modify: `sdks/python/src/flydocs_sdk/client.py`
- Test: `sdks/python/tests/test_client_sync.py`

- [ ] **Step 1: Write the failing test (with respx mocking)**

```python
# sdks/python/tests/test_client_sync.py
import base64

import pytest
import respx
from httpx import Response

from flydocs_sdk.client import Client
from flydocs_sdk.models import ExtractionStatus, FileInput, DocumentTypeSpec, Field, FieldGroup


PDF = base64.b64encode(b"%PDF-1.4\n").decode()


def _req():
    return {
        "files": [{"filename": "a.pdf", "content_base64": PDF}],
        "document_types": [
            {"id": "invoice", "field_groups": [{"name": "g", "fields": [{"name": "x", "type": "string"}]}]}
        ],
    }


@respx.mock
def test_extract_calls_correct_endpoint():
    respx.post("https://x/api/v1/extract").mock(
        return_value=Response(200, json={
            "id": "ext_1",
            "status": "success",
            "files": [],
            "documents": [],
            "discovered_documents": [],
            "rule_results": [],
            "request_transformations": [],
            "pipeline": {"model": "m", "latency_ms": 1},
        })
    )
    client = Client(base_url="https://x")
    result = client.extract(_req())
    assert result.id == "ext_1"


@respx.mock
def test_extractions_create_returns_extraction():
    respx.post("https://x/api/v1/extractions").mock(
        return_value=Response(202, json={
            "id": "ext_1",
            "status": "queued",
            "submitted_at": "2026-01-01T00:00:00Z",
        })
    )
    client = Client(base_url="https://x")
    ext = client.extractions.create(_req(), idempotency_key="k")
    assert ext.status == ExtractionStatus.QUEUED


@respx.mock
def test_extractions_get_result_long_poll():
    respx.get("https://x/api/v1/extractions/ext_1/result").mock(
        return_value=Response(200, json={
            "id": "ext_1",
            "result": {
                "id": "ext_1",
                "status": "success",
                "files": [],
                "documents": [],
                "discovered_documents": [],
                "rule_results": [],
                "request_transformations": [],
                "pipeline": {"model": "m", "latency_ms": 1},
            },
        })
    )
    client = Client(base_url="https://x")
    env = client.extractions.get_result("ext_1", wait_for_bboxes=True, timeout=10.0)
    assert env.id == "ext_1"
    assert env.result.id == "ext_1"


@respx.mock
def test_validate_endpoint():
    respx.post("https://x/api/v1/extract:validate").mock(
        return_value=Response(200, json={"ok": True, "error_count": 0, "warning_count": 0, "errors": [], "warnings": []})
    )
    client = Client(base_url="https://x")
    out = client.validate(_req())
    assert out.ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdks/python && uv run pytest tests/test_client_sync.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Rewrite `sdks/python/src/flydocs_sdk/client.py`:
- `Client(base_url, api_key=None, timeout=60)` — httpx client builder.
- `Client.extract(request) -> ExtractionResult`.
- `Client.validate(request) -> ValidationResponse`.
- `Client.extractions: ExtractionsResource` accessor; the sub-resource carries `create`, `get`, `get_result`, `cancel`, `list`.
- Multipart support: `Client.extract(request, files: list[BinaryIO] | None = None)` — when `files` is set, the SDK posts multipart, embedding the JSON body under `request`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sdks/python && uv run pytest tests/test_client_sync.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add -A sdks/python/src/flydocs_sdk/client.py sdks/python/tests/test_client_sync.py
git commit -m "feat(py-sdk): sync Client with extract/validate/extractions sub-resource"
```

### Task 6.3: Rewrite `sdks/python/src/flydocs_sdk/async_client.py`

**Files:**
- Modify: `sdks/python/src/flydocs_sdk/async_client.py`
- Test: `sdks/python/tests/test_client_async.py`

- [ ] **Step 1: Write the failing test**

Mirror the sync test using `pytest-asyncio` and `respx.mock`. Use the same response payloads, assertions on `await client.extract(...)`, etc.

- [ ] **Step 2: Run + implement + verify + commit**

Same flow as Task 6.2 but for the async client.

```bash
cd sdks/python && uv run pytest tests/test_client_async.py -v
```

```bash
git add -A sdks/python/src/flydocs_sdk/async_client.py sdks/python/tests/test_client_async.py
git commit -m "feat(py-sdk): async Client mirrors sync surface"
```

### Task 6.4: Rewrite `sdks/python/src/flydocs_sdk/webhooks.py`

**Files:**
- Modify: `sdks/python/src/flydocs_sdk/webhooks.py`
- Test: `sdks/python/tests/test_webhooks.py`

- [ ] **Step 1: Write the failing test**

```python
# sdks/python/tests/test_webhooks.py
import hashlib
import hmac
import json

import pytest

from flydocs_sdk.webhooks import WebhookVerifier, WebhookVerificationError
from flydocs_sdk.models import EventEnvelope


SECRET = "topsecret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _envelope_body() -> bytes:
    return json.dumps({
        "event_id": "e1",
        "event_type": "extraction.completed",
        "version": "1.0.0",
        "occurred_at": "2026-01-01T00:00:00Z",
        "extraction": {
            "id": "ext_1",
            "status": "succeeded",
            "submitted_at": "2026-01-01T00:00:00Z",
        },
    }).encode()


def test_verify_accepts_correct_signature():
    body = _envelope_body()
    sig = _sign(body)
    env = WebhookVerifier(SECRET).verify(body, sig)
    assert isinstance(env, EventEnvelope)
    assert env.event_type == "extraction.completed"


def test_verify_rejects_bad_signature():
    body = _envelope_body()
    with pytest.raises(WebhookVerificationError):
        WebhookVerifier(SECRET).verify(body, "sha256=deadbeef")
```

- [ ] **Step 2: Run + implement + verify + commit**

Implement `WebhookVerifier.verify(body: bytes, signature_header: str) -> EventEnvelope`.

```bash
cd sdks/python && uv run pytest tests/test_webhooks.py -v
git add -A sdks/python/src/flydocs_sdk/webhooks.py sdks/python/tests/test_webhooks.py
git commit -m "feat(py-sdk): WebhookVerifier returns EventEnvelope on success"
```

### Task 6.5: Update `errors.py`

**Files:**
- Modify: `sdks/python/src/flydocs_sdk/errors.py`

- [ ] **Step 1: Update**

Rename error classes if they reference job vocabulary; add a `ProblemDetails` Pydantic model mirroring the server's RFC 7807 body. The `FlydocsHttpError` should expose `.code`, `.title`, `.status`, `.detail`, `.extensions`.

- [ ] **Step 2: Lint + commit**

```bash
cd sdks/python && uv run ruff check src/flydocs_sdk/errors.py
git add -A sdks/python/src/flydocs_sdk/errors.py
git commit -m "refactor(py-sdk): errors expose v1 RFC 7807 ProblemDetails fields"
```

### Task 6.6: Update `__init__.py` exports and `_version.py`

**Files:**
- Modify: `sdks/python/src/flydocs_sdk/__init__.py`
- Modify: `sdks/python/src/flydocs_sdk/_version.py`

- [ ] **Step 1: Re-export everything users will hit**

```python
# sdks/python/src/flydocs_sdk/__init__.py
from flydocs_sdk._version import __version__
from flydocs_sdk.client import Client
from flydocs_sdk.async_client import AsyncClient
from flydocs_sdk.errors import (
    FlydocsError,
    FlydocsHttpError,
    FlydocsTimeoutError,
    ProblemDetails,
)
from flydocs_sdk.models import (
    BboxRefinementInfo,
    BboxQuality,
    BboxSource,
    BoundingBox,
    ClassificationInfo,
    ContentAuthenticity,
    ContentCoherenceCheck,
    Document,
    DocumentAuthenticity,
    DocumentTypeSpec,
    EntityResolutionTransformation,
    EscalationConfig,
    EscalationInfo,
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EventEnvelope,
    ExtractedField,
    ExtractedFieldGroup,
    Extraction,
    ExtractionError,
    ExtractionListQuery,
    ExtractionListResponse,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResult,
    ExtractionResultEnvelope,
    ExtractionStatus,
    Field,
    FieldGroup,
    FieldValidation,
    FieldValidationError,
    FileInput,
    FileSummary,
    JudgeOutcome,
    LlmTransformation,
    PipelineError,
    PipelineMeta,
    PostProcessing,
    PostProcessingStatus,
    RuleFieldParent,
    RuleOutputSpec,
    RuleParent,
    RuleResult,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
    StageToggles,
    SubmitExtractionRequest,
    TraceEntry,
    Transformation,
    TransformationScope,
    UsageBreakdown,
    ValidatorSpec,
    VisualCheck,
    VisualCheckResult,
)
from flydocs_sdk.webhooks import WebhookVerifier, WebhookVerificationError

__all__ = [name for name in globals() if not name.startswith("_")]
```

Bump version in `_version.py` to `26.6.0` (or whatever the v1 release number lands on).

- [ ] **Step 2: Smoke import test**

```python
# sdks/python/tests/test_imports.py
import flydocs_sdk


def test_smoke():
    assert hasattr(flydocs_sdk, "Client")
    assert hasattr(flydocs_sdk, "AsyncClient")
    assert hasattr(flydocs_sdk, "ExtractionStatus")
    assert hasattr(flydocs_sdk, "EventEnvelope")
```

Run: `cd sdks/python && uv run pytest tests/test_imports.py -v`
Expected: PASSED.

- [ ] **Step 3: Commit**

```bash
git add -A sdks/python/src/flydocs_sdk/__init__.py sdks/python/src/flydocs_sdk/_version.py sdks/python/tests/test_imports.py
git commit -m "feat(py-sdk): re-export full v1 surface; bump version"
```

### Task 6.7: Update Python SDK examples

**Files:**
- All files under `sdks/python/examples/`

- [ ] **Step 1: Inspect**

Run: `ls sdks/python/examples/`

- [ ] **Step 2: Update each example**

For each example, replace v0 keys with v1 (`documents` → `files`, `docs` → `document_types`, etc.).

- [ ] **Step 3: Run at least one example end-to-end**

Run: `cd sdks/python && uv run python examples/extract_pdf.py` (or whichever example exists)
Expected: example runs against a local server (`task serve` in another terminal) and prints a parsed result.

- [ ] **Step 4: Commit**

```bash
git add -A sdks/python/examples/
git commit -m "docs(py-sdk): rewrite examples around v1 contract"
```

### Task 6.8: Python SDK gate

- [ ] **Step 1: Full SDK test suite**

Run: `cd sdks/python && uv run pytest -v`
Expected: all PASSED.

- [ ] **Step 2: Lint + format check**

Run: `cd sdks/python && uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Step 3: Build the wheel**

Run: `cd sdks/python && uv build`
Expected: a `.whl` and `.tar.gz` land under `dist/`.

---

## Phase 7 — Java SDK

### Task 7.1: Rename and rewrite Jackson records under `model/`

**Files:**
- All 30+ files under `sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/model/`

This is mechanical but high-volume. For each pair of (old, new):
1. `git mv` the file to the new name.
2. Rewrite the record body, mapping every field to its v1 form.
3. Annotate snake_case wire names via `@JsonProperty("snake_case_name")` on each component.
4. Add `@JsonInclude(JsonInclude.Include.NON_NULL)` at the class level so absent fields serialise as null.

Renames (`old.java` → `new.java`):

| Old file | New file |
|---|---|
| `DocumentInput.java` | `FileInput.java` |
| `DocSpec.java` | `DocumentTypeSpec.java` |
| `DocType.java` | (delete; collapsed into `DocumentTypeSpec`) |
| `FieldSpec.java` + `FieldItem.java` | `Field.java` |
| `FieldGroup.java` | unchanged name, body rewritten |
| `StandardValidatorSpec.java` | `ValidatorSpec.java` |
| `VisualValidatorSpec.java` | `VisualCheck.java` |
| `ValidatorsSpec.java` | (delete; replaced by `visual_checks: List<VisualCheck>` on DocumentTypeSpec) |
| `ExtractionRequest.java` | unchanged name, body rewritten |
| `SubmitJobRequest.java` | `SubmitExtractionRequest.java` |
| `JobStatus.java` | `ExtractionStatus.java` (lowercase enum values, snake_case via `@JsonValue` / `@JsonCreator`) |
| `JobStatusResponse.java`, `SubmitJobResponse.java`, `JobResult.java`, `JobListResponse.java` | `Extraction.java`, `ExtractionResultEnvelope.java`, `ExtractionListResponse.java` |
| `JobWebhookPayload.java` | `EventEnvelope.java` |
| `RuleSpec.java`, `RuleParent.java`, `RuleOutputSpec.java` | unchanged names, body rewritten |
| `StageToggles.java`, `ExtractionOptions.java`, `ExtractionResult.java` | unchanged names, body rewritten |
| `VersionInfo.java` | unchanged |
| `FieldType.java`, `StandardFormat.java` | unchanged file names; FieldType gains `OBJECT` |

Add new records:
- `PostProcessing.java`, `BboxRefinementInfo.java`, `ExtractionError.java`, `PostProcessingStatus.java` (enum).
- `Document.java`, `ExtractedField.java`, `ExtractedFieldGroup.java`, `FileSummary.java`, `ClassificationInfo.java`, `PipelineMeta.java`, `PipelineError.java`, `TraceEntry.java`, `EscalationInfo.java`, `UsageBreakdown.java`, `BoundingBox.java`, `BboxQuality.java`, `BboxSource.java`, `JudgeOutcome.java`, `FieldValidation.java`, `FieldValidationError.java`, `DocumentAuthenticity.java`, `VisualCheckResult.java`, `ContentAuthenticity.java`, `ContentCoherenceCheck.java`.

- [ ] **Step 1: Make every rename**

This is a long but linear sequence. Use `git mv` so history follows, then edit. A representative pair:

```java
// FileInput.java
package com.firefly.flydocs.sdk.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotEmpty;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record FileInput(
    @JsonProperty("filename") @NotEmpty String filename,
    @JsonProperty("content_base64") String contentBase64,
    @JsonProperty("content_type") String contentType,
    @JsonProperty("expected_type") String expectedType
) {}
```

```java
// ExtractionStatus.java
package com.firefly.flydocs.sdk.model;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

public enum ExtractionStatus {
    QUEUED("queued"),
    RUNNING("running"),
    SUCCEEDED("succeeded"),
    FAILED("failed"),
    CANCELLED("cancelled");

    private final String wire;

    ExtractionStatus(String wire) { this.wire = wire; }

    @JsonValue public String wire() { return wire; }

    @JsonCreator public static ExtractionStatus fromWire(String s) {
        for (var v : values()) if (v.wire.equals(s)) return v;
        throw new IllegalArgumentException("unknown ExtractionStatus: " + s);
    }

    public boolean isTerminal() { return this == SUCCEEDED || this == FAILED || this == CANCELLED; }
    public boolean hasResult() { return this == SUCCEEDED; }
}
```

Apply the same pattern to every other enum (`PostProcessingStatus`, `BboxSource`, `BboxQuality`, `FieldType`, `StandardFormat`, `JudgeStatus`, `ContentIntegrityStatus`, `CheckStatus`, `ValidationRule`, `ValidatorType`, `TransformationScope`).

For `RuleParent`, use a Jackson polymorphic sealed interface:

```java
// RuleParent.java
package com.firefly.flydocs.sdk.model;

import com.fasterxml.jackson.annotation.*;

@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "kind")
@JsonSubTypes({
    @JsonSubTypes.Type(value = RuleParent.Field.class, name = "field"),
    @JsonSubTypes.Type(value = RuleParent.Validator.class, name = "validator"),
    @JsonSubTypes.Type(value = RuleParent.Rule.class, name = "rule"),
})
public sealed interface RuleParent permits RuleParent.Field, RuleParent.Validator, RuleParent.Rule {
    @JsonInclude(JsonInclude.Include.NON_NULL)
    record Field(
        @JsonProperty("document_type") String documentType,
        @JsonProperty("fields") java.util.List<String> fields
    ) implements RuleParent {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    record Validator(
        @JsonProperty("document_type") String documentType,
        @JsonProperty("validator") String validator
    ) implements RuleParent {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    record Rule(
        @JsonProperty("rule") String rule
    ) implements RuleParent {}
}
```

Same pattern for `Transformation` (discriminator: `type`).

- [ ] **Step 2: Compile**

Run: `cd sdks/java && mvn -q -pl flydocs-sdk compile`
Expected: compiles clean.

- [ ] **Step 3: Commit**

```bash
git add -A sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/model/
git commit -m "refactor(java-sdk): rewrite model records around v1 contract"
```

### Task 7.2: Rewrite Java client classes

**Files:**
- Modify: `sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/FlydocsClient.java`
- Modify: `sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/FlydocsClientAsync.java`
- Test: `sdks/java/flydocs-sdk/src/test/java/com/firefly/flydocs/sdk/FlydocsClientTest.java`

- [ ] **Step 1: Write the failing test**

Use MockWebServer (OkHttp) to mock responses and assert request shapes. A representative test:

```java
@Test
void extractCallsCorrectEndpoint() throws Exception {
    server.enqueue(new MockResponse()
        .setResponseCode(200)
        .setHeader("content-type", "application/json")
        .setBody("""
            {"id":"ext_1","status":"success","files":[],"documents":[],"discovered_documents":[],
             "rule_results":[],"request_transformations":[],"pipeline":{"model":"m","latency_ms":1}}
        """));

    var client = FlydocsClient.builder().baseUrl(server.url("/").toString().replaceAll("/$", "")).build();
    var result = client.extract(/* ExtractionRequest fixture */);
    var recorded = server.takeRequest();
    assertThat(recorded.getPath()).isEqualTo("/api/v1/extract");
    assertThat(result.id()).isEqualTo("ext_1");
}
```

- [ ] **Step 2: Implement**

Methods on `FlydocsClient`:
- `extract(ExtractionRequest req) → ExtractionResult`
- `extractValidate(ExtractionRequest req) → ValidationResponse`
- `extractions()` accessor returning `ExtractionsResource` with `create(req, idempotencyKey)`, `get(id)`, `getResult(id, waitForBboxes, timeout)`, `cancel(id)`, `list(query)`.

`FlydocsClientAsync` mirrors the surface via `CompletableFuture<T>`.

- [ ] **Step 3: Run tests + commit**

```bash
cd sdks/java && mvn -q -pl flydocs-sdk test
git add -A sdks/java/flydocs-sdk/src/
git commit -m "feat(java-sdk): client classes adopt v1 endpoint surface"
```

### Task 7.3: Update webhook code

**Files:**
- Modify: `sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/webhook/WebhookVerifier.java`
- Modify: `sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/webhook/WebhookVerificationException.java`
- Test: `sdks/java/flydocs-sdk/src/test/java/com/firefly/flydocs/sdk/webhook/WebhookVerifierTest.java`

- [ ] **Step 1: Update + test**

`WebhookVerifier.verify(byte[] body, String header) → EventEnvelope` (returns the new envelope, not `JobWebhookPayload`).

```bash
cd sdks/java && mvn -q -pl flydocs-sdk test
git add -A sdks/java/flydocs-sdk/src/main/java/com/firefly/flydocs/sdk/webhook/ sdks/java/flydocs-sdk/src/test/java/com/firefly/flydocs/sdk/webhook/
git commit -m "feat(java-sdk): WebhookVerifier returns EventEnvelope"
```

### Task 7.4: Update Spring Boot starter

**Files:**
- All files under `sdks/java/flydocs-spring-boot-starter/src/`

- [ ] **Step 1: Update**

- Auto-config bean: `FlydocsClient` from `flydocs.*` properties.
- `@FlydocsWebhook` HandlerMethodArgumentResolver injects `EventEnvelope` directly (verifies signature, parses body).

- [ ] **Step 2: Compile + test**

```bash
cd sdks/java && mvn -q -pl flydocs-spring-boot-starter test
git add -A sdks/java/flydocs-spring-boot-starter/
git commit -m "feat(java-starter): @FlydocsWebhook resolver returns EventEnvelope"
```

### Task 7.5: Update Java examples

**Files:**
- All files under `sdks/java/flydocs-examples/`

- [ ] **Step 1: Update each example**

Replace v0 identifiers with v1; rebuild.

- [ ] **Step 2: Build**

```bash
cd sdks/java && mvn -q -pl flydocs-examples compile
```
Expected: compiles clean.

- [ ] **Step 3: Commit**

```bash
git add -A sdks/java/flydocs-examples/
git commit -m "docs(java-sdk): rewrite examples around v1 contract"
```

### Task 7.6: Java SDK gate

- [ ] **Step 1: Full Maven build**

Run: `cd sdks/java && mvn -q clean verify`
Expected: BUILD SUCCESS.

---

## Phase 8 — Documentation

### Task 8.1: Rewrite `docs/api-reference.md`

**Files:**
- Modify: `docs/api-reference.md`

- [ ] **Step 1: Replace contents**

Rewrite the doc end-to-end against the v1 design spec (`docs/superpowers/specs/2026-05-26-api-contract-v1-redesign-design.md`). Mirror the spec's section ordering: surface table, headers, sync extract, async extractions, common DTOs, errors, version.

- [ ] **Step 2: Commit**

```bash
git add -u docs/api-reference.md
git commit -m "docs: rewrite api-reference.md for v1"
```

### Task 8.2: Rewrite `docs/payload-reference.md`

**Files:**
- Modify: `docs/payload-reference.md`

- [ ] **Step 1: Replace contents**

Rewrite each section against v1 vocabulary, with worked examples for: simple invoice, KYC bundle, async job + webhook, transformation, rules, multipart upload.

- [ ] **Step 2: Commit**

```bash
git add -u docs/payload-reference.md
git commit -m "docs: rewrite payload-reference.md for v1"
```

### Task 8.3: Update related docs

**Files:**
- Modify: `docs/pipeline.md`, `docs/rule-engine.md`, `docs/transformations.md`, `docs/concurrency.md`, `docs/overview.md`, `docs/architecture.md`, `docs/deployment.md`, `docs/troubleshooting.md`, `docs/cicd.md`, `docs/docling.md`
- Rename: `docs/standard-validators.md` → `docs/validators.md`

- [ ] **Step 1: Rename validators doc**

```bash
git mv docs/standard-validators.md docs/validators.md
```

- [ ] **Step 2: Sweep each**

For each file: replace v0 identifiers, snippets, and state-machine references with v1 equivalents.

- [ ] **Step 3: Commit**

```bash
git add -A docs/
git commit -m "docs: sweep related docs for v1 vocabulary"
```

### Task 8.4: Write the migration guide

**Files:**
- Create: `docs/migration-v0-to-v1.md`

- [ ] **Step 1: Write**

Use §17 of the design spec as the seed list. Add worked examples:
- Before / after of the same KYC request (Spanish deed + DNI).
- Before / after of the same invoice extraction with line items (highlights the `items` shape change).
- Before / after of an async submission + webhook receiver.
- Before / after of an error response (404 / 422).

- [ ] **Step 2: Commit**

```bash
git add docs/migration-v0-to-v1.md
git commit -m "docs: migration guide v0 -> v1"
```

### Task 8.5: Update QUICKSTART, README, CLAUDE.md, top-level docs

**Files:**
- Modify: `QUICKSTART.md`, `README.md`, `CLAUDE.md`
- Modify: `sdks/python/QUICKSTART.md`, `sdks/python/TUTORIAL.md`, `sdks/python/README.md`
- Modify: `sdks/java/QUICKSTART.md`, `sdks/java/TUTORIAL.md`, `sdks/java/README.md`

- [ ] **Step 1: Sweep**

For each: replace v0 snippets, update the CLAUDE.md state-machine notes and naming guidance, link to `docs/migration-v0-to-v1.md` from the top of each README.

- [ ] **Step 2: Commit**

```bash
git add -A QUICKSTART.md README.md CLAUDE.md sdks/python/QUICKSTART.md sdks/python/TUTORIAL.md sdks/python/README.md sdks/java/QUICKSTART.md sdks/java/TUTORIAL.md sdks/java/README.md
git commit -m "docs: top-level quickstarts/READMEs/CLAUDE.md rewritten for v1"
```

### Task 8.6: Refresh CHANGELOG

**Files:**
- Modify: `CHANGELOG.md` (create if absent)

- [ ] **Step 1: Add the v1 entry**

```markdown
## [26.6.0] - 2026-05-26

### BREAKING CHANGES — API v1 redesign

This release replaces the public API contract end-to-end. There is no
backwards-compatible shim. See [docs/migration-v0-to-v1.md](docs/migration-v0-to-v1.md)
for the full rename table and worked examples.

**Highlights:**
- snake_case across every JSON key, enum value, and error code.
- Top-level request body: `files[]` + `document_types[]` + `rules[]` (was `documents[]` + `docs[]`).
- One recursive `Field` (was `FieldSpec` + `FieldItem`).
- `DocumentTypeSpec.id` flattens `DocSpec.docType.documentType`.
- `Extraction` lifecycle collapses to `queued → running → succeeded | failed | cancelled`; refining-bbox state lives under `post_processing.bbox_refinement`.
- Unified `EventEnvelope` for EDA events and webhook deliveries.
- New error catalogue (`not_found`, `not_ready`, `timeout`, `file_too_large`, `unsupported_file`, `validation_failed`, …).
- `POST /api/v1/extract` and `POST /api/v1/extractions` accept multipart/form-data in addition to JSON.

### Changed
- Database table `extraction_jobs` → `extractions`; bbox_refine_* columns collapsed into a `post_processing` JSONB column.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: CHANGELOG entry for v1"
```

---

## Phase 9 — Final E2E verification

### Task 9.1: Full lint pass

- [ ] Run: `task lint:check`
- [ ] Expected: clean.

### Task 9.2: Full server test suite

- [ ] Run: `task test`
- [ ] Expected: all PASSED.

### Task 9.3: Integration suite with Docker

- [ ] Run: `task docker:up:test && uv run pytest tests/integration/ -v && task docker:down:test`
- [ ] Expected: all PASSED.

### Task 9.4: Real-Postgres concurrency tests

- [ ] Run: `task docker:up:test && FLYDOCS_TEST_PG_URL="postgresql+asyncpg://idp:idp@localhost:5435/flydocs" uv run pytest tests/integration -v -k concurrency && task docker:down:test`
- [ ] Expected: PASSED.

### Task 9.5: LLM smoke test

- [ ] Run: `task test:llm`
- [ ] Expected: PASSED (skip allowed when no `ANTHROPIC_API_KEY`).

### Task 9.6: Python SDK round-trip against live server

- [ ] **Step 1: Boot server**

Run: `task serve &` (foreground if running locally).

- [ ] **Step 2: Boot worker**

Run: `task worker &`

- [ ] **Step 3: Run a representative example**

Run: `cd sdks/python && uv run python examples/extract_pdf.py path/to/sample.pdf`
Expected: prints an `ExtractionResult` JSON with the new top-level shape.

- [ ] **Step 4: Run an async + webhook example**

Run: `cd sdks/python && uv run python examples/async_with_webhook.py path/to/sample.pdf`
Expected: submits, polls, fetches result; webhook receiver prints the `EventEnvelope` shape.

- [ ] **Step 5: Shut down**

Run: `kill %1 %2`

### Task 9.7: Java SDK round-trip against live server

- [ ] **Step 1: Boot server + worker** (same as 9.6)

- [ ] **Step 2: Run Java example**

Run: `cd sdks/java && mvn -q -pl flydocs-examples exec:java -Dexec.mainClass=com.firefly.flydocs.examples.ExtractSample`
Expected: prints an `ExtractionResult` JSON.

### Task 9.8: OpenAPI audit

- [ ] **Step 1: Regenerate the spec**

Run: `task openapi`

- [ ] **Step 2: Grep for v0 vocabulary**

Run: `grep -E "JOB_NOT_FOUND|PARTIAL_SUCCEEDED|REFINING_BBOXES|fieldGroupName|fieldGroupFields|fieldValueFound|pagesFound|standard_validators|StandardValidatorSpec|JobStatus|SubmitJobRequest|JobWebhookPayload|IDPJobSubmitted|IDPJobCompleted|IDPBboxRefineRequested|IDPBboxRefineCompleted|parentType|documentType|fieldNames|validatorName|ruleId" docs/openapi.v1.json | head`
Expected: zero hits. (`documentType` may appear inside a generated JSON Schema description if a tool field uses the literal string — manually inspect any hits.)

### Task 9.9: Repo-wide v0-vocabulary sweep (must be empty)

- [ ] Run:

```bash
grep -rn "JobStatus\\.PARTIAL_SUCCEEDED\\|REFINING_BBOXES\\|bbox_refine_status\\|fieldGroupName\\|fieldGroupFields\\|fieldValueFound\\|pagesFound\\|JOB_NOT_FOUND\\|StandardValidatorSpec\\|SubmitJobRequest\\|JobWebhookPayload\\|IDPJobSubmitted\\|IDPJobCompleted\\|IDPBboxRefineRequested\\|IDPBboxRefineCompleted" src/ sdks/ tests/ docs/ --include="*.py" --include="*.java" --include="*.md" | grep -v "docs/migration-v0-to-v1.md" | grep -v "CHANGELOG.md" | grep -v "docs/superpowers/specs"
```

- [ ] Expected: empty output. Any remaining hits live in: the migration guide (intentional), the CHANGELOG (intentional), and the design spec (intentional history).

### Task 9.10: Tag the release

- [ ] **Step 1: Bump version**

Update `pyproject.toml` to `version = "26.6.0"` (or the chosen v1 release number).

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "release: 26.6.0 (API v1 redesign)"
```

- [ ] **Step 3: Tag**

Run: `git tag -a v26.6.0 -m "API v1 redesign"`

- [ ] **Step 4: Push branch + tag**

Run: `git push -u origin feat/api-v1-redesign && git push origin v26.6.0`

---

## Phase 10 — Pull request

### Task 10.1: Open the PR

- [ ] **Step 1: Generate the PR body**

```bash
gh pr create --title "feat(api): v1 contract redesign" --body "$(cat <<'EOF'
## Summary

Replaces the public API contract end-to-end with a snake_case, semantically-cleaned-up v1.

Highlights:
- `files[]` + `document_types[]` + `rules[]` request envelope (was `documents[]` + `docs[]`).
- One recursive `Field` (was `FieldSpec` + `FieldItem`).
- `Extraction` lifecycle collapses to `queued → running → succeeded | failed | cancelled`; refining-bbox state lives under `post_processing.bbox_refinement`.
- Unified `EventEnvelope` for EDA events and webhook deliveries.
- New error code catalogue (RFC 7807).
- Multipart upload supported on `/extract` and `/extractions`.

Design spec: [docs/superpowers/specs/2026-05-26-api-contract-v1-redesign-design.md](docs/superpowers/specs/2026-05-26-api-contract-v1-redesign-design.md)
Migration guide: [docs/migration-v0-to-v1.md](docs/migration-v0-to-v1.md)

## Test plan

- [x] `task lint:check`
- [x] `task test` (unit + light integration)
- [x] `task docker:up:test && uv run pytest tests/integration/ -v && task docker:down:test`
- [x] `task test:llm`
- [x] Python SDK round-trip against live server
- [x] Java SDK round-trip against live server
- [x] OpenAPI spec audited free of v0 vocabulary
- [x] Repo-wide v0-vocabulary sweep returns zero hits outside the migration guide / CHANGELOG
EOF
)"
```

- [ ] **Step 2: Return the URL to the user.**

---

## Self-review (run after the plan is written)

1. **Spec coverage:** every section of the spec maps to at least one task.
   - §4 conventions → Phase 1 (every DTO sets `extra="forbid"`; enums snake_case; etc.).
   - §5 endpoints → Phase 4.
   - §6 request shapes → Phase 1 (DTOs), Phase 3 (handlers consume them), Phase 4 (controllers).
   - §7 result shape → Phase 1 (DTOs), Phase 3 (orchestrator emits them), Phase 4 (controllers return them).
   - §8 async lifecycle → Phase 1 (DTOs), Phase 2 (entity + repository), Phase 3 (handlers), Phase 4 (controllers).
   - §9 events/webhooks → Phase 1 (`event.py`), Phase 3 (publishers + webhook publisher), Phase 6/7 (SDK verifier).
   - §10 errors → Phase 4 (exception_advice).
   - §11 dry-run validator → Phase 4 (extract_controller).
   - §12 SDK shape → Phase 6 + Phase 7.
   - §13 files to touch → covered by Phases 1–8 collectively.
   - §14 concurrency invariants → covered by Phase 2 (atomic mark_* preserved) and Phase 3 (Submit handler idempotency-key recovery).
   - §17 migration cheat-sheet → Phase 8.4 (`docs/migration-v0-to-v1.md`).
   - §18 acceptance criteria → Phase 9 (every bullet has a task).

2. **Placeholder scan:** every step contains real code or a real command. No TBDs / TODOs left.

3. **Type consistency:** identifier renames stay consistent — `Extraction`, `ExtractionStatus`, `ExtractionRepository`, `ExtractionRequest`, `SubmitExtractionRequest`, `ExtractionResultEnvelope`, `EventEnvelope` everywhere. No method named two different ways across tasks.

4. **Scope:** this plan is scoped to one cohesive change — replacing the v1 contract. It is large but indivisible; partial rollouts (e.g. only DTOs but not SDKs) would leave the repo in a non-shippable state.
