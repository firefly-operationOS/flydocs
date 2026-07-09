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

import asyncio
import base64
import io
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pypdf
import pytest

from flydocs.config import IDPSettings
from flydocs.core.services.extraction.prompts import PromptCatalog
from flydocs.core.services.pipeline.orchestrator import PipelineOrchestrator
from flydocs.core.services.repair import FieldRepairer
from flydocs.core.services.repair.field_repairer import collect_failing_fields
from flydocs.core.services.validation.field_validator import FieldValidator
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.extract import (
    ExtractionOptions,
    ExtractionRequest,
    FileInput,
    RepairInfo,
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


def _request(*, repair: bool = True, judge: bool = True, field_validation: bool = True) -> ExtractionRequest:
    return ExtractionRequest(
        intention="test",
        files=[FileInput(filename="doc.pdf", content_base64=_DUMMY, expected_type="passport")],
        document_types=[_doc_spec()],
        options=ExtractionOptions(
            stages=StageToggles(judge=judge, repair=repair, field_validation=field_validation)
        ),
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


def test_collect_failing_fields_can_exclude_flagged_pass_fields() -> None:
    """include_flagged=False restricts collection to hard failures: a PASS
    field that is merely flag_for_review (ambiguous-but-correct) is skipped."""
    flagged = ExtractedField(
        name="number",
        value="X123",
        judge=JudgeOutcome(status=JudgeStatus.PASS, flag_for_review=True),
    )
    hard_fail = _judge_failed_field("iban", "ES00", "checksum")
    groups = [ExtractedFieldGroup(name="identity", fields=[flagged, hard_fail])]
    failures = collect_failing_fields(groups, include_flagged=False)
    assert [(f.group, f.field) for f in failures] == [("identity", "iban")]


@pytest.mark.asyncio
async def test_maybe_repair_skips_flagged_pass_fields_when_configured() -> None:
    flagged = ExtractedField(
        name="number",
        value="X123",
        judge=JudgeOutcome(status=JudgeStatus.PASS, flag_for_review=True),
    )
    task = _task([ExtractedFieldGroup(name="identity", fields=[flagged])])
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(return_value=[])

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()), include_flagged=False)
    info = await repairer.maybe_repair(_ctx([task]), _request())

    assert info is None
    extractor.extract_repair.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_maybe_repair_renders_array_evidence_compactly() -> None:
    """The repair prompt summarizes array values instead of dumping DTO reprs."""
    array_field = ExtractedField(
        name="line_items",
        value=[ExtractedField(name="row", value=[ExtractedField(name="a", value="1")])],
        validation=FieldValidation(valid=False, errors=[]),
    )
    task = _task([ExtractedFieldGroup(name="items", fields=[array_field])])
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(return_value=[])

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()))
    await repairer.maybe_repair(_ctx([task]), _request(judge=False))

    text = extractor.extract_repair.await_args.kwargs["failing_fields_text"]
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
    include_flagged: bool = True,
    max_failing_fraction: float = 1.0,
    task_concurrency: int = 4,
) -> FieldRepairer:
    return FieldRepairer(
        extractor=extractor,
        judge=judge,
        field_validator=FieldValidator(),
        default_model=default_model,
        include_flagged=include_flagged,
        max_failing_fraction=max_failing_fraction,
        task_concurrency=task_concurrency,
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
# Page-sliced repair
# ---------------------------------------------------------------------------


def _pdf(pages: int) -> bytes:
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _paged_task(groups: list[ExtractedFieldGroup], *, pages: int) -> Any:
    task = _task(groups)
    task.slice_bytes = _pdf(pages)
    task.slice_pages = pages
    return task


@pytest.mark.asyncio
async def test_repair_slices_pdf_to_failing_pages_and_remaps_candidate() -> None:
    """A failure localized on page 3 of 5 re-sends only pages 2-4 (one page
    of margin); the accepted candidate's slice-relative pages are shifted
    back to document coordinates."""
    failing = _judge_failed_field("number", "X123", "misread")
    failing.pages = [3]
    task = _paged_task([ExtractedFieldGroup(name="identity", fields=[failing])], pages=5)
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(
        return_value=[
            ExtractedFieldGroup(
                name="identity", fields=[ExtractedField(name="number", value="Y456", pages=[2])]
            )
        ]
    )

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()))
    info = await repairer.maybe_repair(_ctx([task]), _request(judge=False))

    kwargs = extractor.extract_repair.await_args.kwargs
    assert kwargs["page_count"] == 3
    assert len(pypdf.PdfReader(io.BytesIO(kwargs["document_bytes"])).pages) == 3
    assert info is not None and info.fields_repaired == 1
    assert task.extracted_groups[0].fields[0].pages == [3]


@pytest.mark.asyncio
async def test_repair_sends_full_document_when_failing_pages_unknown() -> None:
    """Null-value failures carry pages=[]; without provenance the repair
    falls back to the full segment slice."""
    failing = _judge_failed_field("number", "X123", "misread")  # pages=[] by default
    task = _paged_task([ExtractedFieldGroup(name="identity", fields=[failing])], pages=5)
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(return_value=[])

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()))
    await repairer.maybe_repair(_ctx([task]), _request(judge=False))

    kwargs = extractor.extract_repair.await_args.kwargs
    assert kwargs["document_bytes"] == task.slice_bytes
    assert kwargs["page_count"] == 5


@pytest.mark.asyncio
async def test_repair_sends_full_document_for_non_pdf_media() -> None:
    failing = _judge_failed_field("number", "X123", "misread")
    failing.pages = [3]
    task = _paged_task([ExtractedFieldGroup(name="identity", fields=[failing])], pages=5)
    task.segment = SimpleNamespace(media_type="image/png")
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(return_value=[])

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()))
    await repairer.maybe_repair(_ctx([task]), _request(judge=False))

    kwargs = extractor.extract_repair.await_args.kwargs
    assert kwargs["document_bytes"] == task.slice_bytes
    assert kwargs["page_count"] == 5


# ---------------------------------------------------------------------------
# Array audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_repair_records_changed_rows_for_arrays() -> None:
    """Arrays are accepted whole, so previously-correct rows may be replaced;
    rows_changed records how many rows differ so QA can diff table repairs."""
    old_rows = [
        ExtractedField(name="row", value=[ExtractedField(name="a", value="1")]),
        ExtractedField(name="row", value=[ExtractedField(name="b", value="-3")]),
    ]
    array_field = ExtractedField(
        name="line_items",
        value=old_rows,
        validation=FieldValidation(valid=False, errors=[]),
    )
    task = _task([ExtractedFieldGroup(name="items", fields=[array_field])])
    new_rows = [
        ExtractedField(name="row", value=[ExtractedField(name="a", value="1")]),  # untouched
        ExtractedField(name="row", value=[ExtractedField(name="b", value="3")]),  # fixed
    ]
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(
        return_value=[
            ExtractedFieldGroup(name="items", fields=[ExtractedField(name="line_items", value=new_rows)])
        ]
    )

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()))
    info = await repairer.maybe_repair(_ctx([task]), _request(judge=False))

    assert info is not None and info.fields_repaired == 1
    assert info.rows_changed == {"items.line_items": 1}


# ---------------------------------------------------------------------------
# Scope cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_repair_skips_task_above_max_failing_fraction() -> None:
    """When most fields failed the extraction is globally untrustworthy:
    targeted repair is skipped and judge_escalation's full re-run (whose
    trigger rate is unchanged) is the right tool."""
    task = _task(
        [
            ExtractedFieldGroup(
                name="identity",
                fields=[
                    _judge_failed_field("number", "X123", "misread"),
                    _judge_failed_field("name", "J0HN", "misread"),
                ],
            )
        ]
    )
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(return_value=[])

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()), max_failing_fraction=0.5)
    info = await repairer.maybe_repair(_ctx([task]), _request())

    extractor.extract_repair.assert_not_awaited()
    assert info is not None and info.triggered
    assert info.fields_flagged == 2
    assert info.fields_repaired == 0
    assert info.tasks_skipped == 1


@pytest.mark.asyncio
async def test_maybe_repair_runs_when_failing_fraction_at_or_below_cap() -> None:
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
    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(return_value=[])

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()), max_failing_fraction=0.5)
    info = await repairer.maybe_repair(_ctx([task]), _request())

    extractor.extract_repair.assert_awaited_once()
    assert info is not None and info.tasks_skipped == 0


# ---------------------------------------------------------------------------
# Concurrency bound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_repair_bounds_concurrent_task_repairs() -> None:
    tasks = [
        _task([ExtractedFieldGroup(name="identity", fields=[_judge_failed_field("number", "X", "bad")])])
        for _ in range(3)
    ]
    in_flight = 0
    peak = 0

    async def _tracked_repair(**_: Any) -> list[ExtractedFieldGroup]:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return []

    extractor = MagicMock()
    extractor.extract_repair = AsyncMock(side_effect=_tracked_repair)

    repairer = _repairer(extractor, MagicMock(judge=AsyncMock()), task_concurrency=1)
    await repairer.maybe_repair(_ctx(tasks), _request())

    assert extractor.extract_repair.await_count == 3
    assert peak == 1


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


@pytest.mark.asyncio
async def test_orchestrator_runs_repair_node_and_reports_audit_block() -> None:
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
    result = await orchestrator.execute(_request())
    repairer.maybe_repair.assert_awaited_once()
    assert result.pipeline.repair is not None
    assert result.pipeline.repair.fields_repaired == 1
    assert "repair" in [t.node for t in result.pipeline.trace]


@pytest.mark.asyncio
async def test_orchestrator_skips_repair_node_when_toggle_off() -> None:
    repairer = MagicMock()
    repairer.maybe_repair = AsyncMock()
    orchestrator = _orchestrator(repairer)
    result = await orchestrator.execute(_request(repair=False))
    repairer.maybe_repair.assert_not_awaited()
    assert result.pipeline.repair is None
    assert "repair" not in [t.node for t in result.pipeline.trace]


@pytest.mark.asyncio
async def test_orchestrator_skips_repair_without_any_signal_stage() -> None:
    """repair needs judge or field_validation verdicts to act on."""
    repairer = MagicMock()
    repairer.maybe_repair = AsyncMock()
    orchestrator = _orchestrator(repairer)
    result = await orchestrator.execute(_request(judge=False, field_validation=False))
    repairer.maybe_repair.assert_not_awaited()
    assert "repair" not in [t.node for t in result.pipeline.trace]
