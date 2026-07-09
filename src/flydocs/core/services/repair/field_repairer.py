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
only when they come back clean -- the repair is monotonic: it can fix
fields, never degrade them.

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


def collect_failing_fields(groups: list[ExtractedFieldGroup]) -> list[FailingField]:
    """Top-level fields whose judge verdict or validation failed.

    A field fails when ``judge.status == FAIL``, ``judge.flag_for_review``
    is set, or ``validation.valid`` is false. The evidence string joins
    the judge's reasoning with every validator error message.
    """
    failures: list[FailingField] = []
    for group in groups:
        for field in group.fields:
            reasons: list[str] = []
            if field.judge.status == JudgeStatus.FAIL or field.judge.flag_for_review:
                reasons.append(field.judge.evidence or field.judge.notes or "judge rejected the value")
            if not field.validation.valid:
                reasons.extend(e.message for e in field.validation.errors)
            if reasons:
                failures.append(
                    FailingField(
                        group=group.name,
                        field=field.name,
                        value=field.value,
                        evidence="; ".join(reasons),
                    )
                )
    return failures


class FieldRepairer:
    """Re-extract failing fields with their failure evidence, merge back what passes."""

    def __init__(
        self,
        *,
        extractor: MultimodalExtractor,
        judge: Judge,
        field_validator: FieldValidator,
        default_model: str | None,
    ) -> None:
        self._extractor = extractor
        self._judge = judge
        self._field_validator = field_validator
        self._default_model = default_model

    async def maybe_repair(self, ctx: Any, request: ExtractionRequest) -> RepairInfo | None:
        """Return a :class:`RepairInfo` when at least one field was flagged, else ``None``.

        Mutates each task's ``extracted_groups`` in place: a repaired
        field replaces the original only when it passes re-verification.
        """
        tasks: list[Any] = ctx.metadata.get("tasks", [])
        model = self._default_model or ctx.metadata.get("model_id")
        flagged = 0
        repaired_paths: list[str] = []

        async def _repair_task(task: Any) -> None:
            nonlocal flagged
            failures = collect_failing_fields(task.extracted_groups)
            if not failures:
                return
            flagged += len(failures)
            try:
                accepted = await self._repair_one(task, failures, request, model)
            except Exception as exc:  # noqa: BLE001 -- repair must never break the pipeline
                logger.warning("repair failed for %s: %s; keeping original fields", task.task_id, exc)
                return
            repaired_paths.extend(accepted)

        await asyncio.gather(*(_repair_task(t) for t in tasks))

        if flagged == 0:
            return None
        info = RepairInfo(
            triggered=True,
            model=model,
            fields_flagged=flagged,
            fields_repaired=len(repaired_paths),
            repaired_fields=sorted(repaired_paths),
        )
        logger.info(
            "repair flagged=%d repaired=%d model=%s",
            info.fields_flagged,
            info.fields_repaired,
            model,
        )
        return info

    async def _repair_one(
        self,
        task: Any,
        failures: list[FailingField],
        request: ExtractionRequest,
        model: str | None,
    ) -> list[str]:
        """Repair one task; return the ``group.field`` paths that were accepted."""
        subset = _subset_spec(task.doc_spec, failures)
        repaired_groups = await self._extractor.extract_repair(
            document_bytes=task.slice_bytes,
            media_type=task.segment.media_type,
            page_count=task.slice_pages,
            doc=subset,
            failing_fields_text=_failures_text(failures),
            language_hint=request.options.language_hint,
            model=model,
        )
        if not repaired_groups:
            return []

        # Re-verify: validators always (deterministic, free); judge subset
        # re-check only when the caller enabled the judge stage.
        self._field_validator.validate(subset.field_groups, repaired_groups)
        if request.options.stages.judge:
            await self._judge.judge(
                document_bytes=task.slice_bytes,
                media_type=task.segment.media_type,
                doc=subset,
                extracted_groups=repaired_groups,
                intention=request.intention,
                model=model,
            )

        failing_keys = {(f.group, f.field) for f in failures}
        repaired_by_key: dict[tuple[str, str], ExtractedField] = {
            (group.name, field.name): field
            for group in repaired_groups
            for field in group.fields
        }
        accepted: list[str] = []
        for group in task.extracted_groups:
            for index, field in enumerate(group.fields):
                key = (group.name, field.name)
                if key not in failing_keys:
                    continue
                candidate = repaired_by_key.get(key)
                if candidate is None or not _is_clean(candidate):
                    continue
                group.fields[index] = candidate
                accepted.append(f"{group.name}.{field.name}")
        return accepted


def _is_clean(field: ExtractedField) -> bool:
    """A repaired field is accepted only when re-verification passed."""
    if not field.validation.valid:
        return False
    return field.judge.status != JudgeStatus.FAIL and not field.judge.flag_for_review


def _subset_spec(doc: DocumentTypeSpec, failures: list[FailingField]) -> DocumentTypeSpec:
    """A copy of ``doc`` whose field_groups contain only the failing fields."""
    failing_keys = {(f.group, f.field) for f in failures}
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
        lines.append(f"- ``{f.group}.{f.field}``: previous value {f.value!r} -- rejected because: {f.evidence}")
    return "\n".join(lines)
