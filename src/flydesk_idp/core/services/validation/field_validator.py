# Copyright 2026 Firefly Software Solutions Inc
"""Pure-Python field validator -- evaluates pattern, format, enum,
minimum, maximum, and every declared :class:`StandardValidatorSpec`
against every extracted field. No LLM involvement.

The verdict is attached directly to each :class:`ExtractedField` via
its ``field_validation`` attribute, so consumers never need a parallel
tree.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID as PyUUID

from flydesk_idp.core.services.validation.standard_validator_registry import run_standard_validator
from flydesk_idp.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    FieldGroup,
    FieldItem,
    FieldSpec,
    FieldValidation,
    FieldValidationError,
)
from flydesk_idp.interfaces.dtos.standard_validator import StandardValidatorSpec
from flydesk_idp.interfaces.enums.field_type import FieldType, StandardFormat
from flydesk_idp.interfaces.enums.status import ValidationRule


class FieldValidator:
    """Runs every constraint on every extracted field."""

    def validate(
        self,
        spec_groups: list[FieldGroup],
        extracted_groups: list[ExtractedFieldGroup],
    ) -> list[ExtractedFieldGroup]:
        """Return *extracted_groups* with each field's ``field_validation`` populated."""
        if not spec_groups or not extracted_groups:
            return extracted_groups
        by_group: dict[str, FieldGroup] = {g.fieldGroupName: g for g in spec_groups}
        for group in extracted_groups:
            spec_group = by_group.get(group.fieldGroupName)
            if spec_group is None:
                continue
            spec_by_name: dict[str, FieldSpec] = {s.fieldName: s for s in spec_group.fieldGroupFields}
            for field in group.fieldGroupFields:
                spec = spec_by_name.get(field.fieldName)
                if spec is None:
                    continue
                self._validate_field(spec, field)
        return extracted_groups

    # ----------------------------------------------------------- private

    def _validate_field(self, spec: FieldSpec, field: ExtractedField) -> None:
        if spec.fieldType == FieldType.ARRAY:
            self._validate_array(spec, field)
            return
        errors = self._run_constraints(
            field_type=spec.fieldType,
            pattern=spec.pattern,
            fmt=spec.format,
            enum=spec.enum,
            minimum=spec.minimum,
            maximum=spec.maximum,
            standard_validators=spec.standard_validators,
            value=field.fieldValueFound,
        )
        # ``severity=warning`` validators record errors but don't flip ``valid``.
        hard_errors = [
            e for e in errors
            if not e.message.endswith("[warning]")
        ]
        field.field_validation = FieldValidation(valid=not hard_errors, errors=errors)

    def _validate_array(self, spec: FieldSpec, field: ExtractedField) -> None:
        row_errors: list[FieldValidationError] = []
        rows = field.fieldValueFound if isinstance(field.fieldValueFound, list) else []
        item_specs: dict[str, FieldItem] = {item.fieldName: item for item in (spec.items or [])}
        all_valid = True
        for row in rows:
            if not isinstance(row, ExtractedField) or not isinstance(row.fieldValueFound, list):
                continue
            for sub_field in row.fieldValueFound:
                if not isinstance(sub_field, ExtractedField):
                    continue
                item = item_specs.get(sub_field.fieldName)
                if item is None:
                    continue
                errors = self._run_constraints(
                    field_type=item.fieldType,
                    pattern=item.pattern,
                    fmt=item.format,
                    enum=item.enum,
                    minimum=item.minimum,
                    maximum=item.maximum,
                    standard_validators=item.standard_validators,
                    value=sub_field.fieldValueFound,
                )
                hard_errors = [e for e in errors if not e.message.endswith("[warning]")]
                sub_field.field_validation = FieldValidation(valid=not hard_errors, errors=errors)
                if hard_errors:
                    all_valid = False
        field.field_validation = FieldValidation(valid=all_valid, errors=row_errors)

    def _run_constraints(
        self,
        *,
        field_type: FieldType,
        pattern: str | None,
        fmt: StandardFormat | None,
        enum: list[Any] | None,
        minimum: float | None,
        maximum: float | None,
        standard_validators: list[StandardValidatorSpec],
        value: Any,
    ) -> list[FieldValidationError]:
        if value is None:
            return []
        errors: list[FieldValidationError] = []
        type_err = self._validate_type(field_type, value)
        if type_err is not None:
            return [type_err]
        if pattern is not None:
            err = self._validate_pattern(pattern, value)
            if err is not None:
                errors.append(err)
        if fmt is not None:
            err = self._validate_format(fmt, value)
            if err is not None:
                errors.append(err)
        if enum is not None:
            if value not in enum:
                errors.append(
                    FieldValidationError(
                        rule=ValidationRule.ENUM,
                        message=f"Value {value!r} is not one of {enum}",
                    )
                )
        if minimum is not None:
            err = self._validate_minimum(minimum, value)
            if err is not None:
                errors.append(err)
        if maximum is not None:
            err = self._validate_maximum(maximum, value)
            if err is not None:
                errors.append(err)
        for sv in standard_validators or []:
            message = run_standard_validator(sv.type, value, sv.params)
            if message is not None:
                suffix = " [warning]" if sv.severity == "warning" else ""
                errors.append(
                    FieldValidationError(
                        rule=ValidationRule.STANDARD,
                        message=f"{sv.type.value}: {message}{suffix}",
                    )
                )
        return errors

    def _validate_type(self, field_type: FieldType, value: Any) -> FieldValidationError | None:
        ok = True
        if field_type == FieldType.STRING:
            ok = isinstance(value, str)
        elif field_type == FieldType.NUMBER:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif field_type == FieldType.INTEGER:
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif field_type == FieldType.BOOLEAN:
            ok = isinstance(value, bool)
        if not ok:
            return FieldValidationError(
                rule=ValidationRule.TYPE,
                message=f"Expected {field_type.value}, got {type(value).__name__}",
            )
        return None

    def _validate_pattern(self, pattern: str, value: Any) -> FieldValidationError | None:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return FieldValidationError(
                rule=ValidationRule.PATTERN, message=f"Invalid regex {pattern!r}: {exc}"
            )
        if not compiled.fullmatch(str(value)):
            return FieldValidationError(
                rule=ValidationRule.PATTERN,
                message=f"Value {value!r} does not match pattern {pattern!r}",
            )
        return None

    def _validate_format(self, fmt: StandardFormat, value: Any) -> FieldValidationError | None:
        raw = str(value)
        try:
            if fmt == StandardFormat.DATE:
                datetime.strptime(raw, "%Y-%m-%d")
            elif fmt == StandardFormat.DATE_TIME:
                datetime.fromisoformat(raw)
            elif fmt == StandardFormat.EMAIL:
                if not re.fullmatch(r"[^@]+@[^@]+\.[^@]+", raw):
                    raise ValueError("invalid email format")
            elif fmt == StandardFormat.URI:
                parsed = urlparse(raw)
                if not parsed.scheme or not parsed.netloc:
                    raise ValueError("missing scheme or host")
            elif fmt == StandardFormat.UUID:
                PyUUID(raw)
        except (ValueError, AttributeError) as exc:
            return FieldValidationError(
                rule=ValidationRule.FORMAT,
                message=f"Value {value!r} does not match format {fmt.value!r}: {exc}",
            )
        return None

    def _validate_minimum(self, minimum: float, value: Any) -> FieldValidationError | None:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return FieldValidationError(
                rule=ValidationRule.MINIMUM, message=f"{value!r} is not a number"
            )
        if num < minimum:
            return FieldValidationError(
                rule=ValidationRule.MINIMUM, message=f"Value {value} is below minimum {minimum}"
            )
        return None

    def _validate_maximum(self, maximum: float, value: Any) -> FieldValidationError | None:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return FieldValidationError(
                rule=ValidationRule.MAXIMUM, message=f"{value!r} is not a number"
            )
        if num > maximum:
            return FieldValidationError(
                rule=ValidationRule.MAXIMUM, message=f"Value {value} exceeds maximum {maximum}"
            )
        return None
