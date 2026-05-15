# Copyright 2026 Firefly Software Solutions Inc
"""Typed EDA event envelopes published / consumed by flydesk-idp.

Every event the service publishes carries:

* ``event_id`` -- a fresh UUID v4. Lets clients dedupe at-least-once
  deliveries, correlate webhook callbacks with outbox rows, and
  reference a specific notification in audit trails.
* ``event_type`` -- the same constant string the EDA bus uses for
  routing (``IDPJobSubmitted``, ``IDPJobCompleted``,
  ``IDPBboxRefineRequested``, ``IDPBboxRefineCompleted``).
* ``version`` -- semver-style payload version. Bump when you change
  the payload shape in a non-backwards-compatible way so consumers
  can branch on it.
* ``occurred_at`` -- UTC ISO-8601 timestamp; when the event was
  produced by the originating service.
* ``correlation_id`` -- request-level correlation that propagates
  through the whole pipeline. Echoes ``X-Correlation-Id``.
* Type-specific payload fields (``job_id``, optionally ``attempt``,
  ``status``, error info, …).

Consumers of the async API (webhooks, the EDA workers themselves)
read the typed envelope rather than the raw dict — see
``JobWorker._on_event`` and ``BboxRefineWorker._on_event``. The
webhook payload now also embeds the envelope under
``event`` so external clients get the same audit surface the
internal workers see.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flydesk_idp.interfaces.enums.job_status import JobStatus


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _new_event_id() -> str:
    return str(uuid.uuid4())


class _BaseEvent(BaseModel):
    """Shared envelope: identity, lifecycle, correlation."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(
        default_factory=_new_event_id,
        description="Unique UUID v4 identifier for this event instance.",
    )
    event_type: str = Field(
        description=(
            "EDA topic constant — must match "
            ":class:`IDPSettings.jobs_event_type` / "
            "``bbox_refine_event_type`` / ``jobs_completed_event_type``."
        ),
    )
    version: str = Field(
        default="1.0.0",
        description=(
            "Semver-style payload version. Consumers should compare "
            "the major component to decide if they understand the "
            "shape; minor / patch are backwards-compatible."
        ),
    )
    occurred_at: datetime = Field(
        default_factory=_now_utc,
        description="UTC timestamp at which the producing service emitted this event.",
    )
    correlation_id: str | None = Field(
        default=None,
        description=(
            "Request-level correlation id propagated through every "
            "stage of the pipeline. Mirrors the value of the inbound "
            "``X-Correlation-Id`` / ``traceparent`` headers when set."
        ),
    )
    tenant_id: str | None = Field(
        default=None,
        description="Optional tenant identifier (echoes ``X-Tenant-Id`` when present).",
    )


class IDPJobSubmittedEvent(_BaseEvent):
    """Published by ``SubmitJobHandler`` after the job row is persisted.

    Triggers ``JobWorker._on_event`` -> ``_process``.
    Re-published by ``JobWorker._delayed_publish`` with the same
    payload shape during retry back-off (``attempt > 1``).
    """

    event_type: Literal["IDPJobSubmitted"] = "IDPJobSubmitted"
    job_id: str = Field(description="Stable UUID of the :class:`ExtractionJob` row.")
    attempt: int = Field(
        default=1,
        ge=1,
        description="1 on first submission, increments on each retry republish.",
    )
    submitted_at: datetime = Field(
        default_factory=_now_utc,
        description=(
            "Persisted ``ExtractionJob.created_at``. May differ from "
            "``occurred_at`` when this is a retry republish (occurred "
            "is *now*, submitted is the original submission time)."
        ),
    )


class IDPJobCompletedEvent(_BaseEvent):
    """Published when a job reaches a terminal state.

    Terminal here means any of ``SUCCEEDED``, ``PARTIAL_SUCCEEDED``,
    ``FAILED``, ``CANCELLED``. Webhook subscribers receive an envelope
    that wraps this event for parity with the EDA bus.
    """

    event_type: Literal["IDPJobCompleted"] = "IDPJobCompleted"
    job_id: str
    status: JobStatus
    started_at: datetime | None = Field(
        default=None,
        description="When the worker first picked the job up.",
    )
    finished_at: datetime | None = Field(
        default=None,
        description="Terminal state timestamp.",
    )
    attempts: int = Field(
        default=1,
        ge=1,
        description="Total attempts consumed before reaching this terminal state.",
    )
    error_code: str | None = None
    error_message: str | None = None


class IDPBboxRefineRequestedEvent(_BaseEvent):
    """Fan-out event the main worker emits when ``stages.bbox_refine`` is on.

    ``BboxRefineWorker._on_event`` consumes it and grounds the bboxes
    out-of-band, then publishes :class:`IDPBboxRefineCompletedEvent`
    once it finishes (success or terminal failure).
    """

    event_type: Literal["IDPBboxRefineRequested"] = "IDPBboxRefineRequested"
    job_id: str
    attempt: int = Field(default=1, ge=1)


class IDPBboxRefineCompletedEvent(_BaseEvent):
    """Emitted by ``BboxRefineWorker`` after refinement settles."""

    event_type: Literal["IDPBboxRefineCompleted"] = "IDPBboxRefineCompleted"
    job_id: str
    status: Literal["succeeded", "failed"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = Field(default=1, ge=1)
    error_code: str | None = None
    error_message: str | None = None


# Discriminated union of every event the service can produce or consume.
IDPEvent = Annotated[
    IDPJobSubmittedEvent | IDPJobCompletedEvent | IDPBboxRefineRequestedEvent | IDPBboxRefineCompletedEvent,
    Field(discriminator="event_type"),
]


def envelope_for_publish(event: _BaseEvent) -> dict[str, Any]:
    """Serialise an event for ``EventPublisher.publish(payload=...)``.

    ``mode="json"`` so the datetime turns into ISO strings, the UUID
    into its hex form, and pydantic does the right thing for the
    enums. ``by_alias=True`` so we emit the canonical field names
    even when callers register aliases on subclasses.
    """
    return event.model_dump(mode="json", by_alias=True)


__all__ = [
    "IDPBboxRefineCompletedEvent",
    "IDPBboxRefineRequestedEvent",
    "IDPEvent",
    "IDPJobCompletedEvent",
    "IDPJobSubmittedEvent",
    "envelope_for_publish",
]
