# Copyright 2026 Firefly Software Solutions Inc
"""``DoclingOcrEngine`` -- ``OcrEngine`` adapter backed by IBM's Docling.

Docling is a local document-parsing library (LF AI&Data Foundation,
MIT). For the bbox refiner it sits in the same slot as
:class:`TesseractOcrEngine`: given image-PDF or raster bytes, return a
per-page word stream with bboxes normalised to ``[0, 1]`` top-left
image-space. The difference is what's behind the slot:

* Tesseract is a flat per-pixel OCR -- it has no idea what a heading,
  a table cell, or a figure is.
* Docling runs a layout model (the ``Heron`` family) *before* OCR. Text
  regions, tables, and figures are detected first; OCR is then run only
  on text regions. The result is more accurate words on noisy scans and
  structured spatial metadata for free (reading order, table cells).

For this Phase 1 adapter we only consume the **text + bbox** signal --
every Docling text item is split into whitespace-delimited tokens and
emitted as :class:`Word` rows. Token bboxes are distributed across the
parent rectangle proportionally to character count, so the union of any
contiguous token window reconstructs the original phrase bbox. The
table-cell / reading-order surface is left for a follow-up that extends
the matcher protocol.

Docling pulls in PyTorch + Hugging Face models and is an **optional**
dependency (``pip install flydocs[docling]``). The import is lazy
so the slim image without the extra still boots; calling
:meth:`recognise` without the package installed raises a clear
``RuntimeError`` rather than crashing the worker process.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any

from pyfly.container import service

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound
from flydocs.core.services.bbox.word_extractor import PageWords, Word
from flydocs.interfaces.dtos.bbox import BboxSource

logger = logging.getLogger(__name__)


_EXT_BY_MIME: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
}


@service
class DoclingOcrEngine:
    """``OcrEngine`` backed by Docling's layout-aware parsing pipeline."""

    def __init__(self, settings: IDPSettings) -> None:
        self._default_lang = settings.bbox_refine_tesseract_lang
        # Docling's converter is heavy to construct (downloads + loads
        # the Heron layout model and the configured OCR backend). Lazy-
        # init on first call so DI graph construction stays cheap and
        # the slim image without the optional dep keeps booting.
        self._converter: Any = None

    def supports(self, media_type: str) -> bool:
        return media_type in _EXT_BY_MIME

    def recognise(
        self,
        data: bytes,
        *,
        media_type: str,
        page_count: int,
        language_hint: str | None = None,
    ) -> list[PageWords]:
        if not data:
            return []

        started = time.monotonic()
        converter = self._get_converter()
        document_stream_cls = _load_document_stream()

        ext = _EXT_BY_MIME.get(media_type, ".pdf")
        source = document_stream_cls(name=f"flydocs-input{ext}", stream=io.BytesIO(data))

        try:
            result = converter.convert(source)
        except Exception as exc:  # noqa: BLE001 -- surface as RuntimeError, never crash the worker
            logger.warning("docling: convert() raised %s", exc)
            log_outbound(
                "docling",
                op=_op_for(media_type),
                status="error",
                latency_ms=(time.monotonic() - started) * 1000,
                error=type(exc).__name__,
            )
            return []

        doc = getattr(result, "document", None)
        if doc is None:
            return []

        # Build page-keyed (width, height) up front so we can normalise
        # every prov bbox without re-looking up the page.
        page_sizes: dict[int, tuple[float, float]] = {}
        for page_no, page in getattr(doc, "pages", {}).items():
            size = getattr(page, "size", None)
            if size is None:
                continue
            width = float(getattr(size, "width", 0.0)) or 1.0
            height = float(getattr(size, "height", 0.0)) or 1.0
            page_sizes[int(page_no)] = (width, height)

        if not page_sizes:
            return []

        pages_words: dict[int, list[Word]] = {p: [] for p in page_sizes}
        # Per-page monotonic counter; the value matcher uses it as a
        # tie-break signal when two equally-scored windows compete.
        reading_order: dict[int, int] = {p: 0 for p in page_sizes}
        for item, _level in doc.iterate_items():
            if _looks_like_table(item):
                self._emit_table_words(
                    item,
                    page_sizes=page_sizes,
                    pages_words=pages_words,
                    reading_order=reading_order,
                )
                continue
            text = getattr(item, "text", None)
            if not text:
                continue
            for prov in getattr(item, "prov", None) or []:
                page_no = int(getattr(prov, "page_no", 0) or 0)
                if page_no not in page_sizes:
                    continue
                bbox = getattr(prov, "bbox", None)
                if bbox is None:
                    continue
                page_w, page_h = page_sizes[page_no]
                tl = _to_top_left(bbox, page_h)
                xmin = _clamp01(tl[0] / page_w)
                ymin = _clamp01(tl[1] / page_h)
                xmax = _clamp01(tl[2] / page_w)
                ymax = _clamp01(tl[3] / page_h)
                if xmin >= xmax or ymin >= ymax:
                    continue
                pages_words[page_no].extend(
                    _tokenize_phrase(
                        str(text),
                        page=page_no,
                        xmin=xmin,
                        ymin=ymin,
                        xmax=xmax,
                        ymax=ymax,
                        reading_order=reading_order[page_no],
                    )
                )
                reading_order[page_no] += 1

        out: list[PageWords] = []
        for page_no in sorted(page_sizes):
            page_w, page_h = page_sizes[page_no]
            words = pages_words.get(page_no, [])
            out.append(
                PageWords(
                    page=page_no,
                    width=page_w,
                    height=page_h,
                    words=words,
                    has_text_layer=bool(words),
                    source=BboxSource.OCR,
                )
            )

        log_outbound(
            "docling",
            op=_op_for(media_type),
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            pages=len(out),
            lang=language_hint or self._default_lang,
        )
        return out

    # ------------------------------------------------------------------

    @staticmethod
    def _emit_table_words(
        table_item: Any,
        *,
        page_sizes: dict[int, tuple[float, float]],
        pages_words: dict[int, list[Word]],
        reading_order: dict[int, int],
    ) -> None:
        """Walk a Docling ``TableItem``'s grid and emit per-cell words.

        Cells without ``.bbox`` fall back to the parent table's bbox so
        the matcher still has *something* to anchor against. Page is
        resolved per cell when available, otherwise inherited from the
        first table-level provenance entry.
        """
        table_id = str(getattr(table_item, "self_ref", None) or f"table_{id(table_item)}")
        # Fallback page + bbox come from the table's own provenance.
        table_prov_list = getattr(table_item, "prov", None) or []
        fallback_page: int | None = None
        fallback_bbox: object | None = None
        for prov in table_prov_list:
            p = int(getattr(prov, "page_no", 0) or 0)
            if p in page_sizes:
                fallback_page = p
                fallback_bbox = getattr(prov, "bbox", None)
                break
        data = getattr(table_item, "data", None)
        cells = _extract_table_cells(data)
        for cell in cells:
            text = getattr(cell, "text", None)
            if not text or not str(text).strip():
                continue
            cell_prov_list = getattr(cell, "prov", None) or []
            cell_prov = cell_prov_list[0] if cell_prov_list else None
            page_no = int(getattr(cell_prov, "page_no", 0) or 0) if cell_prov else 0
            if page_no not in page_sizes and fallback_page is not None:
                page_no = fallback_page
            if page_no not in page_sizes:
                continue
            bbox = (
                getattr(cell, "bbox", None)
                or (getattr(cell_prov, "bbox", None) if cell_prov else None)
                or fallback_bbox
            )
            if bbox is None:
                continue
            page_w, page_h = page_sizes[page_no]
            tl = _to_top_left(bbox, page_h)
            xmin = _clamp01(tl[0] / page_w)
            ymin = _clamp01(tl[1] / page_h)
            xmax = _clamp01(tl[2] / page_w)
            ymax = _clamp01(tl[3] / page_h)
            if xmin >= xmax or ymin >= ymax:
                continue
            row_idx = _maybe_int(getattr(cell, "start_row_offset_idx", None))
            col_idx = _maybe_int(getattr(cell, "start_col_offset_idx", None))
            pages_words[page_no].extend(
                _tokenize_phrase(
                    str(text),
                    page=page_no,
                    xmin=xmin,
                    ymin=ymin,
                    xmax=xmax,
                    ymax=ymax,
                    reading_order=reading_order[page_no],
                    table_id=table_id,
                    row_idx=row_idx,
                    col_idx=col_idx,
                )
            )
            reading_order[page_no] += 1

    def _get_converter(self) -> Any:
        if self._converter is not None:
            return self._converter
        try:
            from docling.document_converter import (  # pyright: ignore[reportMissingImports]
                DocumentConverter,
            )
        except ImportError as exc:  # pragma: no cover -- exercised by the missing-dep test
            raise RuntimeError(
                "docling is not installed; install the optional extra "
                "(``pip install flydocs[docling]``) to enable "
                "FLYDOCS_BBOX_REFINE_OCR_ENGINE=docling"
            ) from exc
        self._converter = DocumentConverter()
        return self._converter


def _load_document_stream() -> type:
    try:
        from docling.datamodel.base_models import (  # pyright: ignore[reportMissingImports]
            DocumentStream,
        )
    except ImportError as exc:  # pragma: no cover -- exercised by the missing-dep test
        raise RuntimeError(
            "docling is not installed; install the optional extra "
            "(``pip install flydocs[docling]``) to enable "
            "FLYDOCS_BBOX_REFINE_OCR_ENGINE=docling"
        ) from exc
    return DocumentStream


def _to_top_left(bbox: object, page_height: float) -> tuple[float, float, float, float]:
    """Pull (left, top, right, bottom) out of a Docling BoundingBox in TOPLEFT origin.

    Docling exposes ``BoundingBox.to_top_left_origin(page_height=...)``
    on its native type; for adapters or future API drift we fall back
    to reading ``coord_origin`` and flipping y manually when needed.
    """
    to_tl = getattr(bbox, "to_top_left_origin", None)
    if callable(to_tl):
        converted = to_tl(page_height=page_height)
        left = float(getattr(converted, "l", 0.0))
        top = float(getattr(converted, "t", 0.0))
        right = float(getattr(converted, "r", 0.0))
        bottom = float(getattr(converted, "b", 0.0))
        return left, top, right, bottom
    # Defensive path -- read coordinates and flip y if the origin is
    # bottom-left (the PDF native convention).
    left = float(getattr(bbox, "l", 0.0))
    top = float(getattr(bbox, "t", 0.0))
    right = float(getattr(bbox, "r", 0.0))
    bottom = float(getattr(bbox, "b", 0.0))
    origin = getattr(bbox, "coord_origin", None)
    if origin is not None and str(origin).upper().endswith("BOTTOMLEFT"):
        # In BOTTOMLEFT, larger y = higher up the page. ``top`` is the
        # top edge measured from the bottom, so it's the bigger number.
        top, bottom = page_height - top, page_height - bottom
    return left, top, right, bottom


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _tokenize_phrase(
    text: str,
    *,
    page: int,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    reading_order: int | None = None,
    table_id: str | None = None,
    row_idx: int | None = None,
    col_idx: int | None = None,
) -> list[Word]:
    """Distribute a phrase bbox across whitespace-delimited tokens.

    Per-token x-range is proportional to the token's character count,
    so the union of all token bboxes reconstructs the parent rectangle
    -- the matcher relies on that union-bbox property when scoring
    multi-word windows. Optional structural metadata (reading order,
    table position) is copied onto every emitted token.
    """
    tokens = text.split()
    if not tokens:
        return []
    total_chars = sum(len(t) for t in tokens)
    if total_chars <= 0:
        return []
    width = xmax - xmin
    cursor = xmin
    out: list[Word] = []
    last_index = len(tokens) - 1
    for idx, token in enumerate(tokens):
        # Snap the last token to the right edge to avoid floating-point
        # drift across many tokens; everything else is proportional.
        tok_xmax = xmax if idx == last_index else min(xmax, cursor + width * len(token) / total_chars)
        tok_xmin = cursor
        cursor = tok_xmax
        if tok_xmin >= tok_xmax:
            continue
        out.append(
            Word(
                text=token,
                page=page,
                xmin=tok_xmin,
                ymin=ymin,
                xmax=tok_xmax,
                ymax=ymax,
                reading_order=reading_order,
                table_id=table_id,
                row_idx=row_idx,
                col_idx=col_idx,
            )
        )
    return out


def _looks_like_table(item: object) -> bool:
    """Heuristic: an item is a Docling TableItem if it has both ``data``
    and an extractable grid / table_cells attribute on that data.

    We avoid an ``isinstance`` check so the engine stays compatible
    across Docling 2.x point releases that reshuffle the class tree.
    """
    data = getattr(item, "data", None)
    if data is None:
        return False
    return getattr(data, "grid", None) is not None or getattr(data, "table_cells", None) is not None


def _extract_table_cells(data: object | None) -> list[object]:
    """Flatten a Docling ``TableData`` into a list of cells.

    Two shapes are supported:

    * ``data.grid: list[list[TableCell]]`` -- the canonical structure
      in Docling 2.93+. Cells with empty text are kept (callers filter
      them out so spanning placeholder cells don't pollute the stream).
    * ``data.table_cells: list[TableCell]`` -- older / alternative
      surface; pass through unchanged.
    """
    if data is None:
        return []
    grid = getattr(data, "grid", None)
    if grid is not None:
        cells: list[object] = []
        for row in grid:
            for cell in row or []:
                if cell is None:
                    continue
                cells.append(cell)
        return cells
    flat = getattr(data, "table_cells", None)
    if flat:
        return list(flat)
    return []


def _maybe_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _op_for(media_type: str) -> str:
    if media_type == "application/pdf":
        return "ocr.pdf"
    return "ocr.image"
