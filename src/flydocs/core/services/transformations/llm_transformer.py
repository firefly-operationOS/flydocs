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

from flydocs.core.observability import DEFAULT_MIDDLEWARE, timed_agent_run
from flydocs.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydocs.interfaces.dtos.transformation import LlmTransformation

logger = logging.getLogger(__name__)

_MAX_OUTPUT_TOKENS = 8192


class _TransformOutput(BaseModel):
    """LLM response envelope.

    Each row is a flat ``{field_name: value}`` object, exactly as the prompt
    instructs the model to emit. (A previous shape wrapped each row under a
    ``values`` key, which the prompt never produced — so every row came back
    empty. Keeping the row a flat dict here matches the prompt 1:1.)
    """

    rows: list[dict[str, Any]] = Field(default_factory=list)


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

        raw = array_field.value if isinstance(array_field.value, list) else []
        rows = [r for r in raw if isinstance(r, ExtractedField)]
        if not rows:
            return None

        rows_json = json.dumps([_serialise_row(r) for r in rows], indent=2, ensure_ascii=False)
        prompt = self._template.render(
            intention=transformation.intention,
            target_group=transformation.target_group,
            rows_json=rows_json,
        )
        agent: FireflyAgent[Any, _TransformOutput] = FireflyAgent(
            name="flydocs-transformer",
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
            name=array_field.name,
            value=produced_rows,
            pages=array_field.pages,
            confidence=array_field.confidence,
            bbox=array_field.bbox,
        )

        if transformation.output_group:
            new_group = ExtractedFieldGroup(
                name=transformation.output_group,
                fields=[new_array],
            )
            groups.append(new_group)
            return new_group

        for idx, fld in enumerate(target.fields):
            if fld is array_field:
                target.fields[idx] = new_array
                break
        return target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_group(groups: list[ExtractedFieldGroup], name: str) -> ExtractedFieldGroup | None:
    for g in groups:
        if g.name == name:
            return g
    return None


def _find_array_field(group: ExtractedFieldGroup) -> ExtractedField | None:
    for f in group.fields:
        if isinstance(f.value, list):
            return f
    return None


def _serialise_row(row: ExtractedField) -> dict[str, Any]:
    """Flatten a row to a JSON dict the LLM can read."""
    inner = row.value if isinstance(row.value, list) else []
    out: dict[str, Any] = {}
    for sub in inner:
        if not isinstance(sub, ExtractedField):
            continue
        out[sub.name] = sub.value
    return out


def _rebuild_rows(llm_rows: list[dict[str, Any]], template_row: ExtractedField) -> list[ExtractedField]:
    """Materialise LLM row dicts back into ExtractedField rows.

    The template row's metadata (bbox, page) is propagated so the
    transformed rows still link back to the source document. Sub-field
    names not present in the LLM response are dropped; new sub-field
    names produced by the LLM are added as fresh ExtractedField
    children with default metadata.
    """
    template_subs = template_row.value if isinstance(template_row.value, list) else []
    template_by_name = {s.name: s for s in template_subs if isinstance(s, ExtractedField)}

    materialised: list[ExtractedField] = []
    for i, lr in enumerate(llm_rows):
        sub_fields: list[ExtractedField] = []
        for name, value in (lr or {}).items():
            tmpl = template_by_name.get(name)
            sub_fields.append(
                ExtractedField(
                    name=name,
                    value=value,
                    pages=tmpl.pages if tmpl else [],
                    confidence=tmpl.confidence if tmpl else 0.0,
                    bbox=tmpl.bbox if tmpl else template_row.bbox,
                )
            )
        materialised.append(
            ExtractedField(
                name=f"row_{i + 1}",
                value=sub_fields,
                pages=template_row.pages,
                confidence=template_row.confidence,
                bbox=template_row.bbox,
            )
        )
    return materialised


__all__ = ["LlmTransformer"]
