# Copyright 2026 Firefly Software Solutions Inc
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

"""Official Python SDK for flydocs (v1 contract).

flydocs is a pure-multimodal Intelligent Document Processing service:
structured field extraction with bounding boxes, validation,
authenticity checks, LLM judge, and a business-rule engine.

This package gives Python callers a typed, async-first client over the
service's REST API, plus a synchronous wrapper for non-async code and a
helper for verifying outbound webhook signatures.

    from flydocs_sdk import (
        Client, DocumentTypeSpec, ExtractionRequest, Field, FieldGroup,
        FieldType, FileInput,
    )

    invoice = DocumentTypeSpec(
        id="invoice",
        field_groups=[
            FieldGroup(name="totals", fields=[
                Field(name="total_amount", type=FieldType.NUMBER, required=True),
                Field(name="currency",     type=FieldType.STRING, required=True),
            ]),
        ],
    )

    with Client("http://localhost:8400") as flydocs:
        result = flydocs.extract(
            ExtractionRequest(
                files=[FileInput.from_path("invoice.pdf")],
                document_types=[invoice],
            )
        )
"""

from flydocs_sdk._version import __version__
from flydocs_sdk.async_client import AsyncClient, AsyncExtractionsResource
from flydocs_sdk.client import Client, ExtractionsResource
from flydocs_sdk.errors import (
    FlydocsAPIError,
    FlydocsClientError,
    FlydocsError,
    FlydocsHTTPError,
    FlydocsHttpError,
    FlydocsTimeoutError,
    ProblemDetails,
)
from flydocs_sdk.models import (
    ALL_EVENT_TYPES,
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    BboxQuality,
    BboxRefinementInfo,
    BboxSource,
    BoundingBox,
    CheckStatus,
    ClassificationInfo,
    ContentAuthenticity,
    ContentCoherenceCheck,
    ContentIntegrityStatus,
    Document,
    DocumentAuthenticity,
    DocumentTypeSpec,
    EntityResolutionTransformation,
    EscalationConfig,
    EscalationInfo,
    EventEnvelope,
    ExtractedField,
    ExtractedFieldGroup,
    Extraction,
    ExtractionError,
    ExtractionListQuery,
    ExtractionListResponse,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResult,
    ExtractionResultEnvelope,
    ExtractionStatus,
    Field,
    FieldGroup,
    FieldType,
    FieldValidation,
    FieldValidationError,
    FileInput,
    FileSummary,
    JudgeOutcome,
    JudgeStatus,
    LlmTransformation,
    PipelineError,
    PipelineMeta,
    PostProcessing,
    PostProcessingStatus,
    RuleFieldParent,
    RuleOutputSpec,
    RuleParent,
    RuleResult,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
    StageToggles,
    StandardFormat,
    SubmitExtractionRequest,
    TraceEntry,
    Transformation,
    TransformationScope,
    UsageBreakdown,
    ValidationResponse,
    ValidationRule,
    ValidatorSpec,
    ValidatorType,
    VersionInfo,
    VisualCheck,
    VisualCheckResult,
)
from flydocs_sdk.webhooks import WebhookVerificationError, WebhookVerifier

__all__ = [
    "__version__",
    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------
    "AsyncClient",
    "AsyncExtractionsResource",
    "Client",
    "ExtractionsResource",
    # ------------------------------------------------------------------
    # Errors
    # ------------------------------------------------------------------
    "FlydocsAPIError",
    "FlydocsClientError",
    "FlydocsError",
    "FlydocsHTTPError",
    "FlydocsHttpError",
    "FlydocsTimeoutError",
    "ProblemDetails",
    # ------------------------------------------------------------------
    # Event-type constants
    # ------------------------------------------------------------------
    "ALL_EVENT_TYPES",
    "EVENT_TYPE_EXTRACTION_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED",
    "EVENT_TYPE_EXTRACTION_SUBMITTED",
    # ------------------------------------------------------------------
    # Wire models -- request side
    # ------------------------------------------------------------------
    "DocumentTypeSpec",
    "EntityResolutionTransformation",
    "EscalationConfig",
    "ExtractionOptions",
    "ExtractionRequest",
    "Field",
    "FieldGroup",
    "FieldType",
    "FileInput",
    "LlmTransformation",
    "RuleFieldParent",
    "RuleOutputSpec",
    "RuleParent",
    "RuleRuleParent",
    "RuleSpec",
    "RuleValidatorParent",
    "StageToggles",
    "StandardFormat",
    "SubmitExtractionRequest",
    "Transformation",
    "TransformationScope",
    "ValidatorSpec",
    "ValidatorType",
    "VisualCheck",
    # ------------------------------------------------------------------
    # Wire models -- response side
    # ------------------------------------------------------------------
    "BboxQuality",
    "BboxSource",
    "BoundingBox",
    "CheckStatus",
    "ClassificationInfo",
    "ContentAuthenticity",
    "ContentCoherenceCheck",
    "ContentIntegrityStatus",
    "Document",
    "DocumentAuthenticity",
    "EscalationInfo",
    "ExtractedField",
    "ExtractedFieldGroup",
    "ExtractionResult",
    "FieldValidation",
    "FieldValidationError",
    "FileSummary",
    "JudgeOutcome",
    "JudgeStatus",
    "PipelineError",
    "PipelineMeta",
    "RuleResult",
    "TraceEntry",
    "UsageBreakdown",
    "ValidationResponse",
    "ValidationRule",
    "VisualCheckResult",
    # ------------------------------------------------------------------
    # Wire models -- extraction lifecycle
    # ------------------------------------------------------------------
    "BboxRefinementInfo",
    "Extraction",
    "ExtractionError",
    "ExtractionListQuery",
    "ExtractionListResponse",
    "ExtractionResultEnvelope",
    "ExtractionStatus",
    "PostProcessing",
    "PostProcessingStatus",
    # ------------------------------------------------------------------
    # Wire models -- identity + events
    # ------------------------------------------------------------------
    "EventEnvelope",
    "VersionInfo",
    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    "WebhookVerificationError",
    "WebhookVerifier",
]
