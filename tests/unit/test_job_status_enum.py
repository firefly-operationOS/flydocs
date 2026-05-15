# Copyright 2026 Firefly Software Solutions Inc
"""``JobStatus`` semantic predicates -- terminal + has_result invariants."""

from __future__ import annotations

import pytest

from flydesk_idp.interfaces.enums.job_status import BboxRefineStatus, JobStatus


@pytest.mark.parametrize(
    "status",
    [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED],
)
def test_terminal_statuses(status: JobStatus) -> None:
    assert status.is_terminal


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.PARTIAL_SUCCEEDED,
        JobStatus.REFINING_BBOXES,
    ],
)
def test_non_terminal_statuses(status: JobStatus) -> None:
    assert not status.is_terminal


@pytest.mark.parametrize(
    "status",
    [JobStatus.SUCCEEDED, JobStatus.PARTIAL_SUCCEEDED, JobStatus.REFINING_BBOXES],
)
def test_statuses_with_readable_result(status: JobStatus) -> None:
    assert status.has_result


@pytest.mark.parametrize(
    "status",
    [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED],
)
def test_statuses_without_readable_result(status: JobStatus) -> None:
    assert not status.has_result


def test_bbox_refine_status_values() -> None:
    # Stable wire values -- the migration + repository depend on these strings.
    assert BboxRefineStatus.PENDING.value == "pending"
    assert BboxRefineStatus.RUNNING.value == "running"
    assert BboxRefineStatus.SUCCEEDED.value == "succeeded"
    assert BboxRefineStatus.FAILED.value == "failed"
