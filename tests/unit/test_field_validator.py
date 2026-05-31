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

"""Unit tests for :class:`FieldValidator` -- the pure-Python validation node.

Verifies the post-extraction validator correctly:
- decorates each extracted field with a ``validation`` object
- rejects values that fail a regex / enum / built-in validator
- treats ``severity=warning`` validators as soft (``valid=true`` but error recorded)
"""

from __future__ import annotations

from flydocs.core.services.validation import FieldValidator
from flydocs.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    Field,
    FieldGroup,
)
from flydocs.interfaces.dtos.validator import ValidatorSpec
from flydocs.interfaces.enums.field_type import FieldType
from flydocs.interfaces.enums.validator import ValidatorType


def _group(spec: Field, extracted: ExtractedField) -> tuple[FieldGroup, ExtractedFieldGroup]:
    return (
        FieldGroup(name="g", description="", fields=[spec]),
        ExtractedFieldGroup(name="g", fields=[extracted]),
    )


def test_enum_rejects_unknown_value() -> None:
    spec = Field(name="currency", type=FieldType.STRING, enum=["EUR", "USD"])
    extracted = ExtractedField(name="currency", value="GBP")
    sg, eg = _group(spec, extracted)
    FieldValidator().validate([sg], [eg])
    assert eg.fields[0].validation.valid is False
    assert eg.fields[0].validation.errors[0].rule.value == "enum"


def test_validator_marks_invalid_email() -> None:
    spec = Field(
        name="contact",
        type=FieldType.STRING,
        validators=[ValidatorSpec(name=ValidatorType.EMAIL)],
    )
    extracted = ExtractedField(name="contact", value="not-an-email")
    sg, eg = _group(spec, extracted)
    FieldValidator().validate([sg], [eg])
    assert eg.fields[0].validation.valid is False
    assert any(e.rule.value == "validator" for e in eg.fields[0].validation.errors)


def test_warning_severity_keeps_field_valid() -> None:
    spec = Field(
        name="iban",
        type=FieldType.STRING,
        validators=[
            ValidatorSpec(name=ValidatorType.IBAN, severity="warning"),
        ],
    )
    extracted = ExtractedField(name="iban", value="NOT-AN-IBAN")
    sg, eg = _group(spec, extracted)
    FieldValidator().validate([sg], [eg])
    fv = eg.fields[0].validation
    assert fv.valid is True
    assert len(fv.errors) == 1
    assert fv.errors[0].message.endswith("[warning]")


def test_none_value_is_skipped() -> None:
    spec = Field(
        name="iban",
        type=FieldType.STRING,
        validators=[ValidatorSpec(name=ValidatorType.IBAN)],
    )
    extracted = ExtractedField(name="iban", value=None)
    sg, eg = _group(spec, extracted)
    FieldValidator().validate([sg], [eg])
    assert eg.fields[0].validation.valid is True
    assert eg.fields[0].validation.errors == []
