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

"""Unit tests for :class:`FieldRepairer` -- closed-loop targeted repair.

After judge + field_validation, fields that failed verification are
re-extracted in a focused second pass that carries the failure evidence
(judge reasoning + validator errors). Repaired values replace the
originals only when they pass re-verification.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flydocs.core.services.extraction.prompts import PromptCatalog
from flydocs.core.services.repair import FailingField, FieldRepairer, collect_failing_fields
from flydocs.core.services.repair.field_repairer import _failures_text
from flydocs.core.services.validation.field_validator import FieldValidator
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.extract import (
    ExtractionOptions,
    ExtractionRequest,
    FileInput,
    StageToggles,
)
from flydocs.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    Field,
    FieldGroup,
    FieldValidation,
    FieldValidationError,
    JudgeOutcome,
)
from flydocs.interfaces.enums.field_type import FieldType
from flydocs.interfaces.enums.status import JudgeStatus, ValidationRule

_DUMMY = base64.b64encode(b"%PDF-1.4").decode("ascii")


def _doc_spec() -> DocumentTypeSpec:
    return DocumentTypeSpec(
        id="passport",
        description="x",
        country="ES",
        field_groups=[
            FieldGroup(
                name="identity",
                fields=[
                    Field(name="number", description="x", type=FieldType.STRING),
                    Field(name="name", description="x", type=FieldType.STRING),
                ],
            )
        ],
    )


def _clean_field(name: str, value: str) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        judge=JudgeOutcome(status=JudgeStatus.PASS),
        validation=FieldValidation(valid=True),
    )


def _judge_failed_field(name: str, value: str, evidence: str) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        judge=JudgeOutcome(status=JudgeStatus.FAIL, evidence=evidence),
        validation=FieldValidation(valid=True),
    )


def _validator_failed_field(name: str, value: str, message: str) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        judge=JudgeOutcome(status=JudgeStatus.PASS),
        validation=FieldValidation(
            valid=False,
            errors=[FieldValidationError(rule=ValidationRule.VALIDATOR, message=message)],
        ),
    )


def _task(groups: list[ExtractedFieldGroup]) -> Any:
    return SimpleNamespace(
        task_id="file0/seg0/passport",
        doc_spec=_doc_spec(),
        slice_bytes=b"%PDF-1.4",
        slice_pages=1,
        segment=SimpleNamespace(media_type="application/pdf"),
        extracted_groups=groups,
        model_used=None,
    )


def _ctx(tasks: list[Any], model_id: str = "base-model") -> Any:
    return SimpleNamespace(metadata={"tasks": tasks, "model_id": model_id})


def _request(*, judge: bool = True) -> ExtractionRequest:
    return ExtractionRequest(
        intention="test",
        files=[FileInput(filename="doc.pdf", content_base64=_DUMMY, expected_type="passport")],
        document_types=[_doc_spec()],
        options=ExtractionOptions(stages=StageToggles(judge=judge, repair=True)),
    )


# ---------------------------------------------------------------------------
# Failure collection
# ---------------------------------------------------------------------------


def test_collect_failing_fields_picks_judge_and_validator_failures() -> None:
    groups = [
        ExtractedFieldGroup(
            name="identity",
            fields=[
                _clean_field("name", "JOHN"),
                _judge_failed_field("number", "X123", "value not present on page 1"),
                _validator_failed_field("iban", "ES00", "IBAN checksum failed"),
            ],
        )
    ]
    failures = collect_failing_fields(groups)
    names = [(f.group, f.field) for f in failures]
    assert ("identity", "number") in names
    assert ("identity", "iban") in names
    assert ("identity", "name") not in names
    evidence = " ".join(f.evidence for f in failures)
    assert "value not present on page 1" in evidence
    assert "IBAN checksum failed" in evidence


def test_collect_failing_fields_includes_flag_for_review() -> None:
    flagged = ExtractedField(
        name="number",
        value="X123",
        judge=JudgeOutcome(status=JudgeStatus.PASS, flag_for_review=True),
    )
    groups = [ExtractedFieldGroup(name="identity", fields=[flagged])]
    failures = collect_failing_fields(groups)
    assert [(f.group, f.field) for f in failures] == [("identity", "number")]


def test_collect_failing_fields_empty_when_all_clean() -> None:
    groups = [ExtractedFieldGroup(name="identity", fields=[_clean_field("name", "JOHN")])]
    assert collect_failing_fields(groups) == []


def test_collect_failing_fields_includes_arrays_with_empty_error_list() -> None:
    """_validate_array stamps the parent valid=False with errors=[]; the
    evidence must be harvested from the row sub-fields instead."""
    bad_sub = ExtractedField(
        name="quantity",
        value="-3",
        validation=FieldValidation(
            valid=False,
            errors=[FieldValidationError(rule=ValidationRule.MINIMUM, message="value below minimum 0")],
        ),
    )
    row = ExtractedField(name="row", value=[bad_sub])
    array_field = ExtractedField(
        name="line_items",
        value=[row],
        validation=FieldValidation(valid=False, errors=[]),  # exactly what _validate_array produces
    )
    groups = [ExtractedFieldGroup(name="items", fields=[array_field])]
    failures = collect_failing_fields(groups)
    assert [(f.group, f.field) for f in failures] == [("items", "line_items")]
    assert "value below minimum 0" in failures[0].evidence


def test_failures_text_renders_array_values_compactly() -> None:
    row = ExtractedField(name="row", value=[ExtractedField(name="a", value="1")])
    text = _failures_text([FailingField(group="items", field="line_items", value=[row], evidence="bad rows")])
    assert "ExtractedField(" not in text
    assert "1 row(s)" in text


# ---------------------------------------------------------------------------
# Repair round-trip
# ---------------------------------------------------------------------------


def _repairer(
    extractor: Any,
    judge: Any,
    *,
    default_model: str | None = None,
) -> FieldRepairer:
    return FieldRepairer(
        extractor=extractor,
        judge=judge,
        field_validator=FieldValidator(),
        default_model=default_model,
    )


@pytest.mark.asyncio
async def test_maybe_repair_returns_none_when_nothing_failed() -> None:
    task = _task([ExtractedFieldGroup(name="identity", fields=[_clean_field("name", "JOHN")])])
    repairer = _repairer(MagicMock(), MagicMock())
    info = await repairer.maybe_repair(_ctx([task]), _request())
    assert info is None


@pytest.mark.asyncio
async def test_maybe_repair_accepts_field_that_passes_recheck() -> None:
    task = _task(
        [
            ExtractedFieldGroup(
                name="identity",
                fields=[
                    _clean_field("name", "JOHN"),
                    _judge_failed_field("number", "X123", "misread"),
                ],
            )
        ]
    )
    repaired_field = ExtractedField(name="number", value="Y456")
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(
        return_value=[ExtractedFieldGroup(name="identity", fields=[repaired_field])]
    )

    async def _stamp_pass(*, extracted_groups: list[ExtractedFieldGroup], **_: Any) -> Any:
        for group in extracted_groups:
            for f in group.fields:
                f.judge = JudgeOutcome(status=JudgeStatus.PASS)
        return extracted_groups

    judge = MagicMock()
    judge.judge = AsyncMock(side_effect=_stamp_pass)

    repairer = _repairer(extractor, judge, default_model="repair-model")
    info = await repairer.maybe_repair(_ctx([task]), _request())

    assert info is not None and info.triggered
    assert info.model == "repair-model"
    assert info.fields_flagged == 1
    assert info.fields_repaired == 1
    assert info.repaired_fields == ["identity.number"]
    # The repaired value replaced the original; the clean field is untouched.
    by_name = {f.name: f for f in task.extracted_groups[0].fields}
    assert by_name["number"].value == "Y456"
    assert by_name["name"].value == "JOHN"
    # The focused re-ask carried the failure evidence.
    kwargs = extractor.extract_repair.await_args.kwargs
    assert "misread" in kwargs["failing_fields_text"]
    assert kwargs["model"] == "repair-model"
    # The subset spec only contains the failing field.
    subset: DocumentTypeSpec = kwargs["doc"]
    assert [f.name for g in subset.field_groups for f in g.fields] == ["number"]


@pytest.mark.asyncio
async def test_maybe_repair_keeps_original_when_recheck_still_fails() -> None:
    original = _judge_failed_field("number", "X123", "misread")
    task = _task([ExtractedFieldGroup(name="identity", fields=[original])])
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(
        return_value=[
            ExtractedFieldGroup(name="identity", fields=[ExtractedField(name="number", value="Z999")])
        ]
    )

    async def _stamp_fail(*, extracted_groups: list[ExtractedFieldGroup], **_: Any) -> Any:
        for group in extracted_groups:
            for f in group.fields:
                f.judge = JudgeOutcome(status=JudgeStatus.FAIL, evidence="still wrong")
        return extracted_groups

    judge = MagicMock()
    judge.judge = AsyncMock(side_effect=_stamp_fail)

    repairer = _repairer(extractor, judge)
    info = await repairer.maybe_repair(_ctx([task]), _request())

    assert info is not None and info.triggered
    assert info.fields_flagged == 1
    assert info.fields_repaired == 0
    assert task.extracted_groups[0].fields[0].value == "X123"


@pytest.mark.asyncio
async def test_maybe_repair_without_judge_stage_uses_validator_only() -> None:
    """When the judge stage is off, acceptance rests on the validator re-check alone."""
    spec = _doc_spec()
    spec.field_groups[0].fields[0].pattern = r"^[A-Z]\d{3}$"  # number must match
    task = _task(
        [
            ExtractedFieldGroup(
                name="identity",
                fields=[_validator_failed_field("number", "bad!", "pattern mismatch")],
            )
        ]
    )
    task.doc_spec = spec
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(
        return_value=[
            ExtractedFieldGroup(name="identity", fields=[ExtractedField(name="number", value="X123")])
        ]
    )
    judge = MagicMock()
    judge.judge = AsyncMock()

    repairer = _repairer(extractor, judge)
    info = await repairer.maybe_repair(_ctx([task]), _request(judge=False))

    assert info is not None and info.fields_repaired == 1
    assert task.extracted_groups[0].fields[0].value == "X123"
    judge.judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_repair_rejects_candidate_the_judge_never_graded() -> None:
    """With the judge stage ON, a candidate the judge omitted keeps the
    default UNCERTAIN outcome and must NOT replace the original."""
    original = _judge_failed_field("number", "X123", "misread")
    task = _task([ExtractedFieldGroup(name="identity", fields=[original])])
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(
        return_value=[
            ExtractedFieldGroup(name="identity", fields=[ExtractedField(name="number", value="Z999")])
        ]
    )
    judge = MagicMock()
    judge.judge = AsyncMock(side_effect=lambda *, extracted_groups, **_: extracted_groups)  # stamps nothing

    repairer = _repairer(extractor, judge)
    info = await repairer.maybe_repair(_ctx([task]), _request())

    assert info is not None and info.fields_repaired == 0
    assert task.extracted_groups[0].fields[0].value == "X123"


@pytest.mark.asyncio
async def test_maybe_repair_never_replaces_a_value_with_none() -> None:
    """A null candidate (repair found nothing) must not erase the original."""
    original = _validator_failed_field("number", "bad!", "pattern mismatch")
    task = _task([ExtractedFieldGroup(name="identity", fields=[original])])
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(
        return_value=[
            ExtractedFieldGroup(name="identity", fields=[ExtractedField(name="number", value=None)])
        ]
    )
    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()))
    info = await repairer.maybe_repair(_ctx([task]), _request(judge=False))

    assert info is not None and info.fields_repaired == 0
    assert task.extracted_groups[0].fields[0].value == "bad!"


@pytest.mark.asyncio
async def test_maybe_repair_falls_back_to_request_model() -> None:
    task = _task([ExtractedFieldGroup(name="identity", fields=[_judge_failed_field("number", "X", "bad")])])
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(return_value=[])
    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()), default_model=None)
    await repairer.maybe_repair(_ctx([task], model_id="request-model"), _request())
    assert extractor.extract_repair.await_args.kwargs["model"] == "request-model"


# ---------------------------------------------------------------------------
# Prompt catalog
# ---------------------------------------------------------------------------


def test_prompt_catalog_ships_extract_repair_template() -> None:
    catalog = PromptCatalog.from_resources()
    assert catalog.extract_repair is not None


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------


def _fake_normalizer() -> Any:
    row = SimpleNamespace(
        bytes=b"%PDF-1.4",
        media_type="application/pdf",
        page_count=1,
        filename="doc.pdf",
        derived_from=[],
    )
    normalizer = MagicMock()
    normalizer.normalise = AsyncMock(return_value=[row])
    return normalizer


def _orchestrator(repairer: Any) -> Any:
    from flydocs.config import IDPSettings
    from flydocs.core.services.pipeline.orchestrator import PipelineOrchestrator

    groups = [ExtractedFieldGroup(name="identity", fields=[])]
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=(groups, "base-model"))
    judge = MagicMock()
    judge.judge = AsyncMock(return_value=None)
    return PipelineOrchestrator(
        extractor=extractor,
        splitter=MagicMock(),
        classifier=MagicMock(),
        field_validator=MagicMock(validate=MagicMock(return_value=None)),
        bbox_validator=MagicMock(validate_groups=MagicMock(return_value=None)),
        bbox_refiner=MagicMock(),
        binary_normalizer=_fake_normalizer(),
        visual_checker=MagicMock(),
        content_checker=MagicMock(),
        judge=judge,
        rule_engine=MagicMock(),
        judge_escalator=MagicMock(),
        transformation_engine=MagicMock(),
        field_repairer=repairer,
        settings=IDPSettings(),
        default_model="base-model",
    )


def _wiring_request(*, repair: bool, judge: bool = True) -> ExtractionRequest:
    return ExtractionRequest(
        intention="test",
        files=[FileInput(filename="doc.pdf", content_base64=_DUMMY, expected_type="passport")],
        document_types=[_doc_spec()],
        options=ExtractionOptions(stages=StageToggles(judge=judge, repair=repair)),
    )


@pytest.mark.asyncio
async def test_orchestrator_runs_repair_node_and_reports_audit_block() -> None:
    from flydocs.interfaces.dtos.extract import RepairInfo

    repairer = MagicMock()
    repairer.maybe_repair = AsyncMock(
        return_value=RepairInfo(
            triggered=True,
            model="repair-model",
            fields_flagged=2,
            fields_repaired=1,
            repaired_fields=["identity.number"],
        )
    )
    orchestrator = _orchestrator(repairer)
    result = await orchestrator.execute(_wiring_request(repair=True))
    repairer.maybe_repair.assert_awaited_once()
    assert result.pipeline.repair is not None
    assert result.pipeline.repair.fields_repaired == 1
    assert "repair" in [t.node for t in result.pipeline.trace]


@pytest.mark.asyncio
async def test_orchestrator_skips_repair_node_when_toggle_off() -> None:
    repairer = MagicMock()
    repairer.maybe_repair = AsyncMock()
    orchestrator = _orchestrator(repairer)
    result = await orchestrator.execute(_wiring_request(repair=False))
    repairer.maybe_repair.assert_not_awaited()
    assert result.pipeline.repair is None
    assert "repair" not in [t.node for t in result.pipeline.trace]


@pytest.mark.asyncio
async def test_orchestrator_skips_repair_without_any_signal_stage() -> None:
    """repair needs judge or field_validation verdicts to act on."""
    repairer = MagicMock()
    repairer.maybe_repair = AsyncMock()
    orchestrator = _orchestrator(repairer)
    request = ExtractionRequest(
        intention="test",
        files=[FileInput(filename="doc.pdf", content_base64=_DUMMY, expected_type="passport")],
        document_types=[_doc_spec()],
        options=ExtractionOptions(stages=StageToggles(judge=False, field_validation=False, repair=True)),
    )
    result = await orchestrator.execute(request)
    repairer.maybe_repair.assert_not_awaited()
    assert "repair" not in [t.node for t in result.pipeline.trace]
