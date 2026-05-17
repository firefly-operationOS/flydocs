# Copyright 2026 Firefly Software Solutions Inc
"""Binary normalization -- turn any caller-supplied binary into LLM-renderable bytes.

The multimodal LLM providers we ship against (Anthropic, OpenAI, Bedrock)
only natively read PDF + a small set of raster image formats. Real
callers send everything: DOCX, XLSX, PPTX, RTF, ODT, HTML, EML/MSG email
with attachments, ZIP / 7z / TAR bundles, HEIC iPhone photos, multi-frame
TIFF fax scans, SVG, encrypted PDFs.

This package normalises every inbound binary into one or more
:class:`NormalisedBinary` rows -- each carrying ready-to-ship bytes plus
the resolved media type. A single inbound ZIP can fan out to many rows;
a born-digital PDF or a clean PNG is a one-row passthrough.

The normalizer is wired through pyfly DI -- :class:`BinaryNormalizer`
is the entry point; the per-format adapters (:class:`LibreOfficeConverter`,
:class:`EmailUnpacker`, :class:`ArchiveUnpacker`, :class:`ImageNormalizer`,
:class:`PdfGuard`) are autoscanned ``@service`` beans injected into it.

Errors raise typed :class:`BinaryNormalizationError` subclasses so
:class:`ExceptionAdvice` can map them to RFC 7807 problem-details.
"""

from __future__ import annotations

from flydocs.core.services.binary.archive import ArchiveUnpacker
from flydocs.core.services.binary.email import EmailUnpacker
from flydocs.core.services.binary.errors import (
    ArchiveExtractionError,
    BinaryNormalizationError,
    EncryptedPdfError,
    ImageConversionError,
    OfficeConversionError,
    UnsupportedBinaryError,
)
from flydocs.core.services.binary.gotenberg import GotenbergConverter
from flydocs.core.services.binary.image import ImageNormalizer
from flydocs.core.services.binary.libreoffice import LibreOfficeConverter
from flydocs.core.services.binary.normalizer import BinaryNormalizer, NormalisedBinary
from flydocs.core.services.binary.office_converter import OfficeConverter
from flydocs.core.services.binary.pdf_guard import PdfGuard
from flydocs.core.services.binary.sniffer import sniff_media_type

__all__ = [
    "ArchiveExtractionError",
    "ArchiveUnpacker",
    "BinaryNormalizationError",
    "BinaryNormalizer",
    "EmailUnpacker",
    "EncryptedPdfError",
    "GotenbergConverter",
    "ImageConversionError",
    "ImageNormalizer",
    "LibreOfficeConverter",
    "NormalisedBinary",
    "OfficeConversionError",
    "OfficeConverter",
    "PdfGuard",
    "UnsupportedBinaryError",
    "sniff_media_type",
]
