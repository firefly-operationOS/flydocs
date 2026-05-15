# Copyright 2026 Firefly Software Solutions Inc
"""``LlmValueMatcher`` -- batched per-page LLM matching with the agent mocked.

The matcher's correctness depends on the LLM's response shape (word
indices); these tests cover that the matcher (1) routes per-field
candidate pages correctly, (2) widens to all pages on miss, (3)
filters invalid indices defensively, (4) unions the bbox correctly,
and (5) respects the configured confidence threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

import pytest

from flydesk_idp.core.services.bbox.llm_matcher import (
    LlmValueMatcher,
    _LlmFieldMatch,
    _LlmMatchResponse,
)
from flydesk_idp.core.services.bbox.value_matcher import MatchResult
from flydesk_idp.core.services.bbox.word_extractor import PageWords, Word
from flydesk_idp.interfaces.dtos.bbox import BboxSource


def _w(text: str, page: int, x0: float, y0: float, x1: float, y1: float) -> Word:
    return Word(text=text, page=page, xmin=x0, ymin=y0, xmax=x1, ymax=y1)


def _page(num: int, words: list[Word], *, source: BboxSource = BboxSource.OCR) -> PageWords:
    return PageWords(
        page=num,
        width=100.0,
        height=100.0,
        words=words,
        has_text_layer=bool(words),
        source=source,
    )


@dataclass
class _RunResult:
    output: _LlmMatchResponse


@dataclass
class _FakeAgent:
    """Replays a script of per-page LLM responses.

    Each ``run`` call pops the next scripted response. Indexed by
    invocation order, not by page -- tests using multi-page pages
    should script in page order.
    """

    script: list[_LlmMatchResponse] = dc_field(default_factory=list)
    calls: list[Any] = dc_field(default_factory=list)

    async def run(self, content: Any, *, context: Any = None) -> _RunResult:
        self.calls.append(content)
        if not self.script:
            return _RunResult(output=_LlmMatchResponse())
        response = self.script.pop(0)
        return _RunResult(output=response)


class _MatcherUnderTest(LlmValueMatcher):
    """Override ``_build_agent`` to return our fake."""

    def __init__(self, *, threshold: float = 0.5, script: list[_LlmMatchResponse] | None = None) -> None:
        # The template is not used because we override _build_agent; pass None.
        class _TemplStub:
            def render(self, **_kw: Any) -> Any:
                class _R:
                    system = ""
                    user = ""

                return _R()

        super().__init__(
            template=_TemplStub(),  # type: ignore[arg-type]
            model="test-model",
            threshold=threshold,
        )
        self._fake = _FakeAgent(script=list(script or []))

    def _build_agent(self) -> Any:  # type: ignore[override]
        return self._fake


# ----------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_no_pages_or_no_fields_returns_empty() -> None:
    matcher = _MatcherUnderTest()
    assert await matcher.locate_all(pages=[], fields=[("a", "x", None)]) == {"a": None}
    assert await matcher.locate_all(pages=[_page(1, [_w("x", 1, 0.1, 0.1, 0.2, 0.2)])], fields=[]) == {}


@pytest.mark.asyncio
async def test_grounds_value_to_word_indices_on_candidate_page() -> None:
    page1 = _page(
        1,
        [
            _w("Name:", 1, 0.1, 0.1, 0.18, 0.12),
            _w("Marta", 1, 0.19, 0.1, 0.26, 0.12),
            _w("Ruiz", 1, 0.27, 0.1, 0.33, 0.12),
            _w("Delgado", 1, 0.34, 0.1, 0.45, 0.12),
        ],
    )
    matcher = _MatcherUnderTest(
        threshold=0.5,
        script=[
            _LlmMatchResponse(
                matches=[
                    _LlmFieldMatch(field="f0", word_indices=[1, 2, 3], confidence=0.97),
                ]
            )
        ],
    )
    out = await matcher.locate_all(
        pages=[page1],
        fields=[("f0", "Marta Ruiz Delgado", [1])],
    )
    assert out["f0"] is not None
    match: MatchResult = out["f0"]  # type: ignore[assignment]
    assert match.page == 1
    assert match.score == pytest.approx(0.97, abs=0.01)
    # Union covers the three matched words.
    assert match.xmin == pytest.approx(0.19, abs=0.001)
    assert match.xmax == pytest.approx(0.45, abs=0.001)


@pytest.mark.asyncio
async def test_widens_to_other_pages_when_candidate_returns_empty() -> None:
    # Field is declared for page 1 but the LLM returns no match there;
    # secondary pass against page 7 picks it up.
    page1 = _page(1, [_w("unrelated", 1, 0.1, 0.1, 0.2, 0.12)])
    page7 = _page(7, [_w("73615289V", 7, 0.1, 0.1, 0.25, 0.12)])
    matcher = _MatcherUnderTest(
        threshold=0.5,
        script=[
            # Primary call on page 1: no match
            _LlmMatchResponse(matches=[_LlmFieldMatch(field="f0", word_indices=[], confidence=1.0)]),
            # Secondary call on page 7: match
            _LlmMatchResponse(matches=[_LlmFieldMatch(field="f0", word_indices=[0], confidence=0.98)]),
        ],
    )
    out = await matcher.locate_all(
        pages=[page1, page7],
        fields=[("f0", "73615289V", [1])],
    )
    assert out["f0"] is not None
    assert out["f0"].page == 7


@pytest.mark.asyncio
async def test_below_threshold_match_is_dropped() -> None:
    page1 = _page(1, [_w("Marta", 1, 0.1, 0.1, 0.2, 0.12)])
    matcher = _MatcherUnderTest(
        threshold=0.85,
        script=[
            _LlmMatchResponse(matches=[_LlmFieldMatch(field="f0", word_indices=[0], confidence=0.4)]),
            # Secondary pass on all-other-pages: no matches available
        ],
    )
    out = await matcher.locate_all(pages=[page1], fields=[("f0", "Marta", [1])])
    assert out["f0"] is None


@pytest.mark.asyncio
async def test_invalid_word_indices_are_filtered() -> None:
    page1 = _page(1, [_w("Marta", 1, 0.1, 0.1, 0.2, 0.12)])
    matcher = _MatcherUnderTest(
        threshold=0.5,
        script=[
            _LlmMatchResponse(
                matches=[_LlmFieldMatch(field="f0", word_indices=[0, 99, -1], confidence=0.95)]
            ),
        ],
    )
    out = await matcher.locate_all(pages=[page1], fields=[("f0", "Marta", [1])])
    assert out["f0"] is not None
    # Only index 0 is valid; the union should be just that word's bbox.
    assert out["f0"].xmin == pytest.approx(0.1)
    assert out["f0"].xmax == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_pages_without_words_are_skipped() -> None:
    empty_page = _page(1, [], source=BboxSource.OCR)
    page2 = _page(2, [_w("hit", 2, 0.1, 0.1, 0.2, 0.12)])
    matcher = _MatcherUnderTest(
        threshold=0.5,
        script=[
            _LlmMatchResponse(matches=[_LlmFieldMatch(field="f0", word_indices=[0], confidence=0.95)]),
        ],
    )
    out = await matcher.locate_all(
        pages=[empty_page, page2],
        fields=[("f0", "hit", None)],
    )
    assert out["f0"] is not None
    assert out["f0"].page == 2
