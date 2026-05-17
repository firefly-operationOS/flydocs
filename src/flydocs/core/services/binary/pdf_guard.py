# Copyright 2026 Firefly Software Solutions Inc
"""PDF integrity + encryption guard.

Runs as the very first step of the normalizer for every ``application/pdf``
input -- so encrypted / corrupt PDFs fail fast with a typed error
instead of crashing PyMuPDF / pypdf later in the pipeline.
"""

from __future__ import annotations

import io
import logging

from pyfly.container import service

from flydocs.core.services.binary.errors import (
    BinaryNormalizationError,
    EncryptedPdfError,
)

logger = logging.getLogger(__name__)


@service
class PdfGuard:
    """Reject encrypted PDFs and confirm the file is parseable."""

    def check(self, data: bytes, *, filename: str | None = None) -> None:
        """Raise on encrypted / truncated / corrupt PDFs; return on success.

        Cheap: opens the PDF with pypdf in non-strict mode, peeks at the
        encryption flag and the page tree. No content streams parsed.
        """
        import pypdf
        from pypdf.errors import PdfReadError

        try:
            reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        except PdfReadError as exc:
            raise BinaryNormalizationError(f"PDF cannot be parsed: {exc}", filename=filename) from exc
        except Exception as exc:  # noqa: BLE001
            raise BinaryNormalizationError(f"PDF cannot be parsed: {exc}", filename=filename) from exc

        if reader.is_encrypted:
            # We never attempt empty-password decrypt -- if the document
            # really has no password, the caller can re-export it
            # client-side. (A future request surface may carry a
            # ``password`` field; this guard would consume it here.)
            raise EncryptedPdfError(
                "PDF is encrypted; supply an unprotected copy.",
                filename=filename,
            )

        # Touch the page tree so a corrupt xref bubbles up here, not later.
        try:
            page_count = len(reader.pages)
        except Exception as exc:  # noqa: BLE001
            raise BinaryNormalizationError(f"PDF page tree is corrupt: {exc}", filename=filename) from exc
        if page_count <= 0:
            raise BinaryNormalizationError("PDF reports zero pages.", filename=filename)
