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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flydesk_idp.interfaces.dtos.authenticity import DocumentAuthenticity
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.field import ExtractedFieldGroup
from flydesk_idp.interfaces.dtos.rule import RuleResult, RuleSpec


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
        description=(
            "Optional MIME type hint. When omitted, the service sniffs from "
            "magic bytes."
        ),
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

    Two input shapes are supported:

    1. **Single file** -- set ``document``. This is the original API
       surface and stays fully compatible. The single file may itself
       contain multiple document types (passport + utility bill in one
       PDF); the splitter stage handles that.
    2. **Multiple files** -- set ``documents``. Each file is processed
       independently. A file may optionally pin its target type via
       ``document_type``; otherwise the classifier stage matches the
       file to one of the declared :class:`DocSpec`s.

    The two fields are mutually exclusive. Internally the orchestrator
    promotes ``document`` to ``documents = [document]`` so the
    pipeline only has to deal with the list shape.
    """

    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    intention: str = Field(
        default="Extract structured data from the document.",
        description="Free-form prompt that nuances every node's behaviour (search, judge, rules).",
    )
    document: DocumentInput | None = Field(
        default=None,
        description=(
            "Legacy single-file shape. Mutually exclusive with "
            "``documents``. Promoted to ``documents = [document]`` "
            "internally."
        ),
    )
    documents: list[DocumentInput] = Field(
        default_factory=list,
        description=(
            "Multi-file input. Each file is processed independently. "
            "Mutually exclusive with ``document``."
        ),
    )
    docs: list[DocSpec] = Field(..., min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)
    options: ExtractionOptions = Field(default_factory=ExtractionOptions)

    @model_validator(mode="after")
    def _normalise_documents(self) -> "ExtractionRequest":
        if self.document is not None and self.documents:
            raise ValueError(
                "request can carry either ``document`` (legacy single-file) "
                "or ``documents`` (multi-file) but not both"
            )
        if self.document is None and not self.documents:
            raise ValueError(
                "request must carry either ``document`` or at least one "
                "entry in ``documents``"
            )
        return self

    @property
    def files(self) -> list[DocumentInput]:
        """Return every input file as a uniform list, regardless of shape."""
        if self.documents:
            return list(self.documents)
        assert self.document is not None  # guaranteed by the model_validator
        return [self.document]


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
    """Summary of one input file.

    For multi-file requests one ``DocumentInfo`` is produced per
    submitted file; the legacy single-file shape ends up as a list
    of length 1.
    """

    filename: str
    media_type: str
    page_count: int
    bytes: int
    document_type: str | None = Field(
        default=None,
        description=(
            "Final document type assigned to this file: the caller's pin "
            "when one was given, the classifier's verdict otherwise. "
            "``null`` in the legacy single-file shape where the doc type "
            "is implicit in ``docs[0]``."
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
        description=(
            "Filename of the input file this extracted document came "
            "from. Populated for multi-file requests; ``null`` for the "
            "legacy single-file shape."
        ),
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


class ExtractionResult(BaseModel):
    """Top-level response."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: uuid.UUID
    document: DocumentInfo | None = Field(
        default=None,
        description=(
            "Legacy single-file echo of the input. ``None`` when the "
            "request used the multi-file ``documents`` shape -- in that "
            "case read ``files`` instead."
        ),
    )
    files: list[DocumentInfo] = Field(
        default_factory=list,
        description=(
            "Per-file summary for every input file the request carried. "
            "For the legacy single-file shape this is a list of length 1."
        ),
    )
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
