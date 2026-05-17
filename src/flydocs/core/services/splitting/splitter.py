# Copyright 2026 Firefly Software Solutions Inc
"""``DocumentSplitter`` -- discover every distinct sub-document inside
a file and pin each one to a contiguous, non-overlapping page range.

Pure segmentation: the splitter does **not** decide which caller-declared
``DocSpec`` each segment matches -- that is the
:class:`flydocs.core.services.classification.DocumentClassifier`'s
job. Keeping the two services separate means a single uploaded file
that happens to contain several documents (a deed + a DNI + a utility
bill in one PDF, say) is segmented first and then each segment is
classified independently against the declared targets. Neither service
needs to know the other.

The splitter short-circuits when the input is a single page (one
segment covers the whole file).
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

from flydocs.core.observability import DEFAULT_MIDDLEWARE, timed_agent_run
from flydocs.interfaces.dtos.doc import DocSpec

logger = logging.getLogger(__name__)


class _PageRangeModel(BaseModel):
    start: int = Field(default=1, ge=1)
    end: int = Field(default=1, ge=1)


class _SegmentModel(BaseModel):
    pages: _PageRangeModel
    provisional_type: str = ""
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class _SplitterOutput(BaseModel):
    segments: list[_SegmentModel] = Field(default_factory=list)


@dataclass(slots=True)
class DiscoveredSegment:
    """One sub-document the splitter identified inside an input file.

    The ``provisional_type`` is a free-text hint from the splitter --
    useful as routing context and for telemetry, but the orchestrator
    routes by the *classifier*'s verdict, not by this hint.
    """

    page_start: int  # 1-indexed, inclusive
    page_end: int  # 1-indexed, inclusive
    provisional_type: str = ""
    description: str = ""
    confidence: float = 0.0


@dataclass(slots=True)
class SplitResult:
    """Every segment the splitter found in a single input file."""

    segments: list[DiscoveredSegment] = field(default_factory=list)


# Backwards-compatible alias retained for downstream imports that
# treated each segment as a "located document". Internally everything
# uses :class:`DiscoveredSegment`.
SplitDocument = DiscoveredSegment


class DocumentSplitter:
    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        agent_name: str = "flydocs-splitter",
    ) -> None:
        self._template = template
        self._model = model
        self._agent_name = agent_name

    async def discover(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        page_count: int,
        targets: list[DocSpec],
        intention: str,
        model: str | None = None,
    ) -> SplitResult:
        """Enumerate every distinct sub-document inside the file.

        ``targets`` is passed to the LLM as routing **context** so it
        can recognise familiar layouts; it does not constrain the
        output. Page ranges in the response are clamped to
        ``[1, page_count]`` and gaps / overlaps are not corrected --
        callers should treat low-confidence segmentations as a hint.
        """
        # Shortcut: single page -> one segment covers the whole file.
        if page_count <= 1:
            return SplitResult(
                segments=[
                    DiscoveredSegment(
                        page_start=1,
                        page_end=max(1, page_count),
                        provisional_type="",
                        description="",
                        confidence=1.0,
                    )
                ]
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
            description="LLM document splitter (discovery)",
            tags=["idp", "splitter"],
            middleware=list(DEFAULT_MIDDLEWARE),
            auto_register=False,
        )
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        run_result = await timed_agent_run(agent, content, op="split", model=model or self._model)
        raw: _SplitterOutput = run_result.output

        segments: list[DiscoveredSegment] = []
        for entry in raw.segments:
            start = max(1, min(page_count, int(entry.pages.start)))
            end = max(start, min(page_count, int(entry.pages.end)))
            segments.append(
                DiscoveredSegment(
                    page_start=start,
                    page_end=end,
                    provisional_type=(entry.provisional_type or "").strip().lower(),
                    description=entry.description.strip(),
                    confidence=float(entry.confidence),
                )
            )

        # Defensive fallback: if the LLM came back empty, treat the
        # whole file as one segment so the pipeline can still proceed.
        if not segments:
            segments.append(
                DiscoveredSegment(
                    page_start=1,
                    page_end=page_count,
                    provisional_type="",
                    description="",
                    confidence=0.0,
                )
            )
        return SplitResult(segments=segments)
