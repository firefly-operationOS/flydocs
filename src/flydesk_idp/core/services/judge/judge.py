# Copyright 2026 Firefly Software Solutions Inc
"""``Judge`` -- LLM cross-validates extracted fields against the document.

For each extracted field the judge returns a PASS / FAIL / UNCERTAIN
verdict, a confidence, a piece of evidence (exact quote / region), one
sentence of reasoning, and a ``flag_for_review`` flag. The judge
mutates the input :class:`ExtractedFieldGroup` list in place by
populating each field's ``judge`` attribute. Prompt template injected
via DI.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from fireflyframework_agentic.types import BinaryContent
from pydantic import BaseModel, Field

from flydesk_idp.core.observability import DEFAULT_MIDDLEWARE, timed_agent_run
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    JudgeOutcome,
)
from flydesk_idp.interfaces.enums.status import JudgeStatus

logger = logging.getLogger(__name__)


class _RawJudgeField(BaseModel):
    fieldName: str
    status: JudgeStatus = JudgeStatus.UNCERTAIN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str = ""
    notes: str = ""
    flag_for_review: bool = False
    items: list["_RawJudgeField"] | None = None


class _RawJudgeGroup(BaseModel):
    fieldGroupName: str
    fieldGroupFields: list[_RawJudgeField] = Field(default_factory=list)


class _JudgeOutput(BaseModel):
    fields: list[_RawJudgeGroup] = Field(default_factory=list)


_RawJudgeField.model_rebuild()


class Judge:
    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        agent_name: str = "flydesk-idp-judge",
    ) -> None:
        self._template = template
        self._model = model
        self._agent_name = agent_name

    async def judge(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        doc: DocSpec,
        extracted_groups: list[ExtractedFieldGroup],
        intention: str,
        model: str | None = None,
    ) -> list[ExtractedFieldGroup]:
        if not extracted_groups:
            return extracted_groups

        extracted_fields_json = json.dumps(
            [g.model_dump(mode="json") for g in extracted_groups],
            indent=2,
            ensure_ascii=False,
        )
        prompt = self._template.render(
            intention=intention,
            documentType=doc.docType.documentType,
            extracted_fields_json=extracted_fields_json,
        )
        agent: FireflyAgent[Any, _JudgeOutput] = FireflyAgent(
            name=self._agent_name,
            model=model or self._model,
            instructions=prompt.system,
            output_type=_JudgeOutput,
            description="Judge / re-evaluator",
            tags=["idp", "judge"],
            middleware=list(DEFAULT_MIDDLEWARE),
            auto_register=False,
        )
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        run_result = await timed_agent_run(
            agent, content, op="judge", model=model or self._model
        )
        judge_by_group: dict[str, dict[str, _RawJudgeField]] = {
            g.fieldGroupName: {f.fieldName: f for f in g.fieldGroupFields}
            for g in run_result.output.fields
        }

        for group in extracted_groups:
            field_map = judge_by_group.get(group.fieldGroupName, {})
            for field in group.fieldGroupFields:
                self._apply(field, field_map.get(field.fieldName))
        return extracted_groups

    def _apply(self, field: ExtractedField, raw: _RawJudgeField | None) -> None:
        if raw is None:
            return
        field.judge = JudgeOutcome(
            status=raw.status,
            confidence=raw.confidence,
            evidence=raw.evidence,
            notes=raw.notes,
            flag_for_review=raw.flag_for_review,
        )
        if raw.items and isinstance(field.fieldValueFound, list):
            for row in field.fieldValueFound:
                if not isinstance(row, ExtractedField) or not isinstance(row.fieldValueFound, list):
                    continue
                raw_items = {r.fieldName: r for r in raw.items}
                for sub_field in row.fieldValueFound:
                    if isinstance(sub_field, ExtractedField):
                        self._apply(sub_field, raw_items.get(sub_field.fieldName))
