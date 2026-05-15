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

from flydesk_idp.core.services.bbox.bbox_refiner import BboxRefiner, RefineCounters
from flydesk_idp.core.services.bbox.bbox_validator import BboxValidator
from flydesk_idp.core.services.bbox.hybrid_matcher import HybridValueMatcher
from flydesk_idp.core.services.bbox.llm_matcher import LlmValueMatcher
from flydesk_idp.core.services.bbox.matcher_protocol import BboxValueMatcher
from flydesk_idp.core.services.bbox.ocr_engine import NoneOcrEngine, OcrEngine
from flydesk_idp.core.services.bbox.pymupdf_words import PyMuPDFWordExtractor
from flydesk_idp.core.services.bbox.tesseract_engine import TesseractOcrEngine
from flydesk_idp.core.services.bbox.value_matcher import MatchResult, ValueMatcher
from flydesk_idp.core.services.bbox.word_extractor import PageWords, Word, WordExtractor
from flydesk_idp.core.services.bbox.word_router import WordRouter

__all__ = [
    "BboxRefiner",
    "BboxValidator",
    "BboxValueMatcher",
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
