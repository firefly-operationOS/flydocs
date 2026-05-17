# Copyright 2026 Firefly Software Solutions Inc
"""Bounding box in normalised image-space coordinates.

All values are floats in ``[0, 1]``. ``(0, 0)`` is the top-left of the
rendered page; ``(1, 1)`` is the bottom-right. The contract is enforced
both by the prompt sent to the LLM and by post-processing in
:mod:`flydocs.core.services.extraction.postprocess`.

The :class:`BboxSource` discriminator tells callers how each coordinate
set was produced:

* ``llm``       -- the multimodal model's visual estimate (default for
                   every extraction). Imprecise: the box lands in the
                   right region of the page but is routinely off by a
                   line or more.
* ``pdf_text``  -- grounded against the PDF's text layer via PyMuPDF.
                   Sub-pixel accurate.
* ``ocr``       -- grounded against an OCR word stream (PaddleOCR /
                   Mistral OCR / etc.) for image-PDFs and raster
                   inputs. Accuracy depends on the engine.
* ``none``      -- no bbox was produced (placeholder field).

The refinement runs as the optional ``bbox_refine`` pipeline stage --
opt-in via ``ExtractionOptions.stages.bbox_refine`` -- and replaces the
LLM coordinates with the tight word-union when a fuzzy match is found
above the configured threshold; otherwise the LLM bbox is kept,
tagged ``source=llm, refinement_confidence=null``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class BboxQuality(StrEnum):
    """Coarse-grained verdict on whether a bbox is trustworthy.

    Stamped by :class:`BboxValidator` after extraction. ``invalid``
    means the box is geometrically broken or the field has no value.
    ``empty`` is the placeholder for fields the LLM didn't locate.
    """

    GOOD = "good"
    POOR = "poor"
    SUSPICIOUS = "suspicious"
    INVALID = "invalid"
    EMPTY = "empty"


class BboxSource(StrEnum):
    """How the coordinates on this bbox were produced.

    See the module docstring for the full lifecycle. The discriminator
    lets strict callers filter to grounded-only boxes (``pdf_text`` /
    ``ocr``) and treat ``llm`` boxes as approximate region hints.
    """

    LLM = "llm"
    PDF_TEXT = "pdf_text"
    OCR = "ocr"
    NONE = "none"


class BoundingBox(BaseModel):
    """Normalised rectangle on a single page."""

    xmin: float = Field(..., ge=0.0, le=1.0, description="Left edge in [0, 1].")
    ymin: float = Field(..., ge=0.0, le=1.0, description="Top edge in [0, 1].")
    xmax: float = Field(..., ge=0.0, le=1.0, description="Right edge in [0, 1].")
    ymax: float = Field(..., ge=0.0, le=1.0, description="Bottom edge in [0, 1].")
    quality: BboxQuality | None = Field(
        default=None,
        description=(
            "Geometric verdict on whether the bbox looks plausibly real "
            "or like an LLM hallucination. Populated by the bbox "
            "validator that runs at the end of the pipeline; ``null`` "
            "means the validator hasn't run for this field yet."
        ),
    )
    quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Continuous geometric quality score in ``[0, 1]``. Combines "
            "area, aspect ratio, and edge sanity. 0.0 for empty / "
            "missing boxes; ~1.0 for boxes that fall in a plausible "
            "text-bounding region. **Does NOT mean the box actually "
            "fences the real text** -- it only means the shape is "
            "plausible. See the module docstring on LLM bbox imprecision."
        ),
    )
    source: BboxSource | None = Field(
        default=None,
        description=(
            "Origin of the coordinates: ``llm`` (multimodal model "
            "estimate, imprecise), ``pdf_text`` (grounded via PyMuPDF "
            "against the PDF text layer, sub-pixel accurate), ``ocr`` "
            "(grounded via an OCR engine for image-PDFs and rasters), "
            "or ``none`` (no bbox produced). ``null`` means the bbox "
            "refiner has not run for this field yet."
        ),
    )
    refinement_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Fuzzy-match score from the bbox refiner in ``[0, 1]``. "
            "Populated when ``source in {pdf_text, ocr}`` and the "
            "matcher located the value above its threshold. ``null`` "
            "for ``source in {llm, none}`` (the LLM doesn't expose "
            "a comparable score)."
        ),
    )

    @model_validator(mode="after")
    def _validate_corners(self) -> BoundingBox:
        if self.xmin >= self.xmax:
            raise ValueError("xmin must be strictly less than xmax")
        if self.ymin >= self.ymax:
            raise ValueError("ymin must be strictly less than ymax")
        return self

    @classmethod
    def empty(cls) -> BoundingBox:
        """A degenerate placeholder used when a field is not found.

        Returning an explicit zero-area box keeps the response schema
        stable: every field carries the same shape whether or not it
        was located in the document.
        """
        # Use 0..eps so the post-validator does not reject it.
        return cls(
            xmin=0.0,
            ymin=0.0,
            xmax=1e-9,
            ymax=1e-9,
            quality=BboxQuality.EMPTY,
            quality_score=0.0,
            source=BboxSource.NONE,
        )
