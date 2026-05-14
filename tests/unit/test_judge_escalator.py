# Copyright 2026 Firefly Software Solutions Inc
"""Unit tests for :class:`JudgeEscalator`."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flydesk_idp.core.services.escalation import JudgeEscalator
from flydesk_idp.core.services.splitting import DiscoveredSegment
from flydesk_idp.interfaces.dtos.bbox import BoundingBox
from flydesk_idp.interfaces.dtos.doc import DocSpec, DocType, ValidatorsSpec
from flydesk_idp.interfaces.dtos.extract import (
    DocumentInput,
    ExtractionOptions,
    ExtractionRequest,
    StageToggles,
)
from flydesk_idp.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    FieldGroup,
    FieldSpec,
    FieldValidation,
    JudgeOutcome,
)
from flydesk_idp.interfaces.enums.field_type import FieldType
from flydesk_idp.interfaces.enums.status import JudgeStatus

_DUMMY = base64.b64encode(b"%PDF-1.4").decode("ascii")


def _doc_spec() -> DocSpec:
    return DocSpec(
        docType=DocType(documentType="passport", description="x", country="ES"),
        fieldGroups=[
            FieldGroup(
                fieldGroupName="g",
                fieldGroupFields=[
                    FieldSpec(fieldName="a", fieldDescription="x", fieldType=FieldType.STRING),
                    FieldSpec(fieldName="b", fieldDescription="x", fieldType=FieldType.STRING),
                ],
            )
        ],
        validators=ValidatorsSpec(),
    )


def _request(*, escalation_threshold=None, escalation_model=None) -> ExtractionRequest:
    opts = ExtractionOptions(stages=StageToggles(judge=True, judge_escalation=True))
    if escalation_threshold is not None:
        opts.escalation_threshold = escalation_threshold
    if escalation_model is not None:
        opts.escalation_model = escalation_model
    return ExtractionRequest(
        document=DocumentInput(filename="x.pdf", content_base64=_DUMMY, content_type="application/pdf"),
        docs=[_doc_spec()],
        rules=[],
        options=opts,
    )


def _field(name: str, value: str | None, judge_status: JudgeStatus, flag: bool = False) -> ExtractedField:
    return ExtractedField(
        fieldName=name,
        fieldValueFound=value,
        confidence=0.9,
        pagesFound=[1],
        bbox=BoundingBox(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0),
        field_validation=FieldValidation(valid=True),
        judge=JudgeOutcome(status=judge_status, confidence=0.9, evidence="e", notes="", flag_for_review=flag),
    )


def _ctx(
    extractor_mock: AsyncMock,
    judge_mock: AsyncMock,
    *,
    per_doc_extracted: dict[str, list[ExtractedFieldGroup]],
    primary_model: str = "anthropic:claude-haiku-4-5",
) -> Any:
    ctx = MagicMock()
    ctx.metadata = {
        "model_id": primary_model,
        "per_doc_extracted": per_doc_extracted,
        "per_doc_inputs": {
            "passport": (
                b"%PDF dummy",
                "application/pdf",
                1,
                _doc_spec(),
                DiscoveredSegment(page_start=1, page_end=1, confidence=1.0, description=""),
            )
        },
        "per_doc_model_used": {"passport": primary_model},
    }
    return ctx


# -- escalation is gated by threshold ---------------------------------------


@pytest.mark.asyncio
async def test_no_escalation_when_threshold_zero() -> None:
    """threshold=0 disables escalation regardless of failure rate."""
    extractor = AsyncMock()
    judge = AsyncMock()
    escalator = JudgeEscalator(
        extractor=extractor,
        judge=judge,
        default_threshold=0.0,
        default_model="anthropic:claude-opus-4-7",
    )
    per_doc = {
        "passport": [
            ExtractedFieldGroup(
                fieldGroupName="g",
                fieldGroupFields=[
                    _field("a", "x", JudgeStatus.FAIL),
                    _field("b", "y", JudgeStatus.FAIL),
                ],
            )
        ]
    }
    ctx = _ctx(extractor, judge, per_doc_extracted=per_doc)
    info = await escalator.maybe_escalate(ctx, _request(escalation_threshold=0.0))
    assert info is None
    extractor.extract.assert_not_called()


@pytest.mark.asyncio
async def test_no_escalation_when_model_not_set() -> None:
    extractor = AsyncMock()
    judge = AsyncMock()
    escalator = JudgeEscalator(
        extractor=extractor,
        judge=judge,
        default_threshold=0.5,
        default_model=None,
    )
    per_doc = {
        "passport": [
            ExtractedFieldGroup(
                fieldGroupName="g",
                fieldGroupFields=[_field("a", "x", JudgeStatus.FAIL), _field("b", "y", JudgeStatus.FAIL)],
            )
        ]
    }
    ctx = _ctx(extractor, judge, per_doc_extracted=per_doc)
    info = await escalator.maybe_escalate(ctx, _request())
    assert info is None


@pytest.mark.asyncio
async def test_no_escalation_when_failure_rate_below_threshold() -> None:
    extractor = AsyncMock()
    judge = AsyncMock()
    escalator = JudgeEscalator(
        extractor=extractor,
        judge=judge,
        default_threshold=0.6,
        default_model="anthropic:claude-opus-4-7",
    )
    per_doc = {
        "passport": [
            ExtractedFieldGroup(
                fieldGroupName="g",
                fieldGroupFields=[
                    _field("a", "x", JudgeStatus.PASS),
                    _field("b", "y", JudgeStatus.FAIL),  # 1/2 = 0.5 < 0.6
                ],
            )
        ]
    }
    ctx = _ctx(extractor, judge, per_doc_extracted=per_doc)
    info = await escalator.maybe_escalate(ctx, _request())
    assert info is None


# -- escalation accepted when it improves the failure rate -----------------


@pytest.mark.asyncio
async def test_escalation_triggered_and_accepted() -> None:
    """Threshold crossed AND escalation improves the result -> accepted=True."""
    new_groups = [
        ExtractedFieldGroup(
            fieldGroupName="g",
            fieldGroupFields=[
                _field("a", "x", JudgeStatus.PASS),
                _field("b", "y", JudgeStatus.PASS),
            ],
        )
    ]
    extractor = AsyncMock()
    extractor.extract = AsyncMock(return_value=(new_groups, "anthropic:claude-opus-4-7"))
    judge = AsyncMock()
    judge.judge = AsyncMock(return_value=new_groups)

    escalator = JudgeEscalator(
        extractor=extractor,
        judge=judge,
        default_threshold=0.5,
        default_model="anthropic:claude-opus-4-7",
    )
    per_doc = {
        "passport": [
            ExtractedFieldGroup(
                fieldGroupName="g",
                fieldGroupFields=[
                    _field("a", "x", JudgeStatus.FAIL),
                    _field("b", "y", JudgeStatus.FAIL),  # 2/2 = 1.0 >= 0.5
                ],
            )
        ]
    }
    ctx = _ctx(extractor, judge, per_doc_extracted=per_doc)

    info = await escalator.maybe_escalate(ctx, _request())

    assert info is not None
    assert info.triggered is True
    assert info.accepted is True
    assert info.primary_fail_rate == 1.0
    assert info.escalation_fail_rate == 0.0
    assert info.escalation_model == "anthropic:claude-opus-4-7"
    # The orchestrator's per_doc_extracted got replaced.
    assert ctx.metadata["per_doc_extracted"] == {"passport": new_groups}
    # Model attribution updated.
    assert ctx.metadata["per_doc_model_used"]["passport"] == "anthropic:claude-opus-4-7"
    extractor.extract.assert_awaited_once()


# -- escalation rejected when it doesn't improve ---------------------------


@pytest.mark.asyncio
async def test_escalation_triggered_but_rejected() -> None:
    """Threshold crossed but escalation result is no better -> accepted=False."""
    new_groups = [
        ExtractedFieldGroup(
            fieldGroupName="g",
            fieldGroupFields=[
                _field("a", "x", JudgeStatus.FAIL),
                _field("b", "y", JudgeStatus.FAIL),
            ],
        )
    ]
    extractor = AsyncMock()
    extractor.extract = AsyncMock(return_value=(new_groups, "anthropic:claude-opus-4-7"))
    judge = AsyncMock()
    judge.judge = AsyncMock(return_value=new_groups)

    escalator = JudgeEscalator(
        extractor=extractor,
        judge=judge,
        default_threshold=0.5,
        default_model="anthropic:claude-opus-4-7",
    )
    per_doc = {
        "passport": [
            ExtractedFieldGroup(
                fieldGroupName="g",
                fieldGroupFields=[
                    _field("a", "x", JudgeStatus.FAIL),
                    _field("b", "y", JudgeStatus.FAIL),
                ],
            )
        ]
    }
    ctx = _ctx(extractor, judge, per_doc_extracted=per_doc)

    info = await escalator.maybe_escalate(ctx, _request())

    assert info is not None
    assert info.triggered is True
    assert info.accepted is False
    # Original extraction preserved.
    assert ctx.metadata["per_doc_extracted"] == per_doc


# -- same-model escalation is a no-op --------------------------------------


@pytest.mark.asyncio
async def test_no_escalation_when_same_model_as_primary() -> None:
    extractor = AsyncMock()
    judge = AsyncMock()
    escalator = JudgeEscalator(
        extractor=extractor,
        judge=judge,
        default_threshold=0.1,
        default_model="anthropic:claude-opus-4-7",
    )
    per_doc = {
        "passport": [
            ExtractedFieldGroup(
                fieldGroupName="g",
                fieldGroupFields=[_field("a", "x", JudgeStatus.FAIL), _field("b", "y", JudgeStatus.FAIL)],
            )
        ]
    }
    ctx = _ctx(extractor, judge, per_doc_extracted=per_doc, primary_model="anthropic:claude-opus-4-7")
    info = await escalator.maybe_escalate(ctx, _request())
    assert info is None
