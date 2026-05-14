# Copyright 2026 Firefly Software Solutions Inc
"""``VisualAuthenticityChecker`` -- runs caller-defined visual validators.

Each validator is a ``(name, description)`` pair the LLM evaluates
against the document image; output is a :class:`VisualValidationOutcome`
per validator with a yes/no verdict, confidence, and free-text notes.
The prompt template is supplied by the DI container.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from fireflyframework_agentic.types import BinaryContent
from pydantic import BaseModel, Field

from flydesk_idp.interfaces.dtos.authenticity import VisualValidationOutcome
from flydesk_idp.interfaces.dtos.doc import DocSpec

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
        agent_name: str = "flydesk-idp-visual-auth",
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
    ) -> list[VisualValidationOutcome]:
        if not doc.validators.visual:
            return []

        validators_json = json.dumps(
            [v.model_dump(mode="json") for v in doc.validators.visual],
            indent=2,
            ensure_ascii=False,
        )
        prompt = self._template.render(
            documentType=doc.docType.documentType,
            country=doc.docType.country,
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
            auto_register=False,
        )
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        run_result = await agent.run(content)
        raw_by_name = {v.name: v for v in run_result.output.validations}
        outcomes: list[VisualValidationOutcome] = []
        for spec in doc.validators.visual:
            raw = raw_by_name.get(spec.name)
            if raw is None:
                outcomes.append(
                    VisualValidationOutcome(name=spec.name, passed=False, confidence=0.0, notes="Not evaluated")
                )
                continue
            outcomes.append(
                VisualValidationOutcome(
                    name=raw.name, passed=bool(raw.passed), confidence=float(raw.confidence), notes=raw.notes
                )
            )
        return outcomes
