# Copyright 2026 Firefly Software Solutions Inc
"""Unit tests for :class:`FieldValidator` -- the pure-Python validation node.

Verifies the post-extraction validator correctly:
- decorates each extracted field with a ``field_validation`` object
- rejects values that fail a regex / enum / standard validator
- treats ``severity=warning`` validators as soft (``valid=true`` but error recorded)
"""

from __future__ import annotations

from flydesk_idp.core.services.validation import FieldValidator
from flydesk_idp.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    FieldGroup,
    FieldSpec,
)
from flydesk_idp.interfaces.dtos.standard_validator import StandardValidatorSpec
from flydesk_idp.interfaces.enums.field_type import FieldType
from flydesk_idp.interfaces.enums.standard_validator import StandardValidatorType


def _group(spec: FieldSpec, extracted: ExtractedField) -> tuple[FieldGroup, ExtractedFieldGroup]:
    return (
        FieldGroup(fieldGroupName="g", fieldGroupDesc="", fieldGroupFields=[spec]),
        ExtractedFieldGroup(fieldGroupName="g", fieldGroupFields=[extracted]),
    )


def test_enum_rejects_unknown_value() -> None:
    spec = FieldSpec(fieldName="currency", fieldType=FieldType.STRING, enum=["EUR", "USD"])
    extracted = ExtractedField(fieldName="currency", fieldValueFound="GBP")
    sg, eg = _group(spec, extracted)
    FieldValidator().validate([sg], [eg])
    assert eg.fieldGroupFields[0].field_validation.valid is False
    assert eg.fieldGroupFields[0].field_validation.errors[0].rule.value == "enum"


def test_standard_validator_marks_invalid_email() -> None:
    spec = FieldSpec(
        fieldName="contact",
        fieldType=FieldType.STRING,
        standard_validators=[StandardValidatorSpec(type=StandardValidatorType.EMAIL)],
    )
    extracted = ExtractedField(fieldName="contact", fieldValueFound="not-an-email")
    sg, eg = _group(spec, extracted)
    FieldValidator().validate([sg], [eg])
    assert eg.fieldGroupFields[0].field_validation.valid is False
    assert any(e.rule.value == "standard" for e in eg.fieldGroupFields[0].field_validation.errors)


def test_warning_severity_keeps_field_valid() -> None:
    spec = FieldSpec(
        fieldName="iban",
        fieldType=FieldType.STRING,
        standard_validators=[
            StandardValidatorSpec(type=StandardValidatorType.IBAN, severity="warning"),
        ],
    )
    extracted = ExtractedField(fieldName="iban", fieldValueFound="NOT-AN-IBAN")
    sg, eg = _group(spec, extracted)
    FieldValidator().validate([sg], [eg])
    fv = eg.fieldGroupFields[0].field_validation
    assert fv.valid is True
    assert len(fv.errors) == 1
    assert fv.errors[0].message.endswith("[warning]")


def test_none_value_is_skipped() -> None:
    spec = FieldSpec(
        fieldName="iban",
        fieldType=FieldType.STRING,
        standard_validators=[StandardValidatorSpec(type=StandardValidatorType.IBAN)],
    )
    extracted = ExtractedField(fieldName="iban", fieldValueFound=None)
    sg, eg = _group(spec, extracted)
    FieldValidator().validate([sg], [eg])
    assert eg.fieldGroupFields[0].field_validation.valid is True
    assert eg.fieldGroupFields[0].field_validation.errors == []
