# Copyright 2026 Firefly Software Solutions Inc
"""``GetJobResultHandler`` -- partial result reads + wait_for_bboxes long-poll."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from flydocs.core.services.jobs.get_job_result_handler import (
    GetJobResultHandler,
    GetJobResultQuery,
    JobNotReady,
)
from flydocs.interfaces.dtos.extract import ExtractionResult
from flydocs.interfaces.enums.job_status import JobStatus


def _result_payload() -> dict[str, Any]:
    return ExtractionResult(
        request_id="00000000-0000-0000-0000-000000000001",
        documents=[],
        model="m",
        latency_ms=10,
    ).model_dump(mode="json", by_alias=True)


@dataclass
class _StubJob:
    id: str = "job-1"
    status: str = JobStatus.SUCCEEDED.value
    result_json: dict[str, Any] | None = field(default_factory=_result_payload)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class _StubRepo:
    """Repository stub that can flip status mid-poll to simulate the refiner."""

    def __init__(self, job: _StubJob, *, flip_to: str | None = None, after_calls: int = 0) -> None:
        self.job = job
        self.flip_to = flip_to
        self.after_calls = after_calls
        self.calls = 0

    async def get(self, job_id: str) -> _StubJob | None:
        self.calls += 1
        if self.flip_to is not None and self.calls > self.after_calls:
            self.job.status = self.flip_to
        return self.job if self.job.id == job_id else None


@pytest.mark.asyncio
async def test_returns_result_for_succeeded() -> None:
    handler = GetJobResultHandler(repository=_StubRepo(_StubJob()))  # type: ignore[arg-type]
    out = await handler.do_handle(GetJobResultQuery(job_id="job-1"))
    assert out is not None
    assert out.job_id == "job-1"


@pytest.mark.asyncio
async def test_returns_partial_result_when_status_partial_succeeded() -> None:
    job = _StubJob(status=JobStatus.PARTIAL_SUCCEEDED.value)
    handler = GetJobResultHandler(repository=_StubRepo(job))  # type: ignore[arg-type]
    out = await handler.do_handle(GetJobResultQuery(job_id="job-1"))
    assert out is not None
    assert out.job_id == "job-1"


@pytest.mark.asyncio
async def test_returns_partial_result_when_status_refining_bboxes() -> None:
    job = _StubJob(status=JobStatus.REFINING_BBOXES.value)
    handler = GetJobResultHandler(repository=_StubRepo(job))  # type: ignore[arg-type]
    out = await handler.do_handle(GetJobResultQuery(job_id="job-1"))
    assert out is not None


@pytest.mark.asyncio
async def test_raises_job_not_ready_for_queued() -> None:
    job = _StubJob(status=JobStatus.QUEUED.value, result_json=None)
    handler = GetJobResultHandler(repository=_StubRepo(job))  # type: ignore[arg-type]
    with pytest.raises(JobNotReady) as ei:
        await handler.do_handle(GetJobResultQuery(job_id="job-1"))
    assert ei.value.status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_raises_job_not_ready_for_failed() -> None:
    job = _StubJob(status=JobStatus.FAILED.value, result_json=None)
    handler = GetJobResultHandler(repository=_StubRepo(job))  # type: ignore[arg-type]
    with pytest.raises(JobNotReady) as ei:
        await handler.do_handle(GetJobResultQuery(job_id="job-1"))
    assert ei.value.status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_returns_none_for_unknown_job() -> None:
    handler = GetJobResultHandler(repository=_StubRepo(_StubJob(id="other")))  # type: ignore[arg-type]
    out = await handler.do_handle(GetJobResultQuery(job_id="missing"))
    assert out is None


@pytest.mark.asyncio
async def test_wait_for_bboxes_returns_partial_at_timeout() -> None:
    # Job stays in PARTIAL_SUCCEEDED throughout; poll should return that
    # state (with its result) once the deadline elapses.
    job = _StubJob(status=JobStatus.PARTIAL_SUCCEEDED.value)
    handler = GetJobResultHandler(repository=_StubRepo(job))  # type: ignore[arg-type]
    started = asyncio.get_running_loop().time()
    out = await handler.do_handle(
        GetJobResultQuery(
            job_id="job-1",
            wait_for_bboxes=True,
            timeout_s=0.3,
            poll_interval_s=0.1,
        )
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert out is not None
    assert 0.2 < elapsed < 1.0  # respected the timeout, didn't return instantly


@pytest.mark.asyncio
async def test_wait_for_bboxes_returns_early_when_status_flips_to_succeeded() -> None:
    # Job starts in PARTIAL_SUCCEEDED; after 2 polls the stub flips it to
    # SUCCEEDED -- handler should return before the timeout fires.
    job = _StubJob(status=JobStatus.PARTIAL_SUCCEEDED.value)
    handler = GetJobResultHandler(
        repository=_StubRepo(job, flip_to=JobStatus.SUCCEEDED.value, after_calls=2)  # type: ignore[arg-type]
    )
    started = asyncio.get_running_loop().time()
    out = await handler.do_handle(
        GetJobResultQuery(
            job_id="job-1",
            wait_for_bboxes=True,
            timeout_s=10.0,
            poll_interval_s=0.1,
        )
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert out is not None
    assert job.status == JobStatus.SUCCEEDED.value
    assert elapsed < 5.0  # well under the 10s timeout
