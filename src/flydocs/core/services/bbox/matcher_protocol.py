# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
