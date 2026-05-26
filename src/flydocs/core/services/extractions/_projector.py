# Copyright 2026 Firefly Software Solutions Inc
"""Shared helpers for projecting :class:`Extraction` rows onto the public DTO."""

from __future__ import annotations

from flydocs.interfaces.dtos.extraction import (
    BboxRefinementInfo,
    Extraction,
    ExtractionError,
    PostProcessing,
)
from flydocs.interfaces.enums.extraction_status import (
    ExtractionStatus,
    PostProcessingStatus,
)
from flydocs.models.entities.extraction import Extraction as ExtractionEntity


def row_to_extraction(row: ExtractionEntity) -> Extraction:
    """Project an :class:`ExtractionEntity` row onto an :class:`Extraction` DTO."""
    error: ExtractionError | None = None
    if row.error_code or row.error_message:
        error = ExtractionError(
            code=row.error_code or "unknown",
            message=row.error_message or "",
        )

    post_processing: PostProcessing | None = None
    if row.post_processing_bbox_status is not None:
        bbox_error: ExtractionError | None = None
        if row.post_processing_bbox_error_code or row.post_processing_bbox_error_message:
            bbox_error = ExtractionError(
                code=row.post_processing_bbox_error_code or "unknown",
                message=row.post_processing_bbox_error_message or "",
            )
        post_processing = PostProcessing(
            bbox_refinement=BboxRefinementInfo(
                status=PostProcessingStatus(row.post_processing_bbox_status),
                started_at=row.post_processing_bbox_started_at,
                finished_at=row.post_processing_bbox_finished_at,
                attempts=row.post_processing_bbox_attempts or 0,
                error=bbox_error,
            )
        )

    return Extraction(
        id=row.id,
        status=ExtractionStatus(row.status),
        submitted_at=row.submitted_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        attempts=row.attempts or 0,
        error=error,
        post_processing=post_processing,
    )


__all__ = ["row_to_extraction"]
