# Copyright 2026 Firefly Software Solutions Inc
"""``HybridValueMatcher`` -- deterministic-first, LLM-fallback bbox matcher.

Architecture decision: most extracted values can be located against the
document's text layer (PyMuPDF) or OCR words (Tesseract) with a cheap
rapidfuzz pass that runs in milliseconds and costs nothing. Reserving
the LLM matcher for the **residual** values -- spelled-out numbers,
date format variants the fuzzy matcher misses, multilingual quirks --
preserves its strengths while killing ~70-90% of the LLM cost on the
refine path.

Cascade:

1. :class:`ValueMatcher` (rapidfuzz, free) is asked to locate every
   field. Successful matches above the configured threshold are kept.
2. Fields the deterministic matcher could not locate are batched and
   sent to :class:`LlmValueMatcher`. The LLM's per-page batched calls
   only see the residual workload.
3. Results from both passes are merged and returned in the same shape
   the refiner expects.

The matcher is transparent to :class:`BboxRefiner`: it implements the
``BboxValueMatcher`` protocol (``async locate_all``) so existing call
sites and tests work without changes.
"""

from __future__ import annotations

import logging

from flydesk_idp.core.services.bbox.llm_matcher import LlmValueMatcher
from flydesk_idp.core.services.bbox.value_matcher import MatchResult, ValueMatcher
from flydesk_idp.core.services.bbox.word_extractor import PageWords

logger = logging.getLogger(__name__)


class HybridValueMatcher:
    """Compose :class:`ValueMatcher` + :class:`LlmValueMatcher` as a cascade."""

    def __init__(
        self,
        *,
        fuzzy: ValueMatcher,
        llm: LlmValueMatcher,
    ) -> None:
        self._fuzzy = fuzzy
        self._llm = llm

    async def locate_all(
        self,
        *,
        pages: list[PageWords],
        fields: list[tuple[str, str, list[int] | None]],
    ) -> dict[str, MatchResult | None]:
        """Locate every field, deterministic-first with LLM fallback.

        The fuzzy matcher already filters by its configured threshold
        (``IDPSettings.bbox_refine_threshold``) and returns ``None`` for
        misses. We forward those misses to the LLM matcher in a single
        batched call.
        """
        if not fields:
            return {}
        fuzzy_results = await self._fuzzy.locate_all(pages=pages, fields=fields)

        residual = [
            (fid, value, candidate) for (fid, value, candidate) in fields if fuzzy_results.get(fid) is None
        ]
        if not residual:
            logger.debug("bbox.hybrid_matcher: all %d fields resolved by fuzzy pass", len(fields))
            return fuzzy_results

        logger.info(
            "bbox.hybrid_matcher: fuzzy resolved %d/%d fields; falling back to LLM for %d",
            len(fields) - len(residual),
            len(fields),
            len(residual),
        )
        llm_results = await self._llm.locate_all(pages=pages, fields=residual)

        merged: dict[str, MatchResult | None] = dict(fuzzy_results)
        for fid, match in llm_results.items():
            merged[fid] = match
        return merged


__all__ = ["HybridValueMatcher"]
