# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for :class:`ExtractionJob`.

Wraps an ``AsyncSession`` factory so callers can be ignorant of the
transaction boundaries. Each method opens its own short-lived session
+ transaction; results are detached pydantic-style dicts so callers
don't accidentally lazy-load through a closed session.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from flydesk_idp.models.entities.extraction_job import ExtractionJob


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

    # -- mutations -----------------------------------------------------

    async def add(self, job: ExtractionJob) -> ExtractionJob:
        async with self._session_factory() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    async def update(self, job_id: str, **changes: Any) -> ExtractionJob | None:
        """Apply *changes* atomically; returns the updated row or None."""
        async with self._session_factory() as session:
            job = await session.get(ExtractionJob, job_id)
            if job is None:
                return None
            for key, value in changes.items():
                setattr(job, key, value)
            await session.commit()
            await session.refresh(job)
            return job

    async def mark_running(self, job_id: str) -> ExtractionJob | None:
        """Transition to RUNNING and increment the attempts counter atomically."""
        async with self._session_factory() as session:
            job = await session.get(ExtractionJob, job_id)
            if job is None:
                return None
            job.status = "RUNNING"
            job.started_at = _utcnow()
            # Increment in Python: the SQLAlchemy ORM session writes back the
            # new scalar on commit. A bare ``Column + 1`` expression doesn't
            # work with setattr on a managed instance.
            job.attempts = (job.attempts or 0) + 1
            await session.commit()
            await session.refresh(job)
            return job

    async def mark_succeeded(self, job_id: str, *, result: dict[str, Any]) -> ExtractionJob | None:
        return await self.update(
            job_id,
            status="SUCCEEDED",
            finished_at=_utcnow(),
            result_json=result,
            error_code=None,
            error_message=None,
        )

    async def mark_failed(self, job_id: str, *, code: str, message: str) -> ExtractionJob | None:
        return await self.update(
            job_id,
            status="FAILED",
            finished_at=_utcnow(),
            error_code=code,
            error_message=message,
        )

    async def mark_cancelled(self, job_id: str) -> ExtractionJob | None:
        return await self.update(job_id, status="CANCELLED", finished_at=_utcnow())

    # -- bbox-refine leg ----------------------------------------------

    async def mark_partial_succeeded(self, job_id: str, *, result: dict[str, Any]) -> ExtractionJob | None:
        """Main extraction done; bbox refine pending.

        Persists the LLM-bbox result, transitions the job to
        ``PARTIAL_SUCCEEDED``, and stamps the bbox leg as ``pending``.
        Callers reading ``GET /api/v1/jobs/{id}/result`` get the
        ungrounded result immediately; grounded coordinates land once
        the refine worker finishes.
        """
        return await self.update(
            job_id,
            status="PARTIAL_SUCCEEDED",
            result_json=result,
            error_code=None,
            error_message=None,
            bbox_refine_status="pending",
        )

    async def mark_bbox_refining(self, job_id: str) -> ExtractionJob | None:
        """Bbox worker has picked up the event and started grounding.

        Atomically transitions ``PARTIAL_SUCCEEDED`` -> ``REFINING_BBOXES``
        and increments the bbox attempt counter so retries are bounded.
        """
        async with self._session_factory() as session:
            job = await session.get(ExtractionJob, job_id)
            if job is None:
                return None
            job.status = "REFINING_BBOXES"
            job.bbox_refine_status = "running"
            job.bbox_refine_started_at = _utcnow()
            job.bbox_refine_attempts = (job.bbox_refine_attempts or 0) + 1
            await session.commit()
            await session.refresh(job)
            return job

    async def mark_bbox_refined(self, job_id: str, *, result: dict[str, Any]) -> ExtractionJob | None:
        """Refiner produced grounded coordinates; flip to fully SUCCEEDED."""
        return await self.update(
            job_id,
            status="SUCCEEDED",
            finished_at=_utcnow(),
            result_json=result,
            bbox_refine_status="succeeded",
            bbox_refine_finished_at=_utcnow(),
            bbox_refine_error_code=None,
            bbox_refine_error_message=None,
        )

    async def mark_bbox_refine_failed(self, job_id: str, *, code: str, message: str) -> ExtractionJob | None:
        """Refiner gave up; revert to ``PARTIAL_SUCCEEDED`` so the LLM-bbox
        result stays readable. Failure context is captured on the row.
        """
        return await self.update(
            job_id,
            status="PARTIAL_SUCCEEDED",
            bbox_refine_status="failed",
            bbox_refine_finished_at=_utcnow(),
            bbox_refine_error_code=code,
            bbox_refine_error_message=message,
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__: list[Callable[..., Any] | str] = ["ExtractionJobRepository"]
