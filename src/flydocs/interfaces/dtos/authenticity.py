# Copyright 2026 Firefly Software Solutions Inc
"""Authenticity DTOs -- visual + content integrity outputs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.enums.status import CheckStatus, ContentIntegrityStatus


class VisualCheckResult(BaseModel):
    """One visual check's outcome on a document."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str | None = None


class ContentCoherenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    status: CheckStatus
    evidence: str | None = None
    reasoning: str | None = None


class ContentAuthenticity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_integrity_status: ContentIntegrityStatus = ContentIntegrityStatus.UNCERTAIN
    checks: list[ContentCoherenceCheck] = Field(default_factory=list)


class DocumentAuthenticity(BaseModel):
    """Aggregated authenticity result for a single document instance."""

    model_config = ConfigDict(extra="forbid")

    visual: list[VisualCheckResult] = Field(default_factory=list)
    content: ContentAuthenticity | None = None
