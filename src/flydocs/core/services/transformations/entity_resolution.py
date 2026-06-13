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

"""``EntityResolutionTransformer`` -- deterministic dedup of array rows.

Implements the matching rules described on
:class:`EntityResolutionTransformation`: DNI-first exact match falls
back to NFKD-folded token-subset matching for rows without DNI. The
matcher is intentionally conservative: it requires at least
``min_shared_tokens`` shared tokens before merging name-only rows so
two unrelated people who happen to share a single first name never
collapse into one canonical row.

The transformer mutates the target group in place (or, when
``output_group`` is set, leaves the original untouched and appends a
new group). It never raises on bad input -- a target group that does
not exist, a target group that is not an array, or an empty match-by
list all degrade to a no-op + a structured warning so the surrounding
pipeline can continue.
"""

from __future__ import annotations

import logging
import unicodedata

from pyfly.container import service

from flydocs.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydocs.interfaces.dtos.transformation import EntityResolutionTransformation

logger = logging.getLogger(__name__)


@service
class EntityResolutionTransformer:
    """Apply a :class:`EntityResolutionTransformation` to a group list."""

    def apply(
        self,
        transformation: EntityResolutionTransformation,
        groups: list[ExtractedFieldGroup],
    ) -> ExtractedFieldGroup | None:
        """Mutate ``groups`` per the transformation. Return the resulting group.

        Behaviour:

        * If ``output_group`` is set, the original group stays put and
          a new group with the deduped rows is appended (and returned).
        * Otherwise, the original group's array field is replaced in
          place with the deduped rows.
        * If the target group is not found or does not contain a
          single array field, the call is a no-op and ``None`` is
          returned.
        """
        target = _find_group(groups, transformation.target_group)
        if target is None:
            logger.debug(
                "entity_resolution: target group %r not found; skipping",
                transformation.target_group,
            )
            return None
        array_field = _find_array_field(target)
        if array_field is None:
            logger.debug(
                "entity_resolution: target group %r has no array field; skipping",
                transformation.target_group,
            )
            return None

        raw = array_field.value if isinstance(array_field.value, list) else []
        rows = [r for r in raw if isinstance(r, ExtractedField)]
        if not rows:
            return None

        merged_rows = _dedupe_rows(rows, transformation)

        # Build the replacement array field. We keep the same field name
        # so downstream consumers don't need to special-case the
        # post-transformation shape.
        new_array = ExtractedField(
            name=array_field.name,
            value=merged_rows,
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

        # Mutate in place — replace the array field on the existing group.
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
    """Return the first field whose value is a list (the array row container)."""
    for f in group.fields:
        if isinstance(f.value, list):
            return f
    return None


def _normalise_key(value: str) -> str:
    """Normalise a strong exact-match key (tax id, account no., SKU): uppercase,
    keep only alphanumerics. Domain-agnostic -- works for any identifier."""
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _normalise_text(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value or "")
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return " ".join(ascii_only.lower().split())


def _name_tokens(value: str) -> frozenset[str]:
    return frozenset(t for t in _normalise_text(value).split() if t)


def _row_value(row: ExtractedField, field_name: str) -> str:
    """Extract a scalar value from a row's sub-fields, by name."""
    inner = row.value if isinstance(row.value, list) else []
    for sub in inner:
        if not isinstance(sub, ExtractedField):
            continue
        if sub.name == field_name:
            v = sub.value
            if isinstance(v, (str, int, float)):
                return str(v).strip()
    return ""


def _name_variant_match(a: str, b: str, min_shared: int) -> bool:
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    if not (ta.issubset(tb) or tb.issubset(ta)):
        return False
    return min(len(ta), len(tb)) >= min_shared


def _dedupe_rows(
    rows: list[ExtractedField],
    t: EntityResolutionTransformation,
) -> list[ExtractedField]:
    """Walk rows, group them by match strategy, collapse each cluster into one canonical row."""
    clusters: list[list[ExtractedField]] = []
    strong_key_to_cluster: dict[str, int] = {}

    # The FIRST declared match_by field is the strong exact-match key (a tax id,
    # account number, SKU, ...); the remaining fields drive fuzzy variant
    # matching. No field name is special-cased -- the caller declares the order.
    strong_key = t.match_by[0] if t.match_by else ""
    name_field = _select_name_field(t.match_by, strong_key)

    for row in rows:
        # Phase 1 — strong exact-key match (the first match_by field).
        key_value = _row_value(row, strong_key) if strong_key else ""
        if key_value:
            key = _normalise_key(key_value)
            if key in strong_key_to_cluster:
                clusters[strong_key_to_cluster[key]].append(row)
            else:
                strong_key_to_cluster[key] = len(clusters)
                clusters.append([row])
            continue

        # Phase 2 — variant match on the secondary field. Walk existing clusters
        # and try to fold this row into the first compatible one.
        if name_field:
            this_name = _row_value(row, name_field)
            matched = False
            for cluster in clusters:
                rep = cluster[0]
                # Don't merge a row lacking the strong key into a strong-key
                # cluster: that cluster's identity is firmer, so emit separately.
                if strong_key and _row_value(rep, strong_key):
                    continue
                rep_name = _row_value(rep, name_field)
                if _name_variant_match(this_name, rep_name, t.min_shared_tokens):
                    cluster.append(row)
                    matched = True
                    break
            if matched:
                continue

        # No cluster matched -> start a new one.
        clusters.append([row])

    return [_canonicalise(cluster, t) for cluster in clusters]


def _select_name_field(match_by: list[str], strong_key: str) -> str:
    """The field used for the fuzzy variant phase: the first ``match_by`` entry
    that is not the strong key. Domain-agnostic -- no hardcoded field names."""
    for f in match_by:
        if f != strong_key:
            return f
    return ""


def _canonicalise(cluster: list[ExtractedField], _t: EntityResolutionTransformation) -> ExtractedField:
    """Build the canonical row from a merged cluster.

    For each sub-field name found across the cluster, we keep the
    "most complete" value -- the longest string, or the first
    non-empty value when comparing scalars / multi-type fields. The
    canonical row inherits its ``name`` and bbox-ish metadata from the
    first row in the cluster (the row we encountered first in source
    order).
    """
    if not cluster:
        # Defensive — callers ensure non-empty clusters but keep this safe.
        return ExtractedField(name="row", value=None)

    base = cluster[0]
    if not isinstance(base.value, list):
        return base

    # Collect every sub-field name we've seen, preserving insertion order.
    seen_names: list[str] = []
    by_name: dict[str, list[ExtractedField]] = {}
    for row in cluster:
        inner = row.value if isinstance(row.value, list) else []
        for sub in inner:
            if not isinstance(sub, ExtractedField):
                continue
            if sub.name not in by_name:
                seen_names.append(sub.name)
                by_name[sub.name] = []
            by_name[sub.name].append(sub)

    merged_subs: list[ExtractedField] = []
    for fname in seen_names:
        candidates = by_name[fname]
        merged_subs.append(_pick_canonical(candidates))

    return ExtractedField(
        name=base.name,
        value=merged_subs,
        pages=_merge_pages(cluster),
        confidence=max((r.confidence for r in cluster), default=0.0),
        bbox=base.bbox,
    )


def _pick_canonical(candidates: list[ExtractedField]) -> ExtractedField:
    """Of N candidate sub-fields for the same name, return the 'best' value."""

    def score(sub: ExtractedField) -> tuple[int, int, str]:
        # Total order: non-empty beats empty, then most-complete, then a stable
        # lexicographic tie-break so the winner never depends on list position.
        v = sub.value
        tie = str(v) if v is not None else ""
        if isinstance(v, str):
            return (1, len(v.strip()), tie)
        if isinstance(v, (int, float)):
            return (1, 1, tie)
        if v is None or v == "":
            return (0, 0, tie)
        if isinstance(v, list):
            return (1, len(v), tie)
        return (1, 1, tie)

    return max(candidates, key=score)


def _merge_pages(cluster: list[ExtractedField]) -> list[int]:
    """Sorted union of all pages across a cluster (deterministic; never set-order)."""
    pages: set[int] = set()
    for row in cluster:
        pages.update(row.pages or [])
    return sorted(pages)


__all__ = ["EntityResolutionTransformer"]
