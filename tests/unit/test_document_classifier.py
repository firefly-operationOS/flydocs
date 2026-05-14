# Copyright 2026 Firefly Software Solutions Inc
"""Unit tests for :class:`DocumentClassifier`.

The classifier wraps a single LLM call -- one ``FireflyAgent.run``
invocation behind :func:`timed_agent_run`. We stub that helper so the
test runs with no provider configured, and check that:

* a matched ``documentType`` produces ``matched=True`` and carries
  through the LLM-reported confidence,
* an out-of-set ``documentType`` is coerced to the ``unmatched``
  sentinel with ``matched=False`` and ``confidence=0``,
* the empty-candidates path short-circuits without an LLM call,
* an LLM error is **not** swallowed here (the orchestrator handles it).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fireflyframework_agentic.prompts import PromptTemplate

from flydesk_idp.core.services.classification import (
    UNMATCHED,
    ClassificationResult,
    DocumentClassifier,
)
from flydesk_idp.core.services.classification import classifier as classifier_module
from flydesk_idp.interfaces.dtos.doc import DocSpec, DocType, ValidatorsSpec
from flydesk_idp.interfaces.dtos.field import FieldGroup, FieldSpec
from flydesk_idp.interfaces.enums.field_type import FieldType


def _template() -> PromptTemplate:
    return PromptTemplate(
        name="flydesk_idp/classifier-test",
        system_template="classify {{ filename }} into {{ targets_json }}",
        user_template="intention: {{ intention }}",
        required_variables=["targets_json", "filename", "media_type", "intention"],
    )


def _passport_spec() -> DocSpec:
    return DocSpec(
        docType=DocType(documentType="passport", description="x", country="ES"),
        fieldGroups=[
            FieldGroup(
                fieldGroupName="g",
                fieldGroupFields=[
                    FieldSpec(fieldName="a", fieldType=FieldType.STRING),
                ],
            )
        ],
        validators=ValidatorsSpec(),
    )


class _StubAgent:
    """Drop-in replacement for :class:`FireflyAgent` that needs no provider."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass


def _patch_llm(monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]) -> None:
    """Stub :func:`timed_agent_run` + ``FireflyAgent`` so no provider is needed."""

    async def _fake(agent: Any, content: Any, *, op: str, model: str) -> Any:
        output = classifier_module._ClassifierOutput(**raw)
        return SimpleNamespace(output=output)

    monkeypatch.setattr(classifier_module, "timed_agent_run", _fake)
    monkeypatch.setattr(classifier_module, "FireflyAgent", _StubAgent)


@pytest.mark.asyncio
async def test_matched_candidate_returns_canonical_doctype(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(
        monkeypatch,
        {"document_type": "passport", "confidence": 0.92, "description": "ES passport", "notes": ""},
    )
    classifier = DocumentClassifier(template=_template(), model="anthropic:claude-opus-4-7")

    result = await classifier.classify(
        document_bytes=b"%PDF dummy",
        media_type="application/pdf",
        filename="some.pdf",
        candidates=[_passport_spec()],
        intention="identity check",
    )

    assert isinstance(result, ClassificationResult)
    assert result.document_type == "passport"
    assert result.matched is True
    assert result.confidence == pytest.approx(0.92)
    assert result.description == "ES passport"


@pytest.mark.asyncio
async def test_unknown_doctype_coerces_to_unmatched(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM picks something outside the closed candidate set -> coerced to UNMATCHED."""
    _patch_llm(
        monkeypatch,
        {"document_type": "drivers_license", "confidence": 0.7, "description": "x", "notes": ""},
    )
    classifier = DocumentClassifier(template=_template(), model="anthropic:claude-opus-4-7")

    result = await classifier.classify(
        document_bytes=b"%PDF dummy",
        media_type="application/pdf",
        filename="x.pdf",
        candidates=[_passport_spec()],
        intention="x",
    )

    assert result.document_type == UNMATCHED
    assert result.matched is False
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_explicit_unmatched_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(
        monkeypatch,
        {"document_type": "unmatched", "confidence": 0.0, "description": "", "notes": "nope"},
    )
    classifier = DocumentClassifier(template=_template(), model="anthropic:claude-opus-4-7")

    result = await classifier.classify(
        document_bytes=b"%PDF dummy",
        media_type="application/pdf",
        filename="x.pdf",
        candidates=[_passport_spec()],
        intention="x",
    )

    assert result.document_type == UNMATCHED
    assert result.matched is False
    assert result.notes == "nope"


@pytest.mark.asyncio
async def test_empty_candidates_short_circuits_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """No candidates means there's nothing to match against -- skip the LLM call."""
    called = False

    async def _spy(agent: Any, content: Any, *, op: str, model: str) -> Any:  # pragma: no cover
        nonlocal called
        called = True
        raise AssertionError("LLM must not be called when candidates is empty")

    monkeypatch.setattr(classifier_module, "timed_agent_run", _spy)
    classifier = DocumentClassifier(template=_template(), model="anthropic:claude-opus-4-7")

    result = await classifier.classify(
        document_bytes=b"%PDF dummy",
        media_type="application/pdf",
        filename="x.pdf",
        candidates=[],
        intention="x",
    )

    assert called is False
    assert result.document_type == UNMATCHED
    assert result.matched is False


@pytest.mark.asyncio
async def test_llm_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The classifier does not swallow LLM errors -- the orchestrator does."""

    async def _boom(agent: Any, content: Any, *, op: str, model: str) -> Any:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(classifier_module, "timed_agent_run", _boom)
    monkeypatch.setattr(classifier_module, "FireflyAgent", _StubAgent)
    classifier = DocumentClassifier(template=_template(), model="anthropic:claude-opus-4-7")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await classifier.classify(
            document_bytes=b"%PDF dummy",
            media_type="application/pdf",
            filename="x.pdf",
            candidates=[_passport_spec()],
            intention="x",
        )
