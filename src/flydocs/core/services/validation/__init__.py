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
