# Copyright 2026 Firefly Software Solutions Inc
"""``BboxRefiner`` -- replace LLM-estimated bboxes with grounded ones.

Walks every :class:`ExtractedField` in every group, asks the configured
:class:`BboxValueMatcher` (LLM-driven by default; deterministic fuzzy
matcher is the opt-in alternative) to locate each value against the
document's word stream, and rewrites the bbox in place when a hit
lands above the configured threshold. Misses keep the LLM bbox tagged
``source=llm, refinement_confidence=null`` -- documented fallback,
never silently drop a coordinate.

Sub-fields of array-typed parents are recursed into; the matcher runs
per leaf value. The refiner collects every leaf into one batched call
so an LLM matcher can issue a single per-page request covering every
field instead of one call per field.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from pyfly.container import service

from flydocs.core.observability import log_outbound
from flydocs.core.services.bbox.matcher_protocol import BboxValueMatcher
from flydocs.core.services.bbox.value_matcher import MatchResult
from flydocs.core.services.bbox.word_extractor import PageWords
from flydocs.core.services.bbox.word_router import WordRouter
from flydocs.interfaces.dtos.bbox import BboxSource, BoundingBox
from flydocs.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup

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
    """Orchestrate text-layer / OCR word collection + batched field matching."""

    def __init__(self, *, router: WordRouter, matcher: BboxValueMatcher) -> None:
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
        # Walk every leaf and collect (field_id, value, candidate_pages)
        # so the matcher can batch them into a single per-page LLM call
        # (or a single fuzzy sweep, depending on the backend).
        leaves: list[ExtractedField] = []
        targets: list[tuple[str, str, list[int] | None]] = []
        for group in groups:
            for field in group.fields:
                self._collect_leaves(field, leaves)
        for idx, field in enumerate(leaves):
            value_str = _value_as_string(field.value)
            if not value_str:
                continue
            targets.append((str(idx), value_str, field.pages or None))

        counters = _Counters()
        counters.fields_seen = len(leaves)
        # Idempotency: when the inline refine pass already grounded a
        # field (source ∈ {PDF_TEXT, OCR}), the out-of-band worker must
        # not re-locate it. Re-running the matcher would burn LLM
        # tokens / OCR CPU and risk replacing a high-confidence
        # rectangle with a weaker one. Count those as already-grounded.
        for field in leaves:
            src = field.bbox.source if field.bbox is not None else None
            if src == BboxSource.PDF_TEXT:
                counters.grounded_pdf_text += 1
            elif src == BboxSource.OCR:
                counters.grounded_ocr += 1
        already_grounded_ids = {
            str(idx)
            for idx, field in enumerate(leaves)
            if field.bbox is not None and field.bbox.source in (BboxSource.PDF_TEXT, BboxSource.OCR)
        }
        residual_targets = [t for t in targets if t[0] not in already_grounded_ids]
        if residual_targets:
            results = await self._matcher.locate_all(pages=pages, fields=residual_targets)
            for field_id, _value, _candidate in residual_targets:
                field = leaves[int(field_id)]
                match = results.get(field_id)
                if match is None:
                    if field.bbox is not None:
                        field.bbox.source = BboxSource.LLM
                        field.bbox.refinement_confidence = None
                    counters.kept_llm += 1
                    continue
                page_source = _page_source(match.page, pages)
                self._replace_bbox(field, match, page_source)
                if page_source == BboxSource.PDF_TEXT:
                    counters.grounded_pdf_text += 1
                else:
                    counters.grounded_ocr += 1

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

    def _collect_leaves(self, field: ExtractedField, sink: list[ExtractedField]) -> None:
        """Flatten array parents -- only leaf scalar fields are matched."""
        if isinstance(field.value, list):
            for child in field.value:
                if isinstance(child, ExtractedField):
                    self._collect_leaves(child, sink)
            return
        sink.append(field)

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
            quality=old.quality if old is not None else None,
            quality_score=old.quality_score if old is not None else 0.0,
            source=source,
            refinement_confidence=match.score,
        )
        field.bbox = new
        if match.page not in field.pages:
            field.pages = [match.page, *field.pages]


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
    """Return the extractor that produced the words on ``page``.

    The discriminator is recorded by the extractor itself
    (PyMuPDF -> PDF_TEXT, Tesseract / Mistral / Paddle -> OCR) so the
    refiner can tag refined bboxes with their true origin. Falls back
    to ``PDF_TEXT`` when the page isn't in the supplied list (defensive
    -- the matcher only matches against pages it actually saw).
    """
    for p in pages:
        if p.page == page:
            return p.source
    return BboxSource.PDF_TEXT
