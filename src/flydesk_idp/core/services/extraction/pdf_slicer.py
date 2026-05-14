# Copyright 2026 Firefly Software Solutions Inc
"""Slice a PDF document by 1-indexed page range, returning a new PDF blob.

Used by the orchestrator after the :class:`DocumentSplitter` has mapped
each requested ``DocSpec`` to its corresponding page range, so the
downstream extractor / authenticity / judge nodes receive only the
relevant pages.

For non-PDF documents this helper is a no-op (the bytes are returned
unchanged), because the splitter never reports a page range for them.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PageRange:
    start: int  # 1-indexed, inclusive
    end: int    # 1-indexed, inclusive


def slice_pdf(pdf_bytes: bytes, page_range: PageRange) -> bytes:
    """Return a PDF containing only ``[start..end]`` pages of the input."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes), strict=False)
    total = len(reader.pages)
    start_idx = max(0, page_range.start - 1)
    end_idx = min(total, page_range.end)
    if start_idx >= end_idx:
        raise ValueError(
            f"Empty page range after clamping: pages {page_range.start}..{page_range.end} "
            f"vs. document total of {total}"
        )

    writer = pypdf.PdfWriter()
    for idx in range(start_idx, end_idx):
        writer.add_page(reader.pages[idx])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def passthrough(data: bytes, _: PageRange | None) -> bytes:
    """Used for non-PDF documents -- returns the bytes unchanged."""
    return data
