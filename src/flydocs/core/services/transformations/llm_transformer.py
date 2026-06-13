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
import re
import unicodedata
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from pydantic import BaseModel, Field

from flydocs.core.observability import DEFAULT_MIDDLEWARE, IDP_MODEL_SETTINGS, timed_agent_run
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

        include_provenance = getattr(transformation, "include_provenance", True)
        rows_json = json.dumps(
            [_serialise_row(r, i, include_provenance=include_provenance) for i, r in enumerate(rows)],
            indent=2,
            ensure_ascii=False,
        )
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
            model_settings={**IDP_MODEL_SETTINGS, "max_tokens": _MAX_OUTPUT_TOKENS},
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

        invariant = getattr(transformation, "invariant", None)
        produced_rows = _rebuild_rows(
            run_result.output.rows, rows, flag_ungrounded=invariant is not None
        )
        if invariant is not None:
            produced_rows = _enforce_invariant(produced_rows, invariant, transformation.id)
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


# Reserved keys the model sees as read-only context / linkage, never as data.
_ROW_ID = "_row_id"
_PROVENANCE = "_provenance"
_SOURCE_ROWS = "_source_rows"
_RESERVED_KEYS = frozenset({_ROW_ID, _PROVENANCE, _SOURCE_ROWS})


def _serialise_row(row: ExtractedField, idx: int, *, include_provenance: bool) -> dict[str, Any]:
    """Flatten a row to a JSON dict the LLM can read.

    Always carries a stable ``_row_id`` so each output row can cite the input
    row(s) it derives from (``_source_rows``) -- the anti-fabrication channel:
    an output row that cites no input row was invented. When
    ``include_provenance`` is set, a read-only ``_provenance`` block surfaces the
    evidence the extractor already captured (pages, confidence, judge evidence
    quote) so the model reconciles membership / supersession / genuineness on
    evidence instead of bare values. Both keys are context, not data.
    """
    inner = row.value if isinstance(row.value, list) else []
    out: dict[str, Any] = {}
    field_meta: dict[str, Any] = {}
    for sub in inner:
        if not isinstance(sub, ExtractedField):
            continue
        out[sub.name] = sub.value
        if include_provenance:
            meta: dict[str, Any] = {}
            if sub.confidence is not None:
                meta["confidence"] = round(float(sub.confidence), 3)
            if sub.judge is not None and sub.judge.evidence:
                meta["evidence"] = sub.judge.evidence
            if meta:
                field_meta[sub.name] = meta
    out[_ROW_ID] = f"r{idx + 1}"
    if include_provenance:
        prov: dict[str, Any] = {}
        if row.source:
            prov["source_document"] = row.source
        if row.pages:
            prov["pages"] = row.pages
        if row.confidence is not None:
            prov["confidence"] = round(float(row.confidence), 3)
        if field_meta:
            prov["fields"] = field_meta
        out[_PROVENANCE] = prov
    return out


def _union_pages(rows: list[ExtractedField]) -> list[int]:
    pages: set[int] = set()
    for r in rows:
        pages.update(r.pages or [])
    return sorted(pages)


def _mean_confidence(rows: list[ExtractedField]) -> float:
    vals = [float(r.confidence) for r in rows if r.confidence is not None]
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def _norm_tokens(value: Any) -> set[str]:
    """Lowercase + accent-fold a value into Unicode-aware identity tokens.

    Keeps tokens of any script and numeric tokens (an invoice / ISIN / SKU number
    is a legitimate identity); drops sub-3-char non-numeric noise. No domain or
    language assumption -- ``\\w`` spans every script, not just Latin.
    """
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return {t for t in re.findall(r"\w+", s.lower(), flags=re.UNICODE) if len(t) >= 3 or t.isdigit()}


def _row_tokens(row: ExtractedField) -> set[str]:
    """Identity tokens of a row: the union of its STRING sub-field tokens.

    Numeric sub-fields (shares / quantities) are excluded -- they collide across
    rows -- but a number embedded in a string identity (e.g. a tax id) is kept.
    """
    out: set[str] = set()
    for sub in row.value if isinstance(row.value, list) else []:
        if isinstance(sub, ExtractedField) and isinstance(sub.value, str) and sub.value.strip():
            out |= _norm_tokens(sub.value)
    return out


def _is_grounded(row: ExtractedField, input_token_sets: list[set[str]]) -> bool:
    """True iff the row shares a DISTINCTIVE identity token with some input row.

    Grounds an output row in the actual input so a consolidation cannot smuggle in
    an entity the source never contained. Matching is by token EQUALITY against
    per-input-row token sets, and only DISTINCTIVE tokens count: a token shared by
    more than half the input rows is boilerplate (a class label, legal suffix,
    common word) and grounds nothing. Script- and length-agnostic; a row with no
    string identity is not penalised.
    """
    if not input_token_sets:
        return True
    row_tokens = _row_tokens(row)
    if not row_tokens:
        return True  # no string identity to check (e.g. an all-numeric row)
    df: dict[str, int] = {}
    for tokens in input_token_sets:
        for t in tokens:
            df[t] = df.get(t, 0) + 1
    distinctive_max = max(1, len(input_token_sets) // 2)
    return any(0 < df.get(t, 0) <= distinctive_max for t in row_tokens)


def _rebuild_rows(
    llm_rows: list[dict[str, Any]],
    rows: list[ExtractedField],
    *,
    flag_ungrounded: bool = False,
) -> list[ExtractedField]:
    """Materialise LLM row dicts back into ExtractedField rows with honest provenance.

    Each output row may cite ``_source_rows: [<_row_id>...]`` naming the input rows
    it derives from; when it does, the row's pages/confidence/bbox are computed
    from those actual contributors instead of blanket-borrowed from ``rows[0]``
    (which previously laundered fabricated rows with real-looking provenance).

    Grounding/flagging is OPT-IN via ``flag_ungrounded`` -- set only when the
    transformation declares a parts-of-whole invariant (an entity consolidation).
    Then a row whose identity appears in NO input row was invented and is flagged
    (confidence 0, ``notes='unmatched to source'``), never dropped, so the
    invariant guard removes it before a genuine row; the check is content-based so
    it catches a wrongly-cited row too. When ``flag_ungrounded`` is False (the
    default -- e.g. a value-REWRITING transform: translate, normalize, reformat)
    NO grounding runs and the model's derived confidence/notes are preserved, so a
    legitimately rewritten string is never penalised. Reserved keys are stripped.
    """
    template_row = rows[0]
    template_subs = template_row.value if isinstance(template_row.value, list) else []
    template_by_name = {s.name: s for s in template_subs if isinstance(s, ExtractedField)}
    by_id = {f"r{i + 1}": r for i, r in enumerate(rows)}
    input_token_sets = [_row_tokens(r) for r in rows] if flag_ungrounded else []

    materialised: list[ExtractedField] = []
    for lr in llm_rows:
        lr = lr or {}
        cited = lr.get(_SOURCE_ROWS) or []
        if isinstance(cited, str):
            cited = [cited]
        contributors = [by_id[c] for c in cited if c in by_id]

        if contributors:
            pages = _union_pages(contributors)
            confidence = _mean_confidence(contributors)
            bbox = contributors[0].bbox
        else:
            pages = template_row.pages
            confidence = template_row.confidence
            bbox = template_row.bbox

        sub_fields: list[ExtractedField] = []
        for name, value in lr.items():
            if name in _RESERVED_KEYS:
                continue
            tmpl = template_by_name.get(name)
            sub_fields.append(
                ExtractedField(
                    name=name,
                    value=value,
                    pages=tmpl.pages if tmpl else pages,
                    confidence=tmpl.confidence if tmpl else confidence,
                    bbox=tmpl.bbox if tmpl else bbox,
                )
            )
        new_row = ExtractedField(
            name=f"row_{len(materialised) + 1}",
            value=sub_fields,
            pages=pages,
            confidence=confidence,
            bbox=bbox,
        )
        if flag_ungrounded and not _is_grounded(new_row, input_token_sets):
            new_row = new_row.model_copy(update={"confidence": 0.0, "notes": "unmatched to source"})
        materialised.append(new_row)
    return materialised


def _row_share(row: ExtractedField, share_field: str) -> float:
    """Read a row's numeric share sub-field; 0.0 when missing/non-numeric."""
    for sub in row.value if isinstance(row.value, list) else []:
        if isinstance(sub, ExtractedField) and sub.name == share_field:
            try:
                return float(sub.value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _enforce_invariant(
    rows: list[ExtractedField],
    invariant: Any,
    transformation_id: str,
) -> list[ExtractedField]:
    """Deterministically enforce a caller-declared parts-of-whole invariant.

    Sums ``share_field`` across the produced rows. When the total exceeds the
    declared whole beyond tolerance, an extra/duplicate/superseded row slipped
    through: in ``repair`` mode the least-trustworthy rows (lowest confidence
    first) are dropped until the sum fits; in ``warn`` mode it is logged and
    left as-is. An under-sum is never altered -- the engine does not invent rows.
    """
    share_field = invariant.share_field
    total = float(invariant.total)
    tolerance = float(invariant.tolerance)

    def _sum(rs: list[ExtractedField]) -> float:
        # Round before the tolerance compare so float reordering never flips it.
        return round(sum(_row_share(r, share_field) for r in rs), 6)

    current = _sum(rows)
    if current <= total + tolerance:
        return rows
    if getattr(invariant, "on_violation", "repair") == "warn":
        logger.warning(
            "llm_transformer: transform %s invariant '%s' sum=%.2f exceeds %.2f (left as-is)",
            transformation_id[:8],
            share_field,
            current,
            total,
        )
        return rows

    kept = list(rows)
    dropped = 0

    # Victim order (a TOTAL order, so the result never depends on list position):
    # rows flagged 'unmatched to source' (invented) first, then lowest confidence,
    # then fewest identity tokens, then lexicographically by joined tokens.
    def _victim_key(r: ExtractedField) -> tuple[int, float, int, str]:
        unmatched = 0 if r.notes == "unmatched to source" else 1
        conf = r.confidence if r.confidence is not None else 0.0
        tokens = _row_tokens(r)
        return (unmatched, conf, len(tokens), " ".join(sorted(tokens)))

    while _sum(kept) > total + tolerance and len(kept) > 1:
        victim = min(kept, key=_victim_key)
        kept.remove(victim)
        dropped += 1
    if dropped:
        logger.info(
            "llm_transformer: transform %s invariant '%s' repaired: dropped %d row(s), sum %.2f -> %.2f (<= %.2f)",
            transformation_id[:8],
            share_field,
            dropped,
            current,
            _sum(kept),
            total,
        )
    return kept


__all__ = ["LlmTransformer"]
