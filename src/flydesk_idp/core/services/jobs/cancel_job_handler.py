# Copyright 2026 Firefly Software Solutions Inc
"""``CancelJobHandler`` -- cancels a job while still QUEUED."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, command_handler

from flydesk_idp.interfaces.dtos.job import JobStatusResponse
from flydesk_idp.interfaces.enums.job_status import JobStatus
from flydesk_idp.models.repositories import ExtractionJobRepository


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
        job = await self._repository.get(command.job_id)
        if job is None:
            return None
        status = JobStatus(job.status)
        if status != JobStatus.QUEUED:
            raise JobNotCancellable(
                f"Job {job.id!r} cannot be cancelled in status {status.value}"
            )
        await self._repository.mark_cancelled(job.id)
        return JobStatusResponse(
            job_id=job.id,
            status=JobStatus.CANCELLED,
            submitted_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            attempts=job.attempts,
        )
