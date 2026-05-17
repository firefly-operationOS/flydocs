# Copyright 2026 Firefly Software Solutions Inc
"""``JudgeEscalator`` -- escalate to a stronger model when the judge fails too much.

The judge stage stamps every extracted field with a PASS / FAIL /
UNCERTAIN verdict and an optional ``flag_for_review`` bit. When too
many of those verdicts are bad (the failure rate exceeds the
caller's threshold), the orchestrator probably can't trust the
extraction -- so this service re-runs the extractor and the judge
with the configured escalation model and, if the new failure rate
is lower, replaces the original extraction in place.

Opt-in via:

  * ``options.stages.judge_escalation`` -- master toggle
  * ``options.escalation_threshold`` -- failure-rate trigger (0.0–1.0).
    Falls back to ``FLYDOCS_ESCALATION_THRESHOLD``.
  * ``options.escalation_model`` -- the model id to escalate to. Falls
    back to ``FLYDOCS_ESCALATION_MODEL``.

A typical production policy is "cheap by default, escalate on
uncertainty": configure the primary as a fast/cheap model
(``claude-haiku``, ``gpt-4o-mini``) and the escalation as a heavy
model (``claude-opus-4-7``, ``gpt-4o``). The service is a no-op when
the threshold is 0 or the escalation model is unset.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from flydocs.core.services.extraction.extractor import MultimodalExtractor
from flydocs.core.services.judge import Judge
from flydocs.interfaces.dtos.extract import EscalationInfo, ExtractionRequest
from flydocs.interfaces.dtos.field import ExtractedFieldGroup

logger = logging.getLogger(__name__)


class JudgeEscalator:
    """Decide whether to escalate, and if so, re-run extract + judge."""

    def __init__(
        self,
        *,
        extractor: MultimodalExtractor,
        judge: Judge,
        default_threshold: float,
        default_model: str | None,
    ) -> None:
        self._extractor = extractor
        self._judge = judge
        self._default_threshold = default_threshold
        self._default_model = default_model

    async def maybe_escalate(self, ctx: Any, request: ExtractionRequest) -> EscalationInfo | None:
        """Return an :class:`EscalationInfo` when escalation fires, ``None`` otherwise.

        Mutates ``ctx.metadata["per_doc_extracted"]`` in place when the
        escalation produces a better result, so the orchestrator's
        ``_build_result`` picks up the improved fields automatically.
        """
        threshold = self._resolve_threshold(request)
        if threshold <= 0:
            return None
        escalation_model = self._resolve_model(request)
        if not escalation_model:
            return None
        primary_model = ctx.metadata.get("model_id")
        if escalation_model == primary_model:
            return None  # nothing to gain from same-model re-run

        per_doc_extracted: dict[str, list[ExtractedFieldGroup]] = ctx.metadata.get("per_doc_extracted", {})
        per_doc_inputs = ctx.metadata.get("per_doc_inputs", {})
        primary_fail, total = _count_failures(per_doc_extracted)
        if total == 0:
            return None
        primary_rate = primary_fail / total
        if primary_rate < threshold:
            return None

        logger.info(
            "judge_escalation triggered primary_model=%s primary_rate=%.2f threshold=%.2f -> escalate to %s",
            primary_model,
            primary_rate,
            threshold,
            escalation_model,
        )

        new_per_doc: dict[str, list[ExtractedFieldGroup]] = {}

        async def _re_extract(doc_type: str) -> None:
            slice_bytes, media_type, pages, doc_spec, _segment = per_doc_inputs[doc_type]
            if not slice_bytes:
                new_per_doc[doc_type] = []
                return
            try:
                groups, _ = await self._extractor.extract(
                    document_bytes=slice_bytes,
                    media_type=media_type,
                    page_count=pages,
                    doc=doc_spec,
                    intention=request.intention,
                    language_hint=request.options.language_hint,
                    model=escalation_model,
                )
                new_per_doc[doc_type] = groups
            except Exception as exc:  # noqa: BLE001
                logger.warning("escalation extract failed for %s: %s", doc_type, exc)
                new_per_doc[doc_type] = list(per_doc_extracted.get(doc_type, []))

        await asyncio.gather(*(_re_extract(dt) for dt in per_doc_inputs))

        async def _re_judge(doc_type: str) -> None:
            groups = new_per_doc.get(doc_type, [])
            if not groups:
                return
            slice_bytes, media_type, _, doc_spec, _ = per_doc_inputs[doc_type]
            try:
                await self._judge.judge(
                    document_bytes=slice_bytes,
                    media_type=media_type,
                    doc=doc_spec,
                    extracted_groups=groups,
                    intention=request.intention,
                    model=escalation_model,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("escalation judge failed for %s: %s", doc_type, exc)

        await asyncio.gather(*(_re_judge(dt) for dt in per_doc_inputs))

        new_fail, _ = _count_failures(new_per_doc)
        new_rate = new_fail / total
        info = EscalationInfo(
            triggered=True,
            primary_model=primary_model,
            escalation_model=escalation_model,
            primary_fail_rate=primary_rate,
            escalation_fail_rate=new_rate,
            accepted=new_fail < primary_fail,
        )
        if info.accepted:
            ctx.metadata["per_doc_extracted"] = new_per_doc
            # Reflect the escalation in the response model field so the
            # caller sees which model actually produced the final fields.
            used = dict(ctx.metadata.get("per_doc_model_used") or {})
            for doc_type in new_per_doc:
                used[doc_type] = escalation_model
            ctx.metadata["per_doc_model_used"] = used
        logger.info(
            "judge_escalation primary_rate=%.2f escalation_rate=%.2f accepted=%s",
            primary_rate,
            new_rate,
            info.accepted,
        )
        return info

    def _resolve_threshold(self, request: ExtractionRequest) -> float:
        t = request.options.escalation_threshold
        if t is None:
            t = self._default_threshold
        return max(0.0, min(1.0, float(t)))

    def _resolve_model(self, request: ExtractionRequest) -> str | None:
        return request.options.escalation_model or self._default_model


def _count_failures(per_doc_extracted: dict[str, list[ExtractedFieldGroup]]) -> tuple[int, int]:
    """Count fields with a bad judge verdict.

    A "bad" verdict is any of:

    * judge.status == FAIL
    * judge.flag_for_review == True

    Fields without a populated judge (``judge.status`` empty) are not
    counted -- the rate is computed only over fields the judge actually
    looked at.
    """
    fail = 0
    total = 0
    for groups in per_doc_extracted.values():
        for group in groups:
            for field in group.fieldGroupFields:
                judge = field.judge
                if judge is None or not judge.status:
                    continue
                # JudgeStatus may be Enum or str depending on rebuild.
                status_value = getattr(judge.status, "value", judge.status)
                total += 1
                if status_value == "FAIL" or judge.flag_for_review:
                    fail += 1
    return fail, total
