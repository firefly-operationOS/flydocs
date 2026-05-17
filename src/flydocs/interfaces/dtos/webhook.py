# Copyright 2026 Firefly Software Solutions Inc
"""Outbound webhook payload delivered to ``callback_url``.

Sent by :class:`flydocs.core.services.webhook.webhook_publisher.WebhookPublisher`
when an async job reaches a terminal state. Signed with HMAC-SHA256 in
the ``X-Flydocs-Signature`` header when ``FLYDOCS_WEBHOOK_HMAC_SECRET``
is configured.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from flydocs.interfaces.dtos.extract import ExtractionResult
from flydocs.interfaces.enums.job_status import JobStatus


def _new_event_id() -> str:
    return str(uuid.uuid4())


class JobWebhookPayload(BaseModel):
    """Webhook envelope mirroring the typed EDA events.

    Carries identity (``event_id``, ``job_id``), lifecycle
    (``occurred_at``, ``started_at``, ``finished_at``), correlation
    (``correlation_id``), and the terminal status. Consumers should
    dedupe by ``event_id`` since the webhook publisher retries on
    delivery failures.
    """

    event_id: str = Field(
        default_factory=_new_event_id,
        description="Unique UUID v4 for this webhook delivery. Use to dedupe on the client.",
    )
    event_type: str = Field(
        default="IDPJobCompleted",
        description="Mirrors the EDA event type that triggered this delivery.",
    )
    version: str = Field(default="1.0.0", description="Payload schema version (semver).")
    job_id: str
    status: JobStatus
    occurred_at: datetime
    started_at: datetime | None = Field(
        default=None,
        description="When the worker first picked the job up.",
    )
    finished_at: datetime | None = Field(
        default=None,
        description="Terminal-state timestamp; mirrors ``ExtractionJob.finished_at``.",
    )
    attempts: int = Field(default=1, ge=1, description="Worker attempts consumed.")
    correlation_id: str | None = Field(
        default=None,
        description="Request-level correlation id propagated through every stage.",
    )
    tenant_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    result: ExtractionResult | None = Field(
        default=None,
        description="Populated when status is SUCCEEDED. Null on FAILED / CANCELLED.",
    )
    error_code: str | None = None
    error_message: str | None = None
