# Copyright 2026 Firefly Software Solutions Inc
"""Raster + vector image normalization.

The multimodal LLM providers we ship against accept PNG, JPEG, GIF, WebP
natively. Everything else has to be converted on our side:

* **HEIC / HEIF**     -- iPhone / iPad photos. Converted to PNG via
                          Pillow + pillow-heif.
* **AVIF**            -- modern web format. Same path (pillow-heif).
* **Multi-frame TIFF** -- common for fax-scanned documents. Each frame
                          becomes a page in a single output PDF so the
                          downstream pipeline keeps a single
                          ``LoadedDocument`` per input.
* **BMP**             -- legacy Windows bitmap. PNG.
* **Single-frame TIFF** -- usually fine for the LLM but normalising to
                          PNG keeps the per-tile decode predictable.
* **SVG**             -- vector. Rasterised to PNG via cairosvg.
* **Animated GIF**    -- first frame only (the LLM treats it as a still).
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass

from pyfly.container import service

from flydocs.core.observability import log_outbound
from flydocs.core.services.binary.errors import ImageConversionError

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class NormalisedImage:
    """Output of :class:`ImageNormalizer.convert`.

    ``page_count`` is > 1 only for multi-frame TIFF that has been folded
    into a multi-page PDF; for everything else it is 1.
    """

    bytes: bytes
    media_type: str
    page_count: int


@service
class ImageNormalizer:
    """Pillow-driven converter for image formats the LLM can't read natively."""

    # Formats Pillow's autodetect names; used to pick the conversion path.
    _HEIC_TYPES = {"image/heic", "image/heif", "image/avif"}
    _PASSTHROUGH = {"image/png", "image/jpeg", "image/gif", "image/webp"}

    def convert(self, data: bytes, *, media_type: str, filename: str | None = None) -> NormalisedImage:
        """Return a renderable PNG / PDF / passthrough image.

        Raises :class:`ImageConversionError` on any decode / encode
        failure. Pillow / pillow-heif must be installed at runtime.
        """
        if not data:
            raise ImageConversionError("image bytes are empty", filename=filename)

        if media_type in self._PASSTHROUGH:
            return NormalisedImage(bytes=data, media_type=media_type, page_count=1)

        if media_type == "image/svg+xml":
            return self._svg_to_png(data, filename)
        if media_type in self._HEIC_TYPES:
            return self._heic_to_png(data, filename)
        if media_type == "image/tiff":
            return self._tiff_to_pdf(data, filename)
        if media_type == "image/bmp":
            return self._raster_to_png(data, "BMP", filename)

        # Catch-all: try Pillow's autodetect + PNG re-encode. Covers exotic
        # raster formats (PCX, TGA, etc.) without us having to enumerate.
        return self._raster_to_png(data, fmt=None, filename=filename)

    # ------------------------------------------------------------------

    def _heic_to_png(self, data: bytes, filename: str | None) -> NormalisedImage:
        # pillow-heif registers HEIF/HEIC/AVIF openers on import. Side-effect
        # registration is the library's published API.
        try:
            import pillow_heif  # pyright: ignore[reportMissingImports]  # noqa: F401
        except ImportError as exc:  # pragma: no cover -- runtime dep guard
            raise ImageConversionError(
                "pillow-heif is required for HEIC/HEIF/AVIF input",
                filename=filename,
            ) from exc
        return self._raster_to_png(data, fmt=None, filename=filename, library="pillow-heif")

    def _raster_to_png(
        self,
        data: bytes,
        fmt: str | None,
        filename: str | None,
        *,
        library: str = "pillow",
    ) -> NormalisedImage:
        from PIL import Image, UnidentifiedImageError

        started = time.monotonic()
        try:
            with Image.open(io.BytesIO(data)) as img:
                if fmt and (img.format or "").upper() != fmt:
                    # Pillow autodetected something else; trust autodetect over
                    # the caller's MIME hint -- mismatched MIME is common.
                    pass
                # Convert to RGB(A) so PNG re-encode never falls into a mode
                # we can't write (e.g. CMYK from a TIFF).
                if img.mode not in ("RGB", "RGBA", "L", "LA"):
                    img = img.convert("RGBA")
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
        except UnidentifiedImageError as exc:
            raise ImageConversionError(
                f"image bytes are not a recognised raster format: {exc}",
                filename=filename,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ImageConversionError(f"image conversion failed: {exc}", filename=filename) from exc
        log_outbound(
            library,
            op="image.png",
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            in_bytes=len(data),
            out_bytes=buf.tell(),
        )
        return NormalisedImage(bytes=buf.getvalue(), media_type="image/png", page_count=1)

    def _tiff_to_pdf(self, data: bytes, filename: str | None) -> NormalisedImage:
        """Multi-frame TIFF → multi-page PDF; single-frame TIFF → PNG.

        We bundle multi-frame scans into one PDF so the downstream
        ``page_count`` matches what the document actually carries.
        """
        from PIL import Image, UnidentifiedImageError

        started = time.monotonic()
        try:
            with Image.open(io.BytesIO(data)) as img:
                frames: list[Image.Image] = []
                try:
                    while True:
                        frames.append(img.copy().convert("RGB"))
                        img.seek(img.tell() + 1)
                except EOFError:
                    pass
        except UnidentifiedImageError as exc:
            raise ImageConversionError(f"TIFF bytes are not parseable: {exc}", filename=filename) from exc
        except Exception as exc:  # noqa: BLE001
            raise ImageConversionError(f"TIFF conversion failed: {exc}", filename=filename) from exc

        if not frames:
            raise ImageConversionError("TIFF contains no frames", filename=filename)

        if len(frames) == 1:
            buf = io.BytesIO()
            frames[0].save(buf, format="PNG", optimize=True)
            log_outbound(
                "pillow",
                op="tiff.png",
                status="ok",
                latency_ms=(time.monotonic() - started) * 1000,
                frames=1,
                out_bytes=buf.tell(),
            )
            return NormalisedImage(bytes=buf.getvalue(), media_type="image/png", page_count=1)

        buf = io.BytesIO()
        frames[0].save(
            buf,
            format="PDF",
            save_all=True,
            append_images=frames[1:],
            resolution=200.0,
        )
        log_outbound(
            "pillow",
            op="tiff.pdf",
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            frames=len(frames),
            out_bytes=buf.tell(),
        )
        return NormalisedImage(
            bytes=buf.getvalue(),
            media_type="application/pdf",
            page_count=len(frames),
        )

    def _svg_to_png(self, data: bytes, filename: str | None) -> NormalisedImage:
        try:
            import cairosvg  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover -- runtime dep guard
            raise ImageConversionError("cairosvg is required for SVG input", filename=filename) from exc

        started = time.monotonic()
        try:
            raw = cairosvg.svg2png(bytestring=data, output_width=2048)
        except Exception as exc:  # noqa: BLE001
            raise ImageConversionError(f"SVG rasterisation failed: {exc}", filename=filename) from exc
        if not raw:
            raise ImageConversionError("SVG rasterisation produced no output", filename=filename)
        png_bytes: bytes = raw if isinstance(raw, bytes) else bytes(raw)
        log_outbound(
            "cairosvg",
            op="svg.png",
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            in_bytes=len(data),
            out_bytes=len(png_bytes),
        )
        return NormalisedImage(bytes=png_bytes, media_type="image/png", page_count=1)
