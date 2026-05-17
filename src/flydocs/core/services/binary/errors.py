# Copyright 2026 Firefly Software Solutions Inc
"""Typed exceptions raised by the binary normalizer.

All subclasses of :class:`BinaryNormalizationError` are mapped to RFC 7807
problem-details by :class:`ExceptionAdvice` so callers receive a
``application/problem+json`` body with a stable ``code`` they can branch on.
"""

from __future__ import annotations


class BinaryNormalizationError(ValueError):
    """Base class -- a caller-supplied binary cannot be made LLM-renderable.

    Subclassing :class:`ValueError` so the existing
    ``ExceptionAdvice.invalid_request`` 400 handler still catches it as
    a fallback when no more specific handler matches.
    """

    code: str = "binary_normalization_error"
    http_status: int = 422

    def __init__(self, message: str, *, filename: str | None = None) -> None:
        super().__init__(message)
        self.filename = filename


class UnsupportedBinaryError(BinaryNormalizationError):
    """The MIME type / magic bytes are recognised but no adapter handles them.

    Examples: a video file, a raw audio sample, a proprietary CAD format.
    The caller needs to convert client-side or pick a different file.
    """

    code = "unsupported_binary"


class EncryptedPdfError(BinaryNormalizationError):
    """The inbound PDF is password-protected and no password was provided.

    The normalizer never attempts to brute-force; if a future request
    surface adds a ``password`` field, the PDF guard will accept it.
    """

    code = "encrypted_pdf"


class OfficeConversionError(BinaryNormalizationError):
    """LibreOffice failed to convert an Office document to PDF.

    Wraps the subprocess failure -- the ``detail`` carries the trimmed
    stderr so callers can see why (e.g. corrupt document, unsupported
    macro, missing language pack).
    """

    code = "office_conversion_failed"


class ArchiveExtractionError(BinaryNormalizationError):
    """A ZIP / 7z / TAR archive could not be expanded.

    Covers: corrupt archive, password-protected member, exceeding the
    configured fan-out / depth limits (zip-bomb guard).
    """

    code = "archive_extraction_failed"


class ImageConversionError(BinaryNormalizationError):
    """Pillow / pillow-heif / cairosvg refused the inbound image.

    Usually means the file is truncated, claims a MIME type its bytes
    don't match, or uses a codec the runtime image lacks (e.g. HEIC
    without ``libheif`` installed).
    """

    code = "image_conversion_failed"
