# Copyright 2026 Firefly Software Solutions Inc
""":class:`ExtractionReaper` / :class:`BboxReaper` -- orphan-revival sweep.

These tests verify the reaper finds rows stuck in non-terminal states
and republishes the right EDA event for them. Concurrency safety
(duplicate publishes from multiple replicas being deduped at claim
time) is covered separately in ``test_extraction_repository.py`` and
``test_worker_concurrency.py``.
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
from flydocs.core.services.workers.job_reaper import ExtractionReaper
from flydocs.models.entities.extraction import Base, Extraction
from flydocs.models.repositories import ExtractionRepository


async def _fresh_repo() -> ExtractionRepository:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return ExtractionRepository(factory, engine=engine)


async def _seed(repo: ExtractionRepository, **overrides) -> Extraction:
    ext = Extraction(
        status=overrides.get("status", "queued"),
        filename=overrides.get("filename", "test.pdf"),
        content_sha256=overrides.get("content_sha256", "0" * 64),
        content_bytes=overrides.get("content_bytes", 1),
        schema_json=overrides.get("schema_json", {}),
        options_json=overrides.get("options_json", {}),
        metadata_json=overrides.get("metadata_json", {}),
        attempts=overrides.get("attempts", 0),
        started_at=overrides.get("started_at"),
        finished_at=overrides.get("finished_at"),
        post_processing_bbox_status=overrides.get("post_processing_bbox_status"),
        post_processing_bbox_started_at=overrides.get("post_processing_bbox_started_at"),
    )
    ext = await repo.add(ext)
    if "submitted_at" in overrides:
        # Backdate submitted_at so the orphan-threshold tests don't have
        # to wait wall-clock time. SQLAlchemy server_default fires on
        # INSERT; we override here after the row exists.
        async with repo._session_factory() as session:  # type: ignore[attr-defined]
            await session.execute(
                update(Extraction)
                .where(Extraction.id == ext.id)
                .values(submitted_at=overrides["submitted_at"])
            )
            await session.commit()
        ext = await repo.get(ext.id)  # type: ignore[assignment]
    return ext


# ---------------------------------------------------------- find_stale_* tests


@pytest.mark.asyncio
async def test_find_stale_running_only_returns_rows_past_lease() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)
    fresh = await _seed(repo, status="running", started_at=now)
    stale = await _seed(repo, status="running", started_at=now - timedelta(seconds=600))

    result = await repo.find_stale_running(lease_seconds=300)
    assert fresh.id not in result
    assert stale.id in result


@pytest.mark.asyncio
async def test_find_stale_running_excludes_succeeded_failed_cancelled() -> None:
    repo = await _fresh_repo()
    long_ago = datetime.now(UTC) - timedelta(seconds=3600)
    for terminal in ("succeeded", "failed", "cancelled"):
        await _seed(repo, status=terminal, started_at=long_ago)

    result = await repo.find_stale_running(lease_seconds=60)
    assert result == []


@pytest.mark.asyncio
async def test_find_stale_queued_picks_up_brand_new_submit_orphans() -> None:
    """started_at IS NULL -> fall back to submitted_at via COALESCE."""
    repo = await _fresh_repo()
    fresh = await _seed(repo, status="queued")  # submitted_at = now
    old = await _seed(
        repo,
        status="queued",
        submitted_at=datetime.now(UTC) - timedelta(seconds=1200),
    )

    result = await repo.find_stale_queued(older_than_seconds=600)
    assert fresh.id not in result
    assert old.id in result


@pytest.mark.asyncio
async def test_find_stale_queued_picks_up_retry_orphans() -> None:
    """status=queued with started_at IS NOT NULL (post-requeue)."""
    repo = await _fresh_repo()
    long_ago = datetime.now(UTC) - timedelta(seconds=1200)
    requeued = await _seed(repo, status="queued", started_at=long_ago)

    result = await repo.find_stale_queued(older_than_seconds=600)
    assert requeued.id in result


@pytest.mark.asyncio
async def test_find_stale_bbox_refining_lease_based() -> None:
    repo = await _fresh_repo()
    fresh = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="running",
        post_processing_bbox_started_at=datetime.now(UTC),
    )
    stale = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="running",
        post_processing_bbox_started_at=datetime.now(UTC) - timedelta(seconds=1200),
    )

    result = await repo.find_stale_bbox_refining(lease_seconds=300)
    assert fresh.id not in result
    assert stale.id in result


@pytest.mark.asyncio
async def test_find_pending_bbox_revive_covers_both_subcases() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)

    # Case A: post_processing_bbox_started_at IS NULL, finished_at is old.
    # The repository uses ``finished_at`` (the main pipeline's terminal
    # timestamp) as the clock for case A.
    case_a = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="pending",
        finished_at=now - timedelta(seconds=2000),
    )
    # Case B: post_processing_bbox_started_at IS NOT NULL but stale.
    case_b = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="pending",
        finished_at=now - timedelta(seconds=120),
        post_processing_bbox_started_at=now - timedelta(seconds=800),
    )
    # Fresh case A: post_processing_bbox_started_at IS NULL but finished_at recent.
    fresh_a = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="pending",
        finished_at=now,
    )
    # Fresh case B: post_processing_bbox_started_at IS NOT NULL and recent.
    fresh_b = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="pending",
        post_processing_bbox_started_at=now - timedelta(seconds=60),
        finished_at=now - timedelta(seconds=120),
    )

    result = await repo.find_pending_bbox_revive(
        pending_threshold_seconds=1200,
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
async def test_extraction_reaper_republishes_stale_running_and_queued() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)

    stale_running = await _seed(repo, status="running", started_at=now - timedelta(seconds=2000))
    orphan_queued = await _seed(
        repo,
        status="queued",
        submitted_at=now - timedelta(seconds=1200),
    )
    fresh_running = await _seed(repo, status="running", started_at=now)
    succeeded = await _seed(repo, status="succeeded")

    publisher = _make_publisher()
    settings = _settings(
        job_run_lease_s=1260,
        queued_orphan_threshold_s=600,
    )
    reaper = ExtractionReaper(
        repository=repo,
        event_publisher=publisher,
        settings=settings,
    )

    await reaper._sweep()

    published_ids = [
        call.kwargs["payload"]["extraction"]["id"]
        for call in publisher.publish.await_args_list
    ]
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
        status="succeeded",
        post_processing_bbox_status="running",
        post_processing_bbox_started_at=now - timedelta(seconds=2000),
    )
    pending_orphan = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="pending",
        finished_at=now - timedelta(seconds=2000),
    )
    fresh_refining = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="running",
        post_processing_bbox_started_at=now,
    )
    already_done = await _seed(repo, status="succeeded")

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

    published_ids = [
        call.kwargs["payload"]["extraction"]["id"]
        for call in publisher.publish.await_args_list
    ]
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
    reaper = ExtractionReaper(repository=repo, event_publisher=publisher, settings=settings)

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
    await _seed(repo, status="running", started_at=now - timedelta(seconds=2000))

    publisher = _make_publisher()
    publisher.publish = AsyncMock(side_effect=RuntimeError("broker down"))
    settings = _settings(reaper_sweep_interval_s=1, job_run_lease_s=300)
    reaper = ExtractionReaper(repository=repo, event_publisher=publisher, settings=settings)

    task = asyncio.create_task(reaper.run_forever())
    await asyncio.sleep(0.05)
    reaper.stop()
    await asyncio.wait_for(task, timeout=2.0)
    # Sweep raised, but the run_forever loop swallowed it; task ended cleanly.
    assert task.done()
    assert task.exception() is None
