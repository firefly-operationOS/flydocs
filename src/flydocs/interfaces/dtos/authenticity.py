# Copyright 2026 Firefly Software Solutions Inc
"""Authenticity DTOs -- visual + content integrity outputs."""

from __future__ import annotations

from pydantic import BaseModel, Field

from flydocs.interfaces.enums.status import CheckStatus, ContentIntegrityStatus


class VisualValidationOutcome(BaseModel):
    name: str
    passed: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""


class ContentCoherenceCheck(BaseModel):
    name: str
    description: str
    status: CheckStatus
    evidence: str = ""
    reasoning: str = ""


class ContentAuthenticity(BaseModel):
    overall_integrity_status: ContentIntegrityStatus = ContentIntegrityStatus.UNCERTAIN
    checks: list[ContentCoherenceCheck] = Field(default_factory=list)


class DocumentAuthenticity(BaseModel):
    """Aggregated authenticity result for a single document instance."""

    visual: list[VisualValidationOutcome] = Field(default_factory=list)
    content: ContentAuthenticity = Field(default_factory=ContentAuthenticity)
