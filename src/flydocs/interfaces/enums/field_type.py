# Copyright 2026 Firefly Software Solutions Inc
"""Supported field primitives + standard formats for the public extraction schema."""

from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    """JSON-Schema-aligned primitive set for the public Field model."""

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class StandardFormat(StrEnum):
    """Standard format hints applied to typed field values at validation time."""

    DATE = "date"
    DATE_TIME = "date-time"
    TIME = "time"
    EMAIL = "email"
    URI = "uri"
    UUID = "uuid"
    CURRENCY = "currency"
