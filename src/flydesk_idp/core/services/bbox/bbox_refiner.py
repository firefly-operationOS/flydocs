# Copyright 2026 Firefly Software Solutions Inc
"""``BboxRefiner`` -- replace LLM-estimated bboxes with grounded ones.

Walks every :class:`ExtractedField` in every group, asks the
:class:`ValueMatcher` to locate the value against the document's word
stream, and rewrites the bbox in place when a hit lands above the
configured threshold. Misses keep the LLM bbox tagged
``source=llm, refinement_confidence=null`` -- documented fallback,
never silently drop a coordinate.

Sub-fields of array-typed parents are recursed into; the matcher runs
per leaf value.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from pyfly.container import service

from flydesk_idp.core.observability import log_outbound
from flydesk_idp.core.services.bbox.value_matcher import MatchResult, ValueMatcher
from flydesk_idp.core.services.bbox.word_extractor import PageWords
from flydesk_idp.core.services.bbox.word_router import WordRouter
from flydesk_idp.interfaces.dtos.bbox import BboxSource, BoundingBox
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class RefineCounters:
    """Per-document refinement summary -- handy for logs + tests."""

    fields_seen: int
    grounded_pdf_text: int
    grounded_ocr: int
    kept_llm: int


@service
class BboxRefiner:
    """Orchestrate text-layer / OCR word collection + per-field matching."""

    def __init__(self, *, router: WordRouter, matcher: ValueMatcher) -> None:
        self._router = router
        self._matcher = matcher

    async def refine(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        page_count: int,
        groups: list[ExtractedFieldGroup],
        language_hint: str | None = None,
    ) -> RefineCounters:
        """Mutate ``groups`` in place; return per-doc counters."""
        if not groups:
            return RefineCounters(0, 0, 0, 0)

        started = time.monotonic()
        # Word collection is CPU-bound (PyMuPDF / OCR). Push to a thread
        # so the asyncio loop stays free for concurrent docs.
        pages = await asyncio.to_thread(
            self._router.collect,
            document_bytes,
            media_type=media_type,
            page_count=page_count,
            language_hint=language_hint,
        )
        counters = _Counters()
        for group in groups:
            for field in group.fieldGroupFields:
                self._refine_field(field, pages, counters)
        elapsed_ms = (time.monotonic() - started) * 1000
        log_outbound(
            "bbox-refiner",
            op="refine",
            status="ok",
            latency_ms=elapsed_ms,
            fields=counters.fields_seen,
            grounded_pdf_text=counters.grounded_pdf_text,
            grounded_ocr=counters.grounded_ocr,
            kept_llm=counters.kept_llm,
            pages=len(pages),
        )
        return RefineCounters(
            fields_seen=counters.fields_seen,
            grounded_pdf_text=counters.grounded_pdf_text,
            grounded_ocr=counters.grounded_ocr,
            kept_llm=counters.kept_llm,
        )

    # ------------------------------------------------------------------

    def _refine_field(
        self,
        field: ExtractedField,
        pages: list[PageWords],
        counters: _Counters,
    ) -> None:
        # Recurse into array-typed parents first; per-leaf matching only.
        if isinstance(field.fieldValueFound, list):
            for child in field.fieldValueFound:
                if isinstance(child, ExtractedField):
                    if isinstance(child.fieldValueFound, list):
                        for sub in child.fieldValueFound:
                            if isinstance(sub, ExtractedField):
                                self._refine_field(sub, pages, counters)
                    else:
                        self._refine_field(child, pages, counters)
            return

        counters.fields_seen += 1

        value_str = _value_as_string(field.fieldValueFound)
        if not value_str:
            self._stamp_no_source(field.bbox)
            return

        match = self._matcher.locate(
            value_str,
            pages=pages,
            candidate_pages=field.pagesFound or None,
        )
        if match is None:
            # No grounding -- keep the LLM bbox shape, tag the source so
            # callers know the coordinates are an estimate.
            field.bbox.source = BboxSource.LLM
            field.bbox.refinement_confidence = None
            counters.kept_llm += 1
            return

        page_source = _page_source(match.page, pages)
        self._replace_bbox(field, match, page_source)
        if page_source == BboxSource.PDF_TEXT:
            counters.grounded_pdf_text += 1
        else:
            counters.grounded_ocr += 1

    @staticmethod
    def _replace_bbox(field: ExtractedField, match: MatchResult, source: BboxSource) -> None:
        # Build a fresh BoundingBox via the constructor so the model
        # validator gets to enforce the corner invariants on the new
        # rectangle. Carry over the geometric quality fields untouched
        # if present (they describe shape sanity, not grounding).
        old = field.bbox
        new = BoundingBox(
            xmin=match.xmin,
            ymin=match.ymin,
            xmax=match.xmax,
            ymax=match.ymax,
            quality=old.quality,
            quality_score=old.quality_score,
            source=source,
            refinement_confidence=match.score,
        )
        field.bbox = new
        if match.page not in field.pagesFound:
            field.pagesFound = [match.page, *field.pagesFound]

    @staticmethod
    def _stamp_no_source(bbox: BoundingBox) -> None:
        # Empty values keep the placeholder bbox but stamp the source.
        if bbox.source is None:
            bbox.source = BboxSource.NONE


@dataclass(slots=True)
class _Counters:
    fields_seen: int = 0
    grounded_pdf_text: int = 0
    grounded_ocr: int = 0
    kept_llm: int = 0


def _value_as_string(value: object) -> str:
    """Coerce primitive field values into a search string.

    Booleans and None never produce a useful match; bool/None get
    skipped (return ""). Everything else stringifies.
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        # Use repr for floats so we never lose precision in the search
        # string; for ints just str().
        if isinstance(value, float):
            return repr(value)
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def _page_source(page: int, pages: list[PageWords]) -> BboxSource:
    for p in pages:
        if p.page == page:
            return BboxSource.PDF_TEXT if p.has_text_layer else BboxSource.OCR
    return BboxSource.PDF_TEXT
