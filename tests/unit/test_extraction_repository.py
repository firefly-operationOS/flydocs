# Copyright 2026 Firefly Software Solutions Inc
""":class:`ExtractionJobRepository` -- concurrency-safety contract.

These tests exercise the atomic state-transition methods against a
real SQLite-backed engine. SQLite serialises writers at the database
file level which is conservative -- if a precondition holds here it
holds under Postgres' row-level locking too. The point of the tests
is to pin the *return value* contract: ``None`` means "the row was
not in a legal predecessor state", non-None means "we won the
compare-and-swap and the post-transition row is the return value".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
        idempotency_key=overrides.get("idempotency_key"),
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
    return await repo.add(job)


# --------------------------------------------------------------------- mark_running


@pytest.mark.asyncio
async def test_mark_running_claims_queued_job() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo)

    claimed = await repo.mark_running(seeded.id, lease_seconds=60)

    assert claimed is not None
    assert claimed.status == "RUNNING"
    assert claimed.attempts == 1
    assert claimed.started_at is not None


@pytest.mark.asyncio
async def test_mark_running_rejects_already_running_with_fresh_lease() -> None:
    """Concurrent re-claim of a job whose lease hasn't expired returns None."""
    repo = await _fresh_repo()
    seeded = await _seed(repo)
    first = await repo.mark_running(seeded.id, lease_seconds=300)
    assert first is not None

    # Second claim immediately after: lease is fresh, must be rejected.
    second = await repo.mark_running(seeded.id, lease_seconds=300)
    assert second is None


@pytest.mark.asyncio
async def test_mark_running_reclaims_stale_running_for_crash_recovery() -> None:
    """A RUNNING job with started_at past the lease window is re-claimable."""
    repo = await _fresh_repo()
    # Simulate a worker that claimed long ago and crashed.
    started_ago = datetime.now(UTC) - timedelta(seconds=600)
    seeded = await _seed(repo, status="RUNNING", started_at=started_ago, attempts=1)

    reclaimed = await repo.mark_running(seeded.id, lease_seconds=60)

    assert reclaimed is not None
    assert reclaimed.status == "RUNNING"
    assert reclaimed.attempts == 2  # crash-recovery bumps attempts


@pytest.mark.asyncio
async def test_mark_running_skips_cancelled_succeeded_failed() -> None:
    for terminal in ("CANCELLED", "SUCCEEDED", "FAILED"):
        repo = await _fresh_repo()
        seeded = await _seed(repo, status=terminal)
        result = await repo.mark_running(seeded.id, lease_seconds=60)
        assert result is None, f"unexpectedly claimed a {terminal} job"


@pytest.mark.asyncio
async def test_mark_running_returns_none_for_missing_job() -> None:
    repo = await _fresh_repo()
    result = await repo.mark_running("does-not-exist", lease_seconds=60)
    assert result is None


@pytest.mark.asyncio
async def test_concurrent_mark_running_only_one_winner() -> None:
    """Two ``mark_running`` calls dispatched concurrently: exactly one wins."""
    repo = await _fresh_repo()
    seeded = await _seed(repo)

    results = await asyncio.gather(
        repo.mark_running(seeded.id, lease_seconds=300),
        repo.mark_running(seeded.id, lease_seconds=300),
    )
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1
    # attempts went up exactly once -- the lost-update bug is gone.
    assert winners[0].attempts == 1


# --------------------------------------------------------------------- mark_cancelled


@pytest.mark.asyncio
async def test_mark_cancelled_succeeds_only_for_queued() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo)

    cancelled = await repo.mark_cancelled(seeded.id)
    assert cancelled is not None
    assert cancelled.status == "CANCELLED"


@pytest.mark.asyncio
async def test_mark_cancelled_rejects_running_job() -> None:
    """The cancel/run race is settled by a single atomic UPDATE."""
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="RUNNING", started_at=datetime.now(UTC))

    result = await repo.mark_cancelled(seeded.id)
    assert result is None


@pytest.mark.asyncio
async def test_concurrent_cancel_vs_claim_exactly_one_wins() -> None:
    """A cancel arriving the same instant as a worker claim: exactly one wins."""
    repo = await _fresh_repo()
    seeded = await _seed(repo)

    cancel_result, claim_result = await asyncio.gather(
        repo.mark_cancelled(seeded.id),
        repo.mark_running(seeded.id, lease_seconds=300),
    )
    # The two transitions have disjoint preconditions -- both target
    # QUEUED but flip the row to mutually exclusive successors. Whichever
    # commits first locks the row in a state the other's WHERE no longer
    # matches.
    winners = [r for r in (cancel_result, claim_result) if r is not None]
    losers = [r for r in (cancel_result, claim_result) if r is None]
    assert len(winners) == 1
    assert len(losers) == 1


# --------------------------------------------------------------------- mark_succeeded / failed


@pytest.mark.asyncio
async def test_mark_succeeded_only_finalises_running_or_refining() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="RUNNING", started_at=datetime.now(UTC))

    finalised = await repo.mark_succeeded(seeded.id, result={"ok": True})
    assert finalised is not None
    assert finalised.status == "SUCCEEDED"
    assert finalised.result_json == {"ok": True}

    # Second mark_succeeded is a no-op.
    again = await repo.mark_succeeded(seeded.id, result={"different": True})
    assert again is None


@pytest.mark.asyncio
async def test_mark_failed_only_finalises_running() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="RUNNING", started_at=datetime.now(UTC))

    failed = await repo.mark_failed(seeded.id, code="X", message="boom")
    assert failed is not None
    assert failed.status == "FAILED"
    # Idempotent: second call returns None instead of clobbering.
    again = await repo.mark_failed(seeded.id, code="Y", message="other")
    assert again is None


# --------------------------------------------------------------------- bbox-refine leg


@pytest.mark.asyncio
async def test_mark_bbox_refining_claims_partial_succeeded() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="PARTIAL_SUCCEEDED", bbox_refine_status="pending")

    claimed = await repo.mark_bbox_refining(seeded.id, lease_seconds=60)
    assert claimed is not None
    assert claimed.status == "REFINING_BBOXES"
    assert claimed.bbox_refine_status == "running"
    assert claimed.bbox_refine_attempts == 1


@pytest.mark.asyncio
async def test_mark_bbox_refining_rejects_fresh_refining_lease() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="PARTIAL_SUCCEEDED", bbox_refine_status="pending")
    first = await repo.mark_bbox_refining(seeded.id, lease_seconds=300)
    assert first is not None
    second = await repo.mark_bbox_refining(seeded.id, lease_seconds=300)
    assert second is None


@pytest.mark.asyncio
async def test_mark_bbox_refining_reclaims_stale_lease() -> None:
    repo = await _fresh_repo()
    long_ago = datetime.now(UTC) - timedelta(seconds=600)
    seeded = await _seed(
        repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=long_ago,
    )
    reclaimed = await repo.mark_bbox_refining(seeded.id, lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.bbox_refine_attempts == 1


@pytest.mark.asyncio
async def test_concurrent_bbox_refining_one_winner() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="PARTIAL_SUCCEEDED", bbox_refine_status="pending")
    results = await asyncio.gather(
        repo.mark_bbox_refining(seeded.id, lease_seconds=300),
        repo.mark_bbox_refining(seeded.id, lease_seconds=300),
    )
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1


@pytest.mark.asyncio
async def test_mark_bbox_refined_only_from_refining() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)
    seeded = await _seed(
        repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=now,
    )
    finalised = await repo.mark_bbox_refined(seeded.id, result={"grounded": True})
    assert finalised is not None
    assert finalised.status == "SUCCEEDED"
    assert finalised.bbox_refine_status == "succeeded"


@pytest.mark.asyncio
async def test_requeue_bbox_refine_only_from_refining() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)
    seeded = await _seed(
        repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=now,
    )
    requeued = await repo.requeue_bbox_refine(seeded.id)
    assert requeued is not None
    assert requeued.status == "PARTIAL_SUCCEEDED"
    assert requeued.bbox_refine_status == "pending"

    again = await repo.requeue_bbox_refine(seeded.id)
    assert again is None


# --------------------------------------------------------------------- requeue_for_retry


@pytest.mark.asyncio
async def test_requeue_for_retry_only_from_running() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="RUNNING", started_at=datetime.now(UTC))
    requeued = await repo.requeue_for_retry(seeded.id)
    assert requeued is not None
    assert requeued.status == "QUEUED"
    # A cancel that arrived while we were running can no longer be
    # racing -- the retry's next ``mark_running`` will atomically claim
    # again. Verify a redundant requeue is a no-op.
    again = await repo.requeue_for_retry(seeded.id)
    assert again is None
