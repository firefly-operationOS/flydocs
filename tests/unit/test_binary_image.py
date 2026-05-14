# Copyright 2026 Firefly Software Solutions Inc
"""ImageNormalizer paths -- passthrough, BMP/TIFF/SVG/multi-frame TIFF."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from flydesk_idp.core.services.binary.errors import ImageConversionError
from flydesk_idp.core.services.binary.image import ImageNormalizer


def _png_bytes(size: tuple[int, int] = (32, 32), color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _bmp_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buf, format="BMP")
    return buf.getvalue()


def _tiff_bytes(frames: int = 1) -> bytes:
    buf = io.BytesIO()
    images = [Image.new("RGB", (16, 16), color) for color in (["red", "green", "blue"][:frames])]
    images[0].save(buf, format="TIFF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def _svg_bytes() -> bytes:
    return (
        b'<?xml version="1.0"?>'
        b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
        b'<rect width="32" height="32" fill="red"/></svg>'
    )


def test_png_passthrough_keeps_bytes() -> None:
    data = _png_bytes()
    out = ImageNormalizer().convert(data, media_type="image/png")
    assert out.bytes is data
    assert out.media_type == "image/png"
    assert out.page_count == 1


def test_bmp_converts_to_png() -> None:
    out = ImageNormalizer().convert(_bmp_bytes(), media_type="image/bmp")
    assert out.media_type == "image/png"
    assert out.page_count == 1
    # Output is decodable PNG.
    Image.open(io.BytesIO(out.bytes)).verify()


def test_single_frame_tiff_becomes_png() -> None:
    out = ImageNormalizer().convert(_tiff_bytes(frames=1), media_type="image/tiff")
    assert out.media_type == "image/png"
    assert out.page_count == 1


def test_multi_frame_tiff_becomes_pdf() -> None:
    out = ImageNormalizer().convert(_tiff_bytes(frames=3), media_type="image/tiff")
    assert out.media_type == "application/pdf"
    assert out.page_count == 3
    assert out.bytes.startswith(b"%PDF-")


def test_svg_rasterises_to_png() -> None:
    # cairosvg dlopens libcairo at import time. Local dev machines without
    # libcairo installed (macOS, distroless base) skip this -- the runtime
    # Docker image bundles libcairo2 so the path is exercised end-to-end
    # via integration tests.
    pytest.importorskip(
        "cairosvg",
        reason="libcairo not available in this environment (installed in the runtime Docker image)",
        exc_type=(ImportError, OSError),
    )
    out = ImageNormalizer().convert(_svg_bytes(), media_type="image/svg+xml")
    assert out.media_type == "image/png"
    assert out.page_count == 1
    Image.open(io.BytesIO(out.bytes)).verify()


def test_empty_bytes_raises() -> None:
    with pytest.raises(ImageConversionError):
        ImageNormalizer().convert(b"", media_type="image/png")


def test_garbage_image_raises() -> None:
    with pytest.raises(ImageConversionError):
        ImageNormalizer().convert(b"random not image bytes", media_type="image/bmp")
