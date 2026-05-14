# Copyright 2026 Firefly Software Solutions Inc
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

from flydesk_idp.interfaces.dtos.authenticity import (
    ContentAuthenticity,
    ContentCoherenceCheck,
)
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.enums.status import CheckStatus, ContentIntegrityStatus

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
        agent_name: str = "flydesk-idp-content-auth",
    ) -> None:
        self._template = template
        self._model = model
        self._agent_name = agent_name

    async def check(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        doc: DocSpec,
        intention: str,
        model: str | None = None,
    ) -> ContentAuthenticity:
        prompt = self._template.render(
            documentType=doc.docType.documentType,
            description=doc.docType.description,
            country=doc.docType.country,
            intention=intention,
        )
        agent: FireflyAgent[Any, _ContentOutput] = FireflyAgent(
            name=self._agent_name,
            model=model or self._model,
            instructions=prompt.system,
            output_type=_ContentOutput,
            description="Content authenticity audit",
            tags=["idp", "authenticity"],
            auto_register=False,
        )
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        run_result = await agent.run(content)
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
