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

"""``VisualAuthenticityChecker`` -- runs caller-defined visual checks.

Each check is a ``(name, description)`` pair the LLM evaluates against
the document image; output is a :class:`VisualCheckResult` per check
with a yes/no verdict, confidence, and free-text notes. The prompt
template is supplied by the DI container.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from fireflyframework_agentic.types import BinaryContent
from pydantic import BaseModel, Field

from flydocs.core.observability import DEFAULT_MIDDLEWARE, timed_agent_run
from flydocs.interfaces.dtos.authenticity import VisualCheckResult
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec

logger = logging.getLogger(__name__)


class _RawVisualValidation(BaseModel):
    name: str
    passed: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""


class _VisualOutput(BaseModel):
    validations: list[_RawVisualValidation] = Field(default_factory=list)


class VisualAuthenticityChecker:
    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        agent_name: str = "flydocs-visual-auth",
    ) -> None:
        self._template = template
        self._model = model
        self._agent_name = agent_name

    async def check(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        doc: DocumentTypeSpec,
        intention: str,
        model: str | None = None,
    ) -> list[VisualCheckResult]:
        if not doc.visual_checks:
            return []

        validators_json = json.dumps(
            [v.model_dump(mode="json") for v in doc.visual_checks],
            indent=2,
            ensure_ascii=False,
        )
        prompt = self._template.render(
            documentType=doc.id,
            country=doc.country,
            intention=intention,
            validators_json=validators_json,
        )
        agent: FireflyAgent[Any, _VisualOutput] = FireflyAgent(
            name=self._agent_name,
            model=model or self._model,
            instructions=prompt.system,
            output_type=_VisualOutput,
            description="Visual authenticity checks",
            tags=["idp", "authenticity"],
            middleware=list(DEFAULT_MIDDLEWARE),
            auto_register=False,
        )
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        run_result = await timed_agent_run(agent, content, op="visual_auth", model=model or self._model)
        raw_by_name = {v.name: v for v in run_result.output.validations}
        outcomes: list[VisualCheckResult] = []
        for spec in doc.visual_checks:
            raw = raw_by_name.get(spec.name)
            if raw is None:
                outcomes.append(
                    VisualCheckResult(name=spec.name, passed=False, confidence=0.0, notes="Not evaluated")
                )
                continue
            outcomes.append(
                VisualCheckResult(
                    name=raw.name, passed=bool(raw.passed), confidence=float(raw.confidence), notes=raw.notes
                )
            )
        return outcomes
