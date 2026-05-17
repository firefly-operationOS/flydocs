# Copyright 2026 Firefly Software Solutions Inc
"""``DoclingOcrEngine`` -- adapter behaviour with the docling library mocked.

Docling pulls in PyTorch + Hugging Face models and is an *optional*
dependency. The tests here fake the entire ``docling`` API surface so
they run on the slim image without paying that cost; an integration
test (gated by the ``docling`` extra) would exercise the real models.
"""

from __future__ import annotations

import io
import sys
import types
from dataclasses import dataclass

import pytest
from PIL import Image

from flydocs.config import IDPSettings
from flydocs.interfaces.dtos.bbox import BboxSource

# ---------------------------------------------------------------------
# Fake docling -- minimal stand-in for the parts DoclingOcrEngine uses.
# ---------------------------------------------------------------------


@dataclass
class _FakeBbox:
    """Mirror Docling's BoundingBox public attribute names (l/t/r/b)
    via properties so the engine's ``getattr(bbox, 'l', ...)`` lookups
    resolve, while the dataclass fields themselves use unambiguous names.
    """

    left: float
    top: float
    right: float
    bottom: float

    @property
    def l(self) -> float:  # noqa: E743 - Docling's wire name
        return self.left

    @property
    def t(self) -> float:
        return self.top

    @property
    def r(self) -> float:
        return self.right

    @property
    def b(self) -> float:
        return self.bottom

    def to_top_left_origin(self, page_height: float) -> _FakeBbox:
        # In the fake we already produce top-left coords, so identity.
        return self


@dataclass
class _FakeProv:
    page_no: int
    bbox: _FakeBbox


@dataclass
class _FakeTextItem:
    text: str
    prov: list[_FakeProv]


@dataclass
class _FakeSize:
    width: float
    height: float


@dataclass
class _FakePage:
    page_no: int
    size: _FakeSize


@dataclass
class _FakeDocument:
    pages: dict[int, _FakePage]
    items: list[_FakeTextItem]

    def iterate_items(self):
        for item in self.items:
            yield item, 0


@dataclass
class _FakeResult:
    document: _FakeDocument


class _FakeConverter:
    """Replaces ``docling.document_converter.DocumentConverter``."""

    last_call: dict[str, object] | None = None
    next_document: _FakeDocument | None = None

    def __init__(self, *args, **kwargs) -> None:
        _FakeConverter.last_call = {"args": args, "kwargs": kwargs}

    def convert(self, source) -> _FakeResult:  # noqa: ANN001 - source is a fake DocumentStream
        doc = _FakeConverter.next_document
        if doc is None:
            raise AssertionError("test forgot to set _FakeConverter.next_document")
        return _FakeResult(document=doc)


@dataclass
class _FakeDocumentStream:
    name: str
    stream: io.BytesIO


def _install_fake_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake ``docling`` package tree into ``sys.modules``."""
    fake_root = types.ModuleType("docling")
    fake_converter_mod = types.ModuleType("docling.document_converter")
    fake_converter_mod.DocumentConverter = _FakeConverter  # type: ignore[attr-defined]
    fake_datamodel_mod = types.ModuleType("docling.datamodel")
    fake_base_models_mod = types.ModuleType("docling.datamodel.base_models")
    fake_base_models_mod.DocumentStream = _FakeDocumentStream  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "docling", fake_root)
    monkeypatch.setitem(sys.modules, "docling.document_converter", fake_converter_mod)
    monkeypatch.setitem(sys.modules, "docling.datamodel", fake_datamodel_mod)
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", fake_base_models_mod)


def _png(size: tuple[int, int] = (400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_supports_pdf_and_common_image_types() -> None:
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    eng = DoclingOcrEngine(IDPSettings())
    assert eng.supports("application/pdf")
    assert eng.supports("image/png")
    assert eng.supports("image/jpeg")
    assert eng.supports("image/tiff")
    assert eng.supports("image/webp")
    assert eng.supports("image/bmp")
    assert not eng.supports("image/heic")
    assert not eng.supports("text/plain")


def test_empty_bytes_short_circuits() -> None:
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    eng = DoclingOcrEngine(IDPSettings())
    assert eng.recognise(b"", media_type="image/png", page_count=1) == []


def test_recognise_image_emits_words_in_normalised_top_left_coords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    _FakeConverter.next_document = _FakeDocument(
        pages={1: _FakePage(page_no=1, size=_FakeSize(width=400, height=300))},
        items=[
            _FakeTextItem(
                text="Hello Mundo",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=20, right=140, bottom=42))],
            ),
            _FakeTextItem(
                text="Linea2",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=60, right=90, bottom=82))],
            ),
        ],
    )

    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(_png((400, 300)), media_type="image/png", page_count=1)

    assert len(pages) == 1
    page = pages[0]
    assert page.page == 1
    assert page.source == BboxSource.OCR
    assert page.has_text_layer is True
    texts = [w.text for w in page.words]
    # Each phrase token-split into separate words.
    assert texts == ["Hello", "Mundo", "Linea2"]
    # Coordinates strictly normalised and in top-left convention.
    for w in page.words:
        assert 0.0 <= w.xmin < w.xmax <= 1.0
        assert 0.0 <= w.ymin < w.ymax <= 1.0
        assert w.page == 1


def test_recognise_distributes_phrase_bbox_across_tokens_proportionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each token's x-range is proportional to its character count.

    The whole phrase's bbox is preserved as the union of all token boxes,
    so downstream `_union_bbox` reconstructs the original rectangle.
    """
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    _FakeConverter.next_document = _FakeDocument(
        pages={1: _FakePage(page_no=1, size=_FakeSize(width=1000, height=1000))},
        items=[
            _FakeTextItem(
                text="aa bbbb",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=100, top=200, right=700, bottom=300))],
            ),
        ],
    )

    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(_png(), media_type="image/png", page_count=1)
    words = pages[0].words
    assert [w.text for w in words] == ["aa", "bbbb"]
    # Total chars=6: "aa" takes 2/6, "bbbb" takes 4/6 of x-range.
    # x-range was [100/1000, 700/1000] = [0.1, 0.7], width 0.6.
    # "aa" ends at 0.1 + 0.6 * (2/6) = 0.3; "bbbb" ends at 0.7.
    assert words[0].xmin == pytest.approx(0.1)
    assert words[0].xmax == pytest.approx(0.3)
    assert words[1].xmin == pytest.approx(0.3)
    assert words[1].xmax == pytest.approx(0.7)
    # All tokens share the same vertical span as the parent phrase.
    for w in words:
        assert w.ymin == pytest.approx(0.2)
        assert w.ymax == pytest.approx(0.3)


def test_recognise_pdf_emits_one_page_words_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    _FakeConverter.next_document = _FakeDocument(
        pages={
            1: _FakePage(page_no=1, size=_FakeSize(width=500, height=700)),
            2: _FakePage(page_no=2, size=_FakeSize(width=500, height=700)),
        },
        items=[
            _FakeTextItem(
                text="page1text",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=200, bottom=50))],
            ),
            _FakeTextItem(
                text="page2text",
                prov=[_FakeProv(page_no=2, bbox=_FakeBbox(left=10, top=10, right=200, bottom=50))],
            ),
        ],
    )

    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(b"%PDF-1.4 fake", media_type="application/pdf", page_count=2)
    assert [p.page for p in pages] == [1, 2]
    assert pages[0].words[0].text == "page1text"
    assert pages[1].words[0].text == "page2text"


def test_pages_with_no_text_get_empty_word_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    _FakeConverter.next_document = _FakeDocument(
        pages={
            1: _FakePage(page_no=1, size=_FakeSize(width=500, height=700)),
            2: _FakePage(page_no=2, size=_FakeSize(width=500, height=700)),
        },
        items=[
            _FakeTextItem(
                text="only_page_1",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=200, bottom=50))],
            ),
        ],
    )

    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(b"%PDF-1.4 fake", media_type="application/pdf", page_count=2)
    assert len(pages) == 2
    assert pages[0].has_text_layer is True
    assert pages[1].has_text_layer is False
    assert pages[1].words == []


def test_degenerate_bbox_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """xmin >= xmax (or ymin >= ymax) entries are dropped, not crashed on."""
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    _FakeConverter.next_document = _FakeDocument(
        pages={1: _FakePage(page_no=1, size=_FakeSize(width=500, height=500))},
        items=[
            _FakeTextItem(
                text="ok",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=50, bottom=50))],
            ),
            _FakeTextItem(
                # Inverted x: l > r -> degenerate, must be skipped.
                text="bad",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=100, top=10, right=50, bottom=50))],
            ),
            _FakeTextItem(
                # Zero-height: t == b -> degenerate, must be skipped.
                text="flat",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=50, bottom=10))],
            ),
        ],
    )

    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(_png(), media_type="image/png", page_count=1)
    assert [w.text for w in pages[0].words] == ["ok"]


def test_items_without_text_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Picture / Table headers / etc. without ``text`` come through silently."""
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    class _PictureLike:
        prov = [_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=50, bottom=50))]
        # No ``text`` attribute on this object.

    _FakeConverter.next_document = _FakeDocument(
        pages={1: _FakePage(page_no=1, size=_FakeSize(width=500, height=500))},
        items=[
            _FakeTextItem(
                text="hi",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=50, bottom=50))],
            ),
            _PictureLike(),  # type: ignore[list-item] - intentional duck-type mix
        ],
    )

    eng = DoclingOcrEngine(IDPSettings())
    pages = eng.recognise(_png(), media_type="image/png", page_count=1)
    assert [w.text for w in pages[0].words] == ["hi"]


def test_missing_docling_dep_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the optional ``docling`` package is not installed, we surface
    a clear ``RuntimeError`` instead of a bare ``ImportError`` somewhere
    deep in the call stack -- matches the pattern of the Tesseract engine.

    Forces the import to fail by blocking ``docling`` and its submodules
    via :class:`MetaPathFinder` regardless of whether the optional extra
    is actually installed -- the test must hold either way.
    """
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    class _Block:
        def find_spec(self, name, path=None, target=None):  # noqa: ANN001
            if name == "docling" or name.startswith("docling."):
                raise ImportError(f"blocked by test: {name}")
            return None

    blocker = _Block()
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])
    for key in list(sys.modules):
        if key == "docling" or key.startswith("docling."):
            monkeypatch.delitem(sys.modules, key, raising=False)

    eng = DoclingOcrEngine(IDPSettings())
    with pytest.raises(RuntimeError, match="docling"):
        eng.recognise(_png(), media_type="image/png", page_count=1)


def test_di_wiring_dispatches_docling_engine() -> None:
    """The IDPCoreConfiguration.ocr_engine bean returns DoclingOcrEngine
    when ``bbox_refine_ocr_engine='docling'``.
    """
    from flydocs.core.configuration import IDPCoreConfiguration
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    settings = IDPSettings(bbox_refine_ocr_engine="docling")
    cfg = IDPCoreConfiguration()
    engine = cfg.ocr_engine(settings=settings)
    assert isinstance(engine, DoclingOcrEngine)


def test_di_unknown_engine_raises_value_error() -> None:
    from flydocs.core.configuration import IDPCoreConfiguration

    settings = IDPSettings(bbox_refine_ocr_engine="paddle")
    cfg = IDPCoreConfiguration()
    with pytest.raises(ValueError, match="paddle"):
        cfg.ocr_engine(settings=settings)


# ---------------------------------------------------------------------
# Reading-order metadata
# ---------------------------------------------------------------------


def test_reading_order_increments_per_emitted_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each text item the engine processes bumps the per-page counter,
    so every emitted Word carries the visual order of its parent item.
    """
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    _FakeConverter.next_document = _FakeDocument(
        pages={1: _FakePage(page_no=1, size=_FakeSize(width=500, height=500))},
        items=[
            _FakeTextItem(
                text="first",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=80, bottom=30))],
            ),
            _FakeTextItem(
                text="second part",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=40, right=180, bottom=60))],
            ),
            _FakeTextItem(
                text="third",
                prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=70, right=80, bottom=90))],
            ),
        ],
    )

    eng = DoclingOcrEngine(IDPSettings())
    page = eng.recognise(_png(), media_type="image/png", page_count=1)[0]
    # Each *item* gets a distinct reading_order; the two tokens from
    # "second part" share their parent's reading_order (=1).
    orders = [(w.text, w.reading_order) for w in page.words]
    assert orders == [
        ("first", 0),
        ("second", 1),
        ("part", 1),
        ("third", 2),
    ]


# ---------------------------------------------------------------------
# Table cell metadata
# ---------------------------------------------------------------------


@dataclass
class _FakeTableCell:
    text: str
    bbox: _FakeBbox
    start_row_offset_idx: int
    start_col_offset_idx: int
    prov: list[_FakeProv] | None = None


@dataclass
class _FakeTableData:
    grid: list[list[_FakeTableCell | None]]


@dataclass
class _FakeTableItem:
    data: _FakeTableData
    prov: list[_FakeProv]
    self_ref: str = "#/tables/0"


def test_table_cells_carry_structural_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cells inside a Docling TableItem get tagged with table_id +
    row_idx + col_idx so downstream matchers can prefer in-table
    grounding for array-row fields.
    """
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    table = _FakeTableItem(
        prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=400, bottom=200))],
        data=_FakeTableData(
            grid=[
                [
                    _FakeTableCell(
                        text="Name",
                        bbox=_FakeBbox(left=10, top=10, right=120, bottom=40),
                        start_row_offset_idx=0,
                        start_col_offset_idx=0,
                    ),
                    _FakeTableCell(
                        text="DNI",
                        bbox=_FakeBbox(left=120, top=10, right=240, bottom=40),
                        start_row_offset_idx=0,
                        start_col_offset_idx=1,
                    ),
                ],
                [
                    _FakeTableCell(
                        text="Andres Contreras",
                        bbox=_FakeBbox(left=10, top=40, right=120, bottom=70),
                        start_row_offset_idx=1,
                        start_col_offset_idx=0,
                    ),
                    _FakeTableCell(
                        text="12345678X",
                        bbox=_FakeBbox(left=120, top=40, right=240, bottom=70),
                        start_row_offset_idx=1,
                        start_col_offset_idx=1,
                    ),
                ],
            ]
        ),
    )
    _FakeConverter.next_document = _FakeDocument(
        pages={1: _FakePage(page_no=1, size=_FakeSize(width=500, height=300))},
        items=[table],  # type: ignore[list-item]
    )

    eng = DoclingOcrEngine(IDPSettings())
    page = eng.recognise(_png(), media_type="image/png", page_count=1)[0]
    by_text = {w.text: w for w in page.words}
    # Header
    assert by_text["Name"].table_id == "#/tables/0"
    assert by_text["Name"].row_idx == 0
    assert by_text["Name"].col_idx == 0
    # Tokens from a multi-word cell share row/col indices.
    assert by_text["Andres"].row_idx == 1
    assert by_text["Andres"].col_idx == 0
    assert by_text["Contreras"].row_idx == 1
    assert by_text["Contreras"].col_idx == 0
    # Sibling cell on the same row, different column.
    assert by_text["12345678X"].row_idx == 1
    assert by_text["12345678X"].col_idx == 1


def test_table_cells_with_no_bbox_fall_back_to_table_bbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Docling emits a cell without its own bbox (common for
    spanning placeholders in some PDFs) we still get a word with the
    table-level bbox -- so matching never silently loses content.
    """
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    table_bbox = _FakeBbox(left=10, top=10, right=400, bottom=200)

    class _CellWithoutBbox:
        text = "fallback"
        bbox = None  # explicit None to exercise the fallback path
        start_row_offset_idx = 0
        start_col_offset_idx = 0
        prov: list[_FakeProv] | None = None

    table = _FakeTableItem(
        prov=[_FakeProv(page_no=1, bbox=table_bbox)],
        data=_FakeTableData(grid=[[_CellWithoutBbox()]]),  # type: ignore[list-item]
    )
    _FakeConverter.next_document = _FakeDocument(
        pages={1: _FakePage(page_no=1, size=_FakeSize(width=500, height=300))},
        items=[table],  # type: ignore[list-item]
    )

    eng = DoclingOcrEngine(IDPSettings())
    page = eng.recognise(_png(), media_type="image/png", page_count=1)[0]
    assert [w.text for w in page.words] == ["fallback"]
    assert page.words[0].table_id == "#/tables/0"


def test_table_data_with_flat_cells_attribute_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Older Docling releases expose ``table_cells`` instead of ``grid``.
    The engine handles both so a version bump never silently drops
    table support.
    """
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.bbox.docling_engine import DoclingOcrEngine

    @dataclass
    class _FlatTableData:
        table_cells: list[_FakeTableCell]
        grid: object | None = None  # explicit None so getattr resolves

    cells = [
        _FakeTableCell(
            text="alpha",
            bbox=_FakeBbox(left=10, top=10, right=80, bottom=30),
            start_row_offset_idx=0,
            start_col_offset_idx=0,
        ),
    ]
    table = _FakeTableItem(
        prov=[_FakeProv(page_no=1, bbox=_FakeBbox(left=10, top=10, right=400, bottom=200))],
        data=_FlatTableData(table_cells=cells),  # type: ignore[arg-type]
    )
    _FakeConverter.next_document = _FakeDocument(
        pages={1: _FakePage(page_no=1, size=_FakeSize(width=500, height=300))},
        items=[table],  # type: ignore[list-item]
    )

    eng = DoclingOcrEngine(IDPSettings())
    page = eng.recognise(_png(), media_type="image/png", page_count=1)[0]
    assert [w.text for w in page.words] == ["alpha"]
    assert page.words[0].table_id == "#/tables/0"
