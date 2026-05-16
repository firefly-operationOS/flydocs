# Copyright 2026 Firefly Software Solutions Inc
"""End-to-end smoke tests against a *real* Docling install.

These tests load the actual Heron layout model + RapidOCR backend the
first time they run, then cache them under ``~/.cache/docling`` and
``rapidocr``. Subsequent runs are seconds; the first run on a clean
machine takes 30-60 s while the model weights download.

Gated by ``pytest.importorskip("docling")`` so the slim CI image
(which doesn't pull the ``docling`` extra) silently skips them. To
run locally::

    uv sync --extra docling --extra dev
    uv run pytest tests/integration/test_docling_real.py -v

The test PDFs are synthesized inline with reportlab -- the project
fixtures stay small and we never check binary blobs into the repo.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("docling")  # noqa: E402

from flydesk_idp.config import IDPSettings  # noqa: E402
from flydesk_idp.core.services.bbox.docling_engine import DoclingOcrEngine  # noqa: E402
from flydesk_idp.core.services.bbox.value_matcher import ValueMatcher  # noqa: E402
from flydesk_idp.core.services.extraction.text_anchor import DoclingTextAnchor  # noqa: E402
from flydesk_idp.interfaces.dtos.bbox import BboxSource  # noqa: E402


def _synth_pdf(lines: list[str]) -> bytes:
    """Build a tiny single-page PDF containing the given lines.

    Reportlab is already a dev dep; using it keeps fixtures inline so
    each test is self-describing and there are no binary blobs to
    maintain.
    """
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    y = 750
    for line in lines:
        c.drawString(100, y, line)
        y -= 30
    c.showPage()
    c.save()
    return buf.getvalue()


def _synth_multipage_pdf(pages: list[list[str]]) -> bytes:
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    for lines in pages:
        y = 750
        for line in lines:
            c.drawString(100, y, line)
            y -= 30
        c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------
# Engine smoke tests
# ---------------------------------------------------------------------


@pytest.mark.timeout(180)
def test_real_docling_extracts_words_from_synth_pdf() -> None:
    pdf_bytes = _synth_pdf(["Andres Contreras Guillen", "Joaquin Sevilla Rodriguez"])
    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(pdf_bytes, media_type="application/pdf", page_count=1)
    assert len(pages) == 1
    page = pages[0]
    assert page.source == BboxSource.OCR
    assert page.has_text_layer is True

    # Every emitted word lives in normalised top-left [0, 1] space.
    for w in page.words:
        assert 0.0 <= w.xmin < w.xmax <= 1.0, f"x out of range for {w.text!r}: {w}"
        assert 0.0 <= w.ymin < w.ymax <= 1.0, f"y out of range for {w.text!r}: {w}"
        assert w.page == 1
        assert w.reading_order is not None, f"reading_order missing on {w.text!r}"

    # The text we drew is recoverable via the matcher -- a real
    # end-to-end check that the engine + matcher cooperate against
    # the real Docling output. We don't assert on visual layout
    # (above / below): Docling reflows reportlab-synthesized PDFs in
    # ways that depend on the layout model's clustering, so a
    # geometric assertion is brittle. The "did we find both?" check
    # is what we actually care about.
    matcher = ValueMatcher(IDPSettings(bbox_refine_threshold=0.75))
    hit_first = matcher.locate("Andres Contreras Guillen", pages=pages)
    hit_second = matcher.locate("Joaquin Sevilla Rodriguez", pages=pages)
    assert hit_first is not None, "first name not located by the matcher"
    assert hit_second is not None, "second name not located by the matcher"
    # Both matches landed somewhere with a strong score -- not the LLM
    # fallback path.
    assert hit_first.score >= 0.75
    assert hit_second.score >= 0.75


@pytest.mark.timeout(180)
def test_real_docling_per_page_words_for_multipage_pdf() -> None:
    pdf_bytes = _synth_multipage_pdf(
        [
            ["Page one heading", "Page one body"],
            ["Page two heading"],
        ]
    )
    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(pdf_bytes, media_type="application/pdf", page_count=2)
    assert [p.page for p in pages] == [1, 2]
    assert all(p.has_text_layer for p in pages), [p.has_text_layer for p in pages]
    # Reading order is per page; each page's first item starts at 0.
    first_words_per_page = [next(iter(p.words), None) for p in pages]
    for fw in first_words_per_page:
        assert fw is not None and fw.reading_order == 0


@pytest.mark.timeout(180)
def test_real_docling_reading_order_strictly_increases_per_page() -> None:
    """Each subsequent text item bumps the per-page counter so the
    matcher's tie-break has a meaningful signal to work with.
    """
    pdf_bytes = _synth_pdf(["Heading", "Body line one", "Body line two", "Body line three"])
    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(pdf_bytes, media_type="application/pdf", page_count=1)
    orders = sorted({w.reading_order for w in pages[0].words if w.reading_order is not None})
    assert orders == list(range(len(orders))), orders


# ---------------------------------------------------------------------
# Text anchor smoke tests
# ---------------------------------------------------------------------


@pytest.mark.timeout(180)
def test_real_docling_text_anchor_returns_markdown_for_pdf() -> None:
    pdf_bytes = _synth_pdf(["Document Title", "Some body content here"])
    anchor = DoclingTextAnchor(IDPSettings())
    out = anchor.produce(pdf_bytes, media_type="application/pdf")
    assert out is not None and out.strip()
    # The body text should make it through Docling's OCR + markdown
    # export. We compare on a casefold-substring rather than equality
    # because Docling occasionally re-flows / classifies content.
    assert "Some body content here".casefold() in out.casefold()


@pytest.mark.timeout(180)
def test_real_docling_text_anchor_respects_max_chars_ceiling() -> None:
    long_pdf = _synth_pdf([f"Line {i}: some long-ish text payload that fills space" for i in range(40)])
    anchor = DoclingTextAnchor(IDPSettings(extraction_text_anchor_max_chars=300))
    out = anchor.produce(long_pdf, media_type="application/pdf")
    assert out is not None
    # 300 chars + a short truncation sentinel (~30 chars).
    assert len(out) < 360, f"anchor not truncated; len={len(out)}"
    assert "[anchor truncated]" in out


# ---------------------------------------------------------------------
# Idempotency: re-running the engine on identical bytes is stable
# ---------------------------------------------------------------------


@pytest.mark.timeout(240)
def test_real_docling_engine_is_deterministic_across_runs() -> None:
    """Same PDF in -> same word texts out. Docling's layout model is
    deterministic on CPU; the test catches regressions that introduce
    non-determinism (e.g. swapping in a stochastic OCR backend).
    """
    pdf_bytes = _synth_pdf(["Determinism check"])
    eng = DoclingOcrEngine(IDPSettings())
    first = eng.recognise(pdf_bytes, media_type="application/pdf", page_count=1)
    second = eng.recognise(pdf_bytes, media_type="application/pdf", page_count=1)
    assert [w.text for w in first[0].words] == [w.text for w in second[0].words]
