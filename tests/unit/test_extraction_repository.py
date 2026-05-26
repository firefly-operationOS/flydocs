# Copyright 2026 Firefly Software Solutions Inc
""":class:`ExtractionRepository` -- concurrency-safety contract.

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
        idempotency_key=overrides.get("idempotency_key"),
        status=overrides.get("status", "queued"),
        filename=overrides.get("filename", "test.pdf"),
        content_sha256=overrides.get("content_sha256", "0" * 64),
        content_bytes=overrides.get("content_bytes", 1),
        schema_json=overrides.get("schema_json", {}),
        options_json=overrides.get("options_json", {}),
        metadata_json=overrides.get("metadata_json", {}),
        attempts=overrides.get("attempts", 0),
        started_at=overrides.get("started_at"),
        post_processing_bbox_status=overrides.get("post_processing_bbox_status"),
        post_processing_bbox_started_at=overrides.get("post_processing_bbox_started_at"),
    )
    return await repo.add(ext)


# --------------------------------------------------------------------- mark_running


@pytest.mark.asyncio
async def test_mark_running_claims_queued_extraction() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo)

    claimed = await repo.mark_running(seeded.id, lease_seconds=60)

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.started_at is not None


@pytest.mark.asyncio
async def test_mark_running_rejects_already_running_with_fresh_lease() -> None:
    """Concurrent re-claim of an extraction whose lease hasn't expired returns None."""
    repo = await _fresh_repo()
    seeded = await _seed(repo)
    first = await repo.mark_running(seeded.id, lease_seconds=300)
    assert first is not None

    # Second claim immediately after: lease is fresh, must be rejected.
    second = await repo.mark_running(seeded.id, lease_seconds=300)
    assert second is None


@pytest.mark.asyncio
async def test_mark_running_reclaims_stale_running_for_crash_recovery() -> None:
    """A running extraction with started_at past the lease window is re-claimable."""
    repo = await _fresh_repo()
    # Simulate a worker that claimed long ago and crashed.
    started_ago = datetime.now(UTC) - timedelta(seconds=600)
    seeded = await _seed(repo, status="running", started_at=started_ago, attempts=1)

    reclaimed = await repo.mark_running(seeded.id, lease_seconds=60)

    assert reclaimed is not None
    assert reclaimed.status == "running"
    assert reclaimed.attempts == 2  # crash-recovery bumps attempts


@pytest.mark.asyncio
async def test_mark_running_skips_cancelled_succeeded_failed() -> None:
    for terminal in ("cancelled", "succeeded", "failed"):
        repo = await _fresh_repo()
        seeded = await _seed(repo, status=terminal)
        result = await repo.mark_running(seeded.id, lease_seconds=60)
        assert result is None, f"unexpectedly claimed a {terminal} extraction"


@pytest.mark.asyncio
async def test_mark_running_returns_none_for_missing_extraction() -> None:
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
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_mark_cancelled_rejects_running_extraction() -> None:
    """The cancel/run race is settled by a single atomic UPDATE."""
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="running", started_at=datetime.now(UTC))

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
    # queued but flip the row to mutually exclusive successors. Whichever
    # commits first locks the row in a state the other's WHERE no longer
    # matches.
    winners = [r for r in (cancel_result, claim_result) if r is not None]
    losers = [r for r in (cancel_result, claim_result) if r is None]
    assert len(winners) == 1
    assert len(losers) == 1


# --------------------------------------------------------------------- mark_succeeded / failed


@pytest.mark.asyncio
async def test_mark_succeeded_only_finalises_running() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="running", started_at=datetime.now(UTC))

    finalised = await repo.mark_succeeded(seeded.id, result={"ok": True})
    assert finalised is not None
    assert finalised.status == "succeeded"
    assert finalised.result_json == {"ok": True}

    # Second mark_succeeded is a no-op.
    again = await repo.mark_succeeded(seeded.id, result={"different": True})
    assert again is None


@pytest.mark.asyncio
async def test_mark_succeeded_with_request_bbox_refinement_sets_pending() -> None:
    """mark_succeeded(request_bbox_refinement=True) flips the bbox leg to pending atomically."""
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="running", started_at=datetime.now(UTC))

    finalised = await repo.mark_succeeded(
        seeded.id,
        result={"ok": True},
        request_bbox_refinement=True,
    )
    assert finalised is not None
    assert finalised.status == "succeeded"
    assert finalised.post_processing_bbox_status == "pending"


@pytest.mark.asyncio
async def test_mark_succeeded_without_bbox_request_leaves_bbox_status_null() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="running", started_at=datetime.now(UTC))

    finalised = await repo.mark_succeeded(seeded.id, result={"ok": True})
    assert finalised is not None
    assert finalised.post_processing_bbox_status is None


@pytest.mark.asyncio
async def test_mark_failed_only_finalises_running() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="running", started_at=datetime.now(UTC))

    failed = await repo.mark_failed(seeded.id, code="X", message="boom")
    assert failed is not None
    assert failed.status == "failed"
    # Idempotent: second call returns None instead of clobbering.
    again = await repo.mark_failed(seeded.id, code="Y", message="other")
    assert again is None


# --------------------------------------------------------------------- bbox-refine leg


@pytest.mark.asyncio
async def test_claim_bbox_refinement_starts_from_pending() -> None:
    """Main status is succeeded + bbox sub-status pending -> running."""
    repo = await _fresh_repo()
    seeded = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="pending",
    )

    claimed = await repo.claim_bbox_refinement(seeded.id, lease_seconds=60)
    assert claimed is not None
    # Main status stays succeeded; only the sub-status moves.
    assert claimed.status == "succeeded"
    assert claimed.post_processing_bbox_status == "running"
    assert claimed.post_processing_bbox_attempts == 1


@pytest.mark.asyncio
async def test_claim_bbox_refinement_rejects_fresh_running_lease() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="pending",
    )
    first = await repo.claim_bbox_refinement(seeded.id, lease_seconds=300)
    assert first is not None
    second = await repo.claim_bbox_refinement(seeded.id, lease_seconds=300)
    assert second is None


@pytest.mark.asyncio
async def test_claim_bbox_refinement_reclaims_stale_lease() -> None:
    repo = await _fresh_repo()
    long_ago = datetime.now(UTC) - timedelta(seconds=600)
    seeded = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="running",
        post_processing_bbox_started_at=long_ago,
    )
    reclaimed = await repo.claim_bbox_refinement(seeded.id, lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.post_processing_bbox_attempts == 1


@pytest.mark.asyncio
async def test_concurrent_claim_bbox_refinement_one_winner() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="pending",
    )
    results = await asyncio.gather(
        repo.claim_bbox_refinement(seeded.id, lease_seconds=300),
        repo.claim_bbox_refinement(seeded.id, lease_seconds=300),
    )
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1


@pytest.mark.asyncio
async def test_complete_bbox_refinement_only_from_running() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)
    seeded = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="running",
        post_processing_bbox_started_at=now,
    )
    finalised = await repo.complete_bbox_refinement(seeded.id, result={"grounded": True})
    assert finalised is not None
    # Main status was already succeeded; sub-status flips to succeeded.
    assert finalised.status == "succeeded"
    assert finalised.post_processing_bbox_status == "succeeded"
    assert finalised.result_json == {"grounded": True}


@pytest.mark.asyncio
async def test_requeue_bbox_refinement_only_from_running() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)
    seeded = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="running",
        post_processing_bbox_started_at=now,
    )
    requeued = await repo.requeue_bbox_refinement(seeded.id)
    assert requeued is not None
    assert requeued.status == "succeeded"
    assert requeued.post_processing_bbox_status == "pending"

    again = await repo.requeue_bbox_refinement(seeded.id)
    assert again is None


@pytest.mark.asyncio
async def test_fail_bbox_refinement_only_from_running() -> None:
    repo = await _fresh_repo()
    now = datetime.now(UTC)
    seeded = await _seed(
        repo,
        status="succeeded",
        post_processing_bbox_status="running",
        post_processing_bbox_started_at=now,
    )
    failed = await repo.fail_bbox_refinement(seeded.id, code="X", message="boom")
    assert failed is not None
    # Main status stays succeeded; only the sub-status fails.
    assert failed.status == "succeeded"
    assert failed.post_processing_bbox_status == "failed"


# --------------------------------------------------------------------- requeue_for_retry


@pytest.mark.asyncio
async def test_requeue_for_retry_only_from_running() -> None:
    repo = await _fresh_repo()
    seeded = await _seed(repo, status="running", started_at=datetime.now(UTC))
    requeued = await repo.requeue_for_retry(seeded.id)
    assert requeued is not None
    assert requeued.status == "queued"
    # A cancel that arrived while we were running can no longer be
    # racing -- the retry's next ``mark_running`` will atomically claim
    # again. Verify a redundant requeue is a no-op.
    again = await repo.requeue_for_retry(seeded.id)
    assert again is None
