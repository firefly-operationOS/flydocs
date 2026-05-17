# Copyright 2026 Firefly Software Solutions Inc
"""Encrypted / corrupt PDF rejection + valid passthrough."""

from __future__ import annotations

import io

import pytest
from reportlab.pdfgen import canvas

from flydocs.core.services.binary.errors import (
    BinaryNormalizationError,
    EncryptedPdfError,
)
from flydocs.core.services.binary.pdf_guard import PdfGuard


def _build_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "hello")
    c.showPage()
    c.save()
    return buf.getvalue()


def _build_encrypted_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, encrypt="secret")
    c.drawString(100, 750, "hello")
    c.showPage()
    c.save()
    return buf.getvalue()


def test_passes_clean_pdf() -> None:
    PdfGuard().check(_build_pdf(), filename="x.pdf")


def test_rejects_encrypted_pdf() -> None:
    with pytest.raises(EncryptedPdfError):
        PdfGuard().check(_build_encrypted_pdf(), filename="x.pdf")


def test_rejects_corrupt_bytes() -> None:
    with pytest.raises(BinaryNormalizationError):
        PdfGuard().check(b"%PDF-1.7\nnot a real pdf", filename="x.pdf")
