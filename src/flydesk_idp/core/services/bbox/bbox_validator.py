# Copyright 2026 Firefly Software Solutions Inc
"""``BboxValidator`` -- geometric hallucination check on extracted bboxes.

The extractor asks the LLM to return a normalised bounding box per
field, but the LLM CAN make up coordinates that don't actually fence
any text. The validator runs after extraction and stamps each box
with a :class:`BboxQuality` verdict plus a continuous
``quality_score`` in ``[0, 1]``.

We deliberately stay client-side and OCR-free: every check is pure
geometry. If the score is low the caller can route the field to manual
review or trigger an LLM-based bbox verification step (future work).

Heuristics, in order of priority (each maps to a verdict and a
weighted contribution to the score):

* ``empty`` -- the field has no extracted value, or the bbox is the
  zero placeholder ``BoundingBox.empty()``. Score 0.0.
* ``invalid`` -- corners outside ``[0, 1]``, degenerate (xmin >= xmax
  or ymin >= ymax), area exactly 0. Score 0.0.
* ``suspicious`` -- area too big to plausibly fence a single value:
  > 70% of the page, or covers > 90% horizontally / vertically. These
  patterns are typical of LLM hallucinations where the model "guesses"
  a generic page region.
* ``poor`` -- area too small (< 0.00005, roughly < 5px at 1000px width)
  or extreme aspect ratio (height/width > 30 or < 1/30).
* ``good`` -- everything else.

The score combines:

* ``area_score`` (1.0 inside [1e-4, 0.5], decays outside),
* ``aspect_score`` (1.0 for plausible text-line aspect ratios),
* ``margin_score`` (penalises boxes that exactly hug the page edges,
  which is another hallucination signal).
"""

from __future__ import annotations

import logging

from flydesk_idp.interfaces.dtos.bbox import BboxQuality, BoundingBox
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup

logger = logging.getLogger(__name__)

# Tunable thresholds. The defaults are conservative and chosen so the
# real-world notarial-deed run -- where every bbox covers ~3% of the
# page -- comes out as ``good``.
_AREA_MIN = 5e-5            # roughly 5px × 5px at 1000px width
_AREA_TYP_MIN = 1e-4
_AREA_TYP_MAX = 0.5
_AREA_SUSPICIOUS = 0.7
_ASPECT_MIN = 1.0 / 30.0    # very narrow
_ASPECT_MAX = 30.0          # very wide
_EDGE_EPSILON = 1e-3        # tolerance for "exactly on the edge"


class BboxValidator:
    """Stamp a quality verdict + score on every extracted field's bbox."""

    def validate_groups(self, groups: list[ExtractedFieldGroup]) -> None:
        """Mutate every field's ``bbox`` in place with quality + score."""
        for group in groups:
            for field in group.fieldGroupFields:
                self._validate_field(field)

    def _validate_field(self, field: ExtractedField) -> None:
        bbox = field.bbox
        if bbox is None:
            return
        # Recurse into nested rows for array fields.
        if isinstance(field.fieldValueFound, list):
            for child in field.fieldValueFound:
                if isinstance(child, ExtractedField):
                    self._validate_field(child)
                    if isinstance(child.fieldValueFound, list):
                        for sub in child.fieldValueFound:
                            if isinstance(sub, ExtractedField):
                                self._validate_field(sub)
        if field.fieldValueFound is None:
            self._stamp(bbox, BboxQuality.EMPTY, 0.0)
            return
        if _is_zero_placeholder(bbox):
            self._stamp(bbox, BboxQuality.EMPTY, 0.0)
            return
        verdict, score = _classify(bbox)
        self._stamp(bbox, verdict, score)

    @staticmethod
    def _stamp(bbox: BoundingBox, verdict: BboxQuality, score: float) -> None:
        # Pydantic models built from already-validated data accept direct
        # attribute assignment.
        bbox.quality = verdict
        bbox.quality_score = round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# Geometric helpers
# ---------------------------------------------------------------------------


def _is_zero_placeholder(bbox: BoundingBox) -> bool:
    return bbox.xmax - bbox.xmin <= 2e-9 and bbox.ymax - bbox.ymin <= 2e-9


def _classify(bbox: BoundingBox) -> tuple[BboxQuality, float]:
    width = bbox.xmax - bbox.xmin
    height = bbox.ymax - bbox.ymin
    area = width * height
    aspect = width / height if height > 0 else 0.0

    if width <= 0.0 or height <= 0.0 or area <= 0.0:
        return BboxQuality.INVALID, 0.0
    if area > _AREA_SUSPICIOUS or width > 0.9 or height > 0.9:
        # Almost-full-page boxes are a classic hallucination signal.
        return BboxQuality.SUSPICIOUS, 0.2
    if area < _AREA_MIN or aspect > _ASPECT_MAX or aspect < _ASPECT_MIN:
        return BboxQuality.POOR, 0.4

    area_score = _area_score(area)
    aspect_score = _aspect_score(aspect)
    margin_score = _margin_score(bbox)
    score = 0.5 * area_score + 0.3 * aspect_score + 0.2 * margin_score
    return BboxQuality.GOOD, score


def _area_score(area: float) -> float:
    """1.0 inside the typical text-bbox range, decays smoothly outside."""
    if _AREA_TYP_MIN <= area <= _AREA_TYP_MAX:
        return 1.0
    if area < _AREA_TYP_MIN:
        # Linear decay from 1.0 (at AREA_TYP_MIN) to 0.0 (at AREA_MIN).
        if area <= _AREA_MIN:
            return 0.0
        return (area - _AREA_MIN) / (_AREA_TYP_MIN - _AREA_MIN)
    # Above AREA_TYP_MAX: linear decay to 0.0 at AREA_SUSPICIOUS.
    if area >= _AREA_SUSPICIOUS:
        return 0.0
    return 1.0 - (area - _AREA_TYP_MAX) / (_AREA_SUSPICIOUS - _AREA_TYP_MAX)


def _aspect_score(aspect: float) -> float:
    """Reward typical text-line / value aspect ratios (wide-rectangle)."""
    if 0.05 <= aspect <= 25.0:
        return 1.0
    if aspect < 0.05:
        return aspect / 0.05
    return max(0.0, 1.0 - (aspect - 25.0) / 30.0)


def _margin_score(bbox: BoundingBox) -> float:
    """Penalise boxes that exactly hug the page edges (hallucination signal)."""
    hugs = 0
    if bbox.xmin <= _EDGE_EPSILON:
        hugs += 1
    if bbox.ymin <= _EDGE_EPSILON:
        hugs += 1
    if bbox.xmax >= 1.0 - _EDGE_EPSILON:
        hugs += 1
    if bbox.ymax >= 1.0 - _EDGE_EPSILON:
        hugs += 1
    return max(0.0, 1.0 - hugs * 0.25)
