# Copyright 2026 Firefly Software Solutions Inc
"""Async-job lifecycle states.

Two state machines live in parallel:

**Default flow** (``options.stages.bbox_refine == false``)::

    QUEUED -> RUNNING -> SUCCEEDED | FAILED
    QUEUED -> CANCELLED   (only while still QUEUED)

**Bbox-refine flow** (``options.stages.bbox_refine == true``)::

    QUEUED -> RUNNING -> PARTIAL_SUCCEEDED -> REFINING_BBOXES -> SUCCEEDED
                                          \\-> (stays PARTIAL_SUCCEEDED if
                                               bbox refine fails -- the
                                               LLM-bbox result is still
                                               readable; bbox_refine_status
                                               column carries the failure)

A job that has already started cannot be cancelled. ``PARTIAL_SUCCEEDED``
results are queryable via ``GET /api/v1/jobs/{id}/result`` -- they carry
the full extraction with LLM-estimated bboxes; the grounded bboxes land
once the bbox refiner finishes and the status transitions to ``SUCCEEDED``.
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PARTIAL_SUCCEEDED = "PARTIAL_SUCCEEDED"
    REFINING_BBOXES = "REFINING_BBOXES"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        """True when no further state transition is expected for this job."""
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def has_result(self) -> bool:
        """True when the job carries a readable ExtractionResult.

        ``PARTIAL_SUCCEEDED`` and ``REFINING_BBOXES`` are readable too --
        the LLM-bbox version of the result is already persisted; bbox
        grounding is an additive overlay that lands later.
        """
        return self in (
            JobStatus.PARTIAL_SUCCEEDED,
            JobStatus.REFINING_BBOXES,
            JobStatus.SUCCEEDED,
        )


class BboxRefineStatus(StrEnum):
    """Sub-state for the out-of-band bbox refinement leg.

    Populated only when ``options.stages.bbox_refine == true``. ``null``
    on the job row means the refiner was never requested (default flow).
    """

    PENDING = "pending"  # event published, worker has not picked it up
    RUNNING = "running"  # worker is grounding bboxes right now
    SUCCEEDED = "succeeded"  # bboxes grounded, job is now ``SUCCEEDED``
    FAILED = "failed"  # refiner failed; job stays ``PARTIAL_SUCCEEDED``
