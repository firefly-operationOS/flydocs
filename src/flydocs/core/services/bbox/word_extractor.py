# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

from flydocs.interfaces.dtos.bbox import BboxSource


@dataclass(slots=True, frozen=True)
class Word:
    """One token from the document text layer.

    Coordinates are normalised to ``[0, 1]`` with ``(0, 0)`` at the
    top-left of the page. ``page`` is 1-indexed to match the rest of
    the response shape.

    Optional structural metadata is populated only by layout-aware
    engines (e.g. :class:`DoclingOcrEngine`); PyMuPDF and Tesseract
    leave every optional field as ``None``. Downstream consumers must
    treat absence as "structure unknown", not "definitely not in a
    table" -- the legacy engines never tag it either way.

    * ``reading_order``  -- monotonically increasing per page in
      visual reading order. Tie-break signal for the value matcher.
    * ``table_id``       -- stable identifier for the table the word
      lives in. ``None`` for non-tabular words.
    * ``row_idx`` / ``col_idx`` -- 0-indexed position inside the table.
    """

    text: str
    page: int
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    reading_order: int | None = None
    table_id: str | None = None
    row_idx: int | None = None
    col_idx: int | None = None


@dataclass(slots=True, frozen=True)
class PageWords:
    """Words on a single page, in reading order.

    Empty for image-only pages when the configured OCR engine is
    ``none``. Downstream code distinguishes between "no page in scope"
    (no entry) and "scanned page, no OCR available" (entry with empty
    ``words``) by checking ``has_text_layer``.

    ``source`` records which extractor produced these words -- the
    refiner carries it through to the response's ``BoundingBox.source``
    field so callers can distinguish grounded-via-PDF-text from
    grounded-via-OCR coordinates.
    """

    page: int
    width: float
    height: float
    words: list[Word]
    has_text_layer: bool
    source: BboxSource = BboxSource.PDF_TEXT


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
