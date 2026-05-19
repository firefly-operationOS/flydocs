# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for :class:`ExtractionJob`.

Wraps an ``AsyncSession`` factory so callers can be ignorant of the
transaction boundaries. Each method opens its own short-lived session +
transaction.

Concurrency model
=================

Every state-changing method is a **single conditional UPDATE** with an
explicit precondition on ``status`` (and where relevant, a lease
threshold on ``started_at``). The query returns the new row only when
the precondition matched; otherwise it returns ``None`` so the caller
can detect "I lost the race". This pattern makes the transitions safe
under concurrent delivery from multiple worker replicas without needing
``SELECT ... FOR UPDATE`` or serialisable isolation -- two writers
trying the same transition will be serialised by Postgres' row-level
lock on UPDATE, and the loser's ``WHERE`` clause won't match the
already-updated row.

The legal predecessor set for each transition is documented inline.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from flydocs.models.entities.extraction_job import ExtractionJob


class ExtractionJobRepository:
    """Async repository for ``extraction_jobs``."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        engine: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine

    @property
    def engine(self) -> Any:
        """Underlying ``AsyncEngine``. Used by the actuator health probe."""
        return self._engine

    # -- factories -----------------------------------------------------

    @classmethod
    def from_url(cls, database_url: str, *, echo: bool = False) -> ExtractionJobRepository:
        engine = create_async_engine(database_url, echo=echo, future=True, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        """Open a session for callers that need to compose multiple operations."""
        async with self._session_factory() as session:
            yield session
            await session.commit()

    # -- queries -------------------------------------------------------

    async def get(self, job_id: str) -> ExtractionJob | None:
        async with self._session_factory() as session:
            return await session.get(ExtractionJob, job_id)

    async def get_by_idempotency_key(self, key: str) -> ExtractionJob | None:
        async with self._session_factory() as session:
            result = await session.execute(select(ExtractionJob).where(ExtractionJob.idempotency_key == key))
            return result.scalars().first()

    async def list_jobs(
        self,
        *,
        statuses: list[str] | None = None,
        bbox_refine_statuses: list[str] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        idempotency_key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExtractionJob], int]:
        """Return ``(rows, total_count)`` for jobs matching the filters.

        Rows are ordered ``created_at DESC`` (newest first). The total
        count reflects the filtered set, not the page; callers paginate
        with ``limit`` / ``offset`` against that total.
        """
        conditions: list[Any] = []
        if statuses:
            conditions.append(ExtractionJob.status.in_(statuses))
        if bbox_refine_statuses:
            conditions.append(ExtractionJob.bbox_refine_status.in_(bbox_refine_statuses))
        if created_after is not None:
            conditions.append(ExtractionJob.created_at >= created_after)
        if created_before is not None:
            conditions.append(ExtractionJob.created_at <= created_before)
        if idempotency_key:
            conditions.append(ExtractionJob.idempotency_key == idempotency_key)
        async with self._session_factory() as session:
            count_stmt = select(func.count()).select_from(ExtractionJob)
            data_stmt = select(ExtractionJob).order_by(ExtractionJob.created_at.desc())
            for c in conditions:
                count_stmt = count_stmt.where(c)
                data_stmt = data_stmt.where(c)
            data_stmt = data_stmt.limit(limit).offset(offset)
            total = int((await session.execute(count_stmt)).scalar_one() or 0)
            rows = list((await session.execute(data_stmt)).scalars().all())
        return rows, total

    # -- reaper helpers ------------------------------------------------
    #
    # The :class:`flydocs.core.services.workers.JobReaper` /
    # :class:`BboxReaper` use these to find rows that are stuck in
    # non-terminal states because their triggering event was lost or
    # because the claimant crashed past its lease window. The reaper
    # republishes a fresh EDA event for each id returned; the atomic
    # ``mark_*`` claims dedupe duplicate publishes.

    async def find_stale_running(
        self,
        *,
        lease_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        """Job ids where status='RUNNING' AND started_at < now()-lease."""
        cutoff = _utcnow() - timedelta(seconds=max(0, lease_seconds))
        async with self._session_factory() as session:
            result = await session.execute(
                select(ExtractionJob.id)
                .where(
                    ExtractionJob.status == "RUNNING",
                    ExtractionJob.started_at < cutoff,
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    async def find_stale_queued(
        self,
        *,
        older_than_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        """Job ids where status='QUEUED' AND nothing's happened in a while.

        Covers both submit-crash orphans (started_at IS NULL → fall back
        to created_at) and retry-publish orphans (started_at IS NOT NULL,
        from the prior failed run). ``COALESCE`` picks the most recent
        timestamp so jobs that were briefly running and got requeued
        aren't reaped any earlier than necessary.
        """
        cutoff = _utcnow() - timedelta(seconds=max(0, older_than_seconds))
        async with self._session_factory() as session:
            result = await session.execute(
                select(ExtractionJob.id)
                .where(
                    ExtractionJob.status == "QUEUED",
                    func.coalesce(
                        ExtractionJob.started_at,
                        ExtractionJob.created_at,
                    )
                    < cutoff,
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    async def find_stale_refining_bboxes(
        self,
        *,
        lease_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        """Bbox-leg analogue of ``find_stale_running``."""
        cutoff = _utcnow() - timedelta(seconds=max(0, lease_seconds))
        async with self._session_factory() as session:
            result = await session.execute(
                select(ExtractionJob.id)
                .where(
                    ExtractionJob.status == "REFINING_BBOXES",
                    ExtractionJob.bbox_refine_started_at < cutoff,
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    async def find_pending_bbox_revive(
        self,
        *,
        partial_threshold_seconds: int,
        bbox_lease_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        """PARTIAL_SUCCEEDED rows whose bbox event needs republishing.

        Two sub-cases unified into one query:

        * ``bbox_refine_started_at IS NULL`` -- the initial publish never
          landed (worker crashed between ``mark_partial_succeeded`` and
          ``publish``). The clock starts at ``started_at`` (the main
          extraction's claim time).
        * ``bbox_refine_started_at IS NOT NULL`` -- the row was
          previously REFINING_BBOXES, the bbox worker requeued itself
          via ``requeue_bbox_refine``, and the delayed-publish task was
          lost. The clock starts at ``bbox_refine_started_at`` (the
          previous refine attempt's claim time).
        """
        now = _utcnow()
        main_cutoff = now - timedelta(seconds=max(0, partial_threshold_seconds))
        refine_cutoff = now - timedelta(seconds=max(0, bbox_lease_seconds))
        async with self._session_factory() as session:
            result = await session.execute(
                select(ExtractionJob.id)
                .where(
                    ExtractionJob.status == "PARTIAL_SUCCEEDED",
                    ExtractionJob.bbox_refine_status == "pending",
                    or_(
                        and_(
                            ExtractionJob.bbox_refine_started_at.is_(None),
                            ExtractionJob.started_at < main_cutoff,
                        ),
                        and_(
                            ExtractionJob.bbox_refine_started_at.is_not(None),
                            ExtractionJob.bbox_refine_started_at < refine_cutoff,
                        ),
                    ),
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    # -- mutations -----------------------------------------------------

    async def add(self, job: ExtractionJob) -> ExtractionJob:
        async with self._session_factory() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    # ``IntegrityError`` re-export so callers can ``except`` it without
    # importing SQLAlchemy themselves -- keeps the repository the single
    # ORM-facing boundary.
    IntegrityError = IntegrityError

    async def update(self, job_id: str, **changes: Any) -> ExtractionJob | None:
        """Unconditional field update.

        WARNING: this is a read-modify-write and is NOT safe for
        status transitions or any other field where concurrent writers
        can race. Use it only for fields no other writer touches (or
        idempotent overwrites). Status transitions go through the
        ``mark_*`` methods below.
        """
        async with self._session_factory() as session:
            job = await session.get(ExtractionJob, job_id)
            if job is None:
                return None
            for key, value in changes.items():
                setattr(job, key, value)
            await session.commit()
            await session.refresh(job)
            return job

    # ------------------------------------------------------------------
    # Atomic state transitions.
    #
    # Each method runs a single ``UPDATE ... WHERE id=? AND status IN
    # (legal_predecessors)`` against Postgres. The row-level lock on
    # UPDATE serialises concurrent writers; the WHERE precondition then
    # decides who wins. A return value of ``None`` means "the row was
    # already past this transition" -- the caller should treat the work
    # as having been done by someone else.
    # ------------------------------------------------------------------

    async def _atomic_update(
        self,
        *,
        job_id: str,
        where: Any,
        values: dict[str, Any],
    ) -> ExtractionJob | None:
        """Execute ``UPDATE ... WHERE id AND <where> RETURNING *``.

        ``RETURNING`` works on Postgres (always) and SQLite >= 3.35 (the
        Python 3.13 stdlib ships >=3.45). Combining row-level UPDATE
        locking with the WHERE-precondition predicate gives us a
        compare-and-swap without ``FOR UPDATE``.
        """
        async with self._session_factory() as session:
            stmt = (
                update(ExtractionJob)
                .where(ExtractionJob.id == job_id, where)
                .values(**values)
                .returning(ExtractionJob)
                .execution_options(synchronize_session=False)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            await session.commit()
            return row

    async def mark_running(
        self,
        job_id: str,
        *,
        lease_seconds: int,
    ) -> ExtractionJob | None:
        """Atomically claim a QUEUED job and transition it to RUNNING.

        Legal predecessors:

        * ``QUEUED`` -- first delivery (or the worker requeued itself
          for a retry).
        * ``RUNNING`` with a stale ``started_at`` -- the worker that
          owned the previous claim crashed (``lease_seconds`` window
          elapsed), so another worker can pick the orphan up.

        Returns the post-claim row when the claim won, ``None`` when
        the job is no longer claimable (e.g. cancelled, succeeded, or
        another worker holds a fresh lease).
        """
        now = _utcnow()
        stale_cutoff = now - timedelta(seconds=max(0, lease_seconds))
        return await self._atomic_update(
            job_id=job_id,
            where=or_(
                ExtractionJob.status == "QUEUED",
                and_(
                    ExtractionJob.status == "RUNNING",
                    # ``started_at < cutoff`` evaluates NULL → row excluded,
                    # so jobs that somehow have RUNNING without started_at
                    # are not reclaimed (defensive).
                    ExtractionJob.started_at < stale_cutoff,
                ),
            ),
            values={
                "status": "RUNNING",
                "started_at": now,
                "attempts": ExtractionJob.attempts + 1,
            },
        )

    async def mark_succeeded(
        self,
        job_id: str,
        *,
        result: dict[str, Any],
    ) -> ExtractionJob | None:
        """RUNNING (or REFINING_BBOXES) -> SUCCEEDED with the final result.

        ``REFINING_BBOXES`` is allowed because the bbox-refine leg
        terminates by writing SUCCEEDED through this method.
        """
        return await self._atomic_update(
            job_id=job_id,
            where=ExtractionJob.status.in_(("RUNNING", "REFINING_BBOXES")),
            values={
                "status": "SUCCEEDED",
                "finished_at": _utcnow(),
                "result_json": result,
                "error_code": None,
                "error_message": None,
            },
        )

    async def mark_failed(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
    ) -> ExtractionJob | None:
        """RUNNING -> FAILED (terminal). No-op if the row already moved on."""
        return await self._atomic_update(
            job_id=job_id,
            where=ExtractionJob.status == "RUNNING",
            values={
                "status": "FAILED",
                "finished_at": _utcnow(),
                "error_code": code,
                "error_message": message,
            },
        )

    async def mark_cancelled(self, job_id: str) -> ExtractionJob | None:
        """QUEUED -> CANCELLED. Returns None if the worker has already started.

        We deliberately do NOT permit cancelling a RUNNING job — there's
        no mid-flight cancellation hook in the orchestrator today. The
        atomic precondition guarantees a cancel sent the same instant
        the worker claims the row will either: (a) win and the worker's
        ``mark_running`` returns ``None``, or (b) lose and the caller
        gets a ``JobNotCancellable`` response. There is no third state.
        """
        return await self._atomic_update(
            job_id=job_id,
            where=ExtractionJob.status == "QUEUED",
            values={
                "status": "CANCELLED",
                "finished_at": _utcnow(),
            },
        )

    async def requeue_for_retry(self, job_id: str) -> ExtractionJob | None:
        """RUNNING -> QUEUED. Used by the worker's retry path.

        Atomic: a cancel arriving in the same instant can race with this.
        The retry's WHERE matches RUNNING; the cancel's WHERE matches
        QUEUED. They cannot both succeed -- one will return None and
        bail. (Cancel cannot match RUNNING anyway, so concretely:
        requeue wins, cancel was already rejected upstream.)
        """
        return await self._atomic_update(
            job_id=job_id,
            where=ExtractionJob.status == "RUNNING",
            values={"status": "QUEUED"},
        )

    # -- bbox-refine leg ----------------------------------------------

    async def mark_partial_succeeded(
        self,
        job_id: str,
        *,
        result: dict[str, Any],
    ) -> ExtractionJob | None:
        """RUNNING -> PARTIAL_SUCCEEDED. Main extraction done; bbox pending.

        Persists the LLM-bbox result, transitions the job, and stamps
        the bbox leg as ``pending``. Callers reading
        ``GET /api/v1/jobs/{id}/result`` get the ungrounded result
        immediately; grounded coordinates land once the refine worker
        finishes.
        """
        return await self._atomic_update(
            job_id=job_id,
            where=ExtractionJob.status == "RUNNING",
            values={
                "status": "PARTIAL_SUCCEEDED",
                "result_json": result,
                "error_code": None,
                "error_message": None,
                "bbox_refine_status": "pending",
            },
        )

    async def mark_bbox_refining(
        self,
        job_id: str,
        *,
        lease_seconds: int,
    ) -> ExtractionJob | None:
        """Atomically claim a PARTIAL_SUCCEEDED job for bbox refinement.

        Legal predecessors:

        * ``PARTIAL_SUCCEEDED`` -- the main extraction just finished
          and published the refine event.
        * ``REFINING_BBOXES`` with stale ``bbox_refine_started_at`` --
          the previous bbox-worker crashed; reclaim is allowed.

        Returns ``None`` when another worker holds a fresh lease, or
        when the job advanced to SUCCEEDED / FAILED in the meantime.
        """
        now = _utcnow()
        stale_cutoff = now - timedelta(seconds=max(0, lease_seconds))
        return await self._atomic_update(
            job_id=job_id,
            where=or_(
                ExtractionJob.status == "PARTIAL_SUCCEEDED",
                and_(
                    ExtractionJob.status == "REFINING_BBOXES",
                    ExtractionJob.bbox_refine_started_at < stale_cutoff,
                ),
            ),
            values={
                "status": "REFINING_BBOXES",
                "bbox_refine_status": "running",
                "bbox_refine_started_at": now,
                "bbox_refine_attempts": ExtractionJob.bbox_refine_attempts + 1,
            },
        )

    async def requeue_bbox_refine(self, job_id: str) -> ExtractionJob | None:
        """REFINING_BBOXES -> PARTIAL_SUCCEEDED (with status=pending).

        Used by the bbox worker's retry path: revert the leg so the next
        delivery's claim precondition matches again.
        """
        return await self._atomic_update(
            job_id=job_id,
            where=ExtractionJob.status == "REFINING_BBOXES",
            values={
                "status": "PARTIAL_SUCCEEDED",
                "bbox_refine_status": "pending",
            },
        )

    async def mark_bbox_refined(
        self,
        job_id: str,
        *,
        result: dict[str, Any],
    ) -> ExtractionJob | None:
        """REFINING_BBOXES -> SUCCEEDED with grounded coordinates."""
        return await self._atomic_update(
            job_id=job_id,
            where=ExtractionJob.status == "REFINING_BBOXES",
            values={
                "status": "SUCCEEDED",
                "finished_at": _utcnow(),
                "result_json": result,
                "bbox_refine_status": "succeeded",
                "bbox_refine_finished_at": _utcnow(),
                "bbox_refine_error_code": None,
                "bbox_refine_error_message": None,
            },
        )

    async def mark_bbox_refine_failed(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
    ) -> ExtractionJob | None:
        """REFINING_BBOXES -> PARTIAL_SUCCEEDED with a failure record.

        The LLM-bbox result stays readable; only the grounded overlay
        is missing. The caller (bbox worker) already published a
        partial webhook -- nothing new to deliver.
        """
        return await self._atomic_update(
            job_id=job_id,
            where=ExtractionJob.status == "REFINING_BBOXES",
            values={
                "status": "PARTIAL_SUCCEEDED",
                "bbox_refine_status": "failed",
                "bbox_refine_finished_at": _utcnow(),
                "bbox_refine_error_code": code,
                "bbox_refine_error_message": message,
            },
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__: list[Callable[..., Any] | str] = ["ExtractionJobRepository"]
