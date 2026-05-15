# Copyright 2026 Firefly Software Solutions Inc
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

from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydesk_idp.interfaces.dtos.transformation import EntityResolutionTransformation

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

        rows = [r for r in array_field.fieldValueFound or [] if isinstance(r, ExtractedField)]
        if not rows:
            return None

        merged_rows = _dedupe_rows(rows, transformation)

        # Build the replacement array field. We keep the same
        # ``fieldName`` so downstream consumers don't need to special-case
        # the post-transformation shape.
        new_array = ExtractedField(
            fieldName=array_field.fieldName,
            fieldValueFound=merged_rows,
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

        # Mutate in place — replace the array field on the existing group.
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
    """Return the first field whose value is a list (the array row container)."""
    for f in group.fieldGroupFields:
        if isinstance(f.fieldValueFound, list):
            return f
    return None


def _normalise_dni(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _normalise_text(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value or "")
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return " ".join(ascii_only.lower().split())


def _name_tokens(value: str) -> frozenset[str]:
    return frozenset(t for t in _normalise_text(value).split() if t)


def _row_value(row: ExtractedField, field_name: str) -> str:
    """Extract a scalar value from a row's sub-fields, by name."""
    inner = row.fieldValueFound if isinstance(row.fieldValueFound, list) else []
    for sub in inner:
        if not isinstance(sub, ExtractedField):
            continue
        if sub.fieldName == field_name:
            v = sub.fieldValueFound
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
    dni_key_to_cluster: dict[str, int] = {}

    name_field = _select_name_field(t.match_by)

    for row in rows:
        # Phase 1 — DNI exact match.
        dni_value = ""
        for field_name in t.match_by:
            v = _row_value(row, field_name)
            if field_name.lower() == "dni" and v:
                dni_value = v
                break
        if dni_value:
            key = _normalise_dni(dni_value)
            if key in dni_key_to_cluster:
                clusters[dni_key_to_cluster[key]].append(row)
            else:
                dni_key_to_cluster[key] = len(clusters)
                clusters.append([row])
            continue

        # Phase 2 — name-variant match. Walk existing clusters and try
        # to fold this row into the first compatible one.
        if name_field:
            this_name = _row_value(row, name_field)
            matched = False
            for cluster in clusters:
                rep = cluster[0]
                rep_dni = ""
                for field_name in t.match_by:
                    v = _row_value(rep, field_name)
                    if field_name.lower() == "dni" and v:
                        rep_dni = v
                        break
                # Don't merge a no-DNI row into a DNI cluster (the
                # representative row's identity is stronger; we'd
                # rather emit the no-DNI row separately).
                if rep_dni:
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


def _select_name_field(match_by: list[str]) -> str:
    """Pick a single field name to use for the name-variant phase.

    Conventional name fields come first; fall back to the first
    non-DNI entry in ``match_by``. Returns ``""`` when no usable field
    is found.
    """
    for candidate in ("nombre", "name", "razon_social", "full_name"):
        if candidate in match_by:
            return candidate
    for f in match_by:
        if f.lower() != "dni":
            return f
    return ""


def _canonicalise(cluster: list[ExtractedField], _t: EntityResolutionTransformation) -> ExtractedField:
    """Build the canonical row from a merged cluster.

    For each sub-field name found across the cluster, we keep the
    "most complete" value -- the longest string, or the first
    non-empty value when comparing scalars / multi-type fields. The
    canonical row inherits its ``fieldName`` and bbox-ish metadata
    from the first row in the cluster (the row we encountered first
    in source order).
    """
    if not cluster:
        # Defensive — callers ensure non-empty clusters but keep this safe.
        return ExtractedField(fieldName="row", fieldValueFound=None)

    base = cluster[0]
    if not isinstance(base.fieldValueFound, list):
        return base

    # Collect every sub-field name we've seen, preserving insertion order.
    seen_names: list[str] = []
    by_name: dict[str, list[ExtractedField]] = {}
    for row in cluster:
        if not isinstance(row.fieldValueFound, list):
            continue
        for sub in row.fieldValueFound:
            if not isinstance(sub, ExtractedField):
                continue
            if sub.fieldName not in by_name:
                seen_names.append(sub.fieldName)
                by_name[sub.fieldName] = []
            by_name[sub.fieldName].append(sub)

    merged_subs: list[ExtractedField] = []
    for name in seen_names:
        candidates = by_name[name]
        merged_subs.append(_pick_canonical(candidates))

    return ExtractedField(
        fieldName=base.fieldName,
        fieldValueFound=merged_subs,
        pagesFound=_merge_pages(cluster),
        confidence=max((r.confidence for r in cluster), default=0.0),
        bbox=base.bbox,
    )


def _pick_canonical(candidates: list[ExtractedField]) -> ExtractedField:
    """Of N candidate sub-fields for the same name, return the 'best' value."""

    def score(sub: ExtractedField) -> tuple[int, int]:
        v = sub.fieldValueFound
        if isinstance(v, str):
            return (1, len(v.strip()))
        if isinstance(v, (int, float)):
            return (1, 1)
        if v is None or v == "":
            return (0, 0)
        if isinstance(v, list):
            return (1, len(v))
        return (1, 1)

    return max(candidates, key=score)


def _merge_pages(cluster: list[ExtractedField]) -> list[int]:
    """Union of all pagesFound across a cluster, preserving order."""
    seen: set[int] = set()
    out: list[int] = []
    for row in cluster:
        for p in row.pagesFound or []:
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


__all__ = ["EntityResolutionTransformer"]
