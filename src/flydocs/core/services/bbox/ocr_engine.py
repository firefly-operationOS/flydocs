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

"""``OcrEngine`` protocol -- pluggable OCR for image-PDFs and raster inputs.

The ``NoneOcrEngine`` returns empty PageWords for every input. Image
pages then keep the LLM bbox tagged ``source=llm``, matching the
documented fallback policy.

Concrete engines plug in behind the same protocol:

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
