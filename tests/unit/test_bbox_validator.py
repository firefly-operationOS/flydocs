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

"""Unit tests for :class:`BboxValidator` -- geometric hallucination check.

Verifies the validator stamps the correct ``BboxQuality`` verdict and a
score in ``[0, 1]`` for every recognised pattern:

* ``None``      -- value-less or zero-area placeholder bboxes get cleared
* ``invalid``   -- corners outside ``[0, 1]`` (caught by pydantic) or
                    degenerate after construction
* ``suspicious``-- ~full-page boxes (LLM hallucinated a generic region)
* ``poor``      -- tiny boxes or extreme aspect ratios
* ``good``      -- plausible text-line bboxes
"""

from __future__ import annotations

from flydocs.core.services.bbox import BboxValidator
from flydocs.interfaces.dtos.bbox import BboxQuality, BoundingBox
from flydocs.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup


def _bbox(xmin: float, ymin: float, xmax: float, ymax: float) -> BoundingBox:
    return BoundingBox(xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax)


def _group(field: ExtractedField) -> ExtractedFieldGroup:
    return ExtractedFieldGroup(name="g", fields=[field])


def _validate(field: ExtractedField) -> ExtractedField:
    BboxValidator().validate_groups([_group(field)])
    return field


def test_good_bbox_for_plausible_text_line() -> None:
    field = ExtractedField(name="name", value="John Doe", bbox=_bbox(0.10, 0.10, 0.30, 0.13))
    _validate(field)
    assert field.bbox is not None
    assert field.bbox.quality is BboxQuality.GOOD
    assert field.bbox.quality_score > 0.8


def test_suspicious_when_bbox_covers_almost_full_page() -> None:
    field = ExtractedField(name="title", value="Some title", bbox=_bbox(0.01, 0.01, 0.99, 0.99))
    _validate(field)
    assert field.bbox is not None
    assert field.bbox.quality is BboxQuality.SUSPICIOUS
    assert field.bbox.quality_score <= 0.3


def test_poor_when_bbox_is_microscopic() -> None:
    # Area = 1e-6 (well below _AREA_MIN = 5e-5).
    field = ExtractedField(name="x", value="x", bbox=_bbox(0.5, 0.5, 0.501, 0.501))
    _validate(field)
    assert field.bbox is not None
    assert field.bbox.quality is BboxQuality.POOR


def test_poor_when_aspect_ratio_extreme() -> None:
    # 0.6 wide × 0.01 tall -> aspect 60, beyond _ASPECT_MAX = 30.
    field = ExtractedField(name="bar", value="bar", bbox=_bbox(0.1, 0.5, 0.7, 0.51))
    _validate(field)
    assert field.bbox is not None
    assert field.bbox.quality is BboxQuality.POOR


def test_bbox_cleared_when_field_value_is_none() -> None:
    """v1 represents 'no bbox' as ``bbox=None`` -- there's no EMPTY verdict."""
    field = ExtractedField(name="missing", value=None, bbox=_bbox(0.1, 0.1, 0.2, 0.13))
    _validate(field)
    assert field.bbox is None


def test_score_penalises_boxes_hugging_all_edges() -> None:
    """A box that hugs every page edge gets a lower score than an interior one."""
    hugging = ExtractedField(name="a", value="a", bbox=_bbox(0.0, 0.0, 0.4, 0.05))
    interior = ExtractedField(name="b", value="b", bbox=_bbox(0.2, 0.2, 0.6, 0.25))
    _validate(hugging)
    _validate(interior)
    assert hugging.bbox is not None and interior.bbox is not None
    assert interior.bbox.quality_score > hugging.bbox.quality_score


def test_recurses_into_array_field_rows() -> None:
    """Nested ExtractedFields inside an array should also get stamped."""
    child = ExtractedField(name="line", value="42", bbox=_bbox(0.1, 0.2, 0.3, 0.22))
    parent = ExtractedField(
        name="items",
        value=[child],
        bbox=_bbox(0.1, 0.1, 0.9, 0.3),
    )
    _validate(parent)
    assert parent.bbox is not None and parent.bbox.quality is BboxQuality.GOOD
    assert child.bbox is not None and child.bbox.quality is BboxQuality.GOOD
