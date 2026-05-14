# Copyright 2026 Firefly Software Solutions Inc
"""``DocumentSplitter`` -- LLM identifies target docTypes and their page
ranges inside a multi-document file.

Built on :class:`FireflyAgent` with structured output. The orchestrator
only calls this when (a) ``options.stages.splitter`` is true and
(b) the input has more than one page **and** more than one target
``DocSpec``. The split prompt is supplied through DI so the service has
no coupling to a specific template revision.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from fireflyframework_agentic.types import BinaryContent
from pydantic import BaseModel, Field

from flydesk_idp.core.observability import timed_agent_run
from flydesk_idp.interfaces.dtos.doc import DocSpec

logger = logging.getLogger(__name__)


class _PageRangeModel(BaseModel):
    start: int = Field(default=1, ge=1)
    end: int = Field(default=1, ge=1)


class _SplitDocumentModel(BaseModel):
    documentType: str
    pages: _PageRangeModel | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str = ""
    missing: bool = False


class _SplitterOutput(BaseModel):
    documents: list[_SplitDocumentModel] = Field(default_factory=list)
    additional_docs: list[_SplitDocumentModel] = Field(default_factory=list)


@dataclass(slots=True)
class SplitDocument:
    """A located target document inside the multi-doc file."""

    document_type: str
    page_start: int | None = None  # 1-indexed, inclusive (None when missing)
    page_end: int | None = None    # 1-indexed, inclusive
    confidence: float = 0.0
    description: str = ""
    missing: bool = False


@dataclass(slots=True)
class SplitResult:
    documents: list[SplitDocument] = field(default_factory=list)
    additional_documents: list[SplitDocument] = field(default_factory=list)


class DocumentSplitter:
    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        agent_name: str = "flydesk-idp-splitter",
    ) -> None:
        self._template = template
        self._model = model
        self._agent_name = agent_name

    async def split(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        page_count: int,
        targets: list[DocSpec],
        intention: str,
        model: str | None = None,
    ) -> SplitResult:
        # Shortcut: single page or single target -> no real split, full range.
        if page_count <= 1 or len(targets) <= 1:
            return SplitResult(
                documents=[
                    SplitDocument(
                        document_type=t.docType.documentType,
                        page_start=1,
                        page_end=page_count,
                        confidence=1.0,
                        description=t.docType.description,
                        missing=False,
                    )
                    for t in targets
                ],
                additional_documents=[],
            )

        targets_json = json.dumps(
            [
                {
                    "documentType": d.docType.documentType,
                    "description": d.docType.description,
                    "country": d.docType.country,
                }
                for d in targets
            ],
            indent=2,
            ensure_ascii=False,
        )
        prompt = self._template.render(
            targets_json=targets_json,
            page_count=page_count,
            intention=intention,
        )
        agent: FireflyAgent[Any, _SplitterOutput] = FireflyAgent(
            name=self._agent_name,
            model=model or self._model,
            instructions=prompt.system,
            output_type=_SplitterOutput,
            description="LLM document splitter",
            tags=["idp", "splitter"],
            auto_register=False,
        )
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        run_result = await timed_agent_run(
            agent, content, op="split", model=model or self._model
        )
        raw = run_result.output

        # Align by document type and clamp pages to the actual range.
        raw_by_type = {d.documentType: d for d in raw.documents}
        documents: list[SplitDocument] = []
        for target in targets:
            doc_type = target.docType.documentType
            raw_doc = raw_by_type.get(doc_type)
            if raw_doc is None or raw_doc.missing or raw_doc.pages is None:
                documents.append(
                    SplitDocument(document_type=doc_type, missing=True, description=target.docType.description)
                )
                continue
            start = max(1, min(page_count, int(raw_doc.pages.start)))
            end = max(start, min(page_count, int(raw_doc.pages.end)))
            documents.append(
                SplitDocument(
                    document_type=doc_type,
                    page_start=start,
                    page_end=end,
                    confidence=float(raw_doc.confidence),
                    description=raw_doc.description or target.docType.description,
                    missing=False,
                )
            )

        additional = [
            SplitDocument(
                document_type=d.documentType,
                page_start=d.pages.start if d.pages else None,
                page_end=d.pages.end if d.pages else None,
                confidence=float(d.confidence),
                description=d.description,
                missing=False,
            )
            for d in raw.additional_docs
        ]
        return SplitResult(documents=documents, additional_documents=additional)
