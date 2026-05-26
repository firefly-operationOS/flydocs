# Copyright 2026 Firefly Software Solutions Inc
"""Status enums shared across pipeline nodes (validation rules, judge verdicts,
content-authenticity verdicts).

All values are lowercase snake_case to match the universal v1 enum convention.
"""

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
