# Copyright 2026 Firefly Software Solutions Inc
"""Bbox validation + grounded refinement.

* :class:`BboxValidator`        -- geometric hallucination check on the
  shape of an LLM-produced bbox (area, aspect, edges).
* :class:`BboxRefiner`          -- replace the LLM's coordinates with a
  grounded rectangle by fuzzy-matching the extracted value against the
  document's real text layer (PyMuPDF for born-digital PDFs;
  :class:`OcrEngine` for image-PDFs and rasters).
* :class:`WordRouter`           -- per-page strategy: text layer if
  enough words are present, otherwise OCR.
* :class:`PyMuPDFWordExtractor` -- text-layer reader.
* :class:`OcrEngine` Protocol   -- pluggable OCR; default
  :class:`NoneOcrEngine` (no-op).
* :class:`ValueMatcher`         -- multilingual, script-aware fuzzy
  matcher with light format coercion.
"""

from flydocs.core.services.bbox.bbox_refiner import BboxRefiner, RefineCounters
from flydocs.core.services.bbox.bbox_validator import BboxValidator
from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine
from flydocs.core.services.bbox.hybrid_matcher import HybridValueMatcher
from flydocs.core.services.bbox.llm_matcher import LlmValueMatcher
from flydocs.core.services.bbox.matcher_protocol import BboxValueMatcher
from flydocs.core.services.bbox.ocr_engine import NoneOcrEngine, OcrEngine
from flydocs.core.services.bbox.pymupdf_words import PyMuPDFWordExtractor
from flydocs.core.services.bbox.tesseract_engine import TesseractOcrEngine
from flydocs.core.services.bbox.value_matcher import MatchResult, ValueMatcher
from flydocs.core.services.bbox.word_extractor import PageWords, Word, WordExtractor
from flydocs.core.services.bbox.word_router import WordRouter

__all__ = [
    "BboxRefiner",
    "BboxValidator",
    "BboxValueMatcher",
    "DoclingOcrEngine",
    "HybridValueMatcher",
    "LlmValueMatcher",
    "MatchResult",
    "NoneOcrEngine",
    "OcrEngine",
    "PageWords",
    "PyMuPDFWordExtractor",
    "RefineCounters",
    "TesseractOcrEngine",
    "ValueMatcher",
    "Word",
    "WordExtractor",
    "WordRouter",
]
