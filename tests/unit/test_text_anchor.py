# Copyright 2026 Firefly Software Solutions Inc
"""``TextAnchor`` -- protocol + Docling adapter behaviour with docling mocked.

Same mocking strategy as ``test_docling_engine.py``: the entire
``docling`` namespace is replaced with a fake module tree so the slim
runtime image without the optional dep can still run these tests.
"""

from __future__ import annotations

import io
import sys
import types
from dataclasses import dataclass

import pytest

from flydocs.config import IDPSettings

# ---------------------------------------------------------------------
# Fake docling -- only the ``export_to_markdown()`` surface is needed.
# ---------------------------------------------------------------------


@dataclass
class _FakeDocument:
    markdown: str

    def export_to_markdown(self) -> str:
        return self.markdown


@dataclass
class _FakeResult:
    document: _FakeDocument


class _FakeConverter:
    next_markdown: str | None = None
    raise_on_convert: Exception | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def convert(self, source) -> _FakeResult:  # noqa: ANN001
        if _FakeConverter.raise_on_convert is not None:
            raise _FakeConverter.raise_on_convert
        markdown = _FakeConverter.next_markdown or ""
        return _FakeResult(document=_FakeDocument(markdown=markdown))


@dataclass
class _FakeDocumentStream:
    name: str
    stream: io.BytesIO


def _install_fake_docling(monkeypatch: pytest.MonkeyPatch) -> None:
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
    # Each test sets its own markdown payload.
    _FakeConverter.next_markdown = None
    _FakeConverter.raise_on_convert = None


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_noop_anchor_always_returns_none() -> None:
    from flydocs.core.services.extraction.text_anchor import NoOpTextAnchor

    anchor = NoOpTextAnchor()
    assert anchor.produce(b"%PDF-1.4", media_type="application/pdf") is None
    assert anchor.produce(b"", media_type="image/png") is None


def test_docling_anchor_returns_markdown_for_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.extraction.text_anchor import DoclingTextAnchor

    _FakeConverter.next_markdown = "# Heading\n\nBody paragraph."

    anchor = DoclingTextAnchor(IDPSettings())
    out = anchor.produce(b"%PDF-1.4 fake", media_type="application/pdf")
    assert out is not None
    assert "Heading" in out
    assert "Body paragraph" in out


def test_docling_anchor_returns_none_for_unsupported_media(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.extraction.text_anchor import DoclingTextAnchor

    anchor = DoclingTextAnchor(IDPSettings())
    assert anchor.produce(b"hi", media_type="text/plain") is None
    assert anchor.produce(b"", media_type="application/pdf") is None


def test_docling_anchor_truncates_on_paragraph_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.extraction.text_anchor import DoclingTextAnchor

    big_markdown = "Para1.\n\n" + ("Para2 content. " * 200) + "\n\nPara3."
    _FakeConverter.next_markdown = big_markdown

    anchor = DoclingTextAnchor(IDPSettings(extraction_text_anchor_max_chars=200))
    out = anchor.produce(b"%PDF-1.4", media_type="application/pdf")
    assert out is not None
    assert len(out) <= 260  # 200 plus the sentinel tail
    assert "[anchor truncated]" in out


def test_docling_anchor_returns_none_when_max_chars_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.extraction.text_anchor import DoclingTextAnchor

    _FakeConverter.next_markdown = "something"
    anchor = DoclingTextAnchor(IDPSettings(extraction_text_anchor_max_chars=0))
    assert anchor.produce(b"%PDF-1.4", media_type="application/pdf") is None


def test_docling_anchor_swallows_convert_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flaky Docling call must NEVER block extraction -- the anchor
    is a best-effort enrichment, so we degrade gracefully on error.
    """
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.extraction.text_anchor import DoclingTextAnchor

    _FakeConverter.raise_on_convert = RuntimeError("model load failed")
    anchor = DoclingTextAnchor(IDPSettings())
    assert anchor.produce(b"%PDF-1.4", media_type="application/pdf") is None


def test_docling_anchor_returns_none_when_markdown_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_docling(monkeypatch)
    from flydocs.core.services.extraction.text_anchor import DoclingTextAnchor

    _FakeConverter.next_markdown = "    \n\n   "
    anchor = DoclingTextAnchor(IDPSettings())
    assert anchor.produce(b"%PDF-1.4", media_type="application/pdf") is None


def test_missing_docling_dep_raises_runtime_error_on_first_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The optional dependency is missing -- surface a clear error.

    Forces the import to fail through a :class:`MetaPathFinder` so the
    test holds whether or not the ``docling`` extra is installed in
    the active venv.
    """
    from flydocs.core.services.extraction.text_anchor import DoclingTextAnchor

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

    anchor = DoclingTextAnchor(IDPSettings())
    with pytest.raises(RuntimeError, match="docling"):
        anchor.produce(b"%PDF-1.4", media_type="application/pdf")


# ---------------------------------------------------------------------
# DI wiring
# ---------------------------------------------------------------------


def test_di_text_anchor_defaults_to_noop() -> None:
    from flydocs.core.configuration import IDPCoreConfiguration
    from flydocs.core.services.extraction.text_anchor import NoOpTextAnchor

    cfg = IDPCoreConfiguration()
    anchor = cfg.text_anchor(settings=IDPSettings())
    assert isinstance(anchor, NoOpTextAnchor)


def test_di_text_anchor_dispatches_docling() -> None:
    from flydocs.core.configuration import IDPCoreConfiguration
    from flydocs.core.services.extraction.text_anchor import DoclingTextAnchor

    cfg = IDPCoreConfiguration()
    anchor = cfg.text_anchor(settings=IDPSettings(extraction_text_anchor="docling"))
    assert isinstance(anchor, DoclingTextAnchor)


def test_di_text_anchor_unknown_raises_value_error() -> None:
    from flydocs.core.configuration import IDPCoreConfiguration

    cfg = IDPCoreConfiguration()
    with pytest.raises(ValueError, match="paddle"):
        cfg.text_anchor(settings=IDPSettings(extraction_text_anchor="paddle"))


# ---------------------------------------------------------------------
# Extractor integration
# ---------------------------------------------------------------------


class _RecordingAnchor:
    """Test double that captures calls + returns a canned anchor."""

    def __init__(self, payload: str | None) -> None:
        self.payload = payload
        self.calls: list[tuple[bytes, str]] = []

    def produce(self, data: bytes, *, media_type: str, max_chars: int | None = None) -> str | None:
        self.calls.append((data, media_type))
        return self.payload


def test_extractor_inserts_anchor_into_user_content_when_present() -> None:
    """Pure-Python unit test on ``_build_user_content`` -- avoids
    spinning up the full FireflyAgent.
    """
    from flydocs.core.services.extraction.extractor import MultimodalExtractor

    rec = _RecordingAnchor("# Title\n\nBody.")
    extractor = MultimodalExtractor.__new__(MultimodalExtractor)
    extractor._text_anchor = rec  # type: ignore[attr-defined]
    content = extractor._build_user_content(
        user_text="User prompt",
        document_bytes=b"%PDF-1.4 fake",
        media_type="application/pdf",
    )
    assert content[0] == "User prompt"
    assert "Title" in content[1]
    assert "Body." in content[1]
    assert "Docling pre-extraction" in content[1]
    # Last entry is the BinaryContent block.
    last = content[-1]
    assert getattr(last, "data", None) == b"%PDF-1.4 fake"
    assert getattr(last, "media_type", None) == "application/pdf"
    # Anchor service was called exactly once with the right inputs.
    assert rec.calls == [(b"%PDF-1.4 fake", "application/pdf")]


def test_extractor_skips_anchor_when_service_returns_none() -> None:
    from flydocs.core.services.extraction.extractor import MultimodalExtractor

    rec = _RecordingAnchor(None)
    extractor = MultimodalExtractor.__new__(MultimodalExtractor)
    extractor._text_anchor = rec  # type: ignore[attr-defined]
    content = extractor._build_user_content(
        user_text="User prompt",
        document_bytes=b"%PDF-1.4 fake",
        media_type="application/pdf",
    )
    # Without an anchor the layout is [user_text, BinaryContent] -- no
    # extra block inserted.
    assert len(content) == 2
    assert content[0] == "User prompt"
    assert getattr(content[1], "data", None) == b"%PDF-1.4 fake"


def test_extractor_swallows_anchor_errors() -> None:
    """A raising anchor must not crash extraction -- we log and skip."""
    from flydocs.core.services.extraction.extractor import MultimodalExtractor

    class _BrokenAnchor:
        def produce(self, data: bytes, *, media_type: str, max_chars: int | None = None) -> str | None:
            raise RuntimeError("boom")

    extractor = MultimodalExtractor.__new__(MultimodalExtractor)
    extractor._text_anchor = _BrokenAnchor()  # type: ignore[attr-defined]
    content = extractor._build_user_content(
        user_text="User prompt",
        document_bytes=b"%PDF-1.4 fake",
        media_type="application/pdf",
    )
    assert len(content) == 2  # No anchor inserted, no crash.
