# Copyright 2026 Firefly Software Solutions Inc
"""``ValueMatcher`` -- locate an extracted value inside a per-page word stream.

Algorithm (per page in scope):

1. Normalise the target value (NFC, casefold, collapse whitespace,
   strip punctuation that PDFs commonly drop -- commas, periods,
   parentheses, currency symbols).
2. Detect the dominant **script class**:
   * ``spaced``   -- Latin / Cyrillic / Greek / Arabic / Hebrew /
                     Devanagari / etc. -- words are separated by
                     whitespace; build sliding windows of N consecutive
                     ``Word`` rows.
   * ``unspaced`` -- CJK / Thai / Khmer / Lao -- no word boundaries;
                     treat each ``Word`` row as a character-grain
                     fragment and slide character spans.
3. Score each candidate window with rapidfuzz's normalised
   indel similarity. Pick the best window above
   :attr:`IDPSettings.bbox_refine_threshold`.
4. Return the union bbox of every word in the winning window plus
   the score.

Format coercion (cheap, additive): when the literal target doesn't
match, retry with light variants (digits-only for numeric, basic ISO
date alternatives for ``YYYY-MM-DD``-looking strings). Heavier date
parsing is intentionally Phase 1.5 -- the basic variants cover most of
the real Spanish notarial-deed corpus.

The matcher is multilingual by construction: NFC + casefold work for
every Unicode script; the spaced/unspaced split handles the
no-word-boundary scripts; bbox union is direction-agnostic so RTL
(Arabic, Hebrew) just works.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from pyfly.container import service

from flydocs.config import IDPSettings
from flydocs.core.services.bbox.word_extractor import PageWords, Word

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class MatchResult:
    """One located value -- bbox + score + page."""

    page: int
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    score: float


# Script ranges that have no inter-word whitespace. We treat anything that
# falls into these ranges as the dominant script when at least 30% of the
# value's letters land here, then switch to character-span matching.
_UNSPACED_RANGES: tuple[tuple[int, int], ...] = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0x0E00, 0x0E7F),  # Thai
    (0x0E80, 0x0EFF),  # Lao
    (0x1780, 0x17FF),  # Khmer
)


def _is_unspaced_codepoint(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _UNSPACED_RANGES)


def _normalise(text: str, *, casefold: bool = True) -> str:
    """NFC + optional casefold + whitespace collapse + safe punctuation strip."""
    out = unicodedata.normalize("NFC", text)
    if casefold:
        out = out.casefold()
    # Strip a handful of punctuation marks that are commonly absent in
    # the source text but added by the LLM ("dot net" → "dot.net").
    out = re.sub(r"[ \s]+", " ", out)
    out = out.strip()
    return out


def _digits_only(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


@service
class ValueMatcher:
    """Locate an extracted value inside per-page word streams."""

    def __init__(self, settings: IDPSettings) -> None:
        self._threshold = settings.bbox_refine_threshold

    async def locate_all(
        self,
        *,
        pages: list[PageWords],
        fields: list[tuple[str, str, list[int] | None]],
    ) -> dict[str, MatchResult | None]:
        """Batched interface -- iterate :meth:`locate` per field.

        Provided so both fuzzy and LLM matchers expose the same
        ``BboxValueMatcher`` protocol to the refiner. The fuzzy path
        is CPU-bound (cheap rapidfuzz scoring) and runs sequentially
        in the asyncio loop -- no thread offload needed at this size.
        """
        out: dict[str, MatchResult | None] = {}
        for field_id, value, candidate in fields:
            out[field_id] = self.locate(value, pages=pages, candidate_pages=candidate)
        return out

    def locate(
        self,
        value: str,
        *,
        pages: list[PageWords],
        candidate_pages: list[int] | None = None,
    ) -> MatchResult | None:
        """Return the best bbox for ``value`` across the given pages.

        ``candidate_pages`` (if given) limits the search to those page
        numbers -- saves work when the LLM already told us which page the
        value is on. ``None`` searches every page.
        """
        norm = _normalise(value)
        if not norm:
            return None

        in_scope: list[PageWords] = (
            [p for p in pages if p.page in candidate_pages] if candidate_pages else list(pages)
        )
        # Always allow falling through to the full page set if the
        # candidate set has no usable text -- the LLM's ``pagesFound`` is
        # often off by one.
        if candidate_pages and not any(p.words for p in in_scope):
            in_scope = list(pages)

        best: MatchResult | None = None
        variants = self._variants(value)
        for page in in_scope:
            if not page.words:
                continue
            for variant in variants:
                hit = self._best_match_on_page(variant, page)
                if hit is None:
                    continue
                if best is None or hit.score > best.score:
                    best = hit
                    if best.score >= 0.999:
                        return best
        return best

    # ------------------------------------------------------------------

    def _variants(self, value: str) -> list[str]:
        """Cheap value variants tried in turn against each page.

        Order: literal first (highest fidelity), then digits-only for
        anything that is mostly numeric (matches "12.345,67" against
        "1234567" forms), then a punctuation-stripped form.
        """
        seen: set[str] = set()
        out: list[str] = []
        literal = _normalise(value)
        if literal:
            seen.add(literal)
            out.append(literal)
        digits = _digits_only(value)
        if digits and len(digits) >= 4 and digits not in seen:
            seen.add(digits)
            out.append(digits)
        stripped = _normalise(re.sub(r"[\.,;:()\[\]{}/\\\-_'\"`]+", " ", value))
        if stripped and stripped not in seen:
            seen.add(stripped)
            out.append(stripped)
        return out

    def _best_match_on_page(self, target: str, page: PageWords) -> MatchResult | None:
        from rapidfuzz import fuzz  # pyright: ignore[reportMissingImports]

        words = page.words
        if not words:
            return None
        # Decide spaced vs unspaced from the *target* value: that's what
        # the matcher is going to score against.
        unspaced = self._is_unspaced(target)
        if unspaced:
            return self._match_unspaced(target, words, fuzz, page.page)
        return self._match_spaced(target, words, fuzz, page.page)

    @staticmethod
    def _is_unspaced(target: str) -> bool:
        letters = [c for c in target if c.isalpha() or _is_unspaced_codepoint(ord(c))]
        if not letters:
            return False
        unspaced_count = sum(1 for c in letters if _is_unspaced_codepoint(ord(c)))
        return unspaced_count / len(letters) >= 0.3

    def _match_spaced(
        self,
        target: str,
        words: list[Word],
        fuzz: object,
        page_number: int,
    ) -> MatchResult | None:
        target_word_count = max(1, len(target.split()))
        # Slide windows of size 1..target_word_count*3 (caps at 12 to keep the
        # outer loop bounded for very long values).
        max_window = min(12, max(target_word_count * 3, target_word_count + 2))
        best_score = 0.0
        best_order: float = float("inf")
        best_window: tuple[int, int] | None = None
        normalised_words = [(_normalise(w.text), w) for w in words]
        for start in range(len(words)):
            for size in range(1, max_window + 1):
                end = start + size
                if end > len(words):
                    break
                joined = " ".join(t for t, _ in normalised_words[start:end] if t)
                if not joined:
                    continue
                score = fuzz.ratio(target, joined) / 100.0  # pyright: ignore[reportAttributeAccessIssue]
                order = _first_reading_order(words[start:end])
                # Tie-break by reading order: when two windows score the
                # same, prefer the one that appears earlier in the
                # document (lower reading_order). Words without a
                # reading_order (PyMuPDF / Tesseract) sort by their
                # array index, which is already reading-order-ish.
                if score > best_score or (score == best_score and order < best_order):
                    best_score = score
                    best_order = order
                    best_window = (start, end)
        if best_window is None or best_score < self._threshold:
            return None
        return _union_bbox(words[best_window[0] : best_window[1]], best_score, page_number)

    def _match_unspaced(
        self,
        target: str,
        words: list[Word],
        fuzz: object,
        page_number: int,
    ) -> MatchResult | None:
        # Unspaced scripts: each Word may already be a single character
        # (PyMuPDF tokenises CJK that way) but it can also be a multi-
        # character run when the PDF spaces glyphs. Walk character-by-
        # character across the joined page text and slide character
        # windows.
        chars: list[tuple[str, Word]] = []
        for w in words:
            norm_word = _normalise(w.text, casefold=False)
            for ch in norm_word:
                if ch.isspace():
                    continue
                chars.append((ch, w))
        if not chars:
            return None
        max_window = min(80, max(len(target) + 4, 6))
        best_score = 0.0
        best_order: float = float("inf")
        best_window: tuple[int, int] | None = None
        for start in range(len(chars)):
            for size in range(max(1, len(target) - 2), max_window + 1):
                end = start + size
                if end > len(chars):
                    break
                joined = "".join(c for c, _ in chars[start:end])
                score = fuzz.ratio(target, joined) / 100.0  # pyright: ignore[reportAttributeAccessIssue]
                order = _first_reading_order([w for _, w in chars[start:end]])
                if score > best_score or (score == best_score and order < best_order):
                    best_score = score
                    best_order = order
                    best_window = (start, end)
        if best_window is None or best_score < self._threshold:
            return None
        winning_words = list({id(w): w for _, w in chars[best_window[0] : best_window[1]]}.values())
        return _union_bbox(winning_words, best_score, page_number)


def _union_bbox(words: list[Word], score: float, page: int) -> MatchResult:
    xmin = min(w.xmin for w in words)
    ymin = min(w.ymin for w in words)
    xmax = max(w.xmax for w in words)
    ymax = max(w.ymax for w in words)
    return MatchResult(
        page=page,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        score=round(min(1.0, max(0.0, score)), 4),
    )


def _first_reading_order(words: list[Word]) -> float:
    """Lowest reading_order across a word slice, or ``+inf`` if none set.

    Returning ``+inf`` when no word carries reading_order keeps the
    tie-break comparison a strict ``<`` -- legacy engines that don't
    populate the field never *win* a tie, but they also never lose to
    nothing, so the matcher's behaviour stays unchanged when every
    word lacks the metadata.
    """
    best: float = float("inf")
    for w in words:
        if w.reading_order is None:
            continue
        if w.reading_order < best:
            best = float(w.reading_order)
    return best
