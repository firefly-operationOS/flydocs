# Copyright 2026 Firefly Software Solutions Inc
"""``PyMuPDFWordExtractor`` -- word + bbox extraction from PDF text layers.

Sub-pixel accurate, microseconds per page, no model. Works for any
script the original PDF embedded (Latin, Cyrillic, CJK, Arabic, RTL,
Devanagari, Thai, ...) because we just read whatever Unicode the PDF's
content stream encoded.

PyMuPDF returns ``page.get_text("words")`` as
``(x0, y0, x1, y1, word, block_no, line_no, word_no)`` with
``(0, 0)`` at the **top-left** of the page (PyMuPDF >= 1.18.x). We
divide by ``page.rect.width`` / ``page.rect.height`` to land in the
``[0, 1]`` image-space convention shared with the rest of the response.

Pages with fewer than the configured min-word threshold are reported
with ``has_text_layer=False`` so the downstream router can fall back to
OCR (or to the LLM bbox when no OCR engine is available).
"""

from __future__ import annotations

import io
import logging

from pyfly.container import service

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.bbox.word_extractor import PageWords, Word

logger = logging.getLogger(__name__)


@service
class PyMuPDFWordExtractor:
    """``WordExtractor`` for PDFs with embedded text layers."""

    def __init__(self, settings: IDPSettings) -> None:
        self._min_words_for_text_layer = settings.bbox_refine_min_text_words
        self._max_pages = settings.bbox_refine_max_text_pages

    def supports(self, media_type: str) -> bool:
        return media_type == "application/pdf"

    def extract(self, data: bytes, *, media_type: str, page_count: int) -> list[PageWords]:
        if media_type != "application/pdf" or not data:
            return []

        try:
            import pymupdf  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover -- runtime dep guard
            raise RuntimeError("pymupdf is required for PDF text-layer extraction") from exc

        pages: list[PageWords] = []
        try:
            doc = pymupdf.open(stream=io.BytesIO(data).getvalue(), filetype="pdf")
        except Exception as exc:  # noqa: BLE001 -- fall through is intentional
            logger.warning("PyMuPDF could not open document: %s", exc)
            return []

        try:
            limit = min(doc.page_count, self._max_pages)
            for page_index in range(limit):
                page = doc[page_index]
                width = float(page.rect.width) or 1.0
                height = float(page.rect.height) or 1.0
                # ``get_text("words")`` returns 8-tuples per word; the
                # last three are block/line/word indices we don't need.
                raw_words = page.get_text("words") or []
                words: list[Word] = []
                for entry in raw_words:
                    if len(entry) < 5:
                        continue
                    x0, y0, x1, y1, text = entry[0], entry[1], entry[2], entry[3], entry[4]
                    text_str = str(text).strip()
                    if not text_str:
                        continue
                    # Clamp + normalise. PyMuPDF occasionally emits
                    # coordinates that overshoot the page rect by a
                    # fraction of a point on rotated pages -- clip.
                    xmin = max(0.0, min(1.0, float(x0) / width))
                    ymin = max(0.0, min(1.0, float(y0) / height))
                    xmax = max(0.0, min(1.0, float(x1) / width))
                    ymax = max(0.0, min(1.0, float(y1) / height))
                    if xmin >= xmax or ymin >= ymax:
                        continue
                    words.append(
                        Word(
                            text=text_str,
                            page=page_index + 1,
                            xmin=xmin,
                            ymin=ymin,
                            xmax=xmax,
                            ymax=ymax,
                        )
                    )
                pages.append(
                    PageWords(
                        page=page_index + 1,
                        width=width,
                        height=height,
                        words=words,
                        has_text_layer=len(words) >= self._min_words_for_text_layer,
                    )
                )
        finally:
            doc.close()
        return pages
