# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Real-Postgres integration tests for the concurrency fixes.

These tests exercise the same contracts as
``tests/unit/test_extraction_repository.py`` and
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

from flydocs.models.entities.extraction import Base, Extraction
from flydocs.models.repositories import ExtractionRepository

_PG_URL = os.environ.get("FLYDOCS_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not _PG_URL, reason="FLYDOCS_TEST_PG_URL not set; skipping real-Postgres tests"
)


@pytest.fixture
async def pg_repo() -> ExtractionRepository:
    """Fresh Postgres engine with a clean ``extractions`` table per test."""
    engine = create_async_engine(_PG_URL, future=True)  # type: ignore[arg-type]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = ExtractionRepository(factory, engine=engine)
    yield repo
    await engine.dispose()


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


@pytest.mark.asyncio
async def test_postgres_atomic_claim_single_winner(pg_repo: ExtractionRepository) -> None:
    """Under real Postgres row-level locking: exactly one of N concurrent
    ``mark_running`` calls wins, ``attempts`` increments exactly once."""
    seeded = await _seed(pg_repo)

    n = 8
    results = await asyncio.gather(*(pg_repo.mark_running(seeded.id, lease_seconds=300) for _ in range(n)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected 1 winner, got {len(winners)} (lost-update?!)"
    assert winners[0].attempts == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_cancel_vs_claim(pg_repo: ExtractionRepository) -> None:
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
async def test_postgres_stale_lease_reclaim(pg_repo: ExtractionRepository) -> None:
    """A running extraction past its lease window is reclaimable; a fresh one isn't."""
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
            update(Extraction)
            .where(Extraction.id == seeded.id)
            .values(started_at=datetime.now(UTC) - timedelta(seconds=600))
        )
        await session.commit()

    # Reclaim now succeeds because the lease has expired.
    stale_reclaim = await pg_repo.mark_running(seeded.id, lease_seconds=60)
    assert stale_reclaim is not None
    assert stale_reclaim.attempts == 2


@pytest.mark.asyncio
async def test_postgres_finalisation_idempotent(pg_repo: ExtractionRepository) -> None:
    """Two concurrent ``mark_succeeded`` calls: one wins, one returns None."""
    seeded = await _seed(pg_repo, status="running", started_at=datetime.now(UTC))

    results = await asyncio.gather(
        pg_repo.mark_succeeded(seeded.id, result={"first": True}),
        pg_repo.mark_succeeded(seeded.id, result={"second": True}),
    )
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1
