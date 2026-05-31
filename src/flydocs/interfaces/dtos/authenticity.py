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

"""Authenticity DTOs -- visual + content integrity outputs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.enums.status import CheckStatus, ContentIntegrityStatus


class VisualCheckResult(BaseModel):
    """One visual check's outcome on a document."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str | None = None


class ContentCoherenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    status: CheckStatus
    evidence: str | None = None
    reasoning: str | None = None


class ContentAuthenticity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_integrity_status: ContentIntegrityStatus = ContentIntegrityStatus.UNCERTAIN
    checks: list[ContentCoherenceCheck] = Field(default_factory=list)


class DocumentAuthenticity(BaseModel):
    """Aggregated authenticity result for a single document instance."""

    model_config = ConfigDict(extra="forbid")

    visual: list[VisualCheckResult] = Field(default_factory=list)
    content: ContentAuthenticity | None = None
