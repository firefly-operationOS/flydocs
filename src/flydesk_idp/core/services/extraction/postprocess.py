# Copyright 2026 Firefly Software Solutions Inc
"""Convert the raw LLM output for one :class:`DocSpec` into a stable
list of :class:`ExtractedFieldGroup` -- value-coerced, bbox-clamped,
field-order preserved.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from flydesk_idp.core.services.extraction.schema import clamp_bbox, coerce_scalar
from flydesk_idp.interfaces.dtos.bbox import BoundingBox
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
    FieldSpec,
)
from flydesk_idp.interfaces.enums.field_type import FieldType


def _scalar_from_payload(spec: FieldSpec, payload: dict[str, Any]) -> ExtractedField:
    value = coerce_scalar(spec.fieldType, payload.get("value"))
    confidence = _clamp01(payload.get("confidence", 0.0))
    page = _int_or_none(payload.get("page"))
    bbox = clamp_bbox(payload.get("bbox"))
    notes = _maybe_str(payload.get("notes"))

    if value is None:
        page = None
        bbox = BoundingBox.empty()

    pages: list[int] = [page] if page is not None else []
    return ExtractedField(
        fieldName=spec.fieldName,
        fieldValueFound=value,
        pagesFound=pages,
        confidence=confidence,
        bbox=bbox,
        notes=notes,
    )


def _array_from_payload(spec: FieldSpec, payload: dict[str, Any]) -> ExtractedField:
    rows = payload.get("rows", []) or []
    coerced_rows: list[ExtractedField] = []
    page_set: set[int] = set()

    for row_idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        sub_fields: list[ExtractedField] = []
        for item in spec.items or []:
            item_payload = row.get(item.fieldName)
            if not isinstance(item_payload, dict):
                sub_fields.append(
                    ExtractedField(fieldName=item.fieldName, fieldValueFound=None)
                )
                continue
            item_spec = FieldSpec.model_validate(
                item.model_dump() | {"name": item.fieldName, "type": item.fieldType}
            )
            sub_field = _scalar_from_payload(item_spec, item_payload)
            sub_fields.append(sub_field)
            page_set.update(sub_field.pagesFound)
        coerced_rows.append(
            ExtractedField(
                fieldName=f"row_{row_idx + 1}",
                fieldValueFound=sub_fields,
                pagesFound=sorted(page_set),
            )
        )

    pages_from_payload = payload.get("pagesFound") or []
    pages = sorted({int(p) for p in pages_from_payload if isinstance(p, int) and p >= 1} | page_set)
    return ExtractedField(
        fieldName=spec.fieldName,
        fieldValueFound=coerced_rows,
        pagesFound=pages,
        confidence=_clamp01(payload.get("confidence", 0.0)),
        notes=_maybe_str(payload.get("notes")),
    )


def normalise_doc(raw_output: BaseModel, doc: DocSpec) -> list[ExtractedFieldGroup]:
    """Build the public list of :class:`ExtractedFieldGroup` for one doc."""
    output_dict = raw_output.model_dump(by_alias=True)
    groups: list[ExtractedFieldGroup] = []
    for group in doc.fieldGroups:
        group_payload = output_dict.get(group.fieldGroupName) or {}
        if not isinstance(group_payload, dict):
            group_payload = {}
        fields: list[ExtractedField] = []
        for spec in group.fieldGroupFields:
            field_payload = group_payload.get(spec.fieldName)
            if not isinstance(field_payload, dict):
                field_payload = {}
            if spec.fieldType == FieldType.ARRAY:
                fields.append(_array_from_payload(spec, field_payload))
            else:
                fields.append(_scalar_from_payload(spec, field_payload))
        groups.append(
            ExtractedFieldGroup(fieldGroupName=group.fieldGroupName, fieldGroupFields=fields)
        )
    return groups


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _clamp01(raw: Any) -> float:
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    if isinstance(raw, str) and raw.isdigit():
        value = int(raw)
        return value if value >= 1 else None
    return None


def _maybe_str(raw: Any) -> str | None:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
