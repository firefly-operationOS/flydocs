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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from flydesk_idp.models.entities.extraction_job import ExtractionJob


class ExtractionJobRepository:
    """Async repository for ``extraction_jobs``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # -- factories -----------------------------------------------------

    @classmethod
    def from_url(cls, database_url: str, *, echo: bool = False) -> ExtractionJobRepository:
        engine = create_async_engine(database_url, echo=echo, future=True, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        return cls(factory)

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
            result = await session.execute(
                select(ExtractionJob).where(ExtractionJob.idempotency_key == key)
            )
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

    async def mark_failed(
        self, job_id: str, *, code: str, message: str
    ) -> ExtractionJob | None:
        return await self.update(
            job_id,
            status="FAILED",
            finished_at=_utcnow(),
            error_code=code,
            error_message=message,
        )

    async def mark_cancelled(self, job_id: str) -> ExtractionJob | None:
        return await self.update(job_id, status="CANCELLED", finished_at=_utcnow())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__: list[Callable[..., Any] | str] = ["ExtractionJobRepository"]
