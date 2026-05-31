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

"""Enumerations referenced by public DTOs."""

from flydocs.interfaces.enums.extraction_status import ExtractionStatus, PostProcessingStatus
from flydocs.interfaces.enums.field_type import FieldType, StandardFormat
from flydocs.interfaces.enums.status import (
    CheckStatus,
    ContentIntegrityStatus,
    JudgeStatus,
    ValidationRule,
)
from flydocs.interfaces.enums.validator import ValidatorType

__all__ = [
    "CheckStatus",
    "ContentIntegrityStatus",
    "ExtractionStatus",
    "FieldType",
    "JudgeStatus",
    "PostProcessingStatus",
    "StandardFormat",
    "ValidationRule",
    "ValidatorType",
]
