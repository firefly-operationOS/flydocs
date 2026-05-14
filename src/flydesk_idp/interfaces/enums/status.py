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
    STANDARD = "standard"


class JudgeStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


class ContentIntegrityStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNCERTAIN = "UNCERTAIN"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


# Re-export so importers can grab everything from .status
__all__ = ["CheckStatus", "ContentIntegrityStatus", "JudgeStatus", "ValidationRule"]
