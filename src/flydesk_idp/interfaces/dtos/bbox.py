# Copyright 2026 Firefly Software Solutions Inc
"""Bounding box in normalised image-space coordinates.

All values are floats in ``[0, 1]``. ``(0, 0)`` is the top-left of the
rendered page; ``(1, 1)`` is the bottom-right. The contract is enforced
both by the prompt sent to the LLM and by post-processing in
:mod:`flydesk_idp.core.services.extraction.postprocess`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class BoundingBox(BaseModel):
    """Normalised rectangle on a single page."""

    xmin: float = Field(..., ge=0.0, le=1.0, description="Left edge in [0, 1].")
    ymin: float = Field(..., ge=0.0, le=1.0, description="Top edge in [0, 1].")
    xmax: float = Field(..., ge=0.0, le=1.0, description="Right edge in [0, 1].")
    ymax: float = Field(..., ge=0.0, le=1.0, description="Bottom edge in [0, 1].")

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
        return cls(xmin=0.0, ymin=0.0, xmax=1e-9, ymax=1e-9)
