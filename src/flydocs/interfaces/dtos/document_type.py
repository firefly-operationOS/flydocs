# Copyright 2026 Firefly Software Solutions Inc
"""DocumentTypeSpec -- schema template for one expected document type.

Replaces the v0 ``DocSpec`` and the nested ``DocType`` envelope, flattening
``docs[i].docType.documentType`` (three layers of "doc" stutter) into
``document_types[i].id`` (one identifier).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.dtos.field import FieldGroup


class VisualCheck(BaseModel):
    """One visual check to run against the document (signature, watermark, seal, ...)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str


class DocumentTypeSpec(BaseModel):
    """One expected document type the caller is submitting fields for."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Stable id (e.g. 'invoice', 'passport').")
    description: str | None = None
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2 country code.")
    field_groups: list[FieldGroup] = Field(..., min_length=1)
    visual_checks: list[VisualCheck] = Field(default_factory=list)
