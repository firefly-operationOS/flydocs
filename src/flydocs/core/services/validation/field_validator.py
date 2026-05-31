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

"""Pure-Python field validator -- evaluates pattern, format, enum,
minimum, maximum, and every declared :class:`ValidatorSpec` against
every extracted field. No LLM involvement.

The verdict is attached directly to each :class:`ExtractedField` via
its ``validation`` attribute, so consumers never need a parallel tree.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID as PyUUID

from flydocs.core.services.validation.validator_registry import run_validator
from flydocs.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    Field,
    FieldGroup,
    FieldValidation,
    FieldValidationError,
)
from flydocs.interfaces.dtos.validator import ValidatorSpec
from flydocs.interfaces.enums.field_type import FieldType, StandardFormat
from flydocs.interfaces.enums.status import ValidationRule


class FieldValidator:
    """Runs every constraint on every extracted field."""

    def validate(
        self,
        spec_groups: list[FieldGroup],
        extracted_groups: list[ExtractedFieldGroup],
    ) -> list[ExtractedFieldGroup]:
        """Return *extracted_groups* with each field's ``validation`` populated."""
        if not spec_groups or not extracted_groups:
            return extracted_groups
        by_group: dict[str, FieldGroup] = {g.name: g for g in spec_groups}
        for group in extracted_groups:
            spec_group = by_group.get(group.name)
            if spec_group is None:
                continue
            spec_by_name: dict[str, Field] = {s.name: s for s in spec_group.fields}
            for field in group.fields:
                spec = spec_by_name.get(field.name)
                if spec is None:
                    continue
                self._validate_field(spec, field)
        return extracted_groups

    # ----------------------------------------------------------- private

    def _validate_field(self, spec: Field, field: ExtractedField) -> None:
        if spec.type == FieldType.ARRAY:
            self._validate_array(spec, field)
            return
        errors = self._run_constraints(
            field_type=spec.type,
            pattern=spec.pattern,
            fmt=spec.format,
            enum=spec.enum,
            minimum=spec.minimum,
            maximum=spec.maximum,
            validators=spec.validators,
            value=field.value,
        )
        # ``severity=warning`` validators record errors but don't flip ``valid``.
        hard_errors = [e for e in errors if not e.message.endswith("[warning]")]
        field.validation = FieldValidation(valid=not hard_errors, errors=errors)

    def _validate_array(self, spec: Field, field: ExtractedField) -> None:
        row_errors: list[FieldValidationError] = []
        rows = field.value if isinstance(field.value, list) else []
        items_spec = spec.items
        # The recursive Field for an array element is typically ``type =
        # object`` -- iterate its declared sub-fields.
        sub_specs: dict[str, Field] = {}
        if items_spec is not None and items_spec.fields:
            sub_specs = {s.name: s for s in items_spec.fields}
        all_valid = True
        for row in rows:
            if not isinstance(row, ExtractedField) or not isinstance(row.value, list):
                continue
            for sub_field in row.value:
                if not isinstance(sub_field, ExtractedField):
                    continue
                sub_spec = sub_specs.get(sub_field.name)
                if sub_spec is None:
                    continue
                errors = self._run_constraints(
                    field_type=sub_spec.type,
                    pattern=sub_spec.pattern,
                    fmt=sub_spec.format,
                    enum=sub_spec.enum,
                    minimum=sub_spec.minimum,
                    maximum=sub_spec.maximum,
                    validators=sub_spec.validators,
                    value=sub_field.value,
                )
                hard_errors = [e for e in errors if not e.message.endswith("[warning]")]
                sub_field.validation = FieldValidation(valid=not hard_errors, errors=errors)
                if hard_errors:
                    all_valid = False
        field.validation = FieldValidation(valid=all_valid, errors=row_errors)

    def _run_constraints(
        self,
        *,
        field_type: FieldType,
        pattern: str | None,
        fmt: StandardFormat | None,
        enum: list[Any] | None,
        minimum: float | None,
        maximum: float | None,
        validators: list[ValidatorSpec],
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
        if enum is not None and value not in enum:
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
        for sv in validators or []:
            message = run_validator(sv.name, value, sv.params)
            if message is not None:
                suffix = " [warning]" if sv.severity == "warning" else ""
                errors.append(
                    FieldValidationError(
                        rule=ValidationRule.VALIDATOR,
                        message=f"{sv.name.value}: {message}{suffix}",
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
            return FieldValidationError(rule=ValidationRule.MINIMUM, message=f"{value!r} is not a number")
        if num < minimum:
            return FieldValidationError(
                rule=ValidationRule.MINIMUM, message=f"Value {value} is below minimum {minimum}"
            )
        return None

    def _validate_maximum(self, maximum: float, value: Any) -> FieldValidationError | None:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return FieldValidationError(rule=ValidationRule.MAXIMUM, message=f"{value!r} is not a number")
        if num > maximum:
            return FieldValidationError(
                rule=ValidationRule.MAXIMUM, message=f"Value {value} exceeds maximum {maximum}"
            )
        return None
