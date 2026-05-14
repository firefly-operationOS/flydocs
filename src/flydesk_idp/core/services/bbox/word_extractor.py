# Copyright 2026 Firefly Software Solutions Inc
"""Word-level text extraction protocol -- the substrate the bbox refiner matches against.

A :class:`WordExtractor` returns one :class:`PageWords` per page in the
document; each carries a list of :class:`Word` rows in **reading order**
with bboxes already normalised to ``[0, 1]`` image-space (so downstream
matchers and DTOs share a single coordinate system).

Multilingual: the extractor must NOT lowercase, transliterate, or
otherwise mutate the word text -- the matcher does NFC normalisation +
optional script-aware folding on its side. Every script (Latin,
Cyrillic, Arabic, Hebrew, CJK, Thai, Khmer, Devanagari, ...) flows
through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True, frozen=True)
class Word:
    """One token from the document text layer.

    Coordinates are normalised to ``[0, 1]`` with ``(0, 0)`` at the
    top-left of the page. ``page`` is 1-indexed to match the rest of
    the response shape.
    """

    text: str
    page: int
    xmin: float
    ymin: float
    xmax: float
    ymax: float


@dataclass(slots=True, frozen=True)
class PageWords:
    """Words on a single page, in reading order.

    Empty for image-only pages when the configured OCR engine is
    ``none``. Downstream code distinguishes between "no page in scope"
    (no entry) and "scanned page, no OCR available" (entry with empty
    ``words``) by checking ``has_text_layer``.
    """

    page: int
    width: float
    height: float
    words: list[Word]
    has_text_layer: bool


@runtime_checkable
class WordExtractor(Protocol):
    """Pull a per-page word stream out of a document.

    Implementations are sync because the realistic backends
    (PyMuPDF, OCR engines) are CPU-bound. The orchestrator wraps the
    call in :func:`asyncio.to_thread` when running inside an async
    pipeline.
    """

    def supports(self, media_type: str) -> bool: ...

    def extract(self, data: bytes, *, media_type: str, page_count: int) -> list[PageWords]: ...
