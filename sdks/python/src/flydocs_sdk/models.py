# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Wire-level Pydantic models for the flydocs v1 HTTP API.

The SDK ships its own copies of the request / response shapes rather
than re-exporting :mod:`flydocs.interfaces` from the service package.
Two reasons:

* **Independence.** Installing ``flydocs-sdk`` should not pull in the
  service runtime (pyfly, agentic, SQLAlchemy, FastAPI, ...). Keeping
  models local keeps the dep graph to ``httpx + pydantic``.
* **Forward-compatibility.** Pydantic v2 with ``model_config =
  ConfigDict(extra="allow")`` lets the SDK tolerate new fields the
  service adds before the SDK is upgraded. Callers can still read the
  new fields out of ``model_extra`` even when the SDK has no typed
  attribute for them.

The v1 contract is snake_case everywhere on the wire. This module
mirrors :mod:`flydocs.interfaces.dtos` on the service side, but every
model declares ``ConfigDict(extra="allow", populate_by_name=True)`` for
forward compatibility.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import Field as _F  # noqa: N814  -- private alias keeps pydantic.Field unshadowed

# ---------------------------------------------------------------------------
# Permissive base
# ---------------------------------------------------------------------------


class _WireBase(BaseModel):
    """Common config for every wire model in the SDK.

    * ``extra="allow"`` -- the SDK doesn't have to be updated in
      lockstep with new fields the service starts emitting.
    * ``populate_by_name=True`` -- callers can construct models with
      either Python-style snake_case or any explicit JSON alias.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ExtractionStatus(StrEnum):
    """Main lifecycle states for an async extraction job.

    Mirrors :class:`flydocs.interfaces.enums.extraction_status.ExtractionStatus`.
    Values are lowercase snake_case in v1 (`queued`, `running`, ...).
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """True when no further state transition is expected."""
        return self in (
            ExtractionStatus.SUCCEEDED,
            ExtractionStatus.FAILED,
            ExtractionStatus.CANCELLED,
        )


class PostProcessingStatus(StrEnum):
    """Sub-state for additive post-processing legs (bbox refinement today)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (PostProcessingStatus.SUCCEEDED, PostProcessingStatus.FAILED)


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


class ValidatorType(StrEnum):
    """Built-in validator catalogue applied to extracted field values.

    Mirrors :class:`flydocs.interfaces.enums.validator.ValidatorType`.
    """

    # Network / web
    EMAIL = "email"
    URI = "uri"
    URL = "url"
    DOMAIN = "domain"
    SLUG = "slug"
    IPV4 = "ipv4"
    IPV6 = "ipv6"

    # Temporal
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    ISO_8601 = "iso_8601"

    # Identifiers
    UUID = "uuid"
    JSON = "json"
    HEX_COLOR = "hex_color"

    # Finance
    IBAN = "iban"
    BIC = "bic"
    CREDIT_CARD = "credit_card"
    CURRENCY_CODE = "currency_code"
    AMOUNT = "amount"

    # Telephony
    PHONE_E164 = "phone_e164"

    # Geographic
    COUNTRY_CODE = "country_code"
    LANGUAGE_CODE = "language_code"
    POSTAL_CODE = "postal_code"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"

    # National IDs
    NIF = "nif"
    NIE = "nie"
    CIF = "cif"
    VAT_ID = "vat_id"
    SSN = "ssn"
    PASSPORT_NUMBER = "passport_number"


class ValidationRule(StrEnum):
    """Which validation check produced a given error."""

    TYPE = "type"
    PATTERN = "pattern"
    FORMAT = "format"
    ENUM = "enum"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    VALIDATOR = "validator"


class JudgeStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


class ContentIntegrityStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


class BboxQuality(StrEnum):
    """Coarse-grained verdict on whether a bbox is trustworthy."""

    GOOD = "good"
    POOR = "poor"
    SUSPICIOUS = "suspicious"
    INVALID = "invalid"


class BboxSource(StrEnum):
    """How the coordinates on this bbox were produced."""

    LLM = "llm"
    PDF_TEXT = "pdf_text"
    OCR = "ocr"


class TransformationScope(StrEnum):
    """Whether a transformation applies per-document or across the whole request."""

    TASK = "task"
    REQUEST = "request"


# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------


class BoundingBox(_WireBase):
    """Normalised rectangle on a single page (coordinates in [0, 1])."""

    xmin: float = _F(..., ge=0.0, le=1.0)
    ymin: float = _F(..., ge=0.0, le=1.0)
    xmax: float = _F(..., ge=0.0, le=1.0)
    ymax: float = _F(..., ge=0.0, le=1.0)
    quality: BboxQuality | None = None
    quality_score: float = _F(default=0.0, ge=0.0, le=1.0)
    source: BboxSource | None = None
    refinement_confidence: float | None = _F(default=None, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Validators (request side)
# ---------------------------------------------------------------------------


class ValidatorSpec(_WireBase):
    """One built-in validator applied to a field.

    Replaces the v0 ``StandardValidatorSpec``. Dispatch key is ``name``
    (not ``type``) so it doesn't collide with :class:`Field.type` when
    both appear on the same parent envelope.

    Examples::

        ValidatorSpec(name=ValidatorType.IBAN)
        ValidatorSpec(name="phone_e164", params={"country": "ES"})
        ValidatorSpec(name="vat_id", params={"country": "ES"}, severity="warning")
    """

    name: ValidatorType
    params: dict[str, Any] = _F(default_factory=dict)
    severity: Literal["error", "warning"] = "error"


# ---------------------------------------------------------------------------
# Recursive Field schema (request side)
# ---------------------------------------------------------------------------


class Field_(_WireBase):  # noqa: N801 -- public alias ``Field`` exported below
    """One field in a schema. Recursive for arrays and objects.

    * Primitives: any of ``string`` / ``number`` / ``integer`` / ``boolean``;
      ``items`` and ``fields`` MUST be ``None``.
    * Array: ``type=array`` + a single ``items`` (a :class:`Field` describing
      the row shape, typically of type ``object``).
    * Object: ``type=object`` + a non-empty ``fields`` list of :class:`Field`.

    Exposed publicly as ``Field``; the trailing underscore avoids clashing
    with :func:`pydantic.Field` inside this module.
    """

    name: str = _F(..., min_length=1)
    description: str | None = None
    type: FieldType = FieldType.STRING
    required: bool = False
    pattern: str | None = None
    format: StandardFormat | None = None
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    items: Field_ | None = None
    fields: list[Field_] | None = None
    validators: list[ValidatorSpec] = _F(default_factory=list)


# Public name. The internal class is :class:`Field_` (trailing underscore
# only to avoid shadowing :func:`pydantic.Field` inside this module).
Field = Field_


class FieldGroup(_WireBase):
    """A named bundle of fields the service should extract together."""

    name: str = _F(..., min_length=1)
    description: str | None = None
    fields: list[Field_] = _F(..., min_length=1)


Field_.model_rebuild()
FieldGroup.model_rebuild()


# ---------------------------------------------------------------------------
# Document type (request side)
# ---------------------------------------------------------------------------


class VisualCheck(_WireBase):
    """One visual check to run against the document (signature, watermark, ...)."""

    name: str = _F(..., min_length=1)
    description: str


class DocumentTypeSpec(_WireBase):
    """One expected document type the caller is submitting fields for.

    Replaces the v0 ``DocSpec`` / ``DocType`` pair. ``id`` is the stable
    identifier (e.g. ``"invoice"``); ``description`` / ``country`` are the
    flattened metadata that used to live under ``DocType``.
    """

    id: str = _F(..., min_length=1)
    description: str | None = None
    country: str | None = None
    field_groups: list[FieldGroup] = _F(..., min_length=1)
    visual_checks: list[VisualCheck] = _F(default_factory=list)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class _BaseRuleParent(_WireBase):
    pass


class RuleFieldParent(_BaseRuleParent):
    kind: Literal["field"] = "field"
    document_type: str
    fields: list[str] = _F(..., min_length=1)


class RuleValidatorParent(_BaseRuleParent):
    kind: Literal["validator"] = "validator"
    document_type: str
    validator: str


class RuleRuleParent(_BaseRuleParent):
    kind: Literal["rule"] = "rule"
    rule: str


RuleParent = Annotated[
    RuleFieldParent | RuleValidatorParent | RuleRuleParent,
    _F(discriminator="kind"),
]


class RuleOutputSpec(_WireBase):
    """How the rule's output is interpreted."""

    type: str = "boolean"
    valid_outputs: list[str] | None = None


class RuleSpec(_WireBase):
    """One business rule expressed as a natural-language predicate.

    RuleSpec(
        id="invoice_total_matches",
        predicate="Total equals the sum of line items",
        parents=[RuleFieldParent(document_type="invoice", fields=["total", "line_items"])],
    )
    """

    id: str = _F(..., min_length=1)
    predicate: str = _F(..., min_length=1)
    parents: list[RuleParent] = _F(default_factory=list)
    output: RuleOutputSpec = _F(default_factory=RuleOutputSpec)


# ---------------------------------------------------------------------------
# Transformations
# ---------------------------------------------------------------------------


class _BaseTransformation(_WireBase):
    id: str = _F(default_factory=lambda: str(uuid.uuid4()))
    target_group: str = _F(..., min_length=1)
    output_group: str | None = None
    scope: TransformationScope = TransformationScope.TASK


class EntityResolutionTransformation(_BaseTransformation):
    """Deterministic deduplication of an array field group's rows."""

    type: Literal["entity_resolution"] = "entity_resolution"
    match_by: list[str] = _F(..., min_length=1)
    min_shared_tokens: int = _F(default=2, ge=1)


class PartsOfWholeInvariant(_WireBase):
    """A caller-declared "the parts must sum to a whole" constraint on an LLM
    transformation: the ``share_field`` values must sum to ``total`` (within
    ``tolerance``); on an over-sum the service repairs or warns. Domain-agnostic."""

    share_field: str = _F(..., min_length=1)
    total: float = _F(default=100.0, gt=0.0)
    tolerance: float = _F(default=0.5, ge=0.0)
    on_violation: Literal["repair", "warn"] = "repair"


class LlmTransformation(_BaseTransformation):
    """Free-form LLM transformation of an array field group's rows."""

    type: Literal["llm"] = "llm"
    intention: str = _F(..., min_length=10)
    prompt_id: str | None = None
    include_provenance: bool = True
    invariant: PartsOfWholeInvariant | None = None


Transformation = Annotated[
    EntityResolutionTransformation | LlmTransformation,
    _F(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Pipeline options
# ---------------------------------------------------------------------------


class StageToggles(_WireBase):
    """Opt-in switches for every optional pipeline stage."""

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


class EscalationConfig(_WireBase):
    """Configuration for the judge_escalation stage."""

    threshold: float = _F(..., ge=0.0, le=1.0)
    model: str = _F(..., min_length=1)


class ExtractionOptions(_WireBase):
    """Per-request pipeline knobs."""

    model: str | None = None
    language_hint: str | None = _F(default=None, max_length=16)
    return_bboxes: bool = True
    declared_media_type: str | None = None
    stages: StageToggles = _F(default_factory=StageToggles)
    escalation: EscalationConfig | None = None
    transformations: list[Transformation | dict[str, Any]] = _F(default_factory=list)


# ---------------------------------------------------------------------------
# Input file
# ---------------------------------------------------------------------------


class FileInput(_WireBase):
    """One input file for an extraction request.

    Replaces the v0 ``DocumentInput``. JSON mode: caller sets
    ``content_base64``. Multipart mode: the binary rides in a separate
    file part; ``content_base64`` is absent and ``filename`` /
    ``content_type`` come from the part headers.
    """

    filename: str = _F(..., min_length=1)
    content_base64: str | None = _F(default=None)
    content_type: str | None = None
    expected_type: str | None = None

    @field_validator("content_base64")
    @classmethod
    def _strip_data_url_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("data:") and "," in value:
            return value.split(",", 1)[1]
        return value

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        filename: str,
        content_type: str | None = None,
        expected_type: str | None = None,
    ) -> FileInput:
        """Build a :class:`FileInput` from raw bytes (encodes to base64)."""
        return cls(
            filename=filename,
            content_base64=base64.b64encode(data).decode("ascii"),
            content_type=content_type,
            expected_type=expected_type,
        )

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        content_type: str | None = None,
        expected_type: str | None = None,
    ) -> FileInput:
        """Read a file off disk and produce a :class:`FileInput`."""
        path = Path(path)
        return cls.from_bytes(
            path.read_bytes(),
            filename=path.name,
            content_type=content_type,
            expected_type=expected_type,
        )


# ---------------------------------------------------------------------------
# Extraction request
# ---------------------------------------------------------------------------


class ExtractionRequest(_WireBase):
    """Request body for ``POST /api/v1/extract`` and ``POST /api/v1/extract:validate``."""

    intention: str = "Extract structured data from the document."
    files: list[FileInput] = _F(..., min_length=1)
    document_types: list[DocumentTypeSpec | dict[str, Any]] = _F(..., min_length=1)
    rules: list[RuleSpec | dict[str, Any]] = _F(default_factory=list)
    options: ExtractionOptions | dict[str, Any] = _F(default_factory=ExtractionOptions)


class SubmitExtractionRequest(ExtractionRequest):
    """Request body for ``POST /api/v1/extractions``.

    Superset of :class:`ExtractionRequest` plus the async-only
    ``callback_url`` and ``metadata`` fields.
    """

    callback_url: str | None = None
    metadata: dict[str, Any] = _F(default_factory=dict)


# ---------------------------------------------------------------------------
# Response side -- field validation
# ---------------------------------------------------------------------------


class FieldValidationError(_WireBase):
    rule: ValidationRule
    message: str


class FieldValidation(_WireBase):
    valid: bool = True
    errors: list[FieldValidationError] = _F(default_factory=list)


class JudgeOutcome(_WireBase):
    status: JudgeStatus = JudgeStatus.UNCERTAIN
    confidence: float = _F(default=0.0, ge=0.0, le=1.0)
    evidence: str | None = None
    notes: str | None = None
    flag_for_review: bool = False


class ExtractedField(_WireBase):
    """One extracted field. Recursive for arrays and objects.

    Replaces the v0 ``ExtractedField`` (which had camelCase aliases and
    a separate ``ExtractedArrayField`` shape). Canonical keys in v1 are
    ``name``, ``value``, ``pages``, ``confidence``, ``bbox``,
    ``validation``, ``judge``, ``notes``.
    """

    name: str
    value: str | int | float | bool | list[ExtractedField] | None = None
    pages: list[int] = _F(default_factory=list)
    confidence: float = _F(default=0.0, ge=0.0, le=1.0)
    bbox: BoundingBox | None = None
    validation: FieldValidation = _F(default_factory=FieldValidation)
    judge: JudgeOutcome = _F(default_factory=JudgeOutcome)
    notes: str | None = None
    source: str | None = None


ExtractedField.model_rebuild()


class ExtractedFieldGroup(_WireBase):
    name: str
    fields: list[ExtractedField]


# ---------------------------------------------------------------------------
# Response side -- authenticity
# ---------------------------------------------------------------------------


class VisualCheckResult(_WireBase):
    name: str
    passed: bool
    confidence: float = _F(default=0.0, ge=0.0, le=1.0)
    notes: str | None = None


class ContentCoherenceCheck(_WireBase):
    name: str
    description: str
    status: CheckStatus
    evidence: str | None = None
    reasoning: str | None = None


class ContentAuthenticity(_WireBase):
    overall_integrity_status: ContentIntegrityStatus = ContentIntegrityStatus.UNCERTAIN
    checks: list[ContentCoherenceCheck] = _F(default_factory=list)


class DocumentAuthenticity(_WireBase):
    visual: list[VisualCheckResult] = _F(default_factory=list)
    content: ContentAuthenticity | None = None


# ---------------------------------------------------------------------------
# Response side -- top-level
# ---------------------------------------------------------------------------


class ClassificationInfo(_WireBase):
    """Per-file classifier verdict."""

    document_type: str
    matched: bool = True
    confidence: float = _F(default=0.0, ge=0.0, le=1.0)
    description: str | None = None
    notes: str | None = None


class FileSummary(_WireBase):
    """Summary of one input file in the response.

    Replaces v0 ``DocumentInfo``. ``matched_type`` replaces the v0
    ``document_type`` field; it carries the caller's pinned
    ``expected_type`` when set, the classifier's verdict otherwise.
    """

    filename: str
    media_type: str
    page_count: int
    bytes: int
    matched_type: str | None = None
    classification: ClassificationInfo | None = None


class Document(_WireBase):
    """Result for one extracted document instance.

    Replaces v0 ``ExtractedDocument``. ``type`` replaces v0
    ``document_type``; ``field_groups`` replaces v0 ``fields``.
    """

    type: str
    source_file: str | None = None
    missing: bool = False
    pages: list[int] = _F(default_factory=list)
    confidence: float = _F(default=0.0, ge=0.0, le=1.0)
    description: str | None = None
    notes: str | None = None
    field_groups: list[ExtractedFieldGroup] = _F(default_factory=list)
    authenticity: DocumentAuthenticity = _F(default_factory=DocumentAuthenticity)


class TraceEntry(_WireBase):
    node: str
    started_at: datetime
    completed_at: datetime
    latency_ms: float
    status: Literal["success", "failed", "skipped"]


class PipelineError(_WireBase):
    node: str
    code: str
    message: str


class EscalationInfo(_WireBase):
    triggered: bool = False
    primary_model: str | None = None
    escalation_model: str | None = None
    primary_fail_rate: float = _F(default=0.0, ge=0.0, le=1.0)
    escalation_fail_rate: float = _F(default=0.0, ge=0.0, le=1.0)
    accepted: bool = False


class UsageBreakdown(_WireBase):
    """Aggregated token usage and cost across every LLM call of one request."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_requests: int = 0
    total_latency_ms: float = 0.0
    record_count: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    by_agent: dict[str, dict[str, Any]] = _F(default_factory=dict)
    by_model: dict[str, dict[str, Any]] = _F(default_factory=dict)


class PipelineMeta(_WireBase):
    """Pipeline-level instrumentation metadata.

    Replaces the top-level ``model``/``latency_ms``/``trace``/
    ``pipeline_errors``/``escalation``/``usage`` fields from the v0
    ``ExtractionResult`` shape. v1 nests them all under one block.
    """

    model: str
    latency_ms: int = _F(..., ge=0)
    trace: list[TraceEntry] = _F(default_factory=list)
    errors: list[PipelineError] = _F(default_factory=list)
    escalation: EscalationInfo | None = None
    usage: UsageBreakdown | None = None


class RuleResult(_WireBase):
    """Per-rule outcome returned in the response.

    Both ``summary`` and ``human_revision`` are optional in v1.
    """

    rule_id: str
    predicate: str
    output: str = ""
    summary: str | None = None
    notes: list[str] = _F(default_factory=list)
    human_revision: str | None = None


class ExtractionResult(_WireBase):
    """Top-level response shape (sync ``/extract`` + async result envelope).

    Replaces v0 ``ExtractionResult``. ``id`` replaces ``request_id``;
    ``discovered_documents`` replaces ``additional_documents``;
    ``pipeline`` collapses the v0 top-level meta fields into one block.
    """

    id: str
    status: Literal["success", "partial"] = "success"
    files: list[FileSummary] = _F(default_factory=list)
    documents: list[Document] = _F(default_factory=list)
    discovered_documents: list[Document] = _F(default_factory=list)
    rule_results: list[RuleResult] = _F(default_factory=list)
    request_transformations: list[ExtractedFieldGroup] = _F(default_factory=list)
    pipeline: PipelineMeta


# ---------------------------------------------------------------------------
# Extraction lifecycle (async)
# ---------------------------------------------------------------------------


class ExtractionError(_WireBase):
    """Terminal-state error info for a failed extraction."""

    code: str
    message: str


class BboxRefinementInfo(_WireBase):
    """Lifecycle info for the bbox-refinement post-processing leg."""

    status: PostProcessingStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    error: ExtractionError | None = None


class PostProcessing(_WireBase):
    """Container for post-processing legs attached to a succeeded extraction."""

    bbox_refinement: BboxRefinementInfo | None = None


class Extraction(_WireBase):
    """Current state snapshot of an async extraction job.

    Replaces v0 ``JobStatusResponse`` and ``SubmitJobResponse`` (both are
    the same shape in v1).
    """

    id: str
    status: ExtractionStatus
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    error: ExtractionError | None = None
    post_processing: PostProcessing | None = None


class ExtractionResultEnvelope(_WireBase):
    """Response body for ``GET /api/v1/extractions/{id}/result``.

    Replaces v0 ``JobResult``.
    """

    id: str
    result: ExtractionResult


class ExtractionListQuery(_WireBase):
    """Query parameters for ``GET /api/v1/extractions``.

    Carried as the typed input to :meth:`flydocs_sdk.client.ExtractionsResource.list`.
    """

    statuses: list[ExtractionStatus] = _F(default_factory=list)
    post_processing_statuses: list[PostProcessingStatus] = _F(default_factory=list)
    created_after: datetime | None = None
    created_before: datetime | None = None
    idempotency_key: str | None = None
    limit: int = _F(default=50, ge=1, le=500)
    offset: int = _F(default=0, ge=0)


class ExtractionListResponse(_WireBase):
    """Paginated list response."""

    items: list[Extraction]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Validation response (POST /api/v1/extract:validate)
# ---------------------------------------------------------------------------


class ValidationResponse(_WireBase):
    """Dry-run validator output. Always returned with 200 OK."""

    ok: bool
    error_count: int = 0
    warning_count: int = 0
    errors: list[dict[str, Any]] = _F(default_factory=list)
    warnings: list[dict[str, Any]] = _F(default_factory=list)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class VersionInfo(_WireBase):
    """Response body for ``GET /api/v1/version``."""

    service: str
    version: str
    model: str
    fallback_model: str = ""
    eda_adapter: str


# ---------------------------------------------------------------------------
# Event envelope (EDA + webhooks)
# ---------------------------------------------------------------------------


EVENT_TYPE_EXTRACTION_SUBMITTED = "extraction.submitted"
EVENT_TYPE_EXTRACTION_COMPLETED = "extraction.completed"
EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED = "extraction.post_processing.requested"
EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED = "extraction.post_processing.completed"

ALL_EVENT_TYPES = (
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _new_event_id() -> str:
    return str(uuid.uuid4())


class EventEnvelope(_WireBase):
    """Shared envelope for EDA events and webhook deliveries.

    Replaces v0 ``JobWebhookPayload``. ``extraction`` carries a current-
    state snapshot of the resource. ``result`` is populated only on
    ``extraction.completed`` events when the terminal status is
    ``succeeded``; null otherwise.
    """

    event_id: str = _F(default_factory=_new_event_id)
    event_type: str
    version: str = "1.0.0"
    occurred_at: datetime = _F(default_factory=_now_utc)
    correlation_id: str | None = None
    tenant_id: str | None = None
    extraction: Extraction
    result: ExtractionResult | None = None
    metadata: dict[str, Any] = _F(default_factory=dict)
