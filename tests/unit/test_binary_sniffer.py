# Copyright 2026 Firefly Software Solutions Inc
"""Magic-byte + filename sniffer for the binary normalizer."""

from __future__ import annotations

import io
import zipfile

import pytest

from flydesk_idp.core.services.binary.sniffer import sniff_media_type


def test_sniffs_pdf() -> None:
    assert sniff_media_type(b"%PDF-1.7\nrest") == "application/pdf"


def test_sniffs_png() -> None:
    assert sniff_media_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"


def test_sniffs_jpeg() -> None:
    assert sniff_media_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"


def test_sniffs_gif() -> None:
    assert sniff_media_type(b"GIF89arest") == "image/gif"


def test_sniffs_webp() -> None:
    assert sniff_media_type(b"RIFF1234WEBPrest") == "image/webp"


def test_sniffs_tiff_le() -> None:
    assert sniff_media_type(b"II*\x00rest") == "image/tiff"


def test_sniffs_tiff_be() -> None:
    assert sniff_media_type(b"MM\x00*rest") == "image/tiff"


def test_sniffs_heic_via_ftyp_brand() -> None:
    # HEIC magic: bytes 4..8 = 'ftyp', bytes 8..12 = 4-char brand.
    blob = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
    assert sniff_media_type(blob) == "image/heic"


def test_sniffs_avif_via_ftyp_brand() -> None:
    blob = b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00mif1heic"
    assert sniff_media_type(blob) == "image/avif"


def test_sniffs_zip_with_no_filename_falls_back_to_zip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hi")
    assert sniff_media_type(buf.getvalue()) == "application/zip"


@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("odt", "application/vnd.oasis.opendocument.text"),
        ("epub", "application/epub+zip"),
    ],
)
def test_disambiguates_zip_via_filename_extension(ext: str, expected: str) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hi")
    assert sniff_media_type(buf.getvalue(), filename=f"doc.{ext}") == expected


def test_sniffs_svg_by_content() -> None:
    assert (
        sniff_media_type(b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        == "image/svg+xml"
    )


def test_sniffs_html_by_content() -> None:
    assert sniff_media_type(b"<!DOCTYPE html>\n<html></html>") == "text/html"


def test_sniffs_eml_by_headers() -> None:
    eml = b"Return-Path: <a@b.com>\nFrom: a@b.com\nTo: c@d.com\nSubject: hi\n\nbody"
    assert sniff_media_type(eml) == "message/rfc822"


def test_falls_back_to_extension_for_unknown_magic() -> None:
    # 7z-like garbage with .pdf extension: extension wins as last resort.
    assert sniff_media_type(b"random bytes", filename="x.pdf") == "application/pdf"


def test_returns_octet_stream_for_truly_unknown() -> None:
    assert sniff_media_type(b"\x00\x01\x02\x03random") == "application/octet-stream"


def test_empty_data_returns_default_or_octet_stream() -> None:
    assert sniff_media_type(b"") == "application/octet-stream"
    assert sniff_media_type(b"", default="image/png") == "image/png"
