# Copyright 2026 Firefly Software Solutions Inc
"""Document loader -- sniff content type, count pages, hand bytes to the LLM.

The extractor ships document bytes straight to the multimodal LLM via
``fireflyframework-agentic``'s ``BinaryContent``; this loader exists
only to (a) detect the MIME type, and (b) compute ``page_count`` for
the response. **There is no rasterisation.** The model decides how to
render internally.

Supported content types out of the box:

- ``application/pdf``                -- multi-page (counted with pypdf)
- ``image/png``, ``image/jpeg``,
  ``image/webp``, ``image/gif``,
  ``image/tiff``                     -- 1 page
- ``image/heic`` / ``image/heif``    -- 1 page (passed through)

Unknown MIME types are passed through to the LLM unchanged; the request
fails fast with a 422 only if the provider rejects it.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GIF_MAGIC_GIF87a = b"GIF87a"
_GIF_MAGIC_GIF89a = b"GIF89a"
_WEBP_RIFF = b"RIFF"
_WEBP_WEBP = b"WEBP"
_TIFF_LE = b"II*\x00"
_TIFF_BE = b"MM\x00*"

_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "image/tiff",
    "image/heic",
    "image/heif",
}


@dataclass(slots=True)
class LoadedDocument:
    """The document, ready to ship to the LLM."""

    bytes: bytes
    media_type: str
    page_count: int

    @property
    def size_bytes(self) -> int:
        return len(self.bytes)


def sniff_media_type(data: bytes, default: str | None = None) -> str:
    """Best-effort content-type detection from magic bytes."""
    if data.startswith(_PDF_MAGIC):
        return "application/pdf"
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(_GIF_MAGIC_GIF87a) or data.startswith(_GIF_MAGIC_GIF89a):
        return "image/gif"
    if data[:4] == _WEBP_RIFF and data[8:12] == _WEBP_WEBP:
        return "image/webp"
    if data.startswith(_TIFF_LE) or data.startswith(_TIFF_BE):
        return "image/tiff"
    return default or "application/octet-stream"


def _count_pdf_pages(data: bytes) -> int:
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        return max(1, len(reader.pages))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to count PDF pages, defaulting to 1: %s", exc)
        return 1


def load_document(data: bytes, *, declared_media_type: str | None = None) -> LoadedDocument:
    """Build a :class:`LoadedDocument` from raw bytes.

    The caller can pass *declared_media_type* (e.g. from an HTTP
    ``Content-Type`` header). When omitted, we sniff magic bytes.
    """
    if not data:
        raise ValueError("document bytes are empty")
    media_type = (declared_media_type or sniff_media_type(data)).lower().split(";")[0].strip()

    if media_type == "application/pdf":
        return LoadedDocument(bytes=data, media_type=media_type, page_count=_count_pdf_pages(data))

    if media_type in _IMAGE_TYPES:
        return LoadedDocument(bytes=data, media_type=media_type, page_count=1)

    # Pass through everything else (DOCX, XLSX, MD, plain text, …). Providers
    # like Anthropic accept some non-image formats natively; OpenAI does not,
    # and will return an error which we surface as 422.
    logger.debug("Passing through unrecognised media type %r", media_type)
    return LoadedDocument(bytes=data, media_type=media_type, page_count=1)
