# Copyright 2026 Firefly Software Solutions Inc
"""``BinaryNormalizer`` orchestration -- routing + ZIP/EML fan-out + limits."""

from __future__ import annotations

import gzip
import io
import zipfile
from email.message import EmailMessage

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.binary.archive import ArchiveUnpacker
from flydesk_idp.core.services.binary.email import EmailUnpacker
from flydesk_idp.core.services.binary.errors import (
    ArchiveExtractionError,
    OfficeConversionError,
    UnsupportedBinaryError,
)
from flydesk_idp.core.services.binary.image import ImageNormalizer
from flydesk_idp.core.services.binary.normalizer import BinaryNormalizer
from flydesk_idp.core.services.binary.office_converter import (
    OFFICE_MEDIA_TYPES,
    OfficeConverter,
)
from flydesk_idp.core.services.binary.pdf_guard import PdfGuard


class _StubOffice(OfficeConverter):
    """Office converter that records calls and returns a fake PDF."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.fail = fail

    @staticmethod
    def supports(media_type: str) -> bool:
        return media_type in OFFICE_MEDIA_TYPES

    async def convert(
        self, data: bytes, *, media_type: str, filename: str | None = None
    ) -> bytes:
        self.calls.append((media_type, filename))
        if self.fail:
            raise OfficeConversionError("stub failure", filename=filename)
        return _build_pdf()


def _build_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "hello")
    c.showPage()
    c.save()
    return buf.getvalue()


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buf, format="PNG")
    return buf.getvalue()


def _heic_like() -> bytes:
    # Synthesise a HEIC magic header so the sniffer routes correctly. The
    # ImageNormalizer.convert path is exercised with a mocked converter
    # in the relevant test rather than relying on libheif being present.
    return b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"x" * 64


def _settings(**overrides: object) -> IDPSettings:
    return IDPSettings(**overrides)  # type: ignore[arg-type]


def _normalizer(office: OfficeConverter | None = None, **settings_overrides: object) -> BinaryNormalizer:
    settings = _settings(**settings_overrides)
    return BinaryNormalizer(
        settings=settings,
        pdf_guard=PdfGuard(),
        image=ImageNormalizer(),
        office=office or _StubOffice(),
        archive=ArchiveUnpacker(settings=settings),
        email_=EmailUnpacker(),
    )


# -------------------------------------------------------------------- passthroughs


@pytest.mark.asyncio
async def test_pdf_passes_through_after_guard_check() -> None:
    rows = await _normalizer().normalise(_build_pdf(), filename="x.pdf")
    assert len(rows) == 1
    assert rows[0].media_type == "application/pdf"
    assert rows[0].filename == "x.pdf"
    assert rows[0].derived_from == ()


@pytest.mark.asyncio
async def test_png_passes_through() -> None:
    rows = await _normalizer().normalise(_png(), filename="x.png")
    assert len(rows) == 1
    assert rows[0].media_type == "image/png"
    assert rows[0].derived_from == ()


# --------------------------------------------------------------------- conversions


@pytest.mark.asyncio
async def test_docx_routed_through_office_converter() -> None:
    office = _StubOffice()
    nz = _normalizer(office=office)
    docx = b"PK\x03\x04stub-docx"  # ZIP magic + filename hint disambiguates
    rows = await nz.normalise(docx, filename="report.docx")
    assert len(rows) == 1
    assert rows[0].media_type == "application/pdf"
    assert rows[0].filename.endswith(".pdf")
    assert rows[0].derived_from == ("report.docx",)
    assert office.calls and office.calls[0][1] == "report.docx"


@pytest.mark.asyncio
async def test_office_failure_propagates_typed_exception() -> None:
    nz = _normalizer(office=_StubOffice(fail=True))
    with pytest.raises(OfficeConversionError):
        await nz.normalise(b"PK\x03\x04stub", filename="r.docx")


# ---------------------------------------------------------------------- archives


@pytest.mark.asyncio
async def test_zip_fans_out_to_per_member_rows() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("passport.pdf", _build_pdf())
        zf.writestr("photo.png", _png())
    rows = await _normalizer().normalise(buf.getvalue(), filename="bundle.zip")
    by_name = {r.filename: r for r in rows}
    assert "passport.pdf" in by_name
    assert "photo.png" in by_name
    assert by_name["passport.pdf"].media_type == "application/pdf"
    assert by_name["photo.png"].media_type == "image/png"
    assert by_name["passport.pdf"].derived_from == ("bundle.zip",)


@pytest.mark.asyncio
async def test_gzip_unwraps_to_inner_format() -> None:
    inner_pdf = _build_pdf()
    gz_bytes = gzip.compress(inner_pdf)
    rows = await _normalizer().normalise(gz_bytes, filename="report.pdf.gz")
    assert len(rows) == 1
    assert rows[0].media_type == "application/pdf"
    assert rows[0].derived_from == ("report.pdf.gz",)


@pytest.mark.asyncio
async def test_recursion_depth_limit_enforced() -> None:
    inner = _build_pdf()
    # Three levels of gzip wrap a single PDF.
    g1 = gzip.compress(inner)
    g2 = gzip.compress(g1)
    g3 = gzip.compress(g2)
    g4 = gzip.compress(g3)
    nz = _normalizer(binary_max_recursion_depth=2)
    with pytest.raises(ArchiveExtractionError):
        await nz.normalise(g4, filename="x.gz")


@pytest.mark.asyncio
async def test_total_fan_out_limit_enforced() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(5):
            zf.writestr(f"f{i}.pdf", _build_pdf())
    nz = _normalizer(binary_max_expanded_files=2)
    with pytest.raises(ArchiveExtractionError):
        await nz.normalise(buf.getvalue(), filename="b.zip")


# ----------------------------------------------------------------------- email


@pytest.mark.asyncio
async def test_eml_attachment_is_recursed_through_normalizer() -> None:
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "x"
    msg.set_content("ignore me")
    msg.add_attachment(
        _build_pdf(),
        maintype="application",
        subtype="pdf",
        filename="visa.pdf",
    )
    rows = await _normalizer().normalise(msg.as_bytes(), filename="msg.eml")
    by_name = {r.filename: r for r in rows}
    assert "visa.pdf" in by_name
    assert by_name["visa.pdf"].media_type == "application/pdf"


# --------------------------------------------------------------------- unsupported


@pytest.mark.asyncio
async def test_truly_unknown_binary_raises_unsupported() -> None:
    with pytest.raises(UnsupportedBinaryError):
        await _normalizer().normalise(b"\x00\x01\x02unknown random", filename="x.bin")


# --------------------------------------------------------------------- kill switch


@pytest.mark.asyncio
async def test_disabled_normalizer_passes_bytes_through() -> None:
    nz = _normalizer(binary_normalize_enabled=False)
    pdf = _build_pdf()
    rows = await nz.normalise(pdf, filename="x.pdf")
    assert len(rows) == 1
    # Unchanged passthrough -- no PdfGuard, no LibreOffice, no nothing.
    assert rows[0].bytes is pdf
