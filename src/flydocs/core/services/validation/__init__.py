# Copyright 2026 Firefly Software Solutions Inc
"""Pure-Python validation -- no LLM involved.

Two layers:

  * :class:`FieldValidator` runs *after* extraction: regex, range, enum
    and ``ValidatorSpec`` checks on each ExtractedField.
  * :class:`RequestValidator` runs *before* the pipeline: semantic
    cross-field checks that pydantic can't express (rule parents that
    reference unknown document types / fields, cycles in the rule DAG,
    duplicate ids, etc.).
"""

from flydocs.core.services.validation.field_validator import FieldValidator
from flydocs.core.services.validation.request_validator import (
    RequestValidator,
    ValidationIssue,
    ValidationReport,
)
from flydocs.core.services.validation.validator_registry import (
    ValidatorRegistry,
    run_validator,
)

__all__ = [
    "FieldValidator",
    "RequestValidator",
    "ValidationIssue",
    "ValidationReport",
    "ValidatorRegistry",
    "run_validator",
]
