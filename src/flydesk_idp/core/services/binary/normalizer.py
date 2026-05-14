# Copyright 2026 Firefly Software Solutions Inc
"""``BinaryNormalizer`` -- the entry point for the binary normalization stage.

Takes ``(bytes, declared_media_type, filename)`` and returns one or more
:class:`NormalisedBinary` rows that downstream stages (extractor, bbox
refiner, judge, etc.) can ship straight to the multimodal LLM.

Routing rules (per inbound binary):

* PDF                          -> :class:`PdfGuard` integrity check, passthrough.
* PNG / JPEG / GIF / WebP      -> passthrough.
* HEIC / HEIF / AVIF / TIFF /
  BMP / SVG                    -> :class:`ImageNormalizer` (PNG or PDF).
* DOC / DOCX / XLS / XLSX /
  PPT / PPTX / ODT / ODS /
  ODP / RTF / HTML             -> :class:`LibreOfficeConverter` (PDF).
* ZIP / 7z / TAR / GZ          -> :class:`ArchiveUnpacker`, recurse on each member.
* EML / MSG                    -> :class:`EmailUnpacker`, recurse on each item.
* Plain text / Markdown / CSV  -> wrapped as text/html via a tiny <pre>
                                  envelope, then through LibreOffice. (No
                                  built-in renderer to keep deps tight.)
* Anything else                -> :class:`UnsupportedBinaryError`.

Recursion depth and total fan-out are bounded by
:class:`IDPSettings.binary_max_recursion_depth` and
:class:`IDPSettings.binary_max_expanded_files`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pyfly.container import service

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.observability import log_outbound
from flydesk_idp.core.services.binary.archive import ArchiveUnpacker
from flydesk_idp.core.services.binary.email import EmailUnpacker
from flydesk_idp.core.services.binary.errors import (
    ArchiveExtractionError,
    BinaryNormalizationError,
    UnsupportedBinaryError,
)
from flydesk_idp.core.services.binary.image import ImageNormalizer
from flydesk_idp.core.services.binary.office_converter import OfficeConverter
from flydesk_idp.core.services.binary.pdf_guard import PdfGuard
from flydesk_idp.core.services.binary.sniffer import sniff_media_type

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class NormalisedBinary:
    """One LLM-renderable row produced by the normalizer.

    A ZIP fan-out yields N rows with ``derived_from`` chaining to the
    original archive name; a one-step PDF passthrough yields one row
    with ``derived_from = ()``.
    """

    bytes: bytes
    media_type: str
    filename: str
    page_count: int
    derived_from: tuple[str, ...] = ()


# Formats the LLM accepts natively -- straight passthrough.
_LLM_NATIVE = {"image/png", "image/jpeg", "image/gif", "image/webp"}
# Plain-text-ish formats wrapped through LibreOffice via a synthetic HTML
# envelope. Keeps prose-extraction working without bringing in pandoc.
_TEXT_WRAPPED = {"text/plain", "text/markdown", "text/csv"}


@service
class BinaryNormalizer:
    """Turn any caller-supplied binary into LLM-renderable bytes."""

    def __init__(
        self,
        *,
        settings: IDPSettings,
        pdf_guard: PdfGuard,
        image: ImageNormalizer,
        office: OfficeConverter,
        archive: ArchiveUnpacker,
        email_: EmailUnpacker,
    ) -> None:
        self._settings = settings
        self._pdf_guard = pdf_guard
        self._image = image
        self._office = office
        self._archive = archive
        self._email = email_

    async def normalise(
        self,
        data: bytes,
        *,
        declared_media_type: str | None = None,
        filename: str | None = None,
    ) -> list[NormalisedBinary]:
        """Entry point. Returns one or more rows; never returns empty."""
        if not data:
            raise BinaryNormalizationError("inbound bytes are empty", filename=filename)
        if not self._settings.binary_normalize_enabled:
            media_type = sniff_media_type(data, default=declared_media_type, filename=filename)
            return [
                NormalisedBinary(
                    bytes=data,
                    media_type=media_type,
                    filename=filename or "document",
                    page_count=_page_count_for(data, media_type),
                )
            ]

        rows = await self._dispatch(
            data,
            declared_media_type=declared_media_type,
            filename=filename or "document",
            depth=0,
            ancestry=(),
        )
        if not rows:
            raise BinaryNormalizationError(
                "normalization produced no LLM-renderable output",
                filename=filename,
            )
        log_outbound(
            "binary",
            op="normalise",
            status="ok",
            latency_ms=0.0,
            in_filename=filename,
            out_rows=len(rows),
        )
        return rows

    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        data: bytes,
        *,
        declared_media_type: str | None,
        filename: str,
        depth: int,
        ancestry: tuple[str, ...],
    ) -> list[NormalisedBinary]:
        if depth > self._settings.binary_max_recursion_depth:
            raise ArchiveExtractionError(
                f"binary nesting exceeds depth {self._settings.binary_max_recursion_depth}",
                filename=filename,
            )

        media_type = sniff_media_type(data, default=declared_media_type, filename=filename)

        # --- LLM-renderable passthroughs ---
        if media_type in _LLM_NATIVE:
            return [
                NormalisedBinary(
                    bytes=data,
                    media_type=media_type,
                    filename=filename,
                    page_count=1,
                    derived_from=ancestry,
                )
            ]
        if media_type == "application/pdf":
            self._pdf_guard.check(data, filename=filename)
            return [
                NormalisedBinary(
                    bytes=data,
                    media_type=media_type,
                    filename=filename,
                    page_count=_page_count_for(data, media_type),
                    derived_from=ancestry,
                )
            ]

        # --- Image conversions ---
        if media_type in {
            "image/heic", "image/heif", "image/avif",
            "image/tiff", "image/bmp", "image/svg+xml",
        }:
            converted = self._image.convert(data, media_type=media_type, filename=filename)
            new_name = _swap_extension(filename, converted.media_type)
            return [
                NormalisedBinary(
                    bytes=converted.bytes,
                    media_type=converted.media_type,
                    filename=new_name,
                    page_count=converted.page_count,
                    derived_from=(*ancestry, filename),
                )
            ]

        # --- Office documents ---
        if self._office.supports(media_type):
            pdf_bytes = await self._office.convert(data, media_type=media_type, filename=filename)
            return [
                NormalisedBinary(
                    bytes=pdf_bytes,
                    media_type="application/pdf",
                    filename=_swap_extension(filename, "application/pdf"),
                    page_count=_page_count_for(pdf_bytes, "application/pdf"),
                    derived_from=(*ancestry, filename),
                )
            ]

        # --- Archives (recurse) ---
        if self._archive.supports(media_type):
            members = self._archive.unpack(data, media_type=media_type, filename=filename)
            rows: list[NormalisedBinary] = []
            for path, member_bytes in members:
                child_rows = await self._dispatch(
                    member_bytes,
                    declared_media_type=None,
                    filename=path.rsplit("/", 1)[-1],
                    depth=depth + 1,
                    ancestry=(*ancestry, filename),
                )
                rows.extend(child_rows)
                self._enforce_total(rows, filename)
            if not rows:
                raise ArchiveExtractionError(
                    f"archive {filename!r} expanded to zero usable members",
                    filename=filename,
                )
            return rows

        # --- Email (recurse) ---
        if self._email.supports(media_type):
            items = self._email.unpack(data, media_type=media_type, filename=filename)
            rows = []
            for path, item_bytes in items:
                child_rows = await self._dispatch(
                    item_bytes,
                    declared_media_type=None,
                    filename=path,
                    depth=depth + 1,
                    ancestry=(*ancestry, filename),
                )
                rows.extend(child_rows)
                self._enforce_total(rows, filename)
            if not rows:
                raise BinaryNormalizationError(
                    f"email {filename!r} carries no extractable content",
                    filename=filename,
                )
            return rows

        # --- Plain-text-ish: wrap in <pre> + go through OfficeConverter ---
        if media_type in _TEXT_WRAPPED:
            wrapped = _wrap_text_as_html(data, media_type)
            pdf_bytes = await self._office.convert(
                wrapped, media_type="text/html", filename=filename
            )
            return [
                NormalisedBinary(
                    bytes=pdf_bytes,
                    media_type="application/pdf",
                    filename=_swap_extension(filename, "application/pdf"),
                    page_count=_page_count_for(pdf_bytes, "application/pdf"),
                    derived_from=(*ancestry, filename),
                )
            ]

        raise UnsupportedBinaryError(
            f"no normalisation adapter for media type {media_type!r}",
            filename=filename,
        )

    def _enforce_total(self, rows: list[NormalisedBinary], filename: str) -> None:
        if len(rows) > self._settings.binary_max_expanded_files:
            raise ArchiveExtractionError(
                f"expansion of {filename!r} exceeds max "
                f"{self._settings.binary_max_expanded_files} files",
                filename=filename,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _swap_extension(filename: str, media_type: str) -> str:
    """Return ``filename`` with its extension swapped for the new media type."""
    ext_for = {
        "application/pdf": "pdf",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    new_ext = ext_for.get(media_type, "bin")
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem}.{new_ext}"


def _page_count_for(data: bytes, media_type: str) -> int:
    if media_type != "application/pdf":
        return 1
    import io as _io

    import pypdf

    try:
        reader = pypdf.PdfReader(_io.BytesIO(data), strict=False)
        return max(1, len(reader.pages))
    except Exception:  # noqa: BLE001
        return 1


def _wrap_text_as_html(data: bytes, media_type: str) -> bytes:
    """Wrap plain text / markdown / CSV in a tiny HTML envelope.

    Escapes the content so pre-formatted layout survives LibreOffice's
    HTML import. Charset hint set to UTF-8; Pillow / cairosvg / Office
    are byte-clean elsewhere so this is the one place we declare it.
    """
    import html as _html

    text = data.decode("utf-8", errors="replace")
    body = _html.escape(text)
    if media_type == "text/csv":
        # Render as a monospace block; LibreOffice will treat it as text.
        body = f"<pre>{body}</pre>"
    elif media_type == "text/markdown":
        # Conservative: render as preformatted text. We don't ship a
        # markdown -> HTML converter to keep the dep surface tight.
        body = f"<pre>{body}</pre>"
    else:
        body = f"<pre>{body}</pre>"
    html = (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset=\"utf-8\"></head>"
        f"<body>{body}</body></html>"
    )
    return html.encode("utf-8")
