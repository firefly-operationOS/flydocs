# Copyright 2026 Firefly Software Solutions Inc
"""``DocumentClassifier`` -- pick which DocSpec a file matches.

Used in multi-file mode when the caller didn't pin a
``document_type`` on a :class:`DocumentInput`. One LLM call per
unclassified file -- the model sees the file bytes (multimodal) plus
the list of candidate DocSpecs and returns the best match (or
``"unmatched"`` when none fit).

The service mirrors the design of :class:`MultimodalExtractor` /
:class:`DocumentSplitter`: prompt template injected through the
container, structured output forced by pydantic-ai, single LLM call
per invocation. No OCR, no client-side text extraction -- the
classification rides on the same multimodal channel as the rest of
the pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from fireflyframework_agentic.types import BinaryContent
from pydantic import BaseModel, Field

from flydesk_idp.core.observability import timed_agent_run
from flydesk_idp.interfaces.dtos.doc import DocSpec

logger = logging.getLogger(__name__)


UNMATCHED = "unmatched"


class _ClassifierOutput(BaseModel):
    document_type: str = UNMATCHED
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str = ""
    notes: str = ""


@dataclass(slots=True)
class ClassificationResult:
    """One classifier verdict for a single input file."""

    document_type: str            # canonical docType from candidates, or ``unmatched``
    confidence: float = 0.0
    description: str = ""
    notes: str = ""
    matched: bool = True          # False when document_type == ``unmatched``


class DocumentClassifier:
    """Assign one DocSpec docType to one input file."""

    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        agent_name: str = "flydesk-idp-classifier",
    ) -> None:
        self._template = template
        self._model = model
        self._agent_name = agent_name

    async def classify(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        filename: str,
        candidates: list[DocSpec],
        intention: str,
        model: str | None = None,
    ) -> ClassificationResult:
        """Run one classifier call. Falls back to ``unmatched`` on errors."""
        if not candidates:
            return ClassificationResult(document_type=UNMATCHED, matched=False, notes="no candidates")

        known: set[str] = {c.docType.documentType for c in candidates}
        targets_json = json.dumps(
            [
                {
                    "documentType": c.docType.documentType,
                    "description": c.docType.description,
                    "country": c.docType.country,
                }
                for c in candidates
            ],
            indent=2,
            ensure_ascii=False,
        )
        prompt = self._template.render(
            targets_json=targets_json,
            filename=filename,
            media_type=media_type,
            intention=intention,
        )
        agent: FireflyAgent[Any, _ClassifierOutput] = FireflyAgent(
            name=self._agent_name,
            model=model or self._model,
            instructions=prompt.system,
            output_type=_ClassifierOutput,
            description="LLM document classifier",
            tags=["idp", "classifier"],
            auto_register=False,
        )
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        run_result = await timed_agent_run(
            agent, content, op="classifier", model=model or self._model
        )
        raw: _ClassifierOutput = run_result.output

        doc_type = (raw.document_type or "").strip() or UNMATCHED
        matched = doc_type in known
        if not matched:
            # The LLM picked something outside the closed set, or
            # ``unmatched``. Always coerce to the unmatched sentinel.
            doc_type = UNMATCHED
        return ClassificationResult(
            document_type=doc_type,
            confidence=float(raw.confidence) if matched else 0.0,
            description=raw.description,
            notes=raw.notes,
            matched=matched,
        )
