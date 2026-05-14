# Copyright 2026 Firefly Software Solutions Inc
"""Field-level DTOs -- schema in, extraction out.

The request side groups fields under a named :class:`FieldGroup` (for
example ``personal``, ``billing``) and supports JSON-Schema-style
constraints plus an extensible :class:`StandardValidatorSpec` list per
field. The response side carries the parallel :class:`ExtractedField`
structure with confidence, page, bounding box, judge verdict, and
field-validation result. Array fields (repeating rows of sub-fields)
are supported recursively.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flydesk_idp.interfaces.dtos.bbox import BoundingBox
from flydesk_idp.interfaces.dtos.standard_validator import StandardValidatorSpec
from flydesk_idp.interfaces.enums.field_type import FieldType, StandardFormat
from flydesk_idp.interfaces.enums.status import JudgeStatus, ValidationRule

# ---------------------------------------------------------------------------
# REQUEST side -- the schema the caller submits
# ---------------------------------------------------------------------------


class FieldItem(BaseModel):
    """One sub-field inside an array field (e.g. a column of a line-items table)."""

    fieldName: str = Field(..., min_length=1)
    fieldDescription: str = ""
    fieldType: FieldType = FieldType.STRING

    pattern: str | None = None
    format: StandardFormat | None = None
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    standard_validators: list[StandardValidatorSpec] = Field(default_factory=list)


class FieldSpec(BaseModel):
    """One field the caller wants extracted.

    For ``fieldType == array`` the ``items`` list describes the columns
    of every repeating row. For primitive types ``items`` must be
    empty / null.
    """

    model_config = ConfigDict(populate_by_name=True)

    fieldName: str = Field(..., min_length=1, alias="name")
    fieldDescription: str = Field(default="", alias="description")
    fieldType: FieldType = Field(default=FieldType.STRING, alias="type")
    required: bool = False

    pattern: str | None = None
    format: StandardFormat | None = None
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    items: list[FieldItem] | None = None
    standard_validators: list[StandardValidatorSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_constraints(self) -> FieldSpec:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must be <= maximum")
        if self.fieldType != FieldType.ARRAY and self.items:
            raise ValueError("items is only valid when fieldType is array")
        return self


class FieldGroup(BaseModel):
    fieldGroupName: str = Field(..., min_length=1)
    fieldGroupDesc: str = ""
    fieldGroupFields: list[FieldSpec] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# RESPONSE side -- the structure returned alongside each extracted value
# ---------------------------------------------------------------------------


class FieldValidationError(BaseModel):
    rule: ValidationRule
    message: str


class FieldValidation(BaseModel):
    valid: bool = True
    errors: list[FieldValidationError] = Field(default_factory=list)


class JudgeOutcome(BaseModel):
    status: JudgeStatus = JudgeStatus.UNCERTAIN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str = ""
    notes: str = ""
    flag_for_review: bool = False


class ExtractedField(BaseModel):
    """One extracted field. Recursive: array fields contain rows of sub-fields."""

    model_config = ConfigDict(populate_by_name=True)

    fieldName: str = Field(..., alias="name")
    fieldValueFound: str | int | float | bool | list[ExtractedField] | None = Field(
        default=None, alias="value"
    )
    pagesFound: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bbox: BoundingBox = Field(default_factory=BoundingBox.empty)
    notes: str | None = None
    judge: JudgeOutcome = Field(default_factory=JudgeOutcome)
    field_validation: FieldValidation = Field(default_factory=FieldValidation)


class ExtractedFieldGroup(BaseModel):
    fieldGroupName: str
    fieldGroupFields: list[ExtractedField]


ExtractedField.model_rebuild()
