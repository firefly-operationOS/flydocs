# Copyright 2026 Firefly Software Solutions Inc
"""Pydantic DTOs used by the public REST API."""

from flydesk_idp.interfaces.dtos.authenticity import (
    ContentAuthenticity,
    ContentCoherenceCheck,
    DocumentAuthenticity,
    VisualValidationOutcome,
)
from flydesk_idp.interfaces.dtos.bbox import BoundingBox
from flydesk_idp.interfaces.dtos.doc import DocSpec, DocType, ValidatorsSpec, VisualValidatorSpec
from flydesk_idp.interfaces.dtos.error import ProblemDetails
from flydesk_idp.interfaces.dtos.extract import (
    DocumentInfo,
    DocumentInput,
    ExtractedDocument,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResult,
    StageToggles,
)
from flydesk_idp.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    FieldGroup,
    FieldItem,
    FieldSpec,
    FieldValidation,
    FieldValidationError,
    JudgeOutcome,
)
from flydesk_idp.interfaces.dtos.job import (
    JobResult,
    JobStatusResponse,
    SubmitJobRequest,
    SubmitJobResponse,
)
from flydesk_idp.interfaces.dtos.rule import (
    RuleFieldParent,
    RuleOutputSpec,
    RuleResult,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
)
from flydesk_idp.interfaces.dtos.standard_validator import StandardValidatorSpec
from flydesk_idp.interfaces.dtos.webhook import JobWebhookPayload

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
