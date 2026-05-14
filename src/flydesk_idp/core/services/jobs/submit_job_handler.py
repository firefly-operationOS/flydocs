# Copyright 2026 Firefly Software Solutions Inc
"""``SubmitJobHandler`` -- persist the job + publish it on the EDA bus.

Before anything is written to Postgres or the EDA outbox, the handler
runs the same :class:`RequestValidator` the sync controller uses. A
semantic mismatch (rule pointing at a non-existent docType, cycles in
the rule DAG, duplicate rule ids, ...) raises :class:`InvalidRequestError`
so the REST layer can return a ``422 invalid_request`` problem-detail
with every issue surfaced -- without persisting an unrunnable job.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, command_handler
from pyfly.eda import EventPublisher
from pyfly.observability.correlation import current_correlation_context

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.validation import RequestValidator, ValidationReport
from flydesk_idp.interfaces.dtos.extract import ExtractionRequest
from flydesk_idp.interfaces.dtos.job import SubmitJobRequest, SubmitJobResponse
from flydesk_idp.interfaces.enums.job_status import JobStatus
from flydesk_idp.models.entities.extraction_job import ExtractionJob
from flydesk_idp.models.repositories import ExtractionJobRepository

logger = logging.getLogger(__name__)


class InvalidRequestError(ValueError):
    """Raised when the semantic validator finds errors on a submit.

    Carries the full :class:`ValidationReport` so the REST controller
    can surface every issue to the caller in one shot.
    """

    def __init__(self, report: ValidationReport) -> None:
        super().__init__(f"{len(report.errors)} validation error(s) on submit")
        self.report = report


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
        event_publisher: EventPublisher,
        validator: RequestValidator,
        settings: IDPSettings,
    ) -> None:
        super().__init__()
        self._repository = repository
        self._publisher = event_publisher
        self._validator = validator
        self._settings = settings

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
        # Reuse the sync semantic validator over an ExtractionRequest
        # built from the submit payload -- same checks, same error shape.
        as_extraction = ExtractionRequest(
            intention=payload.intention,
            document=payload.document,
            docs=payload.docs,
            rules=payload.rules,
            options=payload.options,
        )
        report = self._validator.validate(as_extraction)
        if report.has_errors:
            raise InvalidRequestError(report)
        for issue in report.warnings:
            logger.warning(
                "submit_validation_warning code=%s path=%s message=%s",
                issue.code, issue.path, issue.message,
            )

        bytes_decoded = payload.document.decoded_bytes()
        content_sha256 = hashlib.sha256(bytes_decoded).hexdigest()

        # Persist the inbound correlation context alongside the caller's
        # free-form metadata. The worker reads it back later to stamp
        # outbound webhook headers, so a single Correlation-Id flows from
        # the original HTTP request all the way to the webhook receiver.
        metadata = dict(payload.metadata or {})
        ctx = current_correlation_context()
        if ctx:
            metadata.setdefault("_correlation", ctx)

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
            metadata_json=metadata,
        )
        job = await self._repository.add(job)
        await self._publisher.publish(
            destination=self._settings.jobs_topic,
            event_type=self._settings.jobs_event_type,
            payload={"job_id": job.id},
            headers=ctx,
        )

        return SubmitJobResponse(
            job_id=job.id,
            status=JobStatus(job.status),
            submitted_at=job.created_at or datetime.now(timezone.utc),
        )
