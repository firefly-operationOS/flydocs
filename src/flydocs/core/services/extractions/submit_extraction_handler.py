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

"""``SubmitExtractionHandler`` -- persist the extraction + publish it on the EDA bus.

Before anything is written to Postgres or the EDA outbox, the handler
runs the same :class:`RequestValidator` the sync controller uses. A
semantic mismatch (rule pointing at a non-existent document type,
cycles in the rule DAG, duplicate rule ids, ...) raises
:class:`InvalidRequestError` so the REST layer can return a ``422
validation_failed`` problem-detail with every issue surfaced -- without
persisting an unrunnable extraction.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, command_handler
from pyfly.eda import EventPublisher
from pyfly.observability.correlation import current_correlation_context

from flydocs.config import IDPSettings
from flydocs.core.services.extractions._projector import row_to_extraction
from flydocs.core.services.validation import RequestValidator, ValidationReport
from flydocs.interfaces.dtos.event import (
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EventEnvelope,
    envelope_for_publish,
)
from flydocs.interfaces.dtos.extract import ExtractionRequest
from flydocs.interfaces.dtos.extraction import Extraction, SubmitExtractionRequest
from flydocs.interfaces.enums.extraction_status import ExtractionStatus
from flydocs.models.entities.extraction import Extraction as ExtractionEntity
from flydocs.models.repositories import ExtractionRepository

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
class SubmitExtractionCommand(Command[Extraction]):
    request: SubmitExtractionRequest
    idempotency_key: str | None = None


_row_to_dto = row_to_extraction


@command_handler
@service
class SubmitExtractionHandler(CommandHandler[SubmitExtractionCommand, Extraction]):
    def __init__(
        self,
        repository: ExtractionRepository,
        event_publisher: EventPublisher,
        validator: RequestValidator,
        settings: IDPSettings,
    ) -> None:
        super().__init__()
        self._repository = repository
        self._publisher = event_publisher
        self._validator = validator
        self._settings = settings

    async def do_handle(self, command: SubmitExtractionCommand) -> Extraction:
        if command.idempotency_key:
            existing = await self._repository.get_by_idempotency_key(command.idempotency_key)
            if existing is not None:
                return _row_to_dto(existing)

        # NOTE: the SELECT-then-INSERT above has a TOCTOU window when
        # two requests submit the same idempotency_key concurrently:
        # both SELECTs miss, both INSERTs are attempted, the second
        # hits the partial unique index and the repository raises
        # ``IntegrityError``. We catch it below and re-resolve the
        # winning row instead of surfacing a 500.

        payload = command.request
        # Reuse the sync semantic validator over an ExtractionRequest
        # built from the submit payload -- same checks, same error shape.
        files = payload.files
        as_extraction = ExtractionRequest(
            intention=payload.intention,
            files=files,
            document_types=payload.document_types,
            rules=payload.rules,
            options=payload.options,
        )
        report = self._validator.validate(as_extraction)
        if report.has_errors:
            raise InvalidRequestError(report)
        for issue in report.warnings:
            logger.warning(
                "submit_validation_warning code=%s path=%s message=%s",
                issue.code,
                issue.path,
                issue.message,
            )

        # The DB row carries a single ``filename`` / ``content_sha256``
        # pair; the per-file bytes live in ``schema_json.files``. For
        # multi-file submits the primary filename summarises the bundle
        # ("first (+N more)") and the content hash rolls every file's
        # bytes so idempotency / dedupe checks still discriminate
        # different bundles correctly.
        per_file_bytes = [f.decoded_bytes() for f in files]
        total_bytes = sum(len(b) for b in per_file_bytes)
        if len(files) == 1:
            primary_filename = files[0].filename
            content_sha256 = hashlib.sha256(per_file_bytes[0]).hexdigest()
        else:
            primary_filename = f"{files[0].filename} (+{len(files) - 1} more)"[:255]
            roll = hashlib.sha256()
            for f, b in zip(files, per_file_bytes, strict=True):
                roll.update(f.filename.encode("utf-8"))
                roll.update(b)
            content_sha256 = roll.hexdigest()
        schema_json: dict[str, Any] = {
            "intention": payload.intention,
            "document_types": [d.model_dump(mode="json") for d in payload.document_types],
            "rules": [r.model_dump(mode="json") for r in payload.rules],
            "files": [
                {
                    "filename": f.filename,
                    "content_base64": f.content_base64,
                    "content_type": f.content_type,
                    "expected_type": f.expected_type,
                }
                for f in files
            ],
        }

        # Persist the inbound correlation context alongside the caller's
        # free-form metadata. The worker reads it back later to stamp
        # outbound webhook headers, so a single Correlation-Id flows from
        # the original HTTP request all the way to the webhook receiver.
        metadata = dict(payload.metadata or {})
        ctx = current_correlation_context()
        if ctx:
            metadata.setdefault("_correlation", ctx)

        extraction = ExtractionEntity(
            idempotency_key=command.idempotency_key,
            status=ExtractionStatus.QUEUED.value,
            filename=primary_filename,
            content_sha256=content_sha256,
            content_bytes=total_bytes,
            schema_json=schema_json,
            options_json=payload.options.model_dump(mode="json"),
            callback_url=str(payload.callback_url) if payload.callback_url else None,
            metadata_json=metadata,
        )
        try:
            extraction = await self._repository.add(extraction)
        except self._repository.IntegrityError:
            # Concurrent submit with the same idempotency_key collided
            # on the partial unique index. Re-resolve the winning row
            # and return its identifier -- the caller sees the same
            # idempotent response shape whether they win or lose the
            # race. We only enter this branch when an idempotency key
            # was supplied; any other unique-constraint violation
            # would be a programming error and should re-raise.
            if not command.idempotency_key:
                raise
            winner = await self._repository.get_by_idempotency_key(command.idempotency_key)
            if winner is None:
                # Vanishingly unlikely: the row that caused the
                # violation was rolled back between INSERT and our
                # follow-up SELECT. Re-raise so the caller retries.
                raise
            return _row_to_dto(winner)
        submitted_at = extraction.submitted_at or datetime.now(UTC)
        extraction_dto = _row_to_dto(extraction)
        envelope = EventEnvelope(
            event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
            occurred_at=submitted_at,
            correlation_id=(ctx or {}).get("X-Correlation-Id"),
            tenant_id=(ctx or {}).get("X-Tenant-Id"),
            extraction=extraction_dto,
        )
        await self._publisher.publish(
            destination=self._settings.jobs_topic,
            event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
            payload=envelope_for_publish(envelope),
            headers=ctx,
        )

        return extraction_dto


__all__ = ["InvalidRequestError", "SubmitExtractionCommand", "SubmitExtractionHandler"]
