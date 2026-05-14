# Copyright 2026 Firefly Software Solutions Inc
"""``PyMuPDFWordExtractor`` -- text-layer extraction from synthesized PDFs."""

from __future__ import annotations

import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.bbox.pymupdf_words import PyMuPDFWordExtractor


def _pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


def _multi_page_pdf(pages: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for page_lines in pages:
        y = 750
        for line in page_lines:
            c.drawString(72, y, line)
            y -= 20
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture()
def extractor() -> PyMuPDFWordExtractor:
    return PyMuPDFWordExtractor(IDPSettings(bbox_refine_min_text_words=3))


def test_extracts_words_from_simple_pdf(extractor: PyMuPDFWordExtractor) -> None:
    pdf = _pdf(["Hello World From Test"])
    pages = extractor.extract(pdf, media_type="application/pdf", page_count=1)
    assert len(pages) == 1
    words = [w.text for w in pages[0].words]
    assert "Hello" in words
    assert "World" in words
    assert "Test" in words
    assert pages[0].has_text_layer is True


def test_coordinates_are_normalised_to_unit_range(extractor: PyMuPDFWordExtractor) -> None:
    pdf = _pdf(["Sample"])
    pages = extractor.extract(pdf, media_type="application/pdf", page_count=1)
    for w in pages[0].words:
        assert 0.0 <= w.xmin < w.xmax <= 1.0
        assert 0.0 <= w.ymin < w.ymax <= 1.0


def test_multi_page_pdf_yields_one_pagewords_per_page(extractor: PyMuPDFWordExtractor) -> None:
    pdf = _multi_page_pdf([["First page text here"], ["Second page text here"]])
    pages = extractor.extract(pdf, media_type="application/pdf", page_count=2)
    assert len(pages) == 2
    assert pages[0].page == 1
    assert pages[1].page == 2


def test_low_word_page_marked_as_no_text_layer() -> None:
    # Very short PDF: only 2 words on the page. With min_words=5 this
    # should be flagged as image-only.
    extractor = PyMuPDFWordExtractor(IDPSettings(bbox_refine_min_text_words=5))
    pdf = _pdf(["Hi"])
    pages = extractor.extract(pdf, media_type="application/pdf", page_count=1)
    assert len(pages) == 1
    assert pages[0].has_text_layer is False


def test_empty_bytes_returns_empty(extractor: PyMuPDFWordExtractor) -> None:
    assert extractor.extract(b"", media_type="application/pdf", page_count=0) == []


def test_non_pdf_returns_empty(extractor: PyMuPDFWordExtractor) -> None:
    assert extractor.extract(b"\x89PNG\r\n\x1a\nstub", media_type="image/png", page_count=1) == []


def test_page_count_cap_is_enforced() -> None:
    extractor = PyMuPDFWordExtractor(IDPSettings(bbox_refine_min_text_words=3, bbox_refine_max_text_pages=1))
    pdf = _multi_page_pdf([["Page one text"], ["Page two text"]])
    pages = extractor.extract(pdf, media_type="application/pdf", page_count=2)
    assert len(pages) == 1
