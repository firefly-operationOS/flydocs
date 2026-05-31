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

"""``ContentAuthenticityChecker`` -- LLM audit for content integrity.

Produces a :class:`ContentAuthenticity` aggregate with an overall
verdict and a list of named coherence checks (each with a verdict,
evidence, and reasoning). Prompt template injected via DI.
"""

from __future__ import annotations

import logging
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from fireflyframework_agentic.types import BinaryContent
from pydantic import BaseModel, Field

from flydocs.core.observability import DEFAULT_MIDDLEWARE, timed_agent_run
from flydocs.interfaces.dtos.authenticity import (
    ContentAuthenticity,
    ContentCoherenceCheck,
)
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.enums.status import CheckStatus, ContentIntegrityStatus

logger = logging.getLogger(__name__)


class _RawContentCheck(BaseModel):
    name: str = ""
    description: str = ""
    status: CheckStatus = CheckStatus.UNCERTAIN
    evidence: str = ""
    reasoning: str = ""


class _ContentOutput(BaseModel):
    overall_integrity_status: ContentIntegrityStatus = ContentIntegrityStatus.UNCERTAIN
    checks: list[_RawContentCheck] = Field(default_factory=list)


class ContentAuthenticityChecker:
    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        agent_name: str = "flydocs-content-auth",
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
    ) -> ContentAuthenticity:
        prompt = self._template.render(
            documentType=doc.id,
            description=doc.description,
            country=doc.country,
            intention=intention,
        )
        agent: FireflyAgent[Any, _ContentOutput] = FireflyAgent(
            name=self._agent_name,
            model=model or self._model,
            instructions=prompt.system,
            output_type=_ContentOutput,
            description="Content authenticity audit",
            tags=["idp", "authenticity"],
            middleware=list(DEFAULT_MIDDLEWARE),
            auto_register=False,
        )
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        run_result = await timed_agent_run(agent, content, op="content_auth", model=model or self._model)
        raw = run_result.output
        return ContentAuthenticity(
            overall_integrity_status=raw.overall_integrity_status,
            checks=[
                ContentCoherenceCheck(
                    name=c.name,
                    description=c.description,
                    status=c.status,
                    evidence=c.evidence,
                    reasoning=c.reasoning,
                )
                for c in raw.checks
            ],
        )
