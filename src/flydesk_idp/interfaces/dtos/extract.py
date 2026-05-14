# Copyright 2026 Firefly Software Solutions Inc
"""Top-level request / response DTOs for the public extraction API.

One :class:`ExtractionRequest` carries the document, the schema (one or
more :class:`DocSpec`), optional business rules, and a set of stage
toggles. The :class:`ExtractionResult` returned to the caller folds
every stage's output into a single object: extracted fields with
bounding boxes, field-validation verdicts, visual / content
authenticity outcomes, judge verdicts, and rule results.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flydesk_idp.interfaces.dtos.authenticity import DocumentAuthenticity
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.field import ExtractedFieldGroup
from flydesk_idp.interfaces.dtos.rule import RuleResult, RuleSpec


# ---------------------------------------------------------------------------
# Document input
# ---------------------------------------------------------------------------


class DocumentInput(BaseModel):
    """The document payload provided by the caller (binary, base64-encoded)."""

    filename: str = Field(..., min_length=1)
    content_base64: str = Field(
        ...,
        description=(
            "Base64-encoded document bytes. Any media type the configured "
            "multimodal LLM accepts works (PDF, PNG, JPEG, WebP, TIFF, DOCX, ...). "
            "Data URLs (``data:application/pdf;base64,...``) are accepted -- the "
            "``data:`` prefix is stripped server-side."
        ),
    )
    content_type: str | None = Field(
        default=None,
        description=(
            "Optional MIME type hint. When omitted, the service sniffs from "
            "magic bytes."
        ),
    )

    @field_validator("content_base64")
    @classmethod
    def _validate_base64(cls, value: str) -> str:
        if "," in value and value.startswith("data:"):
            value = value.split(",", 1)[1]
        try:
            base64.b64decode(value, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
        return value

    def decoded_bytes(self) -> bytes:
        return base64.b64decode(self.content_base64)


# ---------------------------------------------------------------------------
# Pipeline options
# ---------------------------------------------------------------------------


class StageToggles(BaseModel):
    """Opt-in switches for every optional pipeline stage.

    The :class:`MultimodalExtractor` is always on (it's what produces
    fields + bbox). Everything else is opt-in. Defaults are conservative
    so a vanilla request stays cheap and fast.
    """

    splitter: bool = Field(
        default=False,
        description=(
            "Run the LLM document splitter to map each target document type "
            "to a page range. Required when ``docs`` has more than one entry "
            "and the submitted file interleaves them."
        ),
    )
    field_validation: bool = True
    visual_authenticity: bool = False
    content_authenticity: bool = False
    judge: bool = False
    rule_engine: bool = False
    judge_escalation: bool = Field(
        default=False,
        description=(
            "When the judge marks too many fields as FAIL / flag_for_review, "
            "re-run extract + judge with the escalation_model and keep the "
            "result that has the lower failure rate. Requires ``judge`` to "
            "be enabled. Threshold + model come from ``options`` or env "
            "defaults (``FLYDESK_IDP_ESCALATION_THRESHOLD`` / "
            "``FLYDESK_IDP_ESCALATION_MODEL``)."
        ),
    )


class ExtractionOptions(BaseModel):
    """Per-request knobs."""

    return_bboxes: bool = True
    language_hint: str | None = Field(default=None, max_length=16)
    model: str | None = None
    declared_media_type: str | None = None
    stages: StageToggles = Field(default_factory=StageToggles)
    escalation_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Failure-rate threshold (0.0–1.0) above which the judge "
            "escalation re-run fires. Overrides "
            "``FLYDESK_IDP_ESCALATION_THRESHOLD`` for this request."
        ),
    )
    escalation_model: str | None = Field(
        default=None,
        description=(
            "Model id used for the escalation re-run "
            "(e.g. ``anthropic:claude-opus-4-7``). Overrides "
            "``FLYDESK_IDP_ESCALATION_MODEL`` for this request."
        ),
    )


# ---------------------------------------------------------------------------
# The request itself
# ---------------------------------------------------------------------------


class ExtractionRequest(BaseModel):
    """One IDP extraction request.

    Carries the document, the schema (one or more :class:`DocSpec`),
    optional business rules, and per-request options. A "single doc,
    free-form schema" call is just a request with one entry in
    ``docs``; multi-document calls add more entries.
    """

    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    intention: str = Field(
        default="Extract structured data from the document.",
        description="Free-form prompt that nuances every node's behaviour (search, judge, rules).",
    )
    document: DocumentInput
    docs: list[DocSpec] = Field(..., min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)
    options: ExtractionOptions = Field(default_factory=ExtractionOptions)


# ---------------------------------------------------------------------------
# Response side
# ---------------------------------------------------------------------------


class DocumentInfo(BaseModel):
    filename: str
    media_type: str
    page_count: int
    bytes: int


class ExtractedDocument(BaseModel):
    """Result for one document instance (one entry per requested ``DocSpec``)."""

    document_type: str
    missing: bool = False
    pages: list[int] = Field(default_factory=list)
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    fields: list[ExtractedFieldGroup] = Field(default_factory=list)
    authenticity: DocumentAuthenticity = Field(default_factory=DocumentAuthenticity)
    notes: str | None = None


class EscalationInfo(BaseModel):
    """Audit block for the judge-driven escalation re-run.

    Populated only when ``stages.judge_escalation`` is enabled and the
    judge's first pass exceeded the configured failure threshold.
    """

    triggered: bool = False
    primary_model: str | None = None
    escalation_model: str | None = None
    primary_fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    escalation_fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    accepted: bool = Field(
        default=False,
        description=(
            "True when the escalation re-run produced fewer judge "
            "failures than the primary and was kept as the response."
        ),
    )


class ExtractionResult(BaseModel):
    """Top-level response."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: uuid.UUID
    document: DocumentInfo
    documents: list[ExtractedDocument] = Field(default_factory=list)
    additional_documents: list[ExtractedDocument] = Field(
        default_factory=list,
        description="Documents found in the source PDF that don't match any requested doc type.",
    )
    rule_results: list[RuleResult] = Field(default_factory=list)
    model: str
    latency_ms: int = Field(..., ge=0)
    pipeline_errors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Non-fatal per-node failures: ``[{code, message, node}]``.",
    )
    escalation: EscalationInfo | None = Field(
        default=None,
        description=(
            "Audit block populated when judge_escalation runs. ``null`` "
            "when escalation is disabled or didn't fire."
        ),
    )
