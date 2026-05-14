# Copyright 2026 Firefly Software Solutions Inc
"""Outbound webhook payload delivered to ``callback_url``.

Sent by :class:`flydesk_idp.core.services.webhook.webhook_publisher.WebhookPublisher`
when an async job reaches a terminal state. Signed with HMAC-SHA256 in
the ``X-Flydesk-Signature`` header when ``FLYDESK_IDP_WEBHOOK_HMAC_SECRET``
is configured.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from flydesk_idp.interfaces.dtos.extract import ExtractionResult
from flydesk_idp.interfaces.enums.job_status import JobStatus


class JobWebhookPayload(BaseModel):
    job_id: str
    status: JobStatus
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    result: ExtractionResult | None = Field(
        default=None,
        description="Populated when status is SUCCEEDED. Null on FAILED / CANCELLED.",
    )
    error_code: str | None = None
    error_message: str | None = None
