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
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flydocs.interfaces.dtos.authenticity import DocumentAuthenticity
from flydocs.interfaces.dtos.doc import DocSpec
from flydocs.interfaces.dtos.field import ExtractedFieldGroup
from flydocs.interfaces.dtos.rule import RuleResult, RuleSpec
from flydocs.interfaces.dtos.transformation import Transformation

# ---------------------------------------------------------------------------
# Document input
# ---------------------------------------------------------------------------


class DocumentInput(BaseModel):
    """The document payload provided by the caller (binary, base64-encoded).

    A request can carry a single file (``document``) or several
    (``documents``). When several are provided the caller may pin each
    one to a target ``document_type`` directly; otherwise the
    classifier stage decides which ``DocSpec`` applies to which file.
    """

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
        description=("Optional MIME type hint. When omitted, the service sniffs from magic bytes."),
    )
    document_type: str | None = Field(
        default=None,
        description=(
            "When the caller knows which DocSpec this file matches, set it "
            "here (e.g. ``passport``). Skips the classifier for this file. "
            "Must match a ``docs[].docType.documentType`` declared in the "
            "request -- the semantic validator rejects unknown values."
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
    classifier: bool = Field(
        default=True,
        description=(
            "When the caller submits multiple files via ``documents[]`` and "
            "does NOT pin them with ``document_type``, this stage asks the "
            "LLM to classify each file into one of the declared DocSpecs. "
            "Cheap to leave on -- it's a no-op when every file already "
            "carries a ``document_type``."
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
            "defaults (``FLYDOCS_ESCALATION_THRESHOLD`` / "
            "``FLYDOCS_ESCALATION_MODEL``)."
        ),
    )
    bbox_refine: bool = Field(
        default=False,
        description=(
            "Replace LLM-estimated bounding boxes with grounded ones by "
            "fuzzy-matching every extracted value against the document's "
            "real text layer (PyMuPDF for PDFs with embedded text; OCR "
            "for image-only pages and raster inputs). Sub-pixel accurate "
            "for born-digital PDFs. Multilingual: script-aware tokenisation "
            "handles Latin / CJK / Arabic / etc. Adds ~50-200ms for a "
            "30-page text PDF; image-PDFs depend on the OCR engine. The "
            "bbox ``source`` discriminator distinguishes refined "
            "(``pdf_text`` / ``ocr``) from LLM-only fallbacks."
        ),
    )
    transform: bool = Field(
        default=False,
        description=(
            "Run the ``transform`` stage. The stage applies every "
            ":class:`Transformation` declared on "
            "``ExtractionOptions.transformations`` -- declarative entity "
            "resolution and/or free-form LLM transformations -- after "
            "extract+judge and before rules/assemble. No-op when the "
            "list is empty even with the toggle on."
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
            "``FLYDOCS_ESCALATION_THRESHOLD`` for this request."
        ),
    )
    escalation_model: str | None = Field(
        default=None,
        description=(
            "Model id used for the escalation re-run "
            "(e.g. ``anthropic:claude-opus-4-7``). Overrides "
            "``FLYDOCS_ESCALATION_MODEL`` for this request."
        ),
    )
    transformations: list[Transformation] = Field(
        default_factory=list,
        description=(
            "Post-extraction transformations applied by the ``transform`` "
            "stage. See :mod:`flydocs.interfaces.dtos.transformation` "
            "for the discriminated union of available types. Empty list "
            "means the stage is a no-op even when ``stages.transform`` "
            "is true."
        ),
    )


# ---------------------------------------------------------------------------
# The request itself
# ---------------------------------------------------------------------------


class ExtractionRequest(BaseModel):
    """One IDP extraction request.

    Every request carries ``documents`` (a non-empty list). A single
    file is just a one-element list; the pipeline never needs to
    branch on cardinality.
    """

    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    intention: str = Field(
        default="Extract structured data from the document.",
        description="Free-form prompt that nuances every node's behaviour (search, judge, rules).",
    )
    documents: list[DocumentInput] = Field(
        ...,
        min_length=1,
        description=(
            "Input files. Each file is processed independently. A file "
            "may optionally pin its target type via ``document_type``; "
            "otherwise the classifier stage matches it to one of the "
            "declared ``docs`` entries."
        ),
    )
    docs: list[DocSpec] = Field(..., min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)
    options: ExtractionOptions = Field(default_factory=ExtractionOptions)


# ---------------------------------------------------------------------------
# Response side
# ---------------------------------------------------------------------------


class ClassificationInfo(BaseModel):
    """Per-file classifier verdict surfaced in the response.

    Populated only when the classifier ran on this file (multi-file
    request with no caller pin and ``stages.classifier`` enabled).
    ``matched=False`` means the file did not fit any declared
    ``DocSpec`` -- the file ends up in ``additional_documents`` with
    ``document_type='unmatched'``.
    """

    document_type: str
    matched: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str = ""
    notes: str = ""


class DocumentInfo(BaseModel):
    """Summary of one input file. One entry per submitted document."""

    filename: str
    media_type: str
    page_count: int
    bytes: int
    document_type: str | None = Field(
        default=None,
        description=(
            "Final document type assigned to this file: the caller's pin "
            "when one was given, the classifier's verdict otherwise. "
            "``null`` when neither the caller nor the classifier could "
            "settle on a type."
        ),
    )
    classification: ClassificationInfo | None = Field(
        default=None,
        description=(
            "Classifier output for this file. ``null`` when the caller "
            "pinned a ``document_type`` (classifier was skipped) or when "
            "the classifier stage was disabled."
        ),
    )


class ExtractedDocument(BaseModel):
    """Result for one document instance (one DocSpec resolved on one file)."""

    document_type: str
    missing: bool = False
    pages: list[int] = Field(default_factory=list)
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    fields: list[ExtractedFieldGroup] = Field(default_factory=list)
    authenticity: DocumentAuthenticity = Field(default_factory=DocumentAuthenticity)
    notes: str | None = None
    source_file: str | None = Field(
        default=None,
        description="Filename of the input file this extracted document came from.",
    )


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


class UsageBreakdown(BaseModel):
    """Aggregated token usage and cost across every LLM call of one request.

    Populated by the orchestrator from the framework's per-call
    :class:`UsageRecord`s, scoped to the request via ``correlation_id``.
    Mirrors :class:`fireflyframework_agentic.observability.UsageSummary`.

    ``by_agent`` keys are the internal agent names (e.g.
    ``flydocs-extractor``, ``flydocs-classifier``,
    ``flydocs-splitter``). ``by_model`` keys are the fully-qualified
    model ids (e.g. ``anthropic:claude-opus-4-7``).
    """

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


class TraceEntry(BaseModel):
    """One node's execution in the pipeline DAG."""

    node: str
    started_at: datetime
    completed_at: datetime
    latency_ms: float
    status: str = Field(description="``success`` | ``failed`` | ``skipped``.")


class ExtractionResult(BaseModel):
    """Top-level response."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: uuid.UUID
    files: list[DocumentInfo] = Field(
        default_factory=list,
        description="Per-file summary for every input file the request carried.",
    )
    documents: list[ExtractedDocument] = Field(default_factory=list)
    additional_documents: list[ExtractedDocument] = Field(
        default_factory=list,
        description="Documents found in the source PDF that don't match any requested doc type.",
    )
    rule_results: list[RuleResult] = Field(default_factory=list)
    request_transformations: list[ExtractedFieldGroup] = Field(
        default_factory=list,
        description=(
            "Output of every ``scope=request`` transformation applied "
            "by the ``transform`` stage. Each entry is a consolidated, "
            "post-transformation field group keyed by the "
            "``output_group`` name from the originating "
            ":class:`Transformation` (or the ``target_group`` when "
            "``output_group`` is null). Empty list when no "
            "request-scope transformation ran or when none of them "
            "produced output."
        ),
    )
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
    usage: UsageBreakdown | None = Field(
        default=None,
        description=(
            "Aggregated token usage and estimated USD cost across every "
            "LLM call this request made (extract, classifier, splitter, "
            "judge, visual, content, rules). ``null`` when cost tracking "
            "is disabled or no LLM calls fired."
        ),
    )
    trace: list[TraceEntry] = Field(
        default_factory=list,
        description=(
            "Per-stage execution trace as the orchestrator's DAG ran it. "
            "One entry per executed node with start/end timestamps, "
            "latency, and status."
        ),
    )
