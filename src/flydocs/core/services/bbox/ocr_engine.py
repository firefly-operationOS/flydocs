# Copyright 2026 Firefly Software Solutions Inc
"""``OcrEngine`` protocol -- pluggable OCR for image-PDFs and raster inputs.

Phase 1a ships only the ``NoneOcrEngine`` (returns empty PageWords for
every input). Image pages then keep the LLM bbox tagged ``source=llm``,
matching the documented fallback policy.

Subsequent phases add concrete engines:

* ``PaddleOcrEngine``  -- PaddleOCR PP-OCRv5, multilingual (80+ langs).
* ``TesseractEngine``  -- Tesseract 5 with explicit lang packs.
* ``MistralOcrEngine`` -- Mistral OCR HTTP API.

All implementations sit behind this Protocol; the active one is picked
by ``IDPSettings.bbox_refine_ocr_engine`` and exposed as the
``OcrEngine`` bean by :class:`IDPCoreConfiguration`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pyfly.container import service

from flydocs.core.services.bbox.word_extractor import PageWords


@runtime_checkable
class OcrEngine(Protocol):
    """OCR a binary into per-page word streams.

    Implementations operate on either rasterised PDF pages (the
    refiner rasterises with PyMuPDF before calling) or on raw image
    bytes. The ``language_hint`` field is the same string the caller
    passes via ``ExtractionOptions.language_hint`` -- engines that
    auto-detect ignore it, engines that need a hint use it.
    """

    def supports(self, media_type: str) -> bool: ...

    def recognise(
        self,
        data: bytes,
        *,
        media_type: str,
        page_count: int,
        language_hint: str | None = None,
    ) -> list[PageWords]: ...


@service
class NoneOcrEngine:
    """``OcrEngine`` no-op -- returns empty word lists for every page.

    Used when ``IDPSettings.bbox_refine_ocr_engine == "none"`` (the
    default). Image pages then fall through to the LLM bbox fallback.
    Lets the bbox refiner ship for the text-PDF flow without dragging
    in a heavy OCR dep.
    """

    def supports(self, media_type: str) -> bool:
        return True

    def recognise(
        self,
        data: bytes,
        *,
        media_type: str,
        page_count: int,
        language_hint: str | None = None,
    ) -> list[PageWords]:
        return []
