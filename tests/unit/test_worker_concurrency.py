# Copyright 2026 Firefly Software Solutions Inc
""":class:`ExtractionWorker` + :class:`BboxRefineWorker` -- race-loser behaviour.

The repository-level concurrency contract is exercised in
``test_extraction_repository.py``. These tests verify the workers
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
from flydocs.core.services.workers.job_worker import ExtractionWorker
from flydocs.interfaces.enums.extraction_status import ExtractionStatus

# --------------------------------------------------------------- shared fixtures


@dataclass
class _Ext:
    id: str = "ext_TEST00000000000000000000001"
    status: str = ExtractionStatus.QUEUED.value
    filename: str = "test.pdf"
    schema_json: dict[str, Any] = field(default_factory=dict)
    options_json: dict[str, Any] = field(default_factory=dict)
    metadata_json: dict[str, Any] = field(default_factory=dict)
    result_json: dict[str, Any] = field(default_factory=dict)
    callback_url: str | None = None
    attempts: int = 0
    post_processing_bbox_status: str | None = None
    post_processing_bbox_attempts: int = 0
    post_processing_bbox_started_at: datetime | None = None
    post_processing_bbox_finished_at: datetime | None = None
    post_processing_bbox_error_code: str | None = None
    post_processing_bbox_error_message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class _Repo:
    """Repository stub with controllable race-loss semantics."""

    def __init__(
        self,
        ext: _Ext,
        *,
        claim_returns_none: bool = False,
        finalise_returns_none: bool = False,
    ) -> None:
        self.ext = ext
        self.claim_returns_none = claim_returns_none
        self.finalise_returns_none = finalise_returns_none
        self.calls: list[str] = []

    async def get(self, ext_id: str) -> _Ext | None:
        return self.ext if self.ext.id == ext_id else None

    async def mark_running(self, ext_id: str, *, lease_seconds: int) -> _Ext | None:
        self.calls.append("mark_running")
        if self.claim_returns_none:
            return None
        self.ext.status = ExtractionStatus.RUNNING.value
        self.ext.attempts += 1
        return self.ext

    async def mark_succeeded(
        self,
        ext_id: str,
        *,
        result: dict[str, Any],
        request_bbox_refinement: bool = False,
    ) -> _Ext | None:
        self.calls.append("mark_succeeded")
        if self.finalise_returns_none:
            return None
        self.ext.status = ExtractionStatus.SUCCEEDED.value
        self.ext.result_json = result
        if request_bbox_refinement:
            self.ext.post_processing_bbox_status = "pending"
        return self.ext

    async def mark_failed(self, ext_id: str, *, code: str, message: str) -> _Ext | None:
        self.calls.append("mark_failed")
        if self.finalise_returns_none:
            return None
        self.ext.status = ExtractionStatus.FAILED.value
        return self.ext

    async def requeue_for_retry(self, ext_id: str) -> _Ext | None:
        self.calls.append("requeue_for_retry")
        if self.finalise_returns_none:
            return None
        self.ext.status = ExtractionStatus.QUEUED.value
        return self.ext

    async def update(self, ext_id: str, **kwargs: Any) -> _Ext | None:
        self.calls.append(f"update:{','.join(sorted(kwargs))}")
        for k, v in kwargs.items():
            setattr(self.ext, k, v)
        return self.ext

    # bbox leg
    async def claim_bbox_refinement(self, ext_id: str, *, lease_seconds: int) -> _Ext | None:
        self.calls.append("claim_bbox_refinement")
        if self.claim_returns_none:
            return None
        self.ext.post_processing_bbox_status = "running"
        self.ext.post_processing_bbox_attempts += 1
        return self.ext

    async def complete_bbox_refinement(self, ext_id: str, *, result: dict[str, Any]) -> _Ext | None:
        self.calls.append("complete_bbox_refinement")
        if self.finalise_returns_none:
            return None
        self.ext.post_processing_bbox_status = "succeeded"
        return self.ext

    async def fail_bbox_refinement(self, ext_id: str, *, code: str, message: str) -> _Ext | None:
        self.calls.append("fail_bbox_refinement")
        if self.finalise_returns_none:
            return None
        self.ext.post_processing_bbox_status = "failed"
        return self.ext

    async def requeue_bbox_refinement(self, ext_id: str) -> _Ext | None:
        self.calls.append("requeue_bbox_refinement")
        if self.finalise_returns_none:
            return None
        self.ext.post_processing_bbox_status = "pending"
        return self.ext


def _v1_schema() -> dict[str, Any]:
    """A minimal v1 schema_json shape the worker reads via _build_request."""
    return {
        "intention": "test",
        "document_types": [
            {
                "id": "invoice",
                "description": "x",
                "field_groups": [
                    {
                        "name": "g",
                        "fields": [
                            {"name": "f", "description": "y", "type": "string"},
                        ],
                    }
                ],
            }
        ],
        "files": [
            {
                "filename": "a.pdf",
                "content_base64": "Zm9v",
                "content_type": "application/pdf",
                "expected_type": None,
            }
        ],
    }


def _make_extraction_worker(
    repo: _Repo,
    *,
    orchestrator_result: Any = None,
    orchestrator_raises: Exception | None = None,
) -> tuple[ExtractionWorker, MagicMock, MagicMock]:
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
    worker = ExtractionWorker(
        orchestrator=orchestrator,
        repository=repo,  # type: ignore[arg-type]
        event_publisher=publisher,
        webhook=webhook,
        settings=settings,
    )
    return worker, orchestrator, webhook


# --------------------------------------------------------------- ExtractionWorker tests


@pytest.mark.asyncio
async def test_extraction_worker_bails_silently_when_claim_returns_none() -> None:
    """A worker that loses the claim race must not run the orchestrator."""
    ext = _Ext()
    repo = _Repo(ext, claim_returns_none=True)
    worker, orchestrator, webhook = _make_extraction_worker(repo)

    await worker._process(ext.id)

    assert repo.calls == ["mark_running"]
    orchestrator.execute.assert_not_called()
    webhook.deliver.assert_not_called()


@pytest.mark.asyncio
async def test_extraction_worker_skips_terminal_status_without_calling_claim() -> None:
    """Re-delivered events for succeeded / cancelled / failed extractions short-circuit."""
    for terminal in (
        ExtractionStatus.SUCCEEDED,
        ExtractionStatus.CANCELLED,
        ExtractionStatus.FAILED,
    ):
        ext = _Ext(status=terminal.value)
        repo = _Repo(ext)
        worker, orchestrator, webhook = _make_extraction_worker(repo)
        await worker._process(ext.id)
        # No mark_running call, no orchestrator run, no webhook fire.
        assert repo.calls == []
        orchestrator.execute.assert_not_called()
        webhook.deliver.assert_not_called()


@pytest.mark.asyncio
async def test_extraction_worker_skips_webhook_when_finalise_returns_none() -> None:
    """If mark_succeeded races and loses, no duplicate webhook fires."""
    from flydocs.interfaces.dtos.extract import ExtractionResult, PipelineMeta

    result = ExtractionResult(
        id="ext_RESULT0000000000000000000000",
        files=[],
        documents=[],
        pipeline=PipelineMeta(model="test", latency_ms=1),
    )
    ext = _Ext(
        callback_url="http://sink/hook",
        schema_json=_v1_schema(),
    )
    repo = _Repo(ext, finalise_returns_none=True)
    worker, orchestrator, webhook = _make_extraction_worker(repo, orchestrator_result=result)

    await worker._process(ext.id)

    assert "mark_succeeded" in repo.calls
    webhook.deliver.assert_not_called()


@pytest.mark.asyncio
async def test_extraction_worker_retry_path_uses_atomic_requeue() -> None:
    """A retryable failure goes through requeue_for_retry, not raw update()."""
    ext = _Ext(
        callback_url=None,
        schema_json=_v1_schema(),
    )
    repo = _Repo(ext)
    worker, orchestrator, webhook = _make_extraction_worker(
        repo, orchestrator_raises=RuntimeError("transient network glitch")
    )

    await worker._process(ext.id)

    assert "requeue_for_retry" in repo.calls
    # The legacy 'update(status=...)' path is gone.
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
    ext = _Ext(
        status=ExtractionStatus.SUCCEEDED.value,
        post_processing_bbox_status="pending",
    )
    repo = _Repo(ext, claim_returns_none=True)
    worker, publisher, webhook = _make_bbox_worker(repo)

    await worker._process(ext.id)

    assert repo.calls == ["claim_bbox_refinement"]
    publisher.publish.assert_not_called()
    webhook.deliver.assert_not_called()
