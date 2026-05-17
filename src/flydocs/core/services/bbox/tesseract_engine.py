# Copyright 2026 Firefly Software Solutions Inc
"""``TesseractOcrEngine`` -- ``OcrEngine`` adapter that shells out to tesseract.

For each page of an image-only PDF the engine:

1. Rasterises the page with PyMuPDF at the configured DPI.
2. Runs ``tesseract`` via :func:`pytesseract.image_to_data` with the
   composed language string (``spa+eng`` by default, overridden per-
   request via :attr:`ExtractionOptions.language_hint`).
3. Walks the returned word stream and emits one :class:`Word` per
   token with bboxes normalised to ``[0, 1]`` image-space.

Raster inputs (PNG / JPEG / etc.) are handed straight to tesseract --
no rasterisation step. Multi-frame TIFF is already normalised to a
multi-page PDF by :class:`ImageNormalizer` upstream, so by the time the
OCR sees it, every input is either ``application/pdf`` or one of the
LLM-renderable image MIMEs.

The engine is multilingual by construction: every Tesseract
``traineddata`` pack the runtime image installs becomes available
without code changes -- the operator just adds ``tesseract-ocr-<lang>``
to the Dockerfile and bumps ``bbox_refine_tesseract_lang``.
"""

from __future__ import annotations

import io
import logging
import time

from pyfly.container import service

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound
from flydocs.core.services.bbox.word_extractor import PageWords, Word
from flydocs.interfaces.dtos.bbox import BboxSource

logger = logging.getLogger(__name__)


# ISO 639-1 -> Tesseract 639-2/B. Extend as more language packs land in
# the Dockerfile. Unknown codes fall back to the default ``lang`` setting.
_ISO1_TO_TESS: dict[str, str] = {
    "es": "spa",
    "en": "eng",
    "fr": "fra",
    "de": "deu",
    "it": "ita",
    "pt": "por",
    "nl": "nld",
    "ca": "cat",
    "eu": "eus",
    "gl": "glg",
    "ja": "jpn",
    "ko": "kor",
    "zh": "chi_sim",
    "ar": "ara",
    "ru": "rus",
}


@service
class TesseractOcrEngine:
    """``OcrEngine`` backed by local Tesseract."""

    def __init__(self, settings: IDPSettings) -> None:
        self._dpi = settings.bbox_refine_ocr_dpi
        self._default_lang = settings.bbox_refine_tesseract_lang

    def supports(self, media_type: str) -> bool:
        if media_type == "application/pdf":
            return True
        return media_type in {"image/png", "image/jpeg", "image/gif", "image/webp"}

    def recognise(
        self,
        data: bytes,
        *,
        media_type: str,
        page_count: int,
        language_hint: str | None = None,
    ) -> list[PageWords]:
        if not data:
            return []
        lang = self._resolve_lang(language_hint)
        if media_type == "application/pdf":
            return self._recognise_pdf(data, lang=lang)
        return self._recognise_image(data, lang=lang)

    # ------------------------------------------------------------------

    def _resolve_lang(self, hint: str | None) -> str:
        if not hint:
            return self._default_lang
        norm = hint.strip().lower()
        if not norm:
            return self._default_lang
        # Accept either ISO 639-1 (``es``) or already-tesseract (``spa``).
        mapped = _ISO1_TO_TESS.get(norm)
        if mapped:
            return mapped
        if len(norm) == 3 and norm.isalpha():
            return norm  # already 3-letter; let tesseract reject if unknown
        return self._default_lang

    def _recognise_pdf(self, data: bytes, *, lang: str) -> list[PageWords]:
        try:
            import pymupdf  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover -- runtime dep guard
            raise RuntimeError("pymupdf is required to rasterise PDFs for OCR") from exc

        pages: list[PageWords] = []
        started = time.monotonic()
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            for page_index in range(doc.page_count):
                page = doc[page_index]
                pix = page.get_pixmap(dpi=self._dpi)
                png_bytes = pix.tobytes("png")
                page_words = self._ocr_image_bytes(
                    png_bytes,
                    page_number=page_index + 1,
                    lang=lang,
                )
                pages.append(page_words)
        finally:
            doc.close()
        log_outbound(
            "tesseract",
            op="ocr.pdf",
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            pages=len(pages),
            lang=lang,
            dpi=self._dpi,
        )
        return pages

    def _recognise_image(self, data: bytes, *, lang: str) -> list[PageWords]:
        started = time.monotonic()
        page_words = self._ocr_image_bytes(data, page_number=1, lang=lang)
        log_outbound(
            "tesseract",
            op="ocr.image",
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            pages=1,
            lang=lang,
        )
        return [page_words]

    def _ocr_image_bytes(
        self,
        image_bytes: bytes,
        *,
        page_number: int,
        lang: str,
    ) -> PageWords:
        from PIL import Image

        try:
            import pytesseract  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover -- runtime dep guard
            raise RuntimeError("pytesseract is required for the Tesseract OCR engine") from exc

        with Image.open(io.BytesIO(image_bytes)) as img:
            width = float(img.width) or 1.0
            height = float(img.height) or 1.0
            try:
                data = pytesseract.image_to_data(  # pyright: ignore[reportAttributeAccessIssue]
                    img,
                    lang=lang,
                    output_type=pytesseract.Output.DICT,  # pyright: ignore[reportAttributeAccessIssue]
                )
            except pytesseract.TesseractNotFoundError as exc:  # pyright: ignore[reportAttributeAccessIssue]
                raise RuntimeError("tesseract binary not found on PATH; install ``tesseract-ocr``") from exc

        words: list[Word] = []
        texts = data.get("text", [])
        confs = data.get("conf", [])
        lefts = data.get("left", [])
        tops = data.get("top", [])
        widths = data.get("width", [])
        heights = data.get("height", [])
        for idx, text in enumerate(texts):
            txt = (text or "").strip()
            if not txt:
                continue
            try:
                conf_val = float(confs[idx])
            except (TypeError, ValueError):
                conf_val = -1.0
            if conf_val < 0:
                # Tesseract emits -1 for tokens it didn't actually recognise
                # (e.g. layout-only entries). Skip.
                continue
            x = float(lefts[idx])
            y = float(tops[idx])
            w = float(widths[idx])
            h = float(heights[idx])
            if w <= 0 or h <= 0:
                continue
            xmin = max(0.0, min(1.0, x / width))
            ymin = max(0.0, min(1.0, y / height))
            xmax = max(0.0, min(1.0, (x + w) / width))
            ymax = max(0.0, min(1.0, (y + h) / height))
            if xmin >= xmax or ymin >= ymax:
                continue
            words.append(
                Word(
                    text=txt,
                    page=page_number,
                    xmin=xmin,
                    ymin=ymin,
                    xmax=xmax,
                    ymax=ymax,
                )
            )
        return PageWords(
            page=page_number,
            width=width,
            height=height,
            words=words,
            has_text_layer=bool(words),
            source=BboxSource.OCR,
        )
