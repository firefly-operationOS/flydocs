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

"""Unified event + webhook envelope.

The same :class:`EventEnvelope` shape is published over the EDA bus
(Postgres LISTEN/NOTIFY, Kafka, Redis, in-memory) and posted to webhook
``callback_url``s. Operators see a single mental model in logs, in broker
UIs, and in receiving webhook handlers.

Event types are dotted snake_case — the only intentional exception to the
"flat snake_case enums" convention, because dots are the de-facto routing
convention for Kafka topics, EventBridge buses, and CloudEvents.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.dtos.extract import ExtractionResult
from flydocs.interfaces.dtos.extraction import Extraction

EVENT_TYPE_EXTRACTION_SUBMITTED = "extraction.submitted"
EVENT_TYPE_EXTRACTION_COMPLETED = "extraction.completed"
EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED = "extraction.post_processing.requested"
EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED = "extraction.post_processing.completed"

ALL_EVENT_TYPES = (
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _new_event_id() -> str:
    return str(uuid.uuid4())


class EventEnvelope(BaseModel):
    """Shared envelope for EDA events and webhook deliveries.

    ``extraction`` carries a current-state snapshot of the resource.
    ``result`` is populated only on ``extraction.completed`` events when
    the terminal status is ``succeeded``; null otherwise.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_event_id)
    event_type: str
    version: str = "1.0.0"
    occurred_at: datetime = Field(default_factory=_now_utc)
    correlation_id: str | None = None
    tenant_id: str | None = None
    extraction: Extraction
    result: ExtractionResult | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def envelope_for_publish(env: EventEnvelope) -> dict[str, Any]:
    """Serialise an envelope for :class:`EventPublisher.publish` payloads.

    ``mode="json"`` so datetimes become ISO strings and enums become their
    string values. ``by_alias=True`` for parity with any pydantic aliases
    consumers register.
    """
    return env.model_dump(mode="json", by_alias=True)


__all__ = [
    "ALL_EVENT_TYPES",
    "EVENT_TYPE_EXTRACTION_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED",
    "EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED",
    "EVENT_TYPE_EXTRACTION_SUBMITTED",
    "EventEnvelope",
    "envelope_for_publish",
]
