# Copyright 2026 Firefly Software Solutions Inc
"""Top-level request / response DTOs for the public extraction API.

One :class:`ExtractionRequest` carries the input files, the schema templates
(one or more :class:`DocumentTypeSpec`), optional business rules, and a set
of stage toggles. The :class:`ExtractionResult` returned to the caller folds
every stage's output into a single object: extracted fields with bounding
boxes, field-validation verdicts, visual / content authenticity outcomes,
judge verdicts, rule results.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flydocs.interfaces.dtos.authenticity import DocumentAuthenticity
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.field import ExtractedFieldGroup
from flydocs.interfaces.dtos.rule import RuleResult, RuleSpec
from flydocs.interfaces.dtos.transformation import Transformation

# ---------------------------------------------------------------------------
# FileInput (request)
# ---------------------------------------------------------------------------


class FileInput(BaseModel):
    """One input file for an extraction request.

    JSON mode: caller sets ``content_base64`` (raw base64 or a ``data:`` URL).
    Multipart mode: the binary rides in a separate file part; ``content_base64``
    is absent and ``filename`` / ``content_type`` come from the part headers.
    """

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., min_length=1)
    content_base64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded document bytes (or ``data:<media-type>;base64,...`` "
            "data URL — the prefix is stripped server-side). Absent in multipart "
            "mode."
        ),
    )
    content_type: str | None = Field(default=None, description="MIME hint; sniffed when omitted.")
    expected_type: str | None = Field(
        default=None,
        description=(
            "Optional caller hint pointing at one of the declared "
            "``document_types[].id`` values. Skips the classifier for this "
            "file when the classifier stage is enabled."
        ),
    )

    @field_validator("content_base64")
    @classmethod
    def _validate_base64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "," in value and value.startswith("data:"):
            value = value.split(",", 1)[1]
        try:
            base64.b64decode(value, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
        return value

    def decoded_bytes(self) -> bytes:
        if self.content_base64 is None:
            raise ValueError("FileInput.content_base64 is not set (multipart mode)")
        return base64.b64decode(self.content_base64)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


class StageToggles(BaseModel):
    """Opt-in switches for every optional pipeline stage."""

    model_config = ConfigDict(extra="forbid")

    splitter: bool = False
    classifier: bool = True
    field_validation: bool = True
    visual_authenticity: bool = False
    content_authenticity: bool = False
    judge: bool = False
    judge_escalation: bool = False
    bbox_refine: bool = False
    transform: bool = False
    rule_engine: bool = False


class EscalationConfig(BaseModel):
    """Configuration for the judge_escalation stage.

    Null on :class:`ExtractionOptions` when judge_escalation is off. When set
    AND ``stages.judge_escalation`` is true, fires the rerun when the judge's
    fail-rate crosses ``threshold``.
    """

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(..., ge=0.0, le=1.0)
    model: str = Field(..., min_length=1)


class ExtractionOptions(BaseModel):
    """Per-request pipeline knobs."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    language_hint: str | None = Field(default=None, max_length=16)
    return_bboxes: bool = True
    declared_media_type: str | None = None
    stages: StageToggles = Field(default_factory=StageToggles)
    escalation: EscalationConfig | None = None
    transformations: list[Transformation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ExtractionRequest(BaseModel):
    """One IDP extraction request.

    Every request carries a non-empty ``files`` list and a non-empty
    ``document_types`` list. A single-file request is just a one-element
    ``files``; the pipeline never branches on cardinality.
    """

    model_config = ConfigDict(extra="forbid")

    intention: str = "Extract structured data from the document."
    files: list[FileInput] = Field(..., min_length=1)
    document_types: list[DocumentTypeSpec] = Field(..., min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)
    options: ExtractionOptions = Field(default_factory=ExtractionOptions)


# ---------------------------------------------------------------------------
# Response side
# ---------------------------------------------------------------------------


class ClassificationInfo(BaseModel):
    """Per-file classifier verdict surfaced in the response."""

    model_config = ConfigDict(extra="forbid")

    document_type: str
    matched: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str | None = None
    notes: str | None = None


class FileSummary(BaseModel):
    """Summary of one input file."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    media_type: str
    page_count: int
    bytes: int
    matched_type: str | None = Field(
        default=None,
        description=(
            "Final document type assigned to this file: the caller's "
            "``expected_type`` when one was given, the classifier's verdict "
            "otherwise. Null when neither resolved."
        ),
    )
    classification: ClassificationInfo | None = None


class Document(BaseModel):
    """Result for one extracted document instance."""

    model_config = ConfigDict(extra="forbid")

    type: str
    source_file: str | None = None
    missing: bool = False
    pages: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str | None = None
    notes: str | None = None
    field_groups: list[ExtractedFieldGroup] = Field(default_factory=list)
    authenticity: DocumentAuthenticity = Field(default_factory=DocumentAuthenticity)


class TraceEntry(BaseModel):
    """One node's execution in the pipeline DAG."""

    model_config = ConfigDict(extra="forbid")

    node: str
    started_at: datetime
    completed_at: datetime
    latency_ms: float
    status: Literal["success", "failed", "skipped"]


class PipelineError(BaseModel):
    """Non-fatal per-node failure surfaced in the response."""

    model_config = ConfigDict(extra="forbid")

    node: str
    code: str
    message: str


class EscalationInfo(BaseModel):
    """Audit block for the judge-driven escalation re-run."""

    model_config = ConfigDict(extra="forbid")

    triggered: bool = False
    primary_model: str | None = None
    escalation_model: str | None = None
    primary_fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    escalation_fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    accepted: bool = False


class UsageBreakdown(BaseModel):
    """Aggregated token usage and cost across every LLM call of one request."""

    model_config = ConfigDict(extra="forbid")

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_requests: int = 0
    total_latency_ms: float = 0.0
    record_count: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    by_agent: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_model: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PipelineMeta(BaseModel):
    """Pipeline-level instrumentation metadata for one extraction."""

    model_config = ConfigDict(extra="forbid")

    model: str
    latency_ms: int = Field(..., ge=0)
    trace: list[TraceEntry] = Field(default_factory=list)
    errors: list[PipelineError] = Field(default_factory=list)
    escalation: EscalationInfo | None = None
    usage: UsageBreakdown | None = None


class ExtractionResult(BaseModel):
    """Top-level response shape (sync /extract, async /extractions/{id}/result)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["success", "partial"] = "success"
    files: list[FileSummary] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    discovered_documents: list[Document] = Field(default_factory=list)
    rule_results: list[RuleResult] = Field(default_factory=list)
    request_transformations: list[ExtractedFieldGroup] = Field(default_factory=list)
    pipeline: PipelineMeta
