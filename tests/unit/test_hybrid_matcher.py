# Copyright 2026 Firefly Software Solutions Inc
"""``HybridValueMatcher`` -- deterministic-first cascade with LLM fallback.

These tests cover the cascade contract:

1. Empty input short-circuits without touching either matcher.
2. When fuzzy resolves every field, the LLM matcher is never called
   (the cheap pass wins outright).
3. When fuzzy partially resolves, only the residual is forwarded to
   the LLM, and the merged result preserves the fuzzy hits.
4. When fuzzy resolves nothing, every field is forwarded to the LLM
   matcher.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

import pytest

from flydesk_idp.core.services.bbox.hybrid_matcher import HybridValueMatcher
from flydesk_idp.core.services.bbox.value_matcher import MatchResult
from flydesk_idp.core.services.bbox.word_extractor import PageWords
from flydesk_idp.interfaces.dtos.bbox import BboxSource


def _page(num: int) -> PageWords:
    return PageWords(
        page=num,
        width=100.0,
        height=100.0,
        words=[],
        has_text_layer=False,
        source=BboxSource.OCR,
    )


def _match(page: int = 1, score: float = 0.9) -> MatchResult:
    return MatchResult(
        page=page,
        xmin=0.1,
        ymin=0.1,
        xmax=0.2,
        ymax=0.12,
        score=score,
    )


@dataclass
class _FakeMatcher:
    """Records calls + returns a scripted mapping."""

    results: dict[str, MatchResult | None] = dc_field(default_factory=dict)
    calls: list[list[tuple[str, str, list[int] | None]]] = dc_field(default_factory=list)

    async def locate_all(
        self,
        *,
        pages: list[PageWords],
        fields: list[tuple[str, str, list[int] | None]],
    ) -> dict[str, MatchResult | None]:
        self.calls.append(list(fields))
        return {fid: self.results.get(fid) for (fid, _v, _c) in fields}


# ----------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_empty_fields_returns_empty_without_calling_either() -> None:
    fuzzy = _FakeMatcher()
    llm = _FakeMatcher()
    matcher = HybridValueMatcher(fuzzy=fuzzy, llm=llm)  # type: ignore[arg-type]

    out = await matcher.locate_all(pages=[_page(1)], fields=[])

    assert out == {}
    assert fuzzy.calls == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_all_fuzzy_hits_skip_llm() -> None:
    fuzzy = _FakeMatcher(results={"a": _match(score=0.95), "b": _match(score=0.91)})
    llm = _FakeMatcher()
    matcher = HybridValueMatcher(fuzzy=fuzzy, llm=llm)  # type: ignore[arg-type]

    out = await matcher.locate_all(
        pages=[_page(1)],
        fields=[("a", "Marta", None), ("b", "Ruiz", None)],
    )

    assert out["a"] is not None and out["a"].score == pytest.approx(0.95)
    assert out["b"] is not None and out["b"].score == pytest.approx(0.91)
    # Fuzzy ran once on all fields; LLM was never touched.
    assert len(fuzzy.calls) == 1
    assert {fid for (fid, _, _) in fuzzy.calls[0]} == {"a", "b"}
    assert llm.calls == []


@pytest.mark.asyncio
async def test_residual_is_forwarded_to_llm_and_merged() -> None:
    # Fuzzy hits "a" only; "b" and "c" miss -> LLM should see {b, c}.
    fuzzy = _FakeMatcher(results={"a": _match(score=0.92)})
    llm = _FakeMatcher(results={"b": _match(score=0.88), "c": None})
    matcher = HybridValueMatcher(fuzzy=fuzzy, llm=llm)  # type: ignore[arg-type]

    fields = [
        ("a", "Marta", None),
        ("b", "veinticinco mil", None),
        ("c", "doesnotexist", None),
    ]
    out = await matcher.locate_all(pages=[_page(1)], fields=fields)

    # Merged: fuzzy hit for a, LLM hit for b, miss for c.
    assert out["a"] is not None and out["a"].score == pytest.approx(0.92)
    assert out["b"] is not None and out["b"].score == pytest.approx(0.88)
    assert out["c"] is None
    # LLM only saw the residual.
    assert len(llm.calls) == 1
    assert {fid for (fid, _, _) in llm.calls[0]} == {"b", "c"}


@pytest.mark.asyncio
async def test_all_fuzzy_misses_forward_everything_to_llm() -> None:
    fuzzy = _FakeMatcher(results={})
    llm = _FakeMatcher(results={"a": _match(), "b": _match()})
    matcher = HybridValueMatcher(fuzzy=fuzzy, llm=llm)  # type: ignore[arg-type]

    out = await matcher.locate_all(
        pages=[_page(1)],
        fields=[("a", "x", None), ("b", "y", None)],
    )

    assert out["a"] is not None
    assert out["b"] is not None
    assert len(llm.calls) == 1
    assert {fid for (fid, _, _) in llm.calls[0]} == {"a", "b"}
