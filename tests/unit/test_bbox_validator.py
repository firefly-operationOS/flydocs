# Copyright 2026 Firefly Software Solutions Inc
"""Unit tests for :class:`BboxValidator` -- geometric hallucination check.

Verifies the validator stamps the correct ``BboxQuality`` verdict and a
score in ``[0, 1]`` for every recognised pattern:

* ``empty``     -- ``None`` value or zero-area placeholder
* ``invalid``   -- corners outside ``[0, 1]`` (caught by pydantic) or
                    degenerate after construction
* ``suspicious``-- ~full-page boxes (LLM hallucinated a generic region)
* ``poor``      -- tiny boxes or extreme aspect ratios
* ``good``      -- plausible text-line bboxes
"""

from __future__ import annotations

from flydesk_idp.core.services.bbox import BboxValidator
from flydesk_idp.interfaces.dtos.bbox import BboxQuality, BoundingBox
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup


def _bbox(xmin: float, ymin: float, xmax: float, ymax: float) -> BoundingBox:
    return BoundingBox(xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax)


def _group(field: ExtractedField) -> ExtractedFieldGroup:
    return ExtractedFieldGroup(fieldGroupName="g", fieldGroupFields=[field])


def _validate(field: ExtractedField) -> ExtractedField:
    BboxValidator().validate_groups([_group(field)])
    return field


def test_good_bbox_for_plausible_text_line() -> None:
    field = ExtractedField(fieldName="name", fieldValueFound="John Doe", bbox=_bbox(0.10, 0.10, 0.30, 0.13))
    _validate(field)
    assert field.bbox.quality is BboxQuality.GOOD
    assert field.bbox.quality_score > 0.8


def test_suspicious_when_bbox_covers_almost_full_page() -> None:
    field = ExtractedField(
        fieldName="title", fieldValueFound="Some title", bbox=_bbox(0.01, 0.01, 0.99, 0.99)
    )
    _validate(field)
    assert field.bbox.quality is BboxQuality.SUSPICIOUS
    assert field.bbox.quality_score <= 0.3


def test_poor_when_bbox_is_microscopic() -> None:
    # Area = 1e-6 (well below _AREA_MIN = 5e-5).
    field = ExtractedField(fieldName="x", fieldValueFound="x", bbox=_bbox(0.5, 0.5, 0.501, 0.501))
    _validate(field)
    assert field.bbox.quality is BboxQuality.POOR


def test_poor_when_aspect_ratio_extreme() -> None:
    # 0.6 wide × 0.01 tall -> aspect 60, beyond _ASPECT_MAX = 30.
    field = ExtractedField(fieldName="bar", fieldValueFound="bar", bbox=_bbox(0.1, 0.5, 0.7, 0.51))
    _validate(field)
    assert field.bbox.quality is BboxQuality.POOR


def test_empty_when_field_value_is_none() -> None:
    field = ExtractedField(fieldName="missing", fieldValueFound=None, bbox=_bbox(0.1, 0.1, 0.2, 0.13))
    _validate(field)
    assert field.bbox.quality is BboxQuality.EMPTY
    assert field.bbox.quality_score == 0.0


def test_empty_when_bbox_is_zero_placeholder() -> None:
    field = ExtractedField(fieldName="placeholder", fieldValueFound="x", bbox=BoundingBox.empty())
    _validate(field)
    assert field.bbox.quality is BboxQuality.EMPTY
    assert field.bbox.quality_score == 0.0


def test_score_penalises_boxes_hugging_all_edges() -> None:
    """A box that hugs every page edge gets a lower score than an interior one."""
    hugging = ExtractedField(fieldName="a", fieldValueFound="a", bbox=_bbox(0.0, 0.0, 0.4, 0.05))
    interior = ExtractedField(fieldName="b", fieldValueFound="b", bbox=_bbox(0.2, 0.2, 0.6, 0.25))
    _validate(hugging)
    _validate(interior)
    assert interior.bbox.quality_score > hugging.bbox.quality_score


def test_recurses_into_array_field_rows() -> None:
    """Nested ExtractedFields inside an array should also get stamped."""
    child = ExtractedField(fieldName="line", fieldValueFound="42", bbox=_bbox(0.1, 0.2, 0.3, 0.22))
    parent = ExtractedField(
        fieldName="items",
        fieldValueFound=[child],
        bbox=_bbox(0.1, 0.1, 0.9, 0.3),
    )
    _validate(parent)
    assert parent.bbox.quality is BboxQuality.GOOD
    assert child.bbox.quality is BboxQuality.GOOD
