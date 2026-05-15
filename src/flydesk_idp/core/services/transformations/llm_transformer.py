# Copyright 2026 Firefly Software Solutions Inc
"""``LlmTransformer`` -- free-form LLM transformation of an array group.

The caller hands us a one-sentence ``intention`` and the rows of a
target group. We serialise the rows to JSON, render a focused prompt,
and ask the LLM to return a list of rows in the same shape. The output
replaces (or, with ``output_group``, augments) the original group.

This is the escape hatch: anything the declarative transformations
can't express (role classification into a closed taxonomy, language
translation, free-text normalisation, schema migrations between
extraction passes) belongs here. The caller pays one LLM call per
transformation per target.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from pydantic import BaseModel, Field

from flydesk_idp.core.observability import DEFAULT_MIDDLEWARE, timed_agent_run
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydesk_idp.interfaces.dtos.transformation import LlmTransformation

logger = logging.getLogger(__name__)

_MAX_OUTPUT_TOKENS = 8192


class _TransformRow(BaseModel):
    """One row returned by the LLM. Free-form key/value dict."""

    values: dict[str, Any] = Field(default_factory=dict)


class _TransformOutput(BaseModel):
    """LLM response envelope."""

    rows: list[_TransformRow] = Field(default_factory=list)


class LlmTransformer:
    """Apply a :class:`LlmTransformation` to a group list."""

    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
    ) -> None:
        self._template = template
        self._model = model

    async def apply(
        self,
        transformation: LlmTransformation,
        groups: list[ExtractedFieldGroup],
        *,
        model: str | None = None,
    ) -> ExtractedFieldGroup | None:
        """Run the transformation. Return the resulting group (or None on no-op).

        On a successful call the target group is either replaced in
        place (default) or a new group is appended (when
        ``output_group`` is set). Failures degrade quietly: the
        exception is logged and the original group stays untouched.
        """
        target = _find_group(groups, transformation.target_group)
        if target is None:
            logger.debug(
                "llm_transformer: target group %r not found; skipping",
                transformation.target_group,
            )
            return None
        array_field = _find_array_field(target)
        if array_field is None:
            logger.debug(
                "llm_transformer: target group %r has no array field; skipping",
                transformation.target_group,
            )
            return None

        rows = [r for r in array_field.fieldValueFound or [] if isinstance(r, ExtractedField)]
        if not rows:
            return None

        rows_json = json.dumps([_serialise_row(r) for r in rows], indent=2, ensure_ascii=False)
        prompt = self._template.render(
            intention=transformation.intention,
            target_group=transformation.target_group,
            rows_json=rows_json,
        )
        agent: FireflyAgent[Any, _TransformOutput] = FireflyAgent(
            name="flydesk-idp-transformer",
            model=model or self._model,
            instructions=prompt.system,
            output_type=_TransformOutput,
            description="Post-extraction LLM transformation",
            tags=["idp", "transform"],
            middleware=list(DEFAULT_MIDDLEWARE),
            auto_register=False,
            model_settings={"max_tokens": _MAX_OUTPUT_TOKENS},
        )

        try:
            run_result = await timed_agent_run(
                agent,
                prompt.user,
                op=f"transform.{transformation.id[:8]}",
                model=model or self._model,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "llm_transformer: transformation %s failed for group %r: %s",
                transformation.id,
                transformation.target_group,
                exc,
            )
            return None

        produced_rows = _rebuild_rows(run_result.output.rows, rows[0])
        new_array = ExtractedField(
            fieldName=array_field.fieldName,
            fieldValueFound=produced_rows,
            pagesFound=array_field.pagesFound,
            confidence=array_field.confidence,
            bbox=array_field.bbox,
        )

        if transformation.output_group:
            new_group = ExtractedFieldGroup(
                fieldGroupName=transformation.output_group,
                fieldGroupFields=[new_array],
            )
            groups.append(new_group)
            return new_group

        for idx, fld in enumerate(target.fieldGroupFields):
            if fld is array_field:
                target.fieldGroupFields[idx] = new_array
                break
        return target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_group(groups: list[ExtractedFieldGroup], name: str) -> ExtractedFieldGroup | None:
    for g in groups:
        if g.fieldGroupName == name:
            return g
    return None


def _find_array_field(group: ExtractedFieldGroup) -> ExtractedField | None:
    for f in group.fieldGroupFields:
        if isinstance(f.fieldValueFound, list):
            return f
    return None


def _serialise_row(row: ExtractedField) -> dict[str, Any]:
    """Flatten a row to a JSON dict the LLM can read."""
    inner = row.fieldValueFound if isinstance(row.fieldValueFound, list) else []
    out: dict[str, Any] = {}
    for sub in inner:
        if not isinstance(sub, ExtractedField):
            continue
        out[sub.fieldName] = sub.fieldValueFound
    return out


def _rebuild_rows(llm_rows: list[_TransformRow], template_row: ExtractedField) -> list[ExtractedField]:
    """Materialise LLM row dicts back into ExtractedField rows.

    The template row's metadata (bbox, page) is propagated so the
    transformed rows still link back to the source document. Sub-field
    names not present in the LLM response are dropped; new sub-field
    names produced by the LLM are added as fresh ExtractedField
    children with default metadata.
    """
    template_subs = template_row.fieldValueFound if isinstance(template_row.fieldValueFound, list) else []
    template_by_name = {s.fieldName: s for s in template_subs if isinstance(s, ExtractedField)}

    materialised: list[ExtractedField] = []
    for i, lr in enumerate(llm_rows):
        sub_fields: list[ExtractedField] = []
        for name, value in lr.values.items():
            tmpl = template_by_name.get(name)
            sub_fields.append(
                ExtractedField(
                    fieldName=name,
                    fieldValueFound=value,
                    pagesFound=tmpl.pagesFound if tmpl else [],
                    confidence=tmpl.confidence if tmpl else 0.0,
                    bbox=tmpl.bbox if tmpl else template_row.bbox,
                )
            )
        materialised.append(
            ExtractedField(
                fieldName=f"row_{i + 1}",
                fieldValueFound=sub_fields,
                pagesFound=template_row.pagesFound,
                confidence=template_row.confidence,
                bbox=template_row.bbox,
            )
        )
    return materialised


__all__ = ["LlmTransformer"]
