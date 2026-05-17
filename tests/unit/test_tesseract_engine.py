# Copyright 2026 Firefly Software Solutions Inc
"""``TesseractOcrEngine`` -- adapter behaviour with the tesseract binary mocked.

Tests run without a local ``tesseract`` install by monkey-patching
``pytesseract.image_to_data`` to return a deterministic word grid. The
runtime Dockerfile installs tesseract + lang packs; an integration
test would exercise the real binary.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from PIL import Image

from flydocs.config import IDPSettings
from flydocs.core.services.bbox.tesseract_engine import TesseractOcrEngine


def _png(size: tuple[int, int] = (400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def fake_tesseract(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, object]]]:
    """Patch pytesseract.image_to_data to return our fixture.

    Records the calls so tests can assert on the resolved ``lang`` and
    the input image size. Returns the recording list for the test to
    introspect.
    """
    import pytesseract  # pyright: ignore[reportMissingImports]

    calls: list[dict[str, object]] = []

    def _fake(image, lang, output_type):  # type: ignore[no-untyped-def]
        calls.append({"lang": lang, "size": image.size})
        return {
            "text": ["Hello", "Mundo", "", "Línea2"],
            "conf": [97.2, 95.5, -1, 88.0],
            "left": [10, 80, 0, 10],
            "top": [20, 20, 0, 60],
            "width": [60, 70, 0, 80],
            "height": [22, 22, 0, 22],
        }

    monkeypatch.setattr(pytesseract, "image_to_data", _fake)
    yield calls


def test_supports_pdf_and_common_image_types() -> None:
    eng = TesseractOcrEngine(IDPSettings())
    assert eng.supports("application/pdf")
    assert eng.supports("image/png")
    assert eng.supports("image/jpeg")
    assert eng.supports("image/gif")
    assert eng.supports("image/webp")
    assert not eng.supports("image/heic")


def test_recognise_image_yields_normalised_word_stream(fake_tesseract: list[dict[str, object]]) -> None:
    eng = TesseractOcrEngine(IDPSettings(bbox_refine_tesseract_lang="spa+eng"))
    pages = eng.recognise(_png((400, 300)), media_type="image/png", page_count=1)
    assert len(pages) == 1
    page = pages[0]
    assert page.page == 1
    assert page.has_text_layer is True
    # Filtered out: the conf=-1 row + the empty-text row.
    texts = [w.text for w in page.words]
    assert texts == ["Hello", "Mundo", "Línea2"]
    # Coordinates normalised to [0, 1] image space.
    for w in page.words:
        assert 0.0 <= w.xmin < w.xmax <= 1.0
        assert 0.0 <= w.ymin < w.ymax <= 1.0


def test_language_hint_maps_iso1_to_tesseract_code(fake_tesseract: list[dict[str, object]]) -> None:
    eng = TesseractOcrEngine(IDPSettings())
    eng.recognise(_png(), media_type="image/png", page_count=1, language_hint="fr")
    assert fake_tesseract[0]["lang"] == "fra"


def test_language_hint_passes_through_three_letter_code(
    fake_tesseract: list[dict[str, object]],
) -> None:
    eng = TesseractOcrEngine(IDPSettings(bbox_refine_tesseract_lang="spa+eng"))
    eng.recognise(_png(), media_type="image/png", page_count=1, language_hint="cat")
    assert fake_tesseract[0]["lang"] == "cat"


def test_unknown_language_hint_falls_back_to_default(
    fake_tesseract: list[dict[str, object]],
) -> None:
    eng = TesseractOcrEngine(IDPSettings(bbox_refine_tesseract_lang="spa+eng"))
    eng.recognise(_png(), media_type="image/png", page_count=1, language_hint="xx")
    assert fake_tesseract[0]["lang"] == "spa+eng"


def test_no_language_hint_uses_default(fake_tesseract: list[dict[str, object]]) -> None:
    eng = TesseractOcrEngine(IDPSettings(bbox_refine_tesseract_lang="spa+eng"))
    eng.recognise(_png(), media_type="image/png", page_count=1, language_hint=None)
    assert fake_tesseract[0]["lang"] == "spa+eng"


def test_empty_bytes_short_circuits(fake_tesseract: list[dict[str, object]]) -> None:
    eng = TesseractOcrEngine(IDPSettings())
    assert eng.recognise(b"", media_type="image/png", page_count=1) == []
    assert fake_tesseract == []


def test_recognise_pdf_rasterises_then_ocrs_each_page(
    fake_tesseract: list[dict[str, object]],
) -> None:
    # Build a 2-page PDF via reportlab so PyMuPDF can rasterise it.
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.drawString(100, 750, "page1")
    c.showPage()
    c.drawString(100, 750, "page2")
    c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()

    eng = TesseractOcrEngine(IDPSettings(bbox_refine_ocr_dpi=100))
    pages = eng.recognise(pdf_bytes, media_type="application/pdf", page_count=2)
    assert [p.page for p in pages] == [1, 2]
    assert len(fake_tesseract) == 2  # one OCR call per page
