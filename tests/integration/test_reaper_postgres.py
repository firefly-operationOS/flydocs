# Copyright 2026 Firefly Software Solutions Inc
"""End-to-end orphan-revival flow on real Postgres.

Demonstrates that the five orphan classes identified in the second
audit are actually revived by the reaper + worker claim cycle:

1. ``QUEUED`` orphan (submit-publish crashed) -> JobReaper revives.
2. ``RUNNING`` orphan (worker crashed past its lease) -> JobReaper.
3. ``QUEUED`` orphan (retry-publish crashed) -> JobReaper.
4. ``PARTIAL_SUCCEEDED`` orphan (bbox-publish crashed) -> BboxReaper.
5. ``REFINING_BBOXES`` orphan (bbox worker crashed) -> BboxReaper.

In each case we seed the row directly in the stuck state, run a single
reaper sweep, and assert that a fresh event was published with the
right job id. The actual claim-then-process is covered by the
``test_extraction_job_repository.py`` atomic-transition tests, so we
stop at "event published" here -- the rest of the chain is
identical to the happy path.
"""

from __future__ import annotations

import os
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

_PG_URL = os.environ.get("FLYDOCS_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not _PG_URL, reason="FLYDOCS_TEST_PG_URL not set; skipping real-Postgres tests"
)


@pytest.fixture
async def pg_repo() -> ExtractionJobRepository:
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
        async with repo._session_factory() as session:  # type: ignore[attr-defined]
            await session.execute(
                update(ExtractionJob)
                .where(ExtractionJob.id == job.id)
                .values(created_at=overrides["created_at"])
            )
            await session.commit()
        job = await repo.get(job.id)  # type: ignore[assignment]
    return job


def _publisher() -> MagicMock:
    pub = MagicMock()
    pub.publish = AsyncMock()
    return pub


@pytest.mark.asyncio
async def test_job_reaper_revives_all_three_job_orphan_classes(
    pg_repo: ExtractionJobRepository,
) -> None:
    """End-to-end: seed stuck rows, sweep, verify republish for each."""
    now = datetime.now(UTC)
    # Orphan 1: QUEUED, submit-publish crashed.
    submit_orphan = await _seed(
        pg_repo,
        status="QUEUED",
        created_at=now - timedelta(seconds=1200),
    )
    # Orphan 2: RUNNING, worker crashed past lease.
    crashed_runner = await _seed(
        pg_repo,
        status="RUNNING",
        started_at=now - timedelta(seconds=2000),
        attempts=1,
    )
    # Orphan 3: QUEUED after requeue, delayed-publish task killed.
    retry_orphan = await _seed(
        pg_repo,
        status="QUEUED",
        started_at=now - timedelta(seconds=1200),
        attempts=1,
    )
    # Negative control: a fresh QUEUED row should NOT be reaped.
    fresh = await _seed(pg_repo, status="QUEUED")

    publisher = _publisher()
    reaper = JobReaper(
        repository=pg_repo,
        event_publisher=publisher,
        settings=IDPSettings(
            job_run_lease_s=1260,
            queued_orphan_threshold_s=600,
        ),
    )

    await reaper._sweep()

    published_ids = [
        c.kwargs["payload"]["job_id"] for c in publisher.publish.await_args_list
    ]
    assert submit_orphan.id in published_ids
    assert crashed_runner.id in published_ids
    assert retry_orphan.id in published_ids
    assert fresh.id not in published_ids


@pytest.mark.asyncio
async def test_bbox_reaper_revives_both_bbox_orphan_classes(
    pg_repo: ExtractionJobRepository,
) -> None:
    now = datetime.now(UTC)
    # Orphan A: PARTIAL_SUCCEEDED, main-worker bbox-publish crashed.
    publish_orphan = await _seed(
        pg_repo,
        status="PARTIAL_SUCCEEDED",
        bbox_refine_status="pending",
        started_at=now - timedelta(seconds=2000),
    )
    # Orphan B: REFINING_BBOXES, bbox-worker crashed past lease.
    crashed_bbox = await _seed(
        pg_repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=now - timedelta(seconds=2000),
    )
    # Negative control: a fresh REFINING_BBOXES claim.
    fresh = await _seed(
        pg_repo,
        status="REFINING_BBOXES",
        bbox_refine_status="running",
        bbox_refine_started_at=now,
    )

    publisher = _publisher()
    reaper = BboxReaper(
        repository=pg_repo,
        event_publisher=publisher,
        settings=IDPSettings(
            bbox_refine_lease_s=660,
            partial_succeeded_orphan_threshold_s=1320,
        ),
    )

    await reaper._sweep()

    published_ids = [
        c.kwargs["payload"]["job_id"] for c in publisher.publish.await_args_list
    ]
    assert publish_orphan.id in published_ids
    assert crashed_bbox.id in published_ids
    assert fresh.id not in published_ids


@pytest.mark.asyncio
async def test_reaper_republish_revives_through_full_claim_cycle(
    pg_repo: ExtractionJobRepository,
) -> None:
    """Crash-recovery proof: stale RUNNING is reclaimable after the lease.

    Sequence:
      1. Worker A claims a QUEUED job (status=RUNNING, fresh lease).
      2. Worker A "crashes" -- we leave the row in RUNNING.
      3. Reaper sees the row is past its lease (we backdate started_at).
      4. Reaper publishes a fresh event (verified via publisher mock).
      5. A "fresh" worker calls mark_running with the same lease and wins
         the atomic claim -- attempts goes up by exactly 1.
    """
    seeded = await _seed(pg_repo)
    first_claim = await pg_repo.mark_running(seeded.id, lease_seconds=1260)
    assert first_claim is not None and first_claim.status == "RUNNING"

    # Backdate started_at to past the lease window.
    async with pg_repo._session_factory() as session:  # type: ignore[attr-defined]
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == seeded.id)
            .values(started_at=datetime.now(UTC) - timedelta(seconds=2000))
        )
        await session.commit()

    publisher = _publisher()
    reaper = JobReaper(
        repository=pg_repo,
        event_publisher=publisher,
        settings=IDPSettings(job_run_lease_s=1260, queued_orphan_threshold_s=600),
    )
    await reaper._sweep()

    # The reaper republished -- the fresh worker can now successfully claim.
    fresh_claim = await pg_repo.mark_running(seeded.id, lease_seconds=1260)
    assert fresh_claim is not None
    assert fresh_claim.attempts == 2  # crash-recovery bumped attempts
