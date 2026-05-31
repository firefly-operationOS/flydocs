# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Field-level DTOs -- schema in, extraction out.

One recursive :class:`Field` handles primitives, arrays, and nested
objects. Arrays require ``items`` (a single ``Field`` describing the row
shape, typically of type ``object``); objects require ``fields`` (a list
of ``Field`` members); primitives forbid both.

The response side carries :class:`ExtractedField` with the same recursion.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import Field as _PydField

from flydocs.interfaces.dtos.bbox import BoundingBox
from flydocs.interfaces.dtos.validator import ValidatorSpec
from flydocs.interfaces.enums.field_type import FieldType, StandardFormat
from flydocs.interfaces.enums.status import JudgeStatus, ValidationRule

# ---------------------------------------------------------------------------
# REQUEST SIDE -- the schema the caller submits
# ---------------------------------------------------------------------------


class Field(BaseModel):
    """One field in a schema. Recursive for arrays and objects."""

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
    items: Field | None = None
    fields: list[Field] | None = None
    validators: list[ValidatorSpec] = _PydField(default_factory=list)

    @model_validator(mode="after")
    def _check_constraints(self) -> Field:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must be <= maximum")

        if self.type == FieldType.ARRAY:
            if self.items is None:
                raise ValueError("type 'array' requires items")
            if self.fields is not None:
                raise ValueError("type 'array' must not set fields")
        elif self.type == FieldType.OBJECT:
            if not self.fields:
                raise ValueError("type 'object' requires fields (non-empty list)")
            if self.items is not None:
                raise ValueError("type 'object' must not set items")
        else:
            if self.items is not None:
                raise ValueError(f"type '{self.type.value}' must not set items")
            if self.fields is not None:
                raise ValueError(f"type '{self.type.value}' must not set fields")
        return self


Field.model_rebuild()


class FieldGroup(BaseModel):
    """A named bundle of fields the service extracts together."""

    model_config = ConfigDict(extra="forbid")

    name: str = _PydField(..., min_length=1)
    description: str | None = None
    fields: list[Field] = _PydField(..., min_length=1)


# ---------------------------------------------------------------------------
# RESPONSE SIDE -- structure returned alongside each extracted value
# ---------------------------------------------------------------------------


class FieldValidationError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: ValidationRule
    message: str


class FieldValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool = True
    errors: list[FieldValidationError] = _PydField(default_factory=list)


class JudgeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: JudgeStatus = JudgeStatus.UNCERTAIN
    confidence: float = _PydField(default=0.0, ge=0.0, le=1.0)
    evidence: str | None = None
    notes: str | None = None
    flag_for_review: bool = False


class ExtractedField(BaseModel):
    """One extracted field. Recursive for arrays and objects."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | int | float | bool | list[ExtractedField] | None = None
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
