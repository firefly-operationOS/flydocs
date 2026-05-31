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

"""Pydantic DTOs used by the public REST API."""

from flydocs.interfaces.dtos.authenticity import (
    ContentAuthenticity,
    ContentCoherenceCheck,
    DocumentAuthenticity,
    VisualCheckResult,
)
from flydocs.interfaces.dtos.bbox import BboxQuality, BboxSource, BoundingBox
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec, VisualCheck
from flydocs.interfaces.dtos.error import ProblemDetails
from flydocs.interfaces.dtos.event import (
    ALL_EVENT_TYPES,
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EventEnvelope,
    envelope_for_publish,
)
from flydocs.interfaces.dtos.extract import (
    ClassificationInfo,
    Document,
    EscalationConfig,
    EscalationInfo,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResult,
    FileInput,
    FileSummary,
    PipelineError,
    PipelineMeta,
    StageToggles,
    TraceEntry,
    UsageBreakdown,
)
from flydocs.interfaces.dtos.extraction import (
    BboxRefinementInfo,
    Extraction,
    ExtractionError,
    ExtractionListQuery,
    ExtractionListResponse,
    ExtractionResultEnvelope,
    PostProcessing,
    SubmitExtractionRequest,
)
from flydocs.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    Field,
    FieldGroup,
    FieldValidation,
    FieldValidationError,
    JudgeOutcome,
)
from flydocs.interfaces.dtos.rule import (
    RuleFieldParent,
    RuleOutputSpec,
    RuleParent,
    RuleResult,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
)
from flydocs.interfaces.dtos.transformation import (
    EntityResolutionTransformation,
    LlmTransformation,
    Transformation,
    TransformationScope,
)
from flydocs.interfaces.dtos.validator import ValidatorSpec

__all__ = [
    "ALL_EVENT_TYPES",
    "BboxQuality",
    "BboxRefinementInfo",
    "BboxSource",
    "BoundingBox",
    "ClassificationInfo",
    "ContentAuthenticity",
    "ContentCoherenceCheck",
    "Document",
    "DocumentAuthenticity",
    "DocumentTypeSpec",
    "EVENT_TYPE_EXTRACTION_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED",
    "EVENT_TYPE_EXTRACTION_SUBMITTED",
    "EntityResolutionTransformation",
    "EscalationConfig",
    "EscalationInfo",
    "EventEnvelope",
    "Extraction",
    "ExtractionError",
    "ExtractionListQuery",
    "ExtractionListResponse",
    "ExtractionOptions",
    "ExtractionRequest",
    "ExtractionResult",
    "ExtractionResultEnvelope",
    "ExtractedField",
    "ExtractedFieldGroup",
    "Field",
    "FieldGroup",
    "FieldValidation",
    "FieldValidationError",
    "FileInput",
    "FileSummary",
    "JudgeOutcome",
    "LlmTransformation",
    "PipelineError",
    "PipelineMeta",
    "PostProcessing",
    "ProblemDetails",
    "RuleFieldParent",
    "RuleOutputSpec",
    "RuleParent",
    "RuleResult",
    "RuleRuleParent",
    "RuleSpec",
    "RuleValidatorParent",
    "StageToggles",
    "SubmitExtractionRequest",
    "TraceEntry",
    "Transformation",
    "TransformationScope",
    "UsageBreakdown",
    "ValidatorSpec",
    "VisualCheck",
    "VisualCheckResult",
    "envelope_for_publish",
]
