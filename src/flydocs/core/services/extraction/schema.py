# Copyright 2026 Firefly Software Solutions Inc
"""Dynamic Pydantic models built from a :class:`DocumentTypeSpec`.

We build a fresh model per request because every caller's schema
differs. Each field becomes a sub-model carrying ``value, confidence,
page, bbox, notes``; arrays carry nested rows of sub-fields. The
result of ``agent.run(...)`` deserialises directly into this model so
downstream code never has to JSON-parse.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model
from pydantic import Field as _PydField

from flydocs.interfaces.dtos.bbox import BoundingBox
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.field import Field as FieldSpec
from flydocs.interfaces.enums.field_type import FieldType


def _safe_attr(name: str, prefix: str = "f") -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name).strip("_") or prefix
    if cleaned[0].isdigit():
        cleaned = f"{prefix}_{cleaned}"
    return cleaned


def _python_type(field_type: FieldType) -> Any:
    if field_type == FieldType.STRING:
        return str | None
    if field_type == FieldType.NUMBER:
        return float | None
    if field_type == FieldType.INTEGER:
        return int | None
    if field_type == FieldType.BOOLEAN:
        return bool | None
    return Any  # arrays handled separately below


class _RawBBox(BaseModel):
    """Permissive bbox the LLM fills -- clamped to a real :class:`BoundingBox` in post-process."""

    page: int = 1
    xmin: float = 0.0
    ymin: float = 0.0
    xmax: float = 0.0
    ymax: float = 0.0


def _scalar_field_model(name: str, field_type: FieldType) -> type[BaseModel]:
    """Build the schema for a primitive field."""
    return create_model(
        f"Scalar_{_safe_attr(name)}",
        value=(_python_type(field_type), _PydField(default=None)),
        confidence=(float, _PydField(default=0.0, ge=0.0, le=1.0)),
        page=(int | None, _PydField(default=None, ge=1)),
        bbox=(_RawBBox, _PydField(default_factory=_RawBBox)),
        notes=(str | None, _PydField(default=None)),
        __config__=ConfigDict(extra="ignore"),  # type: ignore[arg-type]
    )


def _items_specs(spec: FieldSpec) -> list[FieldSpec]:
    """Return the per-row sub-field specs for an array field.

    In v1 :class:`Field` is recursive: an array's row shape lives in
    ``items`` (a single Field, typically of type ``object``). The
    object's sub-fields live in ``items.fields``. This helper unwraps
    both layers and returns the row's sub-field specs in declaration
    order. Returns ``[]`` when the array doesn't declare a row schema.
    """
    if spec.items is None:
        return []
    items_field = spec.items
    if items_field.type == FieldType.OBJECT and items_field.fields:
        return list(items_field.fields)
    # Allow a single recursive primitive items definition for simple
    # arrays — its own ``name`` becomes the lone column name.
    return [items_field]


def _row_model(items: list[FieldSpec]) -> type[BaseModel]:
    """Build the schema for one row of an array field."""
    fields: dict[str, Any] = {}
    used: set[str] = set()
    for item in items:
        attr = _safe_attr(item.name)
        while attr in used:
            attr = f"{attr}_"
        used.add(attr)
        fields[attr] = (
            _scalar_field_model(item.name, item.type),
            _PydField(
                default_factory=_scalar_field_model(item.name, item.type),
                alias=item.name,
            ),
        )
    config = ConfigDict(populate_by_name=True, extra="ignore")
    return create_model("ArrayRow", __config__=config, **fields)  # type: ignore[call-overload]


def _array_field_model(spec: FieldSpec) -> type[BaseModel]:
    row_cls = _row_model(_items_specs(spec))
    return create_model(
        f"Array_{_safe_attr(spec.name)}",
        rows=(list[row_cls], _PydField(default_factory=list)),  # type: ignore[valid-type]
        pages=(list[int], _PydField(default_factory=list)),
        confidence=(float, _PydField(default=0.0, ge=0.0, le=1.0)),
        notes=(str | None, _PydField(default=None)),
        __config__=ConfigDict(extra="ignore"),  # type: ignore[arg-type]
    )


def build_field_group_model(group_name: str, specs: list[FieldSpec]) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    used: set[str] = set()
    for spec in specs:
        attr = _safe_attr(spec.name)
        while attr in used:
            attr = f"{attr}_"
        used.add(attr)
        if spec.type == FieldType.ARRAY:
            sub_cls = _array_field_model(spec)
        else:
            sub_cls = _scalar_field_model(spec.name, spec.type)
        fields[attr] = (sub_cls, _PydField(default_factory=sub_cls, alias=spec.name))
    config = ConfigDict(populate_by_name=True, extra="ignore")
    return create_model(f"Group_{_safe_attr(group_name)}", __config__=config, **fields)  # type: ignore[call-overload]


def build_extraction_output_model(doc: DocumentTypeSpec) -> type[BaseModel]:
    """Produce the dynamic output model the LLM must return for a single document type."""
    groups: dict[str, Any] = {}
    used: set[str] = set()
    for group in doc.field_groups:
        attr = _safe_attr(group.name, prefix="g")
        while attr in used:
            attr = f"{attr}_"
        used.add(attr)
        sub_cls = build_field_group_model(group.name, group.fields)
        groups[attr] = (sub_cls, _PydField(default_factory=sub_cls, alias=group.name))
    config = ConfigDict(populate_by_name=True, extra="ignore")
    return create_model(
        f"ExtractionOutput_{_safe_attr(doc.id)}",
        __config__=config,
        **groups,
    )


# ---------------------------------------------------------------------------
# Value coercion / bbox clamping helpers (used by postprocess)
# ---------------------------------------------------------------------------


def coerce_scalar(field_type: FieldType, raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    try:
        if field_type == FieldType.STRING:
            return str(raw)
        if field_type == FieldType.NUMBER:
            return float(raw)
        if field_type == FieldType.INTEGER:
            return int(float(raw))
        if field_type == FieldType.BOOLEAN:
            if isinstance(raw, str):
                return raw.strip().lower() in ("true", "yes", "1", "t", "y")
            return bool(raw)
    except (ValueError, TypeError):
        return None
    return raw


def clamp_bbox(box: _RawBBox | dict[str, Any] | None) -> BoundingBox | None:
    """Clamp raw LLM bbox coords into a real :class:`BoundingBox`.

    Returns ``None`` for degenerate / missing / zero-area inputs --
    consumers attach ``None`` to ``ExtractedField.bbox`` in v1 instead
    of carrying a synthetic ``empty`` placeholder.
    """
    if box is None:
        return None
    if isinstance(box, dict):
        try:
            box = _RawBBox.model_validate(box)
        except Exception:  # noqa: BLE001
            return None
    xmin = max(0.0, min(1.0, float(getattr(box, "xmin", 0.0))))
    ymin = max(0.0, min(1.0, float(getattr(box, "ymin", 0.0))))
    xmax = max(0.0, min(1.0, float(getattr(box, "xmax", 0.0))))
    ymax = max(0.0, min(1.0, float(getattr(box, "ymax", 0.0))))
    if xmin >= xmax or ymin >= ymax:
        return None
    return BoundingBox(xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax)
