# Copyright 2026 Firefly Software Solutions Inc
"""``SubmitJobHandler`` -- persist the job + publish it to the queue."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, command_handler

from flydesk_idp.core.services.queue import JobQueue
from flydesk_idp.interfaces.dtos.job import SubmitJobRequest, SubmitJobResponse
from flydesk_idp.interfaces.enums.job_status import JobStatus
from flydesk_idp.models.entities.extraction_job import ExtractionJob
from flydesk_idp.models.repositories import ExtractionJobRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmitJobCommand(Command[SubmitJobResponse]):
    request: SubmitJobRequest
    idempotency_key: str | None = None


@command_handler
@service
class SubmitJobHandler(CommandHandler[SubmitJobCommand, SubmitJobResponse]):
    def __init__(
        self,
        repository: ExtractionJobRepository,
        queue: JobQueue,
    ) -> None:
        super().__init__()
        self._repository = repository
        self._queue = queue

    async def do_handle(self, command: SubmitJobCommand) -> SubmitJobResponse:
        if command.idempotency_key:
            existing = await self._repository.get_by_idempotency_key(command.idempotency_key)
            if existing is not None:
                return SubmitJobResponse(
                    job_id=existing.id,
                    status=JobStatus(existing.status),
                    submitted_at=existing.created_at,
                )

        payload = command.request
        bytes_decoded = payload.document.decoded_bytes()
        content_sha256 = hashlib.sha256(bytes_decoded).hexdigest()

        job = ExtractionJob(
            idempotency_key=command.idempotency_key,
            status=JobStatus.QUEUED.value,
            filename=payload.document.filename,
            content_sha256=content_sha256,
            content_bytes=len(bytes_decoded),
            schema_json={
                "intention": payload.intention,
                "docs": [d.model_dump(mode="json") for d in payload.docs],
                "rules": [r.model_dump(mode="json") for r in payload.rules],
                "document_content_base64": payload.document.content_base64,
                "document_content_type": payload.document.content_type,
            },
            options_json=payload.options.model_dump(mode="json"),
            callback_url=str(payload.callback_url) if payload.callback_url else None,
            metadata_json=payload.metadata,
        )
        job = await self._repository.add(job)
        await self._queue.publish(job.id)

        return SubmitJobResponse(
            job_id=job.id,
            status=JobStatus(job.status),
            submitted_at=job.created_at or datetime.now(timezone.utc),
        )
