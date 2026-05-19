# Copyright 2026 Firefly Software Solutions Inc
"""Real-Postgres integration tests for the concurrency fixes.

These tests exercise the same contracts as
``tests/unit/test_extraction_job_repository.py`` and
``tests/unit/test_worker_concurrency.py``, but against a live Postgres
server -- the production substrate. SQLite serialises writers at the
file level so it can hide cases where Postgres' row-level locking +
READ COMMITTED isolation diverge from sqlite3.

The tests opt-in: they're skipped unless ``FLYDOCS_TEST_PG_URL`` is set
(``task test:integration`` exports it; CI does too).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flydocs.models.entities.extraction_job import Base, ExtractionJob
from flydocs.models.repositories import ExtractionJobRepository

_PG_URL = os.environ.get("FLYDOCS_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not _PG_URL, reason="FLYDOCS_TEST_PG_URL not set; skipping real-Postgres tests"
)


@pytest.fixture
async def pg_repo() -> ExtractionJobRepository:
    """Fresh Postgres engine with a clean ``extraction_jobs`` table per test."""
    engine = create_async_engine(_PG_URL, future=True)  # type: ignore[arg-type]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = ExtractionJobRepository(factory, engine=engine)
    yield repo
    await engine.dispose()


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


@pytest.mark.asyncio
async def test_postgres_atomic_claim_single_winner(pg_repo: ExtractionJobRepository) -> None:
    """Under real Postgres row-level locking: exactly one of N concurrent
    ``mark_running`` calls wins, ``attempts`` increments exactly once."""
    seeded = await _seed(pg_repo)

    n = 8
    results = await asyncio.gather(*(pg_repo.mark_running(seeded.id, lease_seconds=300) for _ in range(n)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected 1 winner, got {len(winners)} (lost-update?!)"
    assert winners[0].attempts == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_cancel_vs_claim(pg_repo: ExtractionJobRepository) -> None:
    """Cancel + worker-claim race: exactly one wins, the other gets None."""
    seeded = await _seed(pg_repo)

    # Fire half cancels and half claims interleaved, all targeting the
    # same row simultaneously. Exactly one transition can succeed.
    coros = []
    for i in range(8):
        if i % 2 == 0:
            coros.append(pg_repo.mark_cancelled(seeded.id))
        else:
            coros.append(pg_repo.mark_running(seeded.id, lease_seconds=300))
    results = await asyncio.gather(*coros)
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


@pytest.mark.asyncio
async def test_postgres_stale_lease_reclaim(pg_repo: ExtractionJobRepository) -> None:
    """A RUNNING job past its lease window is reclaimable; a fresh one isn't."""
    seeded = await _seed(pg_repo)
    first = await pg_repo.mark_running(seeded.id, lease_seconds=300)
    assert first is not None

    # Immediately try to reclaim with a fresh lease -- must be rejected.
    fresh = await pg_repo.mark_running(seeded.id, lease_seconds=300)
    assert fresh is None

    # Now backdate started_at to simulate a crashed claimant past the lease.
    # We need to do this through the engine since the repository doesn't
    # expose a "rewind started_at" method -- this is test-only surgery.
    from sqlalchemy import update

    async with pg_repo._session_factory() as session:  # type: ignore[attr-defined]
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == seeded.id)
            .values(started_at=datetime.now(UTC) - timedelta(seconds=600))
        )
        await session.commit()

    # Reclaim now succeeds because the lease has expired.
    stale_reclaim = await pg_repo.mark_running(seeded.id, lease_seconds=60)
    assert stale_reclaim is not None
    assert stale_reclaim.attempts == 2


@pytest.mark.asyncio
async def test_postgres_finalisation_idempotent(pg_repo: ExtractionJobRepository) -> None:
    """Two concurrent ``mark_succeeded`` calls: one wins, one returns None."""
    seeded = await _seed(pg_repo, status="RUNNING", started_at=datetime.now(UTC))

    results = await asyncio.gather(
        pg_repo.mark_succeeded(seeded.id, result={"first": True}),
        pg_repo.mark_succeeded(seeded.id, result={"second": True}),
    )
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1
