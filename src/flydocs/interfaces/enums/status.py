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

"""Status enums shared across pipeline nodes (validation rules, judge verdicts,
content-authenticity verdicts).

All values are lowercase snake_case to match the universal v1 enum convention.
"""

from __future__ import annotations

from enum import StrEnum


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


__all__ = ["CheckStatus", "ContentIntegrityStatus", "JudgeStatus", "ValidationRule"]
