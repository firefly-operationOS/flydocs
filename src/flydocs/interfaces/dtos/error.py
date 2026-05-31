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

"""RFC 7807 ``application/problem+json`` body."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProblemDetails(BaseModel):
    """RFC 7807 problem detail.

    Render as ``Content-Type: application/problem+json``. Additional
    fields are tolerated (``model_config = ConfigDict(extra="allow")``)
    so callers can add domain-specific context without bumping the
    schema.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(default="about:blank", description="URI reference identifying the problem type.")
    title: str = Field(..., description="Short human-readable summary.")
    status: int = Field(..., ge=100, le=599, description="HTTP status code.")
    detail: str | None = Field(
        default=None, description="Human-readable explanation specific to this occurrence."
    )
    instance: str | None = Field(
        default=None, description="URI reference identifying the specific occurrence."
    )
    code: str | None = Field(default=None, description="Stable application error code (snake_case).")
    extensions: dict[str, Any] | None = Field(
        default=None, description="Additional context as a nested object."
    )
