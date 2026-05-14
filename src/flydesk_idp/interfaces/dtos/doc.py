# Copyright 2026 Firefly Software Solutions Inc
"""Doc-type / validator DTOs -- what each expected document looks like."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from flydesk_idp.interfaces.dtos.field import FieldGroup


class DocType(BaseModel):
    documentType: str = Field(
        ..., min_length=1, description="Stable id for this document type (e.g. ``passport``)."
    )
    description: str = ""
    country: str = Field(default="", description="ISO 3166-1 alpha-2 country code.")


class VisualValidatorSpec(BaseModel):
    """One visual check to run against the document (e.g. signature, watermark)."""

    name: str = Field(..., min_length=1)
    description: str


class ValidatorsSpec(BaseModel):
    """Bundle of validator definitions for a single document type.

    Currently only visual validators are exposed publicly; future
    additions (audio, structural) plug in here.
    """

    visual: list[VisualValidatorSpec] = Field(default_factory=list)


class DocSpec(BaseModel):
    """One expected document type the caller is submitting fields / validators for."""

    model_config = ConfigDict(populate_by_name=True)

    docType: DocType
    fieldGroups: list[FieldGroup] = Field(..., min_length=1)
    validators: ValidatorsSpec = Field(default_factory=ValidatorsSpec)
