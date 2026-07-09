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

"""``FieldRepairer`` -- closed-loop targeted repair of failing fields.

The judge stamps every extracted field with PASS / FAIL / UNCERTAIN and
the deterministic validators record per-field errors. Escalation re-runs
the WHOLE extraction blind; this service instead re-asks ONLY the
failing fields, quoting the failure evidence (judge reasoning +
validator messages) back to the model so the second pass corrects the
specific mistake instead of re-rolling the dice on everything.

Repaired values are re-verified (validators always; judge subset
re-check when the judge stage is enabled) and replace the originals
only when they come back clean -- with the judge on, that means an
explicit PASS, and a null candidate never replaces a value. The repair
is monotonic at the field level: a field is only replaced by a verified
candidate. Array fields are the caveat: they are re-extracted and
accepted whole, so a previously-correct row may be replaced by a new
verifier-passing value -- ``RepairInfo.rows_changed`` records how many
rows differ per repaired array so table repairs can be diffed.

Opt-in via ``options.stages.repair``. Runs after judge and BEFORE
judge_escalation, so the expensive full re-run only fires when targeted
repair was not enough. Top-level fields only: rows nested inside array
fields are repaired by re-extracting the array field as a whole.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from flydocs.core.services.extraction.extractor import MultimodalExtractor
from flydocs.core.services.extraction.pdf_slicer import PageRange, slice_pdf
from flydocs.core.services.judge import Judge
from flydocs.core.services.validation.field_validator import FieldValidator
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.extract import ExtractionRequest, RepairInfo
from flydocs.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydocs.interfaces.enums.status import JudgeStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailingField:
    """One field that failed verification, with the evidence to feed back."""

    group: str
    field: str
    value: Any
    evidence: str
    # 1-indexed slice-relative pages the value was reported on; empty when
    # unknown (typical for null-value failures, whose page is discarded).
    pages: tuple[int, ...] = ()


def collect_failing_fields(
    groups: list[ExtractedFieldGroup], *, include_flagged: bool = True
) -> list[FailingField]:
    """Top-level fields whose judge verdict or validation failed.

    A field fails when ``judge.status == FAIL``, ``judge.flag_for_review``
    is set (unless ``include_flagged`` is false -- flagged-but-PASS fields
    are usually ambiguous-but-correct, so cost-sensitive deployments can
    restrict repair to hard failures), or ``validation.valid`` is false.
    The evidence string joins the judge's reasoning with every validator
    error message. Array fields carry ``valid=False`` with an empty parent
    error list (the messages live on the row sub-fields), so the evidence
    for arrays is harvested from the rows.
    """
    failures: list[FailingField] = []
    for group in groups:
        for field in group.fields:
            reasons: list[str] = []
            if field.judge.status == JudgeStatus.FAIL or (include_flagged and field.judge.flag_for_review):
                reasons.append(field.judge.evidence or field.judge.notes or "judge rejected the value")
            if not field.validation.valid:
                messages = [e.message for e in field.validation.errors]
                if not messages and isinstance(field.value, list):
                    messages = _row_error_messages(field.value)
                reasons.extend(messages or ["failed validation"])
            if reasons:
                failures.append(
                    FailingField(
                        group=group.name,
                        field=field.name,
                        value=field.value,
                        evidence="; ".join(reasons),
                        pages=_field_pages(field),
                    )
                )
    return failures


def _field_pages(field: ExtractedField) -> tuple[int, ...]:
    """Every page the field (or, for arrays, its rows) was reported on."""
    pages = set(field.pages)
    if isinstance(field.value, list):
        for sub in field.value:
            if isinstance(sub, ExtractedField):
                pages.update(_field_pages(sub))
    return tuple(sorted(pages))


def _row_error_messages(rows: list[Any]) -> list[str]:
    """Validator messages stamped on the row sub-fields of an array field."""
    messages: list[str] = []
    for row in rows:
        if not isinstance(row, ExtractedField) or not isinstance(row.value, list):
            continue
        for sub_field in row.value:
            if not isinstance(sub_field, ExtractedField):
                continue
            messages.extend(f"{sub_field.name}: {e.message}" for e in sub_field.validation.errors)
    return messages


class FieldRepairer:
    """Re-extract failing fields with their failure evidence, merge back what passes."""

    def __init__(
        self,
        *,
        extractor: MultimodalExtractor,
        judge: Judge,
        field_validator: FieldValidator,
        default_model: str | None,
        include_flagged: bool = True,
        max_failing_fraction: float = 0.5,
        task_concurrency: int = 4,
    ) -> None:
        self._extractor = extractor
        self._judge = judge
        self._field_validator = field_validator
        self._default_model = default_model
        self._include_flagged = include_flagged
        self._max_failing_fraction = max_failing_fraction
        self._task_concurrency = task_concurrency

    async def maybe_repair(self, ctx: Any, request: ExtractionRequest) -> RepairInfo | None:
        """Return a :class:`RepairInfo` when at least one field was flagged, else ``None``.

        Mutates each task's ``extracted_groups`` in place: a repaired
        field replaces the original only when it passes re-verification.
        """
        tasks: list[Any] = ctx.metadata.get("tasks", [])
        model = self._default_model or ctx.metadata.get("model_id")
        flagged = 0
        skipped_tasks = 0
        repaired_paths: list[str] = []
        rows_changed: dict[str, int] = {}
        # Same rationale as bbox_refine_doc_concurrency: each repaired task
        # multiplies in-flight LLM calls; lower it under provider rate limits.
        semaphore = asyncio.Semaphore(max(1, self._task_concurrency))

        async def _repair_task(task: Any) -> None:
            nonlocal flagged, skipped_tasks
            failures = collect_failing_fields(task.extracted_groups, include_flagged=self._include_flagged)
            if not failures:
                return
            flagged += len(failures)
            total = sum(len(group.fields) for group in task.extracted_groups)
            if total and len(failures) / total > self._max_failing_fraction:
                # The extraction is globally untrustworthy: a focused pass
                # would re-extract nearly everything on top of the eventual
                # escalation re-run. Leave the FAIL verdicts in place so
                # judge_escalation triggers as before.
                skipped_tasks += 1
                logger.info(
                    "repair skipped for %s: %d/%d fields failing exceeds max fraction %.2f",
                    task.task_id,
                    len(failures),
                    total,
                    self._max_failing_fraction,
                )
                return
            try:
                async with semaphore:
                    accepted, task_rows_changed = await self._repair_one(task, failures, request, model)
            except Exception as exc:  # noqa: BLE001 -- repair must never break the pipeline
                logger.warning("repair failed for %s: %s; keeping original fields", task.task_id, exc)
                return
            repaired_paths.extend(accepted)
            rows_changed.update(task_rows_changed)

        await asyncio.gather(*(_repair_task(t) for t in tasks))

        if flagged == 0:
            return None
        info = RepairInfo(
            triggered=True,
            model=model,
            fields_flagged=flagged,
            fields_repaired=len(repaired_paths),
            repaired_fields=sorted(repaired_paths),
            tasks_skipped=skipped_tasks,
            rows_changed=rows_changed,
        )
        logger.info(
            "repair flagged=%d repaired=%d skipped_tasks=%d model=%s",
            info.fields_flagged,
            info.fields_repaired,
            info.tasks_skipped,
            model,
        )
        return info

    async def _repair_one(
        self,
        task: Any,
        failures: list[FailingField],
        request: ExtractionRequest,
        model: str | None,
    ) -> tuple[list[str], dict[str, int]]:
        """Repair one task; return the accepted ``group.field`` paths and,
        for accepted array fields, how many rows differ from the original."""
        failing_keys = {(f.group, f.field) for f in failures}
        subset = _subset_spec(task.doc_spec, failing_keys)
        doc_bytes, page_count, page_offset = _repair_slice(task, failures)
        repaired_groups = await self._extractor.extract_repair(
            document_bytes=doc_bytes,
            media_type=task.segment.media_type,
            page_count=page_count,
            doc=subset,
            failing_fields_text=_failures_text(failures),
            language_hint=request.options.language_hint,
            model=model,
        )
        if not repaired_groups:
            return [], {}

        # Re-verify: validators always (deterministic, free); judge subset
        # re-check only when the caller enabled the judge stage.
        self._field_validator.validate(subset.field_groups, repaired_groups)
        if request.options.stages.judge:
            await self._judge.judge(
                document_bytes=doc_bytes,
                media_type=task.segment.media_type,
                doc=subset,
                extracted_groups=repaired_groups,
                intention=request.intention,
                model=model,
            )

        repaired_by_key: dict[tuple[str, str], ExtractedField] = {
            (group.name, field.name): field for group in repaired_groups for field in group.fields
        }
        judged = request.options.stages.judge
        accepted: list[str] = []
        rows_changed: dict[str, int] = {}
        for group in task.extracted_groups:
            for index, field in enumerate(group.fields):
                key = (group.name, field.name)
                if key not in failing_keys:
                    continue
                candidate = repaired_by_key.get(key)
                if candidate is None or not _is_clean(candidate, require_judge_pass=judged):
                    continue
                if page_offset:
                    _shift_pages(candidate, page_offset)
                path = f"{group.name}.{field.name}"
                if isinstance(field.value, list) and isinstance(candidate.value, list):
                    changed = _changed_row_count(field.value, candidate.value)
                    if changed:
                        rows_changed[path] = changed
                group.fields[index] = candidate
                accepted.append(path)
        return accepted, rows_changed


def _is_clean(field: ExtractedField, *, require_judge_pass: bool) -> bool:
    """A repaired field is accepted only when re-verification passed.

    A ``None`` value never replaces the original -- a repair that found
    nothing must not erase data. When the judge stage is on, the
    candidate needs an explicit PASS: a field the judge omitted keeps
    the default UNCERTAIN outcome and would otherwise slip through
    unverified.
    """
    if field.value is None:
        return False
    if not field.validation.valid:
        return False
    if require_judge_pass:
        return field.judge.status == JudgeStatus.PASS and not field.judge.flag_for_review
    return field.judge.status != JudgeStatus.FAIL and not field.judge.flag_for_review


def _repair_slice(task: Any, failures: list[FailingField]) -> tuple[bytes, int, int]:
    """Bytes, page count and page offset for the focused repair pass.

    The dominant repair cost is the input pages, not the schema subset,
    so when every failing field carries page provenance the segment PDF
    is sliced to the failing span plus one page of margin. Falls back to
    the full segment slice when the media is not PDF, any failing field
    has no known pages (nulled values lose theirs), the span would not
    actually shrink the document, or slicing fails.
    """
    full = (task.slice_bytes, task.slice_pages, 0)
    if task.segment.media_type != "application/pdf":
        return full
    if not failures or any(not f.pages for f in failures):
        return full
    pages = sorted({page for f in failures for page in f.pages})
    start = max(1, pages[0] - 1)
    end = min(task.slice_pages, pages[-1] + 1)
    if end - start + 1 >= task.slice_pages:
        return full
    try:
        sliced = slice_pdf(task.slice_bytes, PageRange(start=start, end=end))
    except Exception as exc:  # noqa: BLE001 -- slicing is an optimization, never fatal
        logger.warning("repair page slicing failed for %s: %s; using full slice", task.task_id, exc)
        return full
    return sliced, end - start + 1, start - 1


def _shift_pages(field: ExtractedField, offset: int) -> None:
    """Map slice-relative page numbers of an accepted candidate back to
    segment coordinates (recursing into array rows and sub-fields)."""
    field.pages = [page + offset for page in field.pages]
    if isinstance(field.value, list):
        for sub in field.value:
            if isinstance(sub, ExtractedField):
                _shift_pages(sub, offset)


def _changed_row_count(old_rows: list[Any], new_rows: list[Any]) -> int:
    """Rows of a repaired array whose content differs from the original.

    Compared positionally by (name, value) signature -- rows have no
    stable keys, so an inserted or dropped row also counts as a change.
    """
    changed = sum(
        1 for old, new in zip(old_rows, new_rows) if _row_signature(old) != _row_signature(new)
    )
    return changed + abs(len(old_rows) - len(new_rows))


def _row_signature(row: Any) -> Any:
    if isinstance(row, ExtractedField):
        if isinstance(row.value, list):
            return (row.name, tuple(_row_signature(sub) for sub in row.value))
        return (row.name, row.value)
    return row


def _subset_spec(doc: DocumentTypeSpec, failing_keys: set[tuple[str, str]]) -> DocumentTypeSpec:
    """A copy of ``doc`` whose field_groups contain only the failing fields."""
    groups = []
    for group in doc.field_groups:
        fields = [f for f in group.fields if (group.name, f.name) in failing_keys]
        if fields:
            groups.append(group.model_copy(update={"fields": fields}))
    return doc.model_copy(update={"field_groups": groups})


def _failures_text(failures: list[FailingField]) -> str:
    """Render the per-field failure evidence block for the repair prompt."""
    lines = []
    for f in failures:
        # Arrays stay compact -- a full repr of the nested rows would flood the prompt.
        value = f"<array with {len(f.value)} row(s)>" if isinstance(f.value, list) else repr(f.value)
        lines.append(f"- ``{f.group}.{f.field}``: previous value {value} -- rejected because: {f.evidence}")
    return "\n".join(lines)
