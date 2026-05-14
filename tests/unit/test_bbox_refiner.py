# Copyright 2026 Firefly Software Solutions Inc
"""``BboxRefiner`` -- end-to-end mutation of ExtractedField bboxes."""

from __future__ import annotations

import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.bbox.bbox_refiner import BboxRefiner
from flydesk_idp.core.services.bbox.ocr_engine import NoneOcrEngine
from flydesk_idp.core.services.bbox.pymupdf_words import PyMuPDFWordExtractor
from flydesk_idp.core.services.bbox.value_matcher import ValueMatcher
from flydesk_idp.core.services.bbox.word_router import WordRouter
from flydesk_idp.interfaces.dtos.bbox import BboxSource, BoundingBox
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup


def _make_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


def _llm_bbox() -> BoundingBox:
    return BoundingBox(xmin=0.05, ymin=0.05, xmax=0.95, ymax=0.95)


@pytest.fixture()
def refiner() -> BboxRefiner:
    settings = IDPSettings(bbox_refine_threshold=0.85, bbox_refine_min_text_words=3)
    return BboxRefiner(
        router=WordRouter(
            pymupdf=PyMuPDFWordExtractor(settings),
            ocr=NoneOcrEngine(),
        ),
        matcher=ValueMatcher(settings),
    )


@pytest.mark.asyncio
async def test_grounds_simple_field_against_pdf_text_layer(refiner: BboxRefiner) -> None:
    pdf = _make_pdf(["Customer Name: Acme Corporation Madrid"])
    field = ExtractedField(
        fieldName="customer_name",
        fieldValueFound="Acme Corporation",
        pagesFound=[1],
        bbox=_llm_bbox(),
    )
    group = ExtractedFieldGroup(fieldGroupName="customer", fieldGroupFields=[field])
    counters = await refiner.refine(
        document_bytes=pdf,
        media_type="application/pdf",
        page_count=1,
        groups=[group],
    )
    assert counters.fields_seen == 1
    assert counters.grounded_pdf_text == 1
    assert field.bbox.source == BboxSource.PDF_TEXT
    assert field.bbox.refinement_confidence is not None
    assert field.bbox.refinement_confidence >= 0.85
    # The new bbox should be tighter than the original full-page LLM box.
    assert (field.bbox.xmax - field.bbox.xmin) < 0.6


@pytest.mark.asyncio
async def test_keeps_llm_bbox_for_unfindable_value(refiner: BboxRefiner) -> None:
    pdf = _make_pdf(["totally different text"])
    field = ExtractedField(
        fieldName="customer_name",
        fieldValueFound="Banco Santander S.A.",
        pagesFound=[1],
        bbox=_llm_bbox(),
    )
    group = ExtractedFieldGroup(fieldGroupName="customer", fieldGroupFields=[field])
    counters = await refiner.refine(
        document_bytes=pdf,
        media_type="application/pdf",
        page_count=1,
        groups=[group],
    )
    assert counters.kept_llm == 1
    assert counters.grounded_pdf_text == 0
    assert field.bbox.source == BboxSource.LLM
    assert field.bbox.refinement_confidence is None
    # Original LLM coordinates are preserved.
    assert field.bbox.xmin == 0.05
    assert field.bbox.ymin == 0.05


@pytest.mark.asyncio
async def test_skips_empty_field_value(refiner: BboxRefiner) -> None:
    pdf = _make_pdf(["any content"])
    field = ExtractedField(
        fieldName="missing_field",
        fieldValueFound=None,
        pagesFound=[],
        bbox=BoundingBox.empty(),
    )
    group = ExtractedFieldGroup(fieldGroupName="g", fieldGroupFields=[field])
    counters = await refiner.refine(
        document_bytes=pdf,
        media_type="application/pdf",
        page_count=1,
        groups=[group],
    )
    assert counters.fields_seen == 1
    assert counters.kept_llm == 0
    assert counters.grounded_pdf_text == 0
    # Empty placeholder keeps source=NONE
    assert field.bbox.source == BboxSource.NONE


@pytest.mark.asyncio
async def test_recurses_into_array_field_rows(refiner: BboxRefiner) -> None:
    pdf = _make_pdf(["Items list", "Apple 100", "Banana 200"])
    apple_qty = ExtractedField(fieldName="qty", fieldValueFound=100, pagesFound=[1], bbox=_llm_bbox())
    apple_name = ExtractedField(fieldName="name", fieldValueFound="Apple", pagesFound=[1], bbox=_llm_bbox())
    apple_row = ExtractedField(
        fieldName="row", fieldValueFound=[apple_name, apple_qty], pagesFound=[1], bbox=_llm_bbox()
    )
    items = ExtractedField(fieldName="items", fieldValueFound=[apple_row], pagesFound=[1], bbox=_llm_bbox())
    group = ExtractedFieldGroup(fieldGroupName="invoice", fieldGroupFields=[items])
    counters = await refiner.refine(
        document_bytes=pdf,
        media_type="application/pdf",
        page_count=1,
        groups=[group],
    )
    # Two leaf fields seen (name + qty); the array parent + row are not.
    assert counters.fields_seen == 2
    # Both leaves should ground.
    assert counters.grounded_pdf_text >= 1
    assert apple_name.bbox.source == BboxSource.PDF_TEXT


@pytest.mark.asyncio
async def test_returns_zero_counters_for_empty_groups(refiner: BboxRefiner) -> None:
    counters = await refiner.refine(
        document_bytes=b"%PDF-",
        media_type="application/pdf",
        page_count=1,
        groups=[],
    )
    assert counters.fields_seen == 0
