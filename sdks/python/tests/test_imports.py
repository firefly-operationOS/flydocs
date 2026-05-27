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

"""Smoke import test for the full v1 SDK surface.

If a symbol disappears from ``__all__`` (or its module) this test
fails noisily before the package is shipped. Each name maps to one
of the four surface families described in the spec:

* clients
* errors
* event-type constants + envelope
* wire-level Pydantic models + the recursive Field shape
"""

from __future__ import annotations

import pytest

import flydocs_sdk

EXPECTED_EXPORTS: tuple[str, ...] = (
    # Version
    "__version__",
    # Clients
    "AsyncClient",
    "AsyncExtractionsResource",
    "Client",
    "ExtractionsResource",
    # Errors
    "FlydocsAPIError",
    "FlydocsClientError",
    "FlydocsError",
    "FlydocsHTTPError",
    "FlydocsHttpError",
    "FlydocsTimeoutError",
    "ProblemDetails",
    # Event-type constants
    "ALL_EVENT_TYPES",
    "EVENT_TYPE_EXTRACTION_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED",
    "EVENT_TYPE_EXTRACTION_SUBMITTED",
    # Request side
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
    # Response side
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
    # Extraction lifecycle
    "BboxRefinementInfo",
    "Extraction",
    "ExtractionError",
    "ExtractionListQuery",
    "ExtractionListResponse",
    "ExtractionResultEnvelope",
    "ExtractionStatus",
    "PostProcessing",
    "PostProcessingStatus",
    # Identity + events
    "EventEnvelope",
    "VersionInfo",
    # Webhooks
    "WebhookVerificationError",
    "WebhookVerifier",
)


@pytest.mark.parametrize("name", EXPECTED_EXPORTS)
def test_exported(name: str) -> None:
    assert hasattr(flydocs_sdk, name), f"flydocs_sdk is missing export {name!r}"


def test_version_string() -> None:
    assert isinstance(flydocs_sdk.__version__, str)
    assert flydocs_sdk.__version__.count(".") >= 1


def test_all_set_matches_expected() -> None:
    public = {name for name in flydocs_sdk.__all__ if not name.startswith("_")}
    missing = set(EXPECTED_EXPORTS) - set(flydocs_sdk.__all__) - {"__version__"}
    extras = public - set(EXPECTED_EXPORTS) - {"__version__"}
    assert not missing, f"__all__ is missing: {missing}"
    # ``extras`` is informational only -- we allow forward-compat new names.
    assert isinstance(extras, set)


def test_event_type_constants_are_strings() -> None:
    assert flydocs_sdk.EVENT_TYPE_EXTRACTION_SUBMITTED == "extraction.submitted"
    assert flydocs_sdk.EVENT_TYPE_EXTRACTION_COMPLETED == "extraction.completed"
    assert (
        flydocs_sdk.EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED == "extraction.post_processing.requested"
    )
    assert (
        flydocs_sdk.EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED == "extraction.post_processing.completed"
    )


def test_legacy_v0_names_are_gone() -> None:
    """The old v0 type names must not appear at the package top level."""
    for legacy in (
        "DocumentInput",
        "DocSpec",
        "DocType",
        "FieldItem",
        "FieldSpec",
        "JobStatus",
        "JobStatusResponse",
        "JobResult",
        "JobWebhookPayload",
        "JobListResponse",
        "SubmitJobRequest",
        "SubmitJobResponse",
        "StandardValidatorSpec",
        "StandardValidatorType",
        "VisualValidatorSpec",
        "ValidatorsSpec",
    ):
        assert not hasattr(flydocs_sdk, legacy), f"v0 symbol {legacy!r} should not be re-exported"
