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


class StandardFormat(StrEnum):
    """JSON Schema-style standard formats applied at validation time."""

    DATE = "date"
    DATE_TIME = "date-time"
    EMAIL = "email"
    URI = "uri"
    UUID = "uuid"
