# Copyright 2026 Firefly Software Solutions Inc
""":class:`JobReaper` / :class:`BboxReaper` -- orphan-revival sweep.

These tests verify the reaper finds rows stuck in non-terminal states
and republishes the right EDA event for them. Concurrency safety
(duplicate publishes from multiple replicas being deduped at claim
time) is covered separately in ``test_extraction_job_repository.py``
and ``test_worker_concurrency.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flydocs.config import IDPSettings
from flydocs.core.services.workers.bbox_reaper import BboxReaper
from flydocs.core.services.workers.job_reaper import JobReaper
from flydocs.models.entities.extraction_job import Base, ExtractionJob
from flydocs.models.repositories import ExtractionJobRepository


async def _fresh_repo() -> ExtractionJobRepository:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return ExtractionJobRepository(factory, engine=engine)


async def _seed(repo: ExtractionJobRepository, **overrides) -> ExtractionJob:
    job = ExtractionJob(
        status=overrides.get("status", "QUEUED"),
        filename=overrides.get("filename", "test.pdf"),
        content_sha256=overrides.get("content_sha256", "0" * 64),
        content_bytes=overrides.get("content_bytes", 1),
        schema_json=overrides.get("schema_json", {}),
        options_json=overrides.get("options_json", {}),
        metadata_json=overrides.get("metadata_json", {}),
        attempts=overrides.get("attempts", 0),
        started_at=overrides.get("started_at"),
        bbox_refine_status=overrides.get("bbox_refine_status"),
        bbox_refine_started_at=overrides.get("bbox_refine_started_at"),
    )
    job = await repo.add(job)
    if "created_at" in overrides:
        # Backdate created_at so the orphan-threshold tests don't have
        # to wait wall-clock time. SQLAlchemy server_default fires on
        # INSERT; we override here after the row exists.
        async with repo._session_factory() as session:  # type: ignore[attr-defined]
            await session.execute(
                update(ExtractionJob)
                .where(ExtractionJob.id == job.id)
                .values(created_at=overrides["created_at"])
            )
            await session.commit()
        job = await repo.get(job.id)  # type: ignore[assignment]
    return job


# ---------------------------------------------------------- find_stale_* tests


@pytest.mark.asyncio
async def test_find_stale_running_only_returns_rows_past_lease() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)
    fresh = await _seed(repo, status="RUNNING", started_at=now)
    stale = await _seed(repo, status="RUNNING", started_at=now - timedelta(seconds=600))

    result = await repo.find_stale_running(lease_seconds=300)
    assert fresh.id not in result
    assert stale.id in result


@pytest.mark.asyncio
async def test_find_stale_running_excludes_succeeded_failed_cancelled() -> None:
    repo = await _fresh_repo()
    long_ago = datetime.now(UTC) - timedelta(seconds=3600)
    for terminal in ("SUCCEEDED", "FAILED", "CANCELLED"):
        await _seed(repo, status=terminal, started_at=long_ago)

    result = await repo.find_stale_running(lease_seconds=60)
    assert result == []


@pytest.mark.asyncio
async def test_find_stale_queued_picks_up_brand_new_submit_orphans() -> None:
    """started_at IS NULL → fall back to created_at via COALESCE."""
    repo = await _fresh_repo()
    fresh = await _seed(repo, status="QUEUED")  # created_at = now
    old = await _seed(
        repo,
        status="QUEUED",
        created_at=datetime.now(UTC) - timedelta(seconds=1200),
    )

    result = await repo.find_stale_queued(older_than_seconds=600)
    assert fresh.id not in result
    assert old.id in result


@pytest.mark.asyncio
async def test_find_stale_queued_picks_up_retry_orphans() -> None:
    """status=QUEUED with started_at IS NOT NULL (post-requeue)."""
    repo = await _fresh_repo()
    long_ago = datetime.now(UTC) - timedelta(seconds=1200)
    requeued = await _seed(repo, status="QUEUED", started_at=long_ago)

    result = await repo.find_stale_queued(older_than_seconds=600)
    assert requeued.id in result


@pytest.mark.asyncio
async def test_find_stale_refining_bboxes_lease_based() -> None:
    repo = await _fresh_repo()
    fresh = await _seed(
        repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=datetime.now(UTC),
    )
    stale = await _seed(
        repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=datetime.now(UTC) - timedelta(seconds=1200),
    )

    result = await repo.find_stale_refining_bboxes(lease_seconds=300)
    assert fresh.id not in result
    assert stale.id in result


@pytest.mark.asyncio
async def test_find_pending_bbox_revive_covers_both_subcases() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)

    # Case A: bbox_refine_started_at IS NULL, started_at is old.
    case_a = await _seed(
        repo,
        status="PARTIAL_SUCCEEDED",
        bbox_refine_status="pending",
        started_at=now - timedelta(seconds=2000),
    )
    # Case B: bbox_refine_started_at IS NOT NULL but stale.
    case_b = await _seed(
        repo,
        status="PARTIAL_SUCCEEDED",
        bbox_refine_status="pending",
        started_at=now - timedelta(seconds=120),
        bbox_refine_started_at=now - timedelta(seconds=800),
    )
    # Fresh case A: bbox_refine_started_at IS NULL but started_at is recent.
    fresh_a = await _seed(
        repo,
        status="PARTIAL_SUCCEEDED",
        bbox_refine_status="pending",
        started_at=now,
    )
    # Fresh case B: bbox_refine_started_at IS NOT NULL and recent.
    fresh_b = await _seed(
        repo,
        status="PARTIAL_SUCCEEDED",
        bbox_refine_status="pending",
        bbox_refine_started_at=now - timedelta(seconds=60),
        started_at=now - timedelta(seconds=120),
    )

    result = await repo.find_pending_bbox_revive(
        partial_threshold_seconds=1200,
        bbox_lease_seconds=600,
    )
    assert case_a.id in result
    assert case_b.id in result
    assert fresh_a.id not in result
    assert fresh_b.id not in result


# ---------------------------------------------------------- reaper-sweep tests


def _make_publisher() -> MagicMock:
    pub = MagicMock()
    pub.publish = AsyncMock()
    return pub


def _settings(**overrides) -> IDPSettings:
    return IDPSettings(**overrides)


@pytest.mark.asyncio
async def test_job_reaper_republishes_stale_running_and_queued() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)

    stale_running = await _seed(repo, status="RUNNING", started_at=now - timedelta(seconds=2000))
    orphan_queued = await _seed(
        repo,
        status="QUEUED",
        created_at=now - timedelta(seconds=1200),
    )
    fresh_running = await _seed(repo, status="RUNNING", started_at=now)
    succeeded = await _seed(repo, status="SUCCEEDED")

    publisher = _make_publisher()
    settings = _settings(
        job_run_lease_s=1260,
        queued_orphan_threshold_s=600,
    )
    reaper = JobReaper(
        repository=repo,
        event_publisher=publisher,
        settings=settings,
    )

    await reaper._sweep()

    published_ids = [call.kwargs["payload"]["job_id"] for call in publisher.publish.await_args_list]
    assert stale_running.id in published_ids
    assert orphan_queued.id in published_ids
    assert fresh_running.id not in published_ids
    assert succeeded.id not in published_ids


@pytest.mark.asyncio
async def test_bbox_reaper_republishes_stale_refining_and_pending() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)

    stale_refining = await _seed(
        repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=now - timedelta(seconds=2000),
    )
    pending_orphan = await _seed(
        repo,
        status="PARTIAL_SUCCEEDED",
        bbox_refine_status="pending",
        started_at=now - timedelta(seconds=2000),
    )
    fresh_refining = await _seed(
        repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=now,
    )
    already_done = await _seed(repo, status="SUCCEEDED")

    publisher = _make_publisher()
    settings = _settings(
        bbox_refine_lease_s=660,
        partial_succeeded_orphan_threshold_s=1320,
    )
    reaper = BboxReaper(
        repository=repo,
        event_publisher=publisher,
        settings=settings,
    )

    await reaper._sweep()

    published_ids = [call.kwargs["payload"]["job_id"] for call in publisher.publish.await_args_list]
    assert stale_refining.id in published_ids
    assert pending_orphan.id in published_ids
    assert fresh_refining.id not in published_ids
    assert already_done.id not in published_ids


@pytest.mark.asyncio
async def test_reaper_sweep_loop_stops_when_signalled() -> None:
    """``run_forever`` honours ``stop()`` without leaking the task."""
    repo = await _fresh_repo()
    publisher = _make_publisher()
    settings = _settings(reaper_sweep_interval_s=1)
    reaper = JobReaper(repository=repo, event_publisher=publisher, settings=settings)

    task = asyncio.create_task(reaper.run_forever())
    # Let at least one sweep run.
    await asyncio.sleep(0.05)
    reaper.stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()


@pytest.mark.asyncio
async def test_reaper_sweep_survives_publisher_failure() -> None:
    """A publish error during one sweep doesn't kill the loop."""
    repo = await _fresh_repo()
    now = datetime.now(UTC)
    await _seed(repo, status="RUNNING", started_at=now - timedelta(seconds=2000))

    publisher = _make_publisher()
    publisher.publish = AsyncMock(side_effect=RuntimeError("broker down"))
    settings = _settings(reaper_sweep_interval_s=1, job_run_lease_s=300)
    reaper = JobReaper(repository=repo, event_publisher=publisher, settings=settings)

    task = asyncio.create_task(reaper.run_forever())
    await asyncio.sleep(0.05)
    reaper.stop()
    await asyncio.wait_for(task, timeout=2.0)
    # Sweep raised, but the run_forever loop swallowed it; task ended cleanly.
    assert task.done()
    assert task.exception() is None
