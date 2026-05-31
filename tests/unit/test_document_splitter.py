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

"""Unit tests for :class:`DocumentSplitter` (discovery mode).

The splitter wraps a single LLM call -- one ``FireflyAgent.run``
invocation behind :func:`timed_agent_run`. We stub that helper so the
test runs with no provider configured, and check that:

* every returned segment is clamped to the file's page range,
* the single-page short-circuit avoids the LLM call,
* an empty LLM response is replaced by a single full-range fallback
  segment so the pipeline can still proceed,
* the splitter does not require the LLM to pick anything from the
  declared targets -- they are routing context only.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fireflyframework_agentic.prompts import PromptTemplate

from flydocs.core.services.splitting import (
    DiscoveredSegment,
    DocumentSplitter,
    SplitResult,
)
from flydocs.core.services.splitting import splitter as splitter_module
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.field import Field, FieldGroup
from flydocs.interfaces.enums.field_type import FieldType


def _template() -> PromptTemplate:
    return PromptTemplate(
        name="flydocs/splitter-test",
        system_template="discover sub-documents",
        user_template="pages={{ page_count }} targets={{ targets_json }} intent={{ intention }}",
        required_variables=["targets_json", "page_count", "intention"],
    )


def _spec(doctype: str = "deed") -> DocumentTypeSpec:
    return DocumentTypeSpec(
        id=doctype,
        description="x",
        country="ES",
        field_groups=[
            FieldGroup(
                name="g",
                fields=[Field(name="a", type=FieldType.STRING)],
            )
        ],
    )


class _StubAgent:
    def __init__(self, *_a: Any, **_kw: Any) -> None: ...


def _patch_llm(monkeypatch: pytest.MonkeyPatch, segments: list[dict[str, Any]]) -> None:
    async def _fake(agent: Any, content: Any, *, op: str, model: str) -> Any:
        output = splitter_module._SplitterOutput(
            segments=[splitter_module._SegmentModel(**s) for s in segments]
        )
        return SimpleNamespace(output=output)

    monkeypatch.setattr(splitter_module, "timed_agent_run", _fake)
    monkeypatch.setattr(splitter_module, "FireflyAgent", _StubAgent)


@pytest.mark.asyncio
async def test_single_page_shortcircuits_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 1-page file must not hit the LLM -- one segment covers the file."""

    async def _spy(*_a: Any, **_kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("LLM must not be called for single-page files")

    monkeypatch.setattr(splitter_module, "timed_agent_run", _spy)
    monkeypatch.setattr(splitter_module, "FireflyAgent", _StubAgent)
    splitter = DocumentSplitter(template=_template(), model="anthropic:claude-opus-4-7")

    result = await splitter.discover(
        document_bytes=b"%PDF dummy",
        media_type="application/pdf",
        page_count=1,
        targets=[_spec()],
        intention="x",
    )
    assert isinstance(result, SplitResult)
    assert len(result.segments) == 1
    assert result.segments[0] == DiscoveredSegment(
        page_start=1, page_end=1, provisional_type="", description="", confidence=1.0
    )


@pytest.mark.asyncio
async def test_multi_segment_clamped_to_page_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM-reported pages are clamped to ``[1, page_count]``."""
    _patch_llm(
        monkeypatch,
        [
            {
                "pages": {"start": 1, "end": 3},
                "provisional_type": "DNI",
                "description": "Spanish DNI",
                "confidence": 0.95,
            },
            {
                "pages": {"start": 4, "end": 999},
                "provisional_type": "utility_bill",
                "description": "Utility bill",
                "confidence": 0.8,
            },
        ],
    )
    splitter = DocumentSplitter(template=_template(), model="anthropic:claude-opus-4-7")

    result = await splitter.discover(
        document_bytes=b"%PDF dummy",
        media_type="application/pdf",
        page_count=10,
        targets=[_spec("dni"), _spec("utility")],
        intention="kyc",
    )
    assert len(result.segments) == 2
    assert result.segments[0].page_start == 1
    assert result.segments[0].page_end == 3
    assert result.segments[0].provisional_type == "dni"  # lower-cased
    assert result.segments[1].page_start == 4
    assert result.segments[1].page_end == 10  # clamped from 999


@pytest.mark.asyncio
async def test_empty_llm_response_falls_back_to_whole_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LLM returns no segments, the splitter emits a 1-segment fallback."""
    _patch_llm(monkeypatch, [])
    splitter = DocumentSplitter(template=_template(), model="anthropic:claude-opus-4-7")

    result = await splitter.discover(
        document_bytes=b"%PDF dummy",
        media_type="application/pdf",
        page_count=21,
        targets=[_spec()],
        intention="x",
    )
    assert len(result.segments) == 1
    seg = result.segments[0]
    assert seg.page_start == 1
    assert seg.page_end == 21
    assert seg.confidence == 0.0  # signal that segmentation failed


@pytest.mark.asyncio
async def test_provisional_type_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    """The free-text type label is normalised to snake_case-ish lowercase."""
    _patch_llm(
        monkeypatch,
        [
            {
                "pages": {"start": 1, "end": 21},
                "provisional_type": " Notarial_Deed  ",
                "description": "Spanish notarial deed",
                "confidence": 0.9,
            }
        ],
    )
    splitter = DocumentSplitter(template=_template(), model="anthropic:claude-opus-4-7")

    result = await splitter.discover(
        document_bytes=b"%PDF dummy",
        media_type="application/pdf",
        page_count=21,
        targets=[_spec("escritura_poderes")],
        intention="x",
    )
    assert result.segments[0].provisional_type == "notarial_deed"
