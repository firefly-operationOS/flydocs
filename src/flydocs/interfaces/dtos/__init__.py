# Copyright 2026 Firefly Software Solutions Inc
"""Pydantic DTOs used by the public REST API."""

from flydocs.interfaces.dtos.authenticity import (
    ContentAuthenticity,
    ContentCoherenceCheck,
    DocumentAuthenticity,
    VisualValidationOutcome,
)
from flydocs.interfaces.dtos.bbox import BoundingBox
from flydocs.interfaces.dtos.doc import DocSpec, DocType, ValidatorsSpec, VisualValidatorSpec
from flydocs.interfaces.dtos.error import ProblemDetails
from flydocs.interfaces.dtos.extract import (
    DocumentInfo,
    DocumentInput,
    ExtractedDocument,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResult,
    StageToggles,
)
from flydocs.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    FieldGroup,
    FieldItem,
    FieldSpec,
    FieldValidation,
    FieldValidationError,
    JudgeOutcome,
)
from flydocs.interfaces.dtos.job import (
    JobResult,
    JobStatusResponse,
    SubmitJobRequest,
    SubmitJobResponse,
)
from flydocs.interfaces.dtos.rule import (
    RuleFieldParent,
    RuleOutputSpec,
    RuleResult,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
)
from flydocs.interfaces.dtos.standard_validator import StandardValidatorSpec
from flydocs.interfaces.dtos.webhook import JobWebhookPayload

__all__ = [
    "BoundingBox",
    "ContentAuthenticity",
    "ContentCoherenceCheck",
    "DocSpec",
    "DocType",
    "DocumentAuthenticity",
    "DocumentInfo",
    "DocumentInput",
    "ExtractedDocument",
    "ExtractedField",
    "ExtractedFieldGroup",
    "ExtractionOptions",
    "ExtractionRequest",
    "ExtractionResult",
    "FieldGroup",
    "FieldItem",
    "FieldSpec",
    "FieldValidation",
    "FieldValidationError",
    "JobResult",
    "JobStatusResponse",
    "JobWebhookPayload",
    "JudgeOutcome",
    "ProblemDetails",
    "RuleFieldParent",
    "RuleOutputSpec",
    "RuleResult",
    "RuleRuleParent",
    "RuleSpec",
    "RuleValidatorParent",
    "StageToggles",
    "StandardValidatorSpec",
    "SubmitJobRequest",
    "SubmitJobResponse",
    "ValidatorsSpec",
    "VisualValidationOutcome",
    "VisualValidatorSpec",
]
