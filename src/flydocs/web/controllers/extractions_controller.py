# Copyright 2026 Firefly Software Solutions Inc
"""Asynchronous extraction endpoints -- ``POST /api/v1/extractions`` + lifecycle."""

from __future__ import annotations

import logging
from datetime import datetime

from pyfly.container import rest_controller

# Depend on the concrete bus classes -- pyfly's container resolves by
# exact type and the CQRS auto-config registers DefaultCommandBus /
# DefaultQueryBus (the Protocols are not registered as bean types).
from pyfly.cqrs import DefaultCommandBus, DefaultQueryBus
from pyfly.kernel import ResourceNotFoundException
from pyfly.web import (
    Body,
    Header,
    PathVar,
    QueryParam,
    Valid,
    delete_mapping,
    get_mapping,
    post_mapping,
    request_mapping,
)

from flydocs.core.services.extractions import (
    CancelExtractionCommand,
    GetExtractionQuery,
    GetExtractionResultQuery,
    ListExtractionsQuery,
    SubmitExtractionCommand,
)
from flydocs.core.services.extractions.cancel_extraction_handler import (
    ExtractionNotCancellable,
)
from flydocs.core.services.extractions.get_extraction_result_handler import (
    ExtractionNotReady,
)
from flydocs.core.services.extractions.submit_extraction_handler import (
    InvalidRequestError,
)
from flydocs.interfaces.dtos.extraction import (
    Extraction,
    ExtractionListResponse,
    ExtractionResultEnvelope,
    SubmitExtractionRequest,
)
from flydocs.interfaces.enums.extraction_status import (
    ExtractionStatus,
    PostProcessingStatus,
)

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/extractions")
class ExtractionsController:
    """REST adapter for the asynchronous, queue-backed extraction API.

    The five endpoints cover the full lifecycle: submit (returns an
    extraction id and 202), list, poll status, fetch the final result
    envelope, cancel.

    Submit honours an ``Idempotency-Key`` header so a retried submission
    returns the original response instead of a duplicate row.
    """

    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    @post_mapping("", status_code=202)
    async def submit(
        self,
        request: Valid[Body[SubmitExtractionRequest]],
        idempotency_key: Header[str] = "",
    ) -> Extraction:
        """Submit a queued extraction.

        The request body is the same as ``POST /api/v1/extract`` plus
        the optional ``callback_url`` and ``metadata`` fields. The
        endpoint persists the extraction, publishes it to the bus, and
        returns ``202 Accepted`` with the new extraction id and the
        initial ``queued`` status. The worker drives the same pipeline
        as the sync endpoint behind the scenes.

        Send the same ``Idempotency-Key`` header to replay an existing
        submission instead of creating a duplicate row. The handler also
        runs the semantic ``RequestValidator`` before persisting; a
        mismatch -- e.g. a rule referencing an unknown document type --
        returns ``422 validation_failed`` with every issue surfaced, and
        nothing is written to Postgres or the EDA outbox.
        """
        try:
            return await self._commands.send(
                SubmitExtractionCommand(request=request, idempotency_key=idempotency_key or None)
            )
        except InvalidRequestError as exc:
            raise _http_problem_with_payload(
                status_code=422,
                code="validation_failed",
                title="Request failed semantic validation",
                detail=(
                    f"{len(exc.report.errors)} error(s) and "
                    f"{len(exc.report.warnings)} warning(s) detected before queueing."
                ),
                extra=exc.report.to_payload(),
            ) from exc

    @get_mapping("")
    async def list_extractions(
        self,
        status: QueryParam[str] = "",
        post_processing_status: QueryParam[str] = "",
        idempotency_key: QueryParam[str] = "",
        created_after: QueryParam[str] = "",
        created_before: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> ExtractionListResponse:
        """Paginated, filterable listing of extractions.

        Filters are optional and combine with ``AND``:

        * ``status`` -- comma-separated list of statuses (e.g.
          ``?status=succeeded,failed``). Empty = any status.
        * ``post_processing_status`` -- comma-separated list of
          post-processing bbox sub-states: ``pending``, ``running``,
          ``succeeded``, ``failed``.
        * ``idempotency_key`` -- exact match against the submit-time key.
        * ``created_after`` / ``created_before`` -- RFC 3339 timestamps,
          both inclusive.

        Rows are returned ``submitted_at DESC`` (newest first) with
        ``total`` reflecting the filtered set so the caller can paginate.
        ``limit`` is capped at 500.
        """
        return await self._queries.query(
            ListExtractionsQuery(
                statuses=tuple(ExtractionStatus(s) for s in _split_csv(status)),
                post_processing_statuses=tuple(
                    PostProcessingStatus(s) for s in _split_csv(post_processing_status)
                ),
                created_after=_parse_iso(created_after),
                created_before=_parse_iso(created_before),
                idempotency_key=idempotency_key or None,
                limit=int(limit),
                offset=int(offset),
            )
        )

    @get_mapping("/{extraction_id}")
    async def get_status(self, extraction_id: PathVar[str]) -> Extraction:
        """Read the current state of an extraction.

        Returns the lifecycle metadata (``queued`` / ``running`` /
        ``succeeded`` / ``failed`` / ``cancelled``), the attempt
        counter, the timestamps for submission / start / finish, and
        (when applicable) the additive ``post_processing`` block.
        Returns ``404`` for an unknown id.
        """
        status = await self._queries.query(GetExtractionQuery(extraction_id=extraction_id))
        if status is None:
            raise ResourceNotFoundException(
                f"Extraction {extraction_id!r} not found",
                code="not_found",
                context={"extraction_id": extraction_id},
            )
        return status

    @get_mapping("/{extraction_id}/result")
    async def get_result(
        self,
        extraction_id: PathVar[str],
        wait_for_post_processing: QueryParam[bool] = False,
        timeout: QueryParam[float] = 60.0,
    ) -> ExtractionResultEnvelope:
        """Fetch the :class:`ExtractionResult` for a succeeded extraction.

        Returns the result when the extraction is in ``succeeded``.
        ``queued`` / ``running`` / ``failed`` / ``cancelled`` return
        ``409 not_ready``. Unknown id returns ``404``.

        ``wait_for_post_processing=true`` long-polls the row until the
        bbox refinement leg finishes (``post_processing.bbox_refinement.status``
        ∈ ``succeeded`` / ``failed``) or ``timeout`` (seconds, default 60)
        elapses; on timeout the result is returned with whatever bbox
        coordinates are currently persisted.
        """
        try:
            result = await self._queries.query(
                GetExtractionResultQuery(
                    extraction_id=extraction_id,
                    wait_for_post_processing=bool(wait_for_post_processing),
                    timeout_s=float(timeout),
                )
            )
        except ExtractionNotReady as exc:
            raise _http_problem(409, "not_ready", "Extraction not ready", str(exc)) from exc
        if result is None:
            raise ResourceNotFoundException(
                f"Extraction {extraction_id!r} not found",
                code="not_found",
                context={"extraction_id": extraction_id},
            )
        return result

    @delete_mapping("/{extraction_id}")
    async def cancel(self, extraction_id: PathVar[str]) -> Extraction:
        """Cancel an extraction that hasn't started yet.

        Only valid while ``status == queued``. After the worker has
        started on an extraction there is no mid-flight cancellation
        hook -- the endpoint returns ``409 not_cancellable``. Unknown
        id returns ``404``.
        """
        try:
            cancelled = await self._commands.send(CancelExtractionCommand(extraction_id=extraction_id))
        except ExtractionNotCancellable as exc:
            raise _http_problem(409, "not_cancellable", "Extraction cannot be cancelled", str(exc)) from exc
        if cancelled is None:
            raise ResourceNotFoundException(
                f"Extraction {extraction_id!r} not found",
                code="not_found",
                context={"extraction_id": extraction_id},
            )
        return cancelled


def _split_csv(value: str) -> list[str]:
    """Split a comma-separated query value into trimmed non-empty tokens."""
    if not value:
        return []
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def _parse_iso(value: str) -> datetime | None:
    """Parse an RFC 3339 timestamp; return ``None`` for empty input."""
    if not value:
        return None
    # Python's ``fromisoformat`` accepts ``Z`` from 3.11+ but be defensive.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _http_problem(status_code: int, code: str, title: str, detail: str) -> Exception:
    from fastapi import HTTPException

    return HTTPException(
        status_code=status_code,
        detail={"code": code, "title": title, "detail": detail},
    )


def _http_problem_with_payload(
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    extra: dict,
) -> Exception:
    """RFC 7807-ish problem-detail that also surfaces the validator findings."""
    from fastapi import HTTPException

    body = {"code": code, "title": title, "detail": detail, **extra}
    return HTTPException(status_code=status_code, detail=body)
