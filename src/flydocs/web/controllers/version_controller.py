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

"""Build + model identity endpoint -- ``GET /api/v1/version``."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pyfly.container import rest_controller
from pyfly.web import get_mapping, request_mapping

from flydocs import __version__
from flydocs.config import IDPSettings


class VersionInfo(BaseModel):
    """Identity of the running service instance."""

    service: str = Field(description="Service slug.", examples=["flydocs"])
    version: str = Field(
        description=(
            "CalVer (``YY.MM.PP``) baked into the wheel at build time. "
            "PEP 440 normalises ``26.05.01`` -> ``26.5.1`` so the value "
            "you see here uses the stripped form."
        ),
        examples=["26.6.2"],
    )
    model: str = Field(
        description="Primary multimodal model the orchestrator uses by default.",
        examples=["anthropic:claude-opus-4-7"],
    )
    fallback_model: str = Field(
        description="Secondary model used when the primary errors out. Empty disables the fallback.",
        examples=["openai:gpt-4o"],
    )
    eda_adapter: str = Field(
        description="Event-driven adapter backing the async job queue.",
        examples=["redis", "memory"],
    )


@rest_controller
@request_mapping("/api/v1")
class VersionController:
    """Identity + model information for the running instance.

    Useful for smoke tests, deploy validation, and answering the
    "which model is this hitting?" question from operations channels.
    """

    def __init__(self, settings: IDPSettings) -> None:
        self._settings = settings

    @get_mapping("/version")
    async def version(self) -> VersionInfo:
        """Return the service identity, primary model and queue adapter."""
        return VersionInfo(
            service="flydocs",
            version=__version__,
            model=self._settings.model,
            fallback_model=self._settings.fallback_model or "",
            eda_adapter=self._settings.eda_adapter,
        )
