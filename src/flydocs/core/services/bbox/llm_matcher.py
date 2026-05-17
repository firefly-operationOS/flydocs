# Copyright 2026 Firefly Software Solutions Inc
"""``LlmValueMatcher`` -- generic, locale-agnostic matcher backed by an LLM.

Replaces the rule-based ``ValueMatcher`` for the bbox refiner. The
matcher takes:

* one or more :class:`PageWords` with the OCR / text-layer word stream
  + bboxes already normalised to ``[0, 1]``
* a list of ``(field_name, extracted_value)`` pairs

and asks a focused LLM (no document image, no schema -- just words +
values) to map each value to the **indices** of the words that
constitute it on each page. The refiner unions the matched word
bboxes per field to produce the final precise rectangle.

Why an LLM matcher and not hardcoded variants:

* Format-agnostic. ``1995-10-19 ↔ 19 10 1995 ↔ 19 de octubre de 1995``,
  ``ANDRÉS ↔ ANDRES``, ``1.487 ↔ 1487 ↔ mil cuatrocientos ochenta y
  siete``: the LLM understands the canonical equivalence intrinsically.
* Multilingual. No per-locale tables to maintain.
* Generic. The same matcher works for any new field type the schema
  evolves to support -- no new variant generators to write.

Cost shape: one LLM call per page that has words AND at least one
field whose ``pagesFound`` lands on it (or none -- broadcast fallback).
Calls run in parallel. The prompt is small (a few hundred to a few
thousand tokens depending on word density); structured output keeps
the response deterministic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from pydantic import BaseModel, Field

from flydocs.core.observability import DEFAULT_MIDDLEWARE, timed_agent_run
from flydocs.core.services.bbox.value_matcher import MatchResult
from flydocs.core.services.bbox.word_extractor import PageWords, Word

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


# Output schema sent through pydantic-ai's structured-response path.
# Validates indices server-side so a hallucinated index doesn't crash
# the refiner.
class _LlmFieldMatch(BaseModel):
    field: str = Field(..., description="The field_name from the input list.")
    word_indices: list[int] = Field(
        default_factory=list,
        description=(
            "0-based indices into the input ``words`` list. Empty when the value is not on this page."
        ),
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class _LlmMatchResponse(BaseModel):
    matches: list[_LlmFieldMatch] = Field(default_factory=list)


class LlmValueMatcher:
    """Generic value-to-words matcher backed by ``FireflyAgent``."""

    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        threshold: float,
        agent_name: str = "flydocs-bbox-matcher",
    ) -> None:
        self._template = template
        self._model = model
        self._threshold = threshold
        self._agent_name = agent_name

    async def locate_all(
        self,
        *,
        pages: list[PageWords],
        fields: list[tuple[str, str, list[int] | None]],
    ) -> dict[str, MatchResult | None]:
        """Match every field across every page in one batched flow.

        ``fields`` is ``[(field_id, value, candidate_pages_or_None)]``.
        The matcher first tries each field on its candidate pages; any
        field not matched there is retried against the remaining pages.
        Returns ``{field_id: MatchResult | None}`` -- the refiner stamps
        the bbox per field from this dict.
        """
        if not pages or not fields:
            return {fid: None for fid, _, _ in fields}

        agent = self._build_agent()

        # Group fields by the page set we'd like to try first. ``None``
        # means \"all pages\".
        pages_with_words = [p for p in pages if p.words]
        if not pages_with_words:
            return {fid: None for fid, _, _ in fields}

        results: dict[str, MatchResult | None] = {fid: None for fid, _, _ in fields}

        # Pass 1: each field against its declared candidate pages
        primary = self._group_by_page(fields, pages_with_words, fallback_all=False)
        await self._run_pages(agent, pages_with_words, primary, results)

        # Pass 2: any field still unmatched is broadcast to every page
        # the primary pass didn't already check
        unmatched = [(fid, value, cand) for (fid, value, cand) in fields if results.get(fid) is None]
        if unmatched:
            secondary: dict[int, list[tuple[str, str]]] = {}
            for fid, value, cand in unmatched:
                primary_pages = set(cand or [])
                for p in pages_with_words:
                    if p.page in primary_pages:
                        continue
                    secondary.setdefault(p.page, []).append((fid, value))
            await self._run_pages(agent, pages_with_words, secondary, results)
        return results

    # ------------------------------------------------------------------

    def _build_agent(self) -> FireflyAgent[object, _LlmMatchResponse]:
        # Render the system template once -- the per-call user prompt
        # carries the page-specific words + fields.
        rendered = self._template.render(
            page_number=0,
            words_json="[]",
            fields_json="[]",
        )
        return FireflyAgent(
            name=self._agent_name,
            model=self._model,
            instructions=rendered.system,
            output_type=_LlmMatchResponse,
            description="Locate extracted values inside per-page OCR/text word streams.",
            tags=["idp", "bbox", "matcher"],
            middleware=list(DEFAULT_MIDDLEWARE),
            auto_register=False,
        )

    def _group_by_page(
        self,
        fields: list[tuple[str, str, list[int] | None]],
        pages_with_words: list[PageWords],
        *,
        fallback_all: bool,
    ) -> dict[int, list[tuple[str, str]]]:
        valid_pages = {p.page for p in pages_with_words}
        per_page: dict[int, list[tuple[str, str]]] = {}
        for fid, value, candidate in fields:
            target_pages: Iterable[int]
            if candidate:
                target_pages = [p for p in candidate if p in valid_pages]
                if not target_pages and fallback_all:
                    target_pages = valid_pages
            else:
                target_pages = valid_pages
            for page in target_pages:
                per_page.setdefault(page, []).append((fid, value))
        return per_page

    async def _run_pages(
        self,
        agent: FireflyAgent[object, _LlmMatchResponse],
        pages: list[PageWords],
        per_page: dict[int, list[tuple[str, str]]],
        results: dict[str, MatchResult | None],
    ) -> None:
        """Issue one LLM call per page (in parallel) with its open fields.

        Pages with no remaining open fields are skipped, so re-delivery
        after a primary hit is a no-op.
        """
        if not per_page:
            return
        page_by_num = {p.page: p for p in pages}
        tasks = []
        for page_num, fields_on_page in per_page.items():
            page = page_by_num.get(page_num)
            if page is None or not fields_on_page:
                continue
            still_open = [(fid, value) for fid, value in fields_on_page if results.get(fid) is None]
            if not still_open:
                continue
            tasks.append(self._run_one_page(agent, page, still_open, results))
        if tasks:
            await asyncio.gather(*tasks)

    async def _run_one_page(
        self,
        agent: FireflyAgent[object, _LlmMatchResponse],
        page: PageWords,
        fields_on_page: list[tuple[str, str]],
        results: dict[str, MatchResult | None],
    ) -> None:
        words_payload = [{"index": idx, "text": w.text} for idx, w in enumerate(page.words)]
        fields_payload = [{"field_name": fid, "value": value} for fid, value in fields_on_page]
        prompt = self._template.render(
            page_number=page.page,
            words_json=json.dumps(words_payload, ensure_ascii=False),
            fields_json=json.dumps(fields_payload, ensure_ascii=False),
        )
        try:
            run = await timed_agent_run(
                agent,
                prompt.user,
                op=f"bbox.matcher.page.{page.page}",
                model=self._model,
            )
        except Exception as exc:  # noqa: BLE001 -- non-fatal degrade
            logger.warning(
                "LLM bbox matcher failed on page %s for %d fields: %s",
                page.page,
                len(fields_on_page),
                exc,
            )
            return

        output: _LlmMatchResponse = run.output
        for match in output.matches:
            if not match.word_indices:
                continue
            if match.confidence < self._threshold:
                continue
            words = self._words_for_indices(page.words, match.word_indices)
            if not words:
                continue
            result = _union_bbox(words, match.confidence, page.page)
            existing = results.get(match.field)
            if existing is None or result.score > existing.score:
                results[match.field] = result

    @staticmethod
    def _words_for_indices(words: list[Word], indices: list[int]) -> list[Word]:
        out: list[Word] = []
        max_idx = len(words) - 1
        for raw in indices:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= idx <= max_idx:
                out.append(words[idx])
        return out


def _union_bbox(words: list[Word], score: float, page: int) -> MatchResult:
    xmin = min(w.xmin for w in words)
    ymin = min(w.ymin for w in words)
    xmax = max(w.xmax for w in words)
    ymax = max(w.ymax for w in words)
    return MatchResult(
        page=page,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        score=round(min(1.0, max(0.0, score)), 4),
    )


# Re-export so ``IDPCoreConfiguration`` can pick the matcher by class name
__all__ = ["LlmValueMatcher"]
