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

"""Async repository for :class:`Extraction`.

Wraps an ``AsyncSession`` factory so callers can be ignorant of
transaction boundaries. Each method opens its own short-lived session +
transaction.

Concurrency model
=================

Every state-changing method is a **single conditional UPDATE** with an
explicit precondition on ``status`` (and where relevant, a lease
threshold on a timestamp). The query returns the new row only when the
precondition matched; otherwise it returns ``None`` so the caller can
detect "I lost the race". This pattern makes the transitions safe under
concurrent delivery from multiple worker replicas without needing
``SELECT ... FOR UPDATE`` or serialisable isolation -- two writers
trying the same transition will be serialised by Postgres' row-level
lock on UPDATE, and the loser's ``WHERE`` clause won't match the
already-updated row.

The v1 lifecycle is linear (``queued -> running -> succeeded | failed |
cancelled``). The bbox refinement leg is additive: a job is fully
``succeeded`` the moment the main pipeline completes; the
``post_processing_bbox_*`` columns separately track grounding progress.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from flydocs.models.entities.extraction import Extraction


class ExtractionRepository:
    """Async repository for ``extractions``."""

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
        """Underlying ``AsyncEngine`` -- used by the actuator health probe."""
        return self._engine

    @classmethod
    def from_url(cls, database_url: str, *, echo: bool = False) -> ExtractionRepository:
        engine = create_async_engine(database_url, echo=echo, future=True, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    # ---- queries -------------------------------------------------------

    async def get(self, ext_id: str) -> Extraction | None:
        async with self._session_factory() as session:
            return await session.get(Extraction, ext_id)

    async def get_by_idempotency_key(self, key: str) -> Extraction | None:
        async with self._session_factory() as session:
            result = await session.execute(select(Extraction).where(Extraction.idempotency_key == key))
            return result.scalars().first()

    async def list_extractions(
        self,
        *,
        statuses: list[str] | None = None,
        post_processing_bbox_statuses: list[str] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        idempotency_key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Extraction], int]:
        """Return ``(rows, total_count)`` for extractions matching the filters."""
        conditions: list[Any] = []
        if statuses:
            conditions.append(Extraction.status.in_(statuses))
        if post_processing_bbox_statuses:
            conditions.append(Extraction.post_processing_bbox_status.in_(post_processing_bbox_statuses))
        if created_after is not None:
            conditions.append(Extraction.submitted_at >= created_after)
        if created_before is not None:
            conditions.append(Extraction.submitted_at <= created_before)
        if idempotency_key:
            conditions.append(Extraction.idempotency_key == idempotency_key)
        async with self._session_factory() as session:
            count_stmt = select(func.count()).select_from(Extraction)
            data_stmt = select(Extraction).order_by(Extraction.submitted_at.desc())
            for c in conditions:
                count_stmt = count_stmt.where(c)
                data_stmt = data_stmt.where(c)
            data_stmt = data_stmt.limit(limit).offset(offset)
            total = int((await session.execute(count_stmt)).scalar_one() or 0)
            rows = list((await session.execute(data_stmt)).scalars().all())
        return rows, total

    # ---- reaper helpers ------------------------------------------------

    async def find_stale_running(
        self,
        *,
        lease_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        cutoff = _utcnow() - timedelta(seconds=max(0, lease_seconds))
        async with self._session_factory() as session:
            result = await session.execute(
                select(Extraction.id)
                .where(
                    Extraction.status == "running",
                    Extraction.started_at < cutoff,
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
        cutoff = _utcnow() - timedelta(seconds=max(0, older_than_seconds))
        async with self._session_factory() as session:
            result = await session.execute(
                select(Extraction.id)
                .where(
                    Extraction.status == "queued",
                    func.coalesce(Extraction.started_at, Extraction.submitted_at) < cutoff,
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    async def find_stale_bbox_refining(
        self,
        *,
        lease_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        cutoff = _utcnow() - timedelta(seconds=max(0, lease_seconds))
        async with self._session_factory() as session:
            result = await session.execute(
                select(Extraction.id)
                .where(
                    Extraction.post_processing_bbox_status == "running",
                    Extraction.post_processing_bbox_started_at < cutoff,
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    async def find_pending_bbox_revive(
        self,
        *,
        pending_threshold_seconds: int,
        bbox_lease_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        """Succeeded extractions whose bbox refinement event needs republishing.

        Two sub-cases:
        * ``post_processing_bbox_started_at IS NULL`` -- the initial publish
          never landed; clock starts at the main extraction's ``finished_at``.
        * ``post_processing_bbox_started_at IS NOT NULL`` -- the previous
          refining attempt's worker crashed or requeued itself and the
          delayed-publish task was lost; clock starts at that prior
          attempt's start.
        """
        now = _utcnow()
        main_cutoff = now - timedelta(seconds=max(0, pending_threshold_seconds))
        refine_cutoff = now - timedelta(seconds=max(0, bbox_lease_seconds))
        async with self._session_factory() as session:
            result = await session.execute(
                select(Extraction.id)
                .where(
                    Extraction.status == "succeeded",
                    Extraction.post_processing_bbox_status == "pending",
                    or_(
                        and_(
                            Extraction.post_processing_bbox_started_at.is_(None),
                            Extraction.finished_at < main_cutoff,
                        ),
                        and_(
                            Extraction.post_processing_bbox_started_at.is_not(None),
                            Extraction.post_processing_bbox_started_at < refine_cutoff,
                        ),
                    ),
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    # ---- mutations -----------------------------------------------------

    async def add(self, ext: Extraction) -> Extraction:
        async with self._session_factory() as session:
            session.add(ext)
            await session.commit()
            await session.refresh(ext)
            return ext

    IntegrityError = IntegrityError

    async def update(self, ext_id: str, **changes: Any) -> Extraction | None:
        """Unconditional field update.

        WARNING: read-modify-write; NOT safe for status transitions. Use the
        ``mark_*`` / ``request_bbox_refinement`` / ``claim_bbox_refinement``
        atomic methods below for any field where concurrent writers can race.
        """
        async with self._session_factory() as session:
            ext = await session.get(Extraction, ext_id)
            if ext is None:
                return None
            for key, value in changes.items():
                setattr(ext, key, value)
            await session.commit()
            await session.refresh(ext)
            return ext

    async def _atomic_update(
        self,
        *,
        ext_id: str,
        where: Any,
        values: dict[str, Any],
    ) -> Extraction | None:
        """Execute ``UPDATE ... WHERE id AND <where> RETURNING *``."""
        async with self._session_factory() as session:
            stmt = (
                update(Extraction)
                .where(Extraction.id == ext_id, where)
                .values(**values)
                .returning(Extraction)
                .execution_options(synchronize_session=False)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            await session.commit()
            return row

    # ---- main lifecycle transitions ------------------------------------

    async def mark_running(
        self,
        ext_id: str,
        *,
        lease_seconds: int,
    ) -> Extraction | None:
        """queued (or stale running) -> running."""
        now = _utcnow()
        stale_cutoff = now - timedelta(seconds=max(0, lease_seconds))
        return await self._atomic_update(
            ext_id=ext_id,
            where=or_(
                Extraction.status == "queued",
                and_(
                    Extraction.status == "running",
                    Extraction.started_at < stale_cutoff,
                ),
            ),
            values={
                "status": "running",
                "started_at": now,
                "attempts": Extraction.attempts + 1,
            },
        )

    async def mark_succeeded(
        self,
        ext_id: str,
        *,
        result: dict[str, Any],
        request_bbox_refinement: bool = False,
    ) -> Extraction | None:
        """running -> succeeded with the final result.

        When ``request_bbox_refinement`` is True, simultaneously sets the
        post-processing leg to ``pending`` so the refine event publisher
        can pick it up. This single atomic update is the only place where
        the bbox leg starts.
        """
        values: dict[str, Any] = {
            "status": "succeeded",
            "finished_at": _utcnow(),
            "result_json": result,
            "error_code": None,
            "error_message": None,
        }
        if request_bbox_refinement:
            values["post_processing_bbox_status"] = "pending"
        return await self._atomic_update(
            ext_id=ext_id,
            where=Extraction.status == "running",
            values=values,
        )

    async def mark_failed(
        self,
        ext_id: str,
        *,
        code: str,
        message: str,
    ) -> Extraction | None:
        return await self._atomic_update(
            ext_id=ext_id,
            where=Extraction.status == "running",
            values={
                "status": "failed",
                "finished_at": _utcnow(),
                "error_code": code,
                "error_message": message,
            },
        )

    async def mark_cancelled(self, ext_id: str) -> Extraction | None:
        return await self._atomic_update(
            ext_id=ext_id,
            where=Extraction.status == "queued",
            values={
                "status": "cancelled",
                "finished_at": _utcnow(),
            },
        )

    async def requeue_for_retry(self, ext_id: str) -> Extraction | None:
        return await self._atomic_update(
            ext_id=ext_id,
            where=Extraction.status == "running",
            values={"status": "queued"},
        )

    # ---- bbox-refinement leg -------------------------------------------

    async def claim_bbox_refinement(
        self,
        ext_id: str,
        *,
        lease_seconds: int,
    ) -> Extraction | None:
        """pending (or stale running) -> running. Bbox-leg sub-status only."""
        now = _utcnow()
        stale_cutoff = now - timedelta(seconds=max(0, lease_seconds))
        return await self._atomic_update(
            ext_id=ext_id,
            where=and_(
                Extraction.status == "succeeded",
                or_(
                    Extraction.post_processing_bbox_status == "pending",
                    and_(
                        Extraction.post_processing_bbox_status == "running",
                        Extraction.post_processing_bbox_started_at < stale_cutoff,
                    ),
                ),
            ),
            values={
                "post_processing_bbox_status": "running",
                "post_processing_bbox_started_at": now,
                "post_processing_bbox_attempts": Extraction.post_processing_bbox_attempts + 1,
            },
        )

    async def requeue_bbox_refinement(self, ext_id: str) -> Extraction | None:
        """running -> pending on the bbox-leg sub-status."""
        return await self._atomic_update(
            ext_id=ext_id,
            where=and_(
                Extraction.status == "succeeded",
                Extraction.post_processing_bbox_status == "running",
            ),
            values={"post_processing_bbox_status": "pending"},
        )

    async def complete_bbox_refinement(
        self,
        ext_id: str,
        *,
        result: dict[str, Any],
    ) -> Extraction | None:
        """running -> succeeded on the bbox-leg sub-status, with refined result."""
        return await self._atomic_update(
            ext_id=ext_id,
            where=and_(
                Extraction.status == "succeeded",
                Extraction.post_processing_bbox_status == "running",
            ),
            values={
                "result_json": result,
                "post_processing_bbox_status": "succeeded",
                "post_processing_bbox_finished_at": _utcnow(),
                "post_processing_bbox_error_code": None,
                "post_processing_bbox_error_message": None,
            },
        )

    async def fail_bbox_refinement(
        self,
        ext_id: str,
        *,
        code: str,
        message: str,
    ) -> Extraction | None:
        """running -> failed on the bbox-leg sub-status (main result is unchanged)."""
        return await self._atomic_update(
            ext_id=ext_id,
            where=and_(
                Extraction.status == "succeeded",
                Extraction.post_processing_bbox_status == "running",
            ),
            values={
                "post_processing_bbox_status": "failed",
                "post_processing_bbox_finished_at": _utcnow(),
                "post_processing_bbox_error_code": code,
                "post_processing_bbox_error_message": message,
            },
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__: list[Callable[..., Any] | str] = ["ExtractionRepository"]
