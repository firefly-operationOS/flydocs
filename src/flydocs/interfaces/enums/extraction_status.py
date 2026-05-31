# Copyright 2026 Firefly Software Solutions Inc
"""Async extraction lifecycle states.

One linear state machine: queued -> running -> succeeded | failed | cancelled.
Post-processing (bbox refinement today, more tomorrow) lives in a separate
block on the Extraction with its own PostProcessingStatus lifecycle.

Replaces the legacy JobStatus / BboxRefineStatus pair from v0. The two-phase
machine (PARTIAL_SUCCEEDED -> REFINING_BBOXES -> SUCCEEDED) is gone: a job
reaches "succeeded" the moment the main pipeline finishes, and bbox
refinement runs as an additive post-processing step that does not gate the
main lifecycle.
"""

from __future__ import annotations

from enum import StrEnum


class ExtractionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """True when no further state transition is expected for this extraction."""
        return self in (ExtractionStatus.SUCCEEDED, ExtractionStatus.FAILED, ExtractionStatus.CANCELLED)

    @property
    def has_result(self) -> bool:
        """True when the extraction carries a readable ExtractionResult.

        Only ``succeeded`` does. Refinement runs as additive post-processing
        on a fully-succeeded result, so there are no partial / refining
        result states.
        """
        return self is ExtractionStatus.SUCCEEDED


class PostProcessingStatus(StrEnum):
    """Sub-state for additive post-processing legs (bbox refinement today)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (PostProcessingStatus.SUCCEEDED, PostProcessingStatus.FAILED)
