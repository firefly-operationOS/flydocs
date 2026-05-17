# Copyright 2026 Firefly Software Solutions Inc
"""``BboxValueMatcher`` -- shared interface for the bbox refiner's matchers.

Two concrete impls ship in-tree:

* :class:`LlmValueMatcher` -- the default. Generic, locale-agnostic,
  multilingual. Calls a focused LLM per page to map each extracted
  value to the indices of the words that constitute it. No hardcoded
  date variants, no diacritic strips, no language-specific rules.
* :class:`ValueMatcher`    -- deterministic fuzzy-string fallback for
  callers that want zero LLM cost on the refine path. Uses rapidfuzz
  with basic NFC + casefold + digits-only + punctuation-stripped
  variants only -- no locale-specific transformations.

The active matcher is picked by ``IDPSettings.bbox_refine_matcher`` and
exposed as the ``BboxValueMatcher`` bean by
:class:`IDPCoreConfiguration`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from flydocs.core.services.bbox.value_matcher import MatchResult
from flydocs.core.services.bbox.word_extractor import PageWords


@runtime_checkable
class BboxValueMatcher(Protocol):
    """Locate every extracted value's word run in one batched flow."""

    async def locate_all(
        self,
        *,
        pages: list[PageWords],
        fields: list[tuple[str, str, list[int] | None]],
    ) -> dict[str, MatchResult | None]: ...
