# Copyright 2026 Firefly Software Solutions Inc
"""Bounding box in normalised image-space coordinates.

All values are floats in [0, 1]. (0, 0) is the top-left of the rendered
page; (1, 1) is the bottom-right.

Absence of a bbox is represented by ``null`` at the consuming field site
(``ExtractedField.bbox = None``) — there is no synthetic "empty" placeholder
box in v1.

The :class:`BboxSource` discriminator tells callers how each coordinate set
was produced:

* ``llm``       -- multimodal model's visual estimate (default for every
                   first-pass extraction). Imprecise: lands in the right
                   region but is routinely off by a line or more.
* ``pdf_text``  -- grounded against the PDF's text layer via PyMuPDF.
                   Sub-pixel accurate.
* ``ocr``       -- grounded against an OCR word stream for image-PDFs and
                   raster inputs. Accuracy depends on the engine.

The refinement runs as the optional ``bbox_refine`` pipeline stage —
opt-in via ``ExtractionOptions.stages.bbox_refine`` — and replaces the
LLM coordinates with the tight word-union when a fuzzy match is found
above the configured threshold; otherwise the LLM bbox is kept,
tagged ``source=llm, refinement_confidence=null``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BboxQuality(StrEnum):
    """Coarse-grained verdict on whether a bbox is trustworthy."""

    GOOD = "good"
    POOR = "poor"
    SUSPICIOUS = "suspicious"
    INVALID = "invalid"


class BboxSource(StrEnum):
    """How the coordinates on this bbox were produced."""

    LLM = "llm"
    PDF_TEXT = "pdf_text"
    OCR = "ocr"


class BoundingBox(BaseModel):
    """Normalised rectangle on a single page."""

    model_config = ConfigDict(extra="forbid")

    xmin: float = Field(..., ge=0.0, le=1.0, description="Left edge in [0, 1].")
    ymin: float = Field(..., ge=0.0, le=1.0, description="Top edge in [0, 1].")
    xmax: float = Field(..., ge=0.0, le=1.0, description="Right edge in [0, 1].")
    ymax: float = Field(..., ge=0.0, le=1.0, description="Bottom edge in [0, 1].")
    quality: BboxQuality | None = None
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source: BboxSource | None = None
    refinement_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_corners(self) -> BoundingBox:
        if self.xmin >= self.xmax:
            raise ValueError("xmin must be strictly less than xmax")
        if self.ymin >= self.ymax:
            raise ValueError("ymin must be strictly less than ymax")
        return self
