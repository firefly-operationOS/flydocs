# Copyright 2026 Firefly Software Solutions Inc
""":class:`JobWorker` + :class:`BboxRefineWorker` -- race-loser behaviour.

The repository-level concurrency contract is exercised in
``test_extraction_job_repository.py``. These tests verify the workers
*react* correctly when their atomic claim returns ``None``:

* No duplicate orchestrator call.
* No duplicate webhook delivery.
* No duplicate bbox-refine event published.

In short: the loser of a race must be a complete no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flydocs.config import IDPSettings
from flydocs.core.services.workers.bbox_refine_worker import BboxRefineWorker
from flydocs.core.services.workers.job_worker import JobWorker
from flydocs.interfaces.enums.job_status import JobStatus

# --------------------------------------------------------------- shared fixtures


@dataclass
class _Job:
    id: str = "job-1"
    status: str = JobStatus.QUEUED.value
    filename: str = "test.pdf"
    schema_json: dict[str, Any] = field(default_factory=dict)
    options_json: dict[str, Any] = field(default_factory=dict)
    metadata_json: dict[str, Any] = field(default_factory=dict)
    result_json: dict[str, Any] = field(default_factory=dict)
    callback_url: str | None = None
    attempts: int = 0
    bbox_refine_status: str | None = None
    bbox_refine_attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class _Repo:
    """Repository stub with controllable race-loss semantics."""

    def __init__(
        self,
        job: _Job,
        *,
        claim_returns_none: bool = False,
        finalise_returns_none: bool = False,
    ) -> None:
        self.job = job
        self.claim_returns_none = claim_returns_none
        self.finalise_returns_none = finalise_returns_none
        self.calls: list[str] = []

    async def get(self, job_id: str) -> _Job | None:
        return self.job if self.job.id == job_id else None

    async def mark_running(self, job_id: str, *, lease_seconds: int) -> _Job | None:
        self.calls.append("mark_running")
        if self.claim_returns_none:
            return None
        self.job.status = JobStatus.RUNNING.value
        self.job.attempts += 1
        return self.job

    async def mark_succeeded(self, job_id: str, *, result: dict[str, Any]) -> _Job | None:
        self.calls.append("mark_succeeded")
        if self.finalise_returns_none:
            return None
        self.job.status = JobStatus.SUCCEEDED.value
        self.job.result_json = result
        return self.job

    async def mark_partial_succeeded(self, job_id: str, *, result: dict[str, Any]) -> _Job | None:
        self.calls.append("mark_partial_succeeded")
        if self.finalise_returns_none:
            return None
        self.job.status = JobStatus.PARTIAL_SUCCEEDED.value
        self.job.result_json = result
        return self.job

    async def mark_failed(self, job_id: str, *, code: str, message: str) -> _Job | None:
        self.calls.append("mark_failed")
        if self.finalise_returns_none:
            return None
        self.job.status = JobStatus.FAILED.value
        return self.job

    async def requeue_for_retry(self, job_id: str) -> _Job | None:
        self.calls.append("requeue_for_retry")
        if self.finalise_returns_none:
            return None
        self.job.status = JobStatus.QUEUED.value
        return self.job

    async def update(self, job_id: str, **kwargs: Any) -> _Job | None:
        self.calls.append(f"update:{','.join(sorted(kwargs))}")
        for k, v in kwargs.items():
            setattr(self.job, k, v)
        return self.job

    # bbox leg
    async def mark_bbox_refining(self, job_id: str, *, lease_seconds: int) -> _Job | None:
        self.calls.append("mark_bbox_refining")
        if self.claim_returns_none:
            return None
        self.job.status = JobStatus.REFINING_BBOXES.value
        self.job.bbox_refine_attempts += 1
        return self.job

    async def mark_bbox_refined(self, job_id: str, *, result: dict[str, Any]) -> _Job | None:
        self.calls.append("mark_bbox_refined")
        if self.finalise_returns_none:
            return None
        self.job.status = JobStatus.SUCCEEDED.value
        return self.job

    async def mark_bbox_refine_failed(self, job_id: str, *, code: str, message: str) -> _Job | None:
        self.calls.append("mark_bbox_refine_failed")
        if self.finalise_returns_none:
            return None
        return self.job

    async def requeue_bbox_refine(self, job_id: str) -> _Job | None:
        self.calls.append("requeue_bbox_refine")
        if self.finalise_returns_none:
            return None
        return self.job


def _make_job_worker(
    repo: _Repo,
    *,
    orchestrator_result: Any = None,
    orchestrator_raises: Exception | None = None,
) -> tuple[JobWorker, MagicMock, MagicMock]:
    orchestrator = MagicMock()
    if orchestrator_raises is not None:
        orchestrator.execute = AsyncMock(side_effect=orchestrator_raises)
    else:
        orchestrator.execute = AsyncMock(return_value=orchestrator_result)
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    webhook = MagicMock()
    webhook.deliver = AsyncMock()
    settings = IDPSettings(job_max_attempts=3)
    worker = JobWorker(
        orchestrator=orchestrator,
        repository=repo,  # type: ignore[arg-type]
        event_publisher=publisher,
        webhook=webhook,
        settings=settings,
    )
    return worker, orchestrator, webhook


# --------------------------------------------------------------- JobWorker tests


@pytest.mark.asyncio
async def test_job_worker_bails_silently_when_claim_returns_none() -> None:
    """A worker that loses the claim race must not run the orchestrator."""
    job = _Job()
    repo = _Repo(job, claim_returns_none=True)
    worker, orchestrator, webhook = _make_job_worker(repo)

    await worker._process(job.id)

    assert repo.calls == ["mark_running"]
    orchestrator.execute.assert_not_called()
    webhook.deliver.assert_not_called()


@pytest.mark.asyncio
async def test_job_worker_skips_terminal_status_without_calling_claim() -> None:
    """Re-delivered events for SUCCEEDED / CANCELLED / FAILED jobs short-circuit."""
    for terminal in (JobStatus.SUCCEEDED, JobStatus.CANCELLED, JobStatus.FAILED):
        job = _Job(status=terminal.value)
        repo = _Repo(job)
        worker, orchestrator, webhook = _make_job_worker(repo)
        await worker._process(job.id)
        # No mark_running call, no orchestrator run, no webhook fire.
        assert repo.calls == []
        orchestrator.execute.assert_not_called()
        webhook.deliver.assert_not_called()


@pytest.mark.asyncio
async def test_job_worker_skips_webhook_when_finalise_returns_none() -> None:
    """If mark_succeeded races and loses, no duplicate webhook fires."""
    from flydocs.interfaces.dtos.extract import ExtractionResult

    result = ExtractionResult(
        request_id="00000000-0000-0000-0000-000000000001",
        documents=[],
        model="test",
        latency_ms=1,
    )
    job = _Job(
        callback_url="http://sink/hook",
        schema_json={
            "intention": "test",
            "docs": [
                {
                    "docType": {"documentType": "invoice", "description": "x"},
                    "fieldGroups": [
                        {
                            "fieldGroupName": "g",
                            "fieldGroupFields": [
                                {"fieldName": "f", "fieldDescription": "y", "fieldType": "string"}
                            ],
                        }
                    ],
                }
            ],
            "documents": [
                {"filename": "a.pdf", "content_base64": "Zm9v", "content_type": "application/pdf"}
            ],
        },
    )
    repo = _Repo(job, finalise_returns_none=True)
    worker, orchestrator, webhook = _make_job_worker(repo, orchestrator_result=result)

    await worker._process(job.id)

    assert "mark_succeeded" in repo.calls
    webhook.deliver.assert_not_called()


@pytest.mark.asyncio
async def test_job_worker_retry_path_uses_atomic_requeue() -> None:
    """A retryable failure goes through requeue_for_retry, not raw update()."""
    job = _Job(
        callback_url=None,
        schema_json={
            "intention": "test",
            "docs": [
                {
                    "docType": {"documentType": "invoice", "description": "x"},
                    "fieldGroups": [
                        {
                            "fieldGroupName": "g",
                            "fieldGroupFields": [
                                {"fieldName": "f", "fieldDescription": "y", "fieldType": "string"}
                            ],
                        }
                    ],
                }
            ],
            "documents": [
                {"filename": "a.pdf", "content_base64": "Zm9v", "content_type": "application/pdf"}
            ],
        },
    )
    repo = _Repo(job)
    worker, orchestrator, webhook = _make_job_worker(
        repo, orchestrator_raises=RuntimeError("transient network glitch")
    )

    await worker._process(job.id)

    assert "requeue_for_retry" in repo.calls
    # The legacy 'update(status=QUEUED)' path is gone.
    assert not any(call.startswith("update:") and "status" in call for call in repo.calls)


# --------------------------------------------------------------- BboxRefineWorker tests


def _make_bbox_worker(repo: _Repo) -> tuple[BboxRefineWorker, MagicMock, MagicMock]:
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    webhook = MagicMock()
    webhook.deliver = AsyncMock()
    normalizer = MagicMock()
    refiner = MagicMock()
    settings = IDPSettings(bbox_refine_lease_s=300)
    worker = BboxRefineWorker(
        repository=repo,  # type: ignore[arg-type]
        event_publisher=publisher,
        webhook=webhook,
        normalizer=normalizer,
        refiner=refiner,
        settings=settings,
    )
    return worker, publisher, webhook


@pytest.mark.asyncio
async def test_bbox_worker_bails_silently_when_claim_returns_none() -> None:
    """Bbox worker that loses the claim must do absolutely nothing else."""
    job = _Job(status=JobStatus.PARTIAL_SUCCEEDED.value)
    repo = _Repo(job, claim_returns_none=True)
    worker, publisher, webhook = _make_bbox_worker(repo)

    await worker._process(job.id)

    assert repo.calls == ["mark_bbox_refining"]
    publisher.publish.assert_not_called()
    webhook.deliver.assert_not_called()
