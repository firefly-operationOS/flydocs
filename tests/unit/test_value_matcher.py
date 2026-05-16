# Copyright 2026 Firefly Software Solutions Inc
"""``ValueMatcher`` -- multilingual fuzzy matching across word streams."""

from __future__ import annotations

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.bbox.value_matcher import ValueMatcher
from flydesk_idp.core.services.bbox.word_extractor import PageWords, Word


def _settings(threshold: float = 0.85) -> IDPSettings:
    return IDPSettings(bbox_refine_threshold=threshold)


def _words(*pairs: tuple[str, float, float, float, float]) -> list[Word]:
    return [Word(text=t, page=1, xmin=x0, ymin=y0, xmax=x1, ymax=y1) for t, x0, y0, x1, y1 in pairs]


def _words_with_order(
    *pairs: tuple[str, float, float, float, float, int],
) -> list[Word]:
    """Variant of ``_words`` that stamps ``reading_order`` on each word."""
    return [
        Word(text=t, page=1, xmin=x0, ymin=y0, xmax=x1, ymax=y1, reading_order=order)
        for t, x0, y0, x1, y1, order in pairs
    ]


def _page(words: list[Word]) -> PageWords:
    return PageWords(page=1, width=100.0, height=100.0, words=words, has_text_layer=True)


# ---------------------------------------------------------------------- spaced


def test_locates_exact_value_in_latin_text() -> None:
    page = _page(
        _words(
            ("Nombre:", 0.1, 0.1, 0.2, 0.12),
            ("Juan", 0.21, 0.1, 0.28, 0.12),
            ("Pérez", 0.29, 0.1, 0.36, 0.12),
            ("García", 0.37, 0.1, 0.44, 0.12),
        )
    )
    match = ValueMatcher(_settings()).locate("Juan Pérez García", pages=[page])
    assert match is not None
    assert match.score >= 0.95
    assert match.xmin == 0.21
    assert match.xmax == 0.44
    assert match.page == 1


def test_handles_case_difference() -> None:
    page = _page(
        _words(
            ("ACME", 0.1, 0.1, 0.2, 0.12),
            ("CORPORATION", 0.21, 0.1, 0.4, 0.12),
        )
    )
    match = ValueMatcher(_settings()).locate("acme corporation", pages=[page])
    assert match is not None
    assert match.xmin == 0.1


def test_returns_none_below_threshold() -> None:
    page = _page(_words(("totally", 0.1, 0.1, 0.2, 0.12), ("unrelated", 0.21, 0.1, 0.4, 0.12)))
    match = ValueMatcher(_settings(threshold=0.95)).locate("Banco Santander", pages=[page])
    assert match is None


def test_digits_only_variant_matches_formatted_number() -> None:
    # Document text: "1.234.567" (Spanish thousands) -- value: "1234567"
    page = _page(_words(("1.234.567", 0.1, 0.1, 0.3, 0.12)))
    match = ValueMatcher(_settings()).locate("1234567", pages=[page])
    assert match is not None


def test_candidate_pages_filter_works() -> None:
    p1 = PageWords(
        page=1,
        width=100.0,
        height=100.0,
        words=_words(("hello", 0.1, 0.1, 0.2, 0.12)),
        has_text_layer=True,
    )
    p2 = PageWords(
        page=2,
        width=100.0,
        height=100.0,
        words=_words(
            ("Total:", 0.1, 0.1, 0.2, 0.12),
            ("42.50", 0.21, 0.1, 0.3, 0.12),
        ),
        has_text_layer=True,
    )
    match = ValueMatcher(_settings()).locate("42.50", pages=[p1, p2], candidate_pages=[2])
    assert match is not None
    assert match.page == 2


def test_falls_back_to_all_pages_when_candidate_pages_have_no_words() -> None:
    p1_empty = PageWords(page=1, width=100.0, height=100.0, words=[], has_text_layer=False)
    p2 = PageWords(
        page=2,
        width=100.0,
        height=100.0,
        words=_words(("Madrid", 0.1, 0.1, 0.25, 0.12)),
        has_text_layer=True,
    )
    # Caller said "page 1" but page 1 is image-only; matcher should
    # still find on page 2.
    match = ValueMatcher(_settings()).locate("Madrid", pages=[p1_empty, p2], candidate_pages=[1])
    assert match is not None
    assert match.page == 2


# -------------------------------------------------------------- unspaced (CJK)


def test_locates_chinese_value() -> None:
    # Chinese: "北京市" (Beijing City). PyMuPDF often emits CJK character
    # by character; the matcher's char-span path should pick the union.
    page = _page(
        _words(
            ("北", 0.1, 0.1, 0.13, 0.12),
            ("京", 0.13, 0.1, 0.16, 0.12),
            ("市", 0.16, 0.1, 0.19, 0.12),
            ("中", 0.20, 0.1, 0.23, 0.12),
            ("国", 0.23, 0.1, 0.26, 0.12),
        )
    )
    match = ValueMatcher(_settings()).locate("北京市", pages=[page])
    assert match is not None
    assert match.score >= 0.95
    assert abs(match.xmin - 0.1) < 1e-6
    assert abs(match.xmax - 0.19) < 1e-6


def test_locates_arabic_value() -> None:
    # Arabic letters are spaced just like Latin; the matcher should
    # treat them as spaced words. Bbox union is direction-agnostic.
    page = _page(
        _words(
            ("شركة", 0.7, 0.1, 0.8, 0.12),  # "Sharika" (company)
            ("الفجر", 0.6, 0.1, 0.69, 0.12),  # "Al-Fajr" (the dawn)
        )
    )
    match = ValueMatcher(_settings()).locate("شركة الفجر", pages=[page])
    assert match is not None
    assert match.score >= 0.85


def test_diacritic_difference_still_matches() -> None:
    # Source text without accent; LLM extracted with accent (or vice versa).
    # rapidfuzz handles small character differences gracefully.
    page = _page(
        _words(
            ("Cafe", 0.1, 0.1, 0.18, 0.12),
            ("Madrid", 0.19, 0.1, 0.32, 0.12),
        )
    )
    match = ValueMatcher(_settings(threshold=0.85)).locate("Café Madrid", pages=[page])
    assert match is not None


def test_empty_value_returns_none() -> None:
    page = _page(_words(("Hello", 0.1, 0.1, 0.2, 0.12)))
    assert ValueMatcher(_settings()).locate("", pages=[page]) is None
    assert ValueMatcher(_settings()).locate("   ", pages=[page]) is None


def test_no_pages_returns_none() -> None:
    assert ValueMatcher(_settings()).locate("anything", pages=[]) is None


# --------------------------------------------------------- reading-order tie-break


def test_tie_break_prefers_earlier_reading_order() -> None:
    """Two equally-scored windows on the same page -- prefer the one
    that comes earlier in the document's reading order. This is what
    layout-aware engines (Docling) give us when the same value
    appears in a header AND a body paragraph.
    """
    # Two identical "Madrid" matches: the LAYOUT-earlier one carries
    # reading_order=0, the second one reading_order=5. The matcher
    # without the tie-break would pick whichever comes first in the
    # array. We arrange them so the LATER (higher reading_order) word
    # appears earlier in the array -- forcing the tie-break to matter.
    page = _page(
        _words_with_order(
            # Body paragraph match (reading_order=5) comes first in array.
            ("Madrid", 0.4, 0.7, 0.5, 0.72, 5),
            # Header match (reading_order=0) comes second in array.
            ("Madrid", 0.4, 0.1, 0.5, 0.12, 0),
        )
    )
    match = ValueMatcher(_settings()).locate("Madrid", pages=[page])
    assert match is not None
    # The HEADER match (reading_order=0, ymin=0.1) wins despite being
    # second in the array.
    assert match.ymin == 0.1
    assert match.ymax == 0.12


def test_tie_break_is_inert_when_reading_order_unset() -> None:
    """When no word carries reading_order (PyMuPDF, Tesseract path)
    the tie-break degrades to "first encountered wins" -- the existing
    behaviour, no regression for legacy engines.
    """
    page = _page(
        _words(
            ("Madrid", 0.4, 0.1, 0.5, 0.12),
            ("Madrid", 0.4, 0.7, 0.5, 0.72),
        )
    )
    match = ValueMatcher(_settings()).locate("Madrid", pages=[page])
    assert match is not None
    # First array entry wins (existing behaviour).
    assert match.ymin == 0.1
