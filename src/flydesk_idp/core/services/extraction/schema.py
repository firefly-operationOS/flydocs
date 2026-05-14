# Copyright 2026 Firefly Software Solutions Inc
"""Dynamic Pydantic models built from a :class:`DocSpec`.

We build a fresh model per request because every caller's schema
differs. Each field becomes a sub-model carrying ``value, confidence,
page, bbox, notes``; arrays carry nested rows of sub-fields. The
result of ``agent.run(...)`` deserialises directly into this model so
downstream code never has to JSON-parse.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from flydesk_idp.interfaces.dtos.bbox import BoundingBox
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.field import FieldItem, FieldSpec
from flydesk_idp.interfaces.enums.field_type import FieldType


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
        value=(_python_type(field_type), Field(default=None)),
        confidence=(float, Field(default=0.0, ge=0.0, le=1.0)),
        page=(int | None, Field(default=None, ge=1)),
        bbox=(_RawBBox, Field(default_factory=_RawBBox)),
        notes=(str | None, Field(default=None)),
        __config__=ConfigDict(extra="ignore"),  # type: ignore[arg-type]
    )


def _row_model(items: list[FieldItem]) -> type[BaseModel]:
    """Build the schema for one row of an array field."""
    fields: dict[str, Any] = {}
    used: set[str] = set()
    for item in items:
        attr = _safe_attr(item.fieldName)
        while attr in used:
            attr = f"{attr}_"
        used.add(attr)
        fields[attr] = (
            _scalar_field_model(item.fieldName, item.fieldType),
            Field(default_factory=_scalar_field_model(item.fieldName, item.fieldType), alias=item.fieldName),
        )
    config = ConfigDict(populate_by_name=True, extra="ignore")
    return create_model("ArrayRow", __config__=config, **fields)  # type: ignore[call-overload]


def _array_field_model(spec: FieldSpec) -> type[BaseModel]:
    row_cls = _row_model(spec.items or [])
    return create_model(
        f"Array_{_safe_attr(spec.fieldName)}",
        rows=(list[row_cls], Field(default_factory=list)),  # type: ignore[valid-type]
        pagesFound=(list[int], Field(default_factory=list)),
        confidence=(float, Field(default=0.0, ge=0.0, le=1.0)),
        notes=(str | None, Field(default=None)),
        __config__=ConfigDict(extra="ignore"),  # type: ignore[arg-type]
    )


def build_field_group_model(group_name: str, specs: list[FieldSpec]) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    used: set[str] = set()
    for spec in specs:
        attr = _safe_attr(spec.fieldName)
        while attr in used:
            attr = f"{attr}_"
        used.add(attr)
        if spec.fieldType == FieldType.ARRAY:
            sub_cls = _array_field_model(spec)
        else:
            sub_cls = _scalar_field_model(spec.fieldName, spec.fieldType)
        fields[attr] = (sub_cls, Field(default_factory=sub_cls, alias=spec.fieldName))
    config = ConfigDict(populate_by_name=True, extra="ignore")
    return create_model(f"Group_{_safe_attr(group_name)}", __config__=config, **fields)  # type: ignore[call-overload]


def build_extraction_output_model(doc: DocSpec) -> type[BaseModel]:
    """Produce the dynamic output model the LLM must return for a single doc."""
    groups: dict[str, Any] = {}
    used: set[str] = set()
    for group in doc.fieldGroups:
        attr = _safe_attr(group.fieldGroupName, prefix="g")
        while attr in used:
            attr = f"{attr}_"
        used.add(attr)
        sub_cls = build_field_group_model(group.fieldGroupName, group.fieldGroupFields)
        groups[attr] = (sub_cls, Field(default_factory=sub_cls, alias=group.fieldGroupName))
    config = ConfigDict(populate_by_name=True, extra="ignore")
    return create_model(
        f"ExtractionOutput_{_safe_attr(doc.docType.documentType)}",
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


def clamp_bbox(box: _RawBBox | dict[str, Any] | None) -> BoundingBox:
    if box is None:
        return BoundingBox.empty()
    if isinstance(box, dict):
        try:
            box = _RawBBox.model_validate(box)
        except Exception:  # noqa: BLE001
            return BoundingBox.empty()
    xmin = max(0.0, min(1.0, float(getattr(box, "xmin", 0.0))))
    ymin = max(0.0, min(1.0, float(getattr(box, "ymin", 0.0))))
    xmax = max(0.0, min(1.0, float(getattr(box, "xmax", 0.0))))
    ymax = max(0.0, min(1.0, float(getattr(box, "ymax", 0.0))))
    if xmin >= xmax or ymin >= ymax:
        return BoundingBox.empty()
    return BoundingBox(xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax)
