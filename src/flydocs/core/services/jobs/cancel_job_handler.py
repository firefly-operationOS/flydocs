# Copyright 2026 Firefly Software Solutions Inc
"""``CancelJobHandler`` -- cancels a job while still QUEUED.

The cancel is a single atomic ``UPDATE ... WHERE status='QUEUED'``
against Postgres. If the row is no longer QUEUED (worker just claimed
it, the job is already terminal, or it never existed under that id),
the UPDATE matches zero rows and we surface the appropriate error.

This eliminates the previous TOCTOU window where a SELECT-then-UPDATE
pair could clobber a worker that claimed the job in between.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, command_handler

from flydocs.interfaces.dtos.job import JobStatusResponse
from flydocs.interfaces.enums.job_status import JobStatus
from flydocs.models.repositories import ExtractionJobRepository


@dataclass(frozen=True)
class CancelJobCommand(Command[JobStatusResponse | None]):
    job_id: str


class JobNotCancellable(RuntimeError):
    """Raised when the job is past the QUEUED state."""


@command_handler
@service
class CancelJobHandler(CommandHandler[CancelJobCommand, JobStatusResponse | None]):
    def __init__(self, repository: ExtractionJobRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, command: CancelJobCommand) -> JobStatusResponse | None:
        # Atomic compare-and-swap: ``mark_cancelled`` only succeeds when
        # the row's current status is QUEUED. A worker claiming the row
        # in the same instant moves the status to RUNNING with a single
        # UPDATE -- Postgres serialises the two UPDATEs by row-level lock
        # and exactly one of them matches its precondition.
        cancelled = await self._repository.mark_cancelled(command.job_id)
        if cancelled is not None:
            return JobStatusResponse(
                job_id=cancelled.id,
                status=JobStatus.CANCELLED,
                submitted_at=cancelled.created_at,
                started_at=cancelled.started_at,
                finished_at=cancelled.finished_at,
                attempts=cancelled.attempts,
            )
        # mark_cancelled returned None -- either the job doesn't exist
        # or it's past QUEUED. Distinguish the two so the REST layer
        # can emit 404 vs 409 correctly.
        job = await self._repository.get(command.job_id)
        if job is None:
            return None
        raise JobNotCancellable(f"Job {job.id!r} cannot be cancelled in status {job.status}")
