# Copyright 2026 Firefly Software Solutions Inc
""":class:`ListJobsHandler` -- pagination + filter contract.

The handler delegates to ``ExtractionJobRepository.list_jobs``; here
we mock the repository and assert (a) the right filter args travel
through, (b) the row mapping into :class:`JobStatusResponse` is
faithful, and (c) ``total`` reflects the filtered set independent of
``limit``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flydesk_idp.core.services.jobs.list_jobs_handler import (
    ListJobsHandler,
    ListJobsQuery,
)
from flydesk_idp.interfaces.enums.job_status import JobStatus


def _row(**overrides):
    base = {
        "id": "job-1",
        "status": "SUCCEEDED",
        "created_at": datetime(2026, 5, 15, 10, 0, tzinfo=UTC),
        "started_at": datetime(2026, 5, 15, 10, 0, 1, tzinfo=UTC),
        "finished_at": datetime(2026, 5, 15, 10, 1, tzinfo=UTC),
        "attempts": 1,
        "error_code": None,
        "error_message": None,
        "bbox_refine_status": None,
        "bbox_refine_attempts": 0,
        "bbox_refine_started_at": None,
        "bbox_refine_finished_at": None,
        "bbox_refine_error_code": None,
        "bbox_refine_error_message": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_passes_filters_through_and_maps_rows() -> None:
    repository = MagicMock()
    repository.list_jobs = AsyncMock(
        return_value=(
            [
                _row(id="job-1", status="SUCCEEDED"),
                _row(
                    id="job-2",
                    status="PARTIAL_SUCCEEDED",
                    bbox_refine_status="pending",
                ),
            ],
            42,  # total across the filter
        )
    )
    handler = ListJobsHandler(repository=repository)

    response = await handler.do_handle(
        ListJobsQuery(
            statuses=(JobStatus.SUCCEEDED, JobStatus.PARTIAL_SUCCEEDED),
            bbox_refine_statuses=("pending",),
            created_after=datetime(2026, 5, 15, tzinfo=UTC),
            limit=2,
            offset=0,
        )
    )

    repository.list_jobs.assert_awaited_once()
    kwargs = repository.list_jobs.await_args.kwargs
    assert kwargs["statuses"] == ["SUCCEEDED", "PARTIAL_SUCCEEDED"]
    assert kwargs["bbox_refine_statuses"] == ["pending"]
    assert kwargs["limit"] == 2
    assert kwargs["offset"] == 0

    assert response.total == 42  # filtered total, not limited
    assert response.limit == 2
    assert response.offset == 0
    assert [i.job_id for i in response.items] == ["job-1", "job-2"]
    assert response.items[0].status is JobStatus.SUCCEEDED
    assert response.items[1].status is JobStatus.PARTIAL_SUCCEEDED
    assert response.items[1].bbox_refine_status == "pending"


@pytest.mark.asyncio
async def test_empty_filter_lists_passes_none_to_repository() -> None:
    """Empty tuples should become ``None`` so the repository builds no SQL clause."""
    repository = MagicMock()
    repository.list_jobs = AsyncMock(return_value=([], 0))
    handler = ListJobsHandler(repository=repository)

    await handler.do_handle(ListJobsQuery())

    kwargs = repository.list_jobs.await_args.kwargs
    assert kwargs["statuses"] is None
    assert kwargs["bbox_refine_statuses"] is None
    assert kwargs["idempotency_key"] is None


@pytest.mark.asyncio
async def test_pagination_defaults() -> None:
    repository = MagicMock()
    repository.list_jobs = AsyncMock(return_value=([], 0))
    handler = ListJobsHandler(repository=repository)

    response = await handler.do_handle(ListJobsQuery())

    assert response.limit == 50
    assert response.offset == 0
