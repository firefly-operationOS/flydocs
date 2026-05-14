# Copyright 2026 Firefly Software Solutions Inc
"""``WordRouter`` -- per-page strategy: text layer vs OCR.

For each page in the input:

1. PyMuPDF reads the embedded text layer.
2. If the page has >= ``bbox_refine_min_text_words`` extracted words,
   keep the text-layer result (sub-pixel accurate).
3. Otherwise the page is image-only: hand it to the configured
   :class:`OcrEngine`. When the engine is :class:`NoneOcrEngine` (the
   default), the page yields an empty word list and downstream matching
   for fields on that page falls back to the LLM bbox.

This per-page routing handles **hybrid PDFs** where some pages are
born-digital and others are scanned.
"""

from __future__ import annotations

import logging

from pyfly.container import service

from flydesk_idp.core.services.bbox.ocr_engine import OcrEngine
from flydesk_idp.core.services.bbox.pymupdf_words import PyMuPDFWordExtractor
from flydesk_idp.core.services.bbox.word_extractor import PageWords

logger = logging.getLogger(__name__)


@service
class WordRouter:
    """Decide per page whether to use the PDF text layer or OCR."""

    def __init__(self, *, pymupdf: PyMuPDFWordExtractor, ocr: OcrEngine) -> None:
        self._pymupdf = pymupdf
        self._ocr = ocr

    def collect(
        self,
        data: bytes,
        *,
        media_type: str,
        page_count: int,
        language_hint: str | None = None,
    ) -> list[PageWords]:
        """Return one :class:`PageWords` per page in routing order.

        For PDFs: text-layer first, OCR for image-only pages.
        For images: OCR (no text-layer to read).
        """
        if media_type == "application/pdf":
            text_pages = self._pymupdf.extract(data, media_type=media_type, page_count=page_count)
            # Pages PyMuPDF couldn't read or that fell below the text-layer
            # threshold are routed to OCR. We always attempt OCR even if it's
            # the no-op engine -- the router doesn't know whether a real
            # adapter is wired and the engine itself is responsible for
            # short-circuiting.
            image_pages = [p.page for p in text_pages if not p.has_text_layer]
            if not image_pages:
                return text_pages
            ocr_pages = self._ocr.recognise(
                data,
                media_type=media_type,
                page_count=page_count,
                language_hint=language_hint,
            )
            ocr_by_page = {p.page: p for p in ocr_pages}
            merged: list[PageWords] = []
            for tp in text_pages:
                if tp.has_text_layer:
                    merged.append(tp)
                elif tp.page in ocr_by_page:
                    merged.append(ocr_by_page[tp.page])
                else:
                    merged.append(tp)  # OCR returned nothing -- keep the empty entry
            return merged

        # Raster inputs go straight to OCR.
        if media_type.startswith("image/"):
            return self._ocr.recognise(
                data,
                media_type=media_type,
                page_count=page_count,
                language_hint=language_hint,
            )

        # Anything else (text/plain, application/octet-stream, ...) has no
        # word stream; the refiner will fall back to LLM bboxes.
        return []
