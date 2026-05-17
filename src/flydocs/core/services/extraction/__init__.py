# Copyright 2026 Firefly Software Solutions Inc
"""Multimodal extraction core -- schema-driven, LLM-backed, bbox-aware."""

from flydocs.core.services.extraction.extractor import MultimodalExtractor
from flydocs.core.services.extraction.loader import LoadedDocument, load_document, sniff_media_type
from flydocs.core.services.extraction.postprocess import normalise_doc
from flydocs.core.services.extraction.schema import build_extraction_output_model

__all__ = [
    "LoadedDocument",
    "MultimodalExtractor",
    "build_extraction_output_model",
    "load_document",
    "normalise_doc",
    "sniff_media_type",
]
