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

"""Convert the raw LLM output for one :class:`DocumentTypeSpec` into a
stable list of :class:`ExtractedFieldGroup` -- value-coerced,
bbox-clamped, field-order preserved.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from flydocs.core.services.extraction.schema import clamp_bbox, coerce_scalar
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.field import (
    ExtractedField,
    ExtractedFieldGroup,
)
from flydocs.interfaces.dtos.field import (
    Field as FieldSpec,
)
from flydocs.interfaces.enums.field_type import FieldType


def _row_items(spec: FieldSpec) -> list[FieldSpec]:
    """Return the per-row sub-field specs for an array field.

    Mirrors ``schema._items_specs`` -- v1 :class:`Field` is recursive
    so arrays carry their row shape under ``items`` (typically a Field
    of type ``object`` whose ``fields`` list contains the columns).
    """
    if spec.items is None:
        return []
    items_field = spec.items
    if items_field.type == FieldType.OBJECT and items_field.fields:
        return list(items_field.fields)
    return [items_field]


def _scalar_from_payload(spec: FieldSpec, payload: dict[str, Any]) -> ExtractedField:
    value = coerce_scalar(spec.type, payload.get("value"))
    confidence = _clamp01(payload.get("confidence", 0.0))
    page = _int_or_none(payload.get("page"))
    bbox = clamp_bbox(payload.get("bbox"))
    notes = _maybe_str(payload.get("notes"))

    if value is None:
        page = None
        bbox = None

    pages: list[int] = [page] if page is not None else []
    return ExtractedField(
        name=spec.name,
        value=value,
        pages=pages,
        confidence=confidence,
        bbox=bbox,
        notes=notes,
    )


def _array_from_payload(spec: FieldSpec, payload: dict[str, Any]) -> ExtractedField:
    rows = payload.get("rows", []) or []
    coerced_rows: list[ExtractedField] = []
    page_set: set[int] = set()

    items = _row_items(spec)
    for row_idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        sub_fields: list[ExtractedField] = []
        for item in items:
            item_payload = row.get(item.name)
            if not isinstance(item_payload, dict):
                sub_fields.append(ExtractedField(name=item.name, value=None))
                continue
            sub_field = _scalar_from_payload(item, item_payload)
            sub_fields.append(sub_field)
            page_set.update(sub_field.pages)
        coerced_rows.append(
            ExtractedField(
                name=f"row_{row_idx + 1}",
                value=sub_fields,
                pages=sorted(page_set),
            )
        )

    pages_from_payload = payload.get("pages") or []
    pages = sorted({int(p) for p in pages_from_payload if isinstance(p, int) and p >= 1} | page_set)
    return ExtractedField(
        name=spec.name,
        value=coerced_rows,
        pages=pages,
        confidence=_clamp01(payload.get("confidence", 0.0)),
        notes=_maybe_str(payload.get("notes")),
    )


def normalise_doc(raw_output: BaseModel, doc: DocumentTypeSpec) -> list[ExtractedFieldGroup]:
    """Build the public list of :class:`ExtractedFieldGroup` for one document type."""
    output_dict = raw_output.model_dump(by_alias=True)
    groups: list[ExtractedFieldGroup] = []
    for group in doc.field_groups:
        group_payload = output_dict.get(group.name) or {}
        if not isinstance(group_payload, dict):
            group_payload = {}
        fields: list[ExtractedField] = []
        for spec in group.fields:
            field_payload = group_payload.get(spec.name)
            if not isinstance(field_payload, dict):
                field_payload = {}
            if spec.type == FieldType.ARRAY:
                fields.append(_array_from_payload(spec, field_payload))
            else:
                fields.append(_scalar_from_payload(spec, field_payload))
        groups.append(ExtractedFieldGroup(name=group.name, fields=fields))
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
