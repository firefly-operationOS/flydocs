# Copyright 2026 Firefly Software Solutions Inc
"""Unified :class:`EventEnvelope` covering EDA + webhook deliveries.

Coverage:

1. Defaults populate ``event_id`` (UUID4), ``occurred_at`` (UTC datetime),
   ``version`` and accept any of the four canonical event-type strings.
2. ``envelope_for_publish`` produces a JSON-friendly dict suitable for
   :func:`EventPublisher.publish(payload=...)`: datetimes serialise to ISO
   strings, the event id round-trips, and enums become their string values.
3. The envelope round-trips through pydantic from raw dicts the EDA bus
   would deliver, so the consumer-side parse is loss-free.
4. The four event-type constants carry the dotted snake_case form
   (the deliberate exception to the flat-snake convention).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from flydocs.interfaces.dtos.event import (
    ALL_EVENT_TYPES,
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EventEnvelope,
    envelope_for_publish,
)
from flydocs.interfaces.dtos.extract import (
    ExtractionResult,
    PipelineMeta,
)
from flydocs.interfaces.dtos.extraction import Extraction
from flydocs.interfaces.enums.extraction_status import ExtractionStatus


def _extraction(status: ExtractionStatus = ExtractionStatus.QUEUED) -> Extraction:
    return Extraction(
        id="ext_TEST00000000000000000000000",
        status=status,
        submitted_at=datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Event-type constants
# ---------------------------------------------------------------------------


def test_event_type_constants_use_dotted_snake_case() -> None:
    """The deliberate exception to flat snake_case enums."""
    assert EVENT_TYPE_EXTRACTION_SUBMITTED == "extraction.submitted"
    assert EVENT_TYPE_EXTRACTION_COMPLETED == "extraction.completed"
    assert EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED == "extraction.post_processing.requested"
    assert EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED == "extraction.post_processing.completed"
    assert set(ALL_EVENT_TYPES) == {
        EVENT_TYPE_EXTRACTION_SUBMITTED,
        EVENT_TYPE_EXTRACTION_COMPLETED,
        EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
        EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
    }


# ---------------------------------------------------------------------------
# Envelope defaults
# ---------------------------------------------------------------------------


def test_envelope_defaults_populate_id_timestamp_version() -> None:
    """Constructor populates id + timestamp + version."""
    env = EventEnvelope(
        event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
        extraction=_extraction(),
    )
    # event_id is a valid UUID4 string.
    parsed = uuid.UUID(env.event_id)
    assert parsed.version == 4
    # occurred_at is timezone-aware UTC.
    assert isinstance(env.occurred_at, datetime)
    assert env.occurred_at.tzinfo is not None
    # Discriminator carries the dotted snake form.
    assert env.event_type == "extraction.submitted"
    assert env.version == "1.0.0"


# ---------------------------------------------------------------------------
# envelope_for_publish serialisation
# ---------------------------------------------------------------------------


def test_envelope_for_publish_is_json_friendly() -> None:
    """The serialiser produces a primitive dict suitable for EventPublisher."""
    occurred = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    env = EventEnvelope(
        event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
        occurred_at=occurred,
        correlation_id="cor-42",
        tenant_id="acme",
        extraction=_extraction(),
    )
    payload = envelope_for_publish(env)
    # Discriminator preserved verbatim.
    assert payload["event_type"] == "extraction.submitted"
    assert payload["correlation_id"] == "cor-42"
    assert payload["tenant_id"] == "acme"
    # Datetimes become ISO strings (mode='json').
    assert payload["occurred_at"].startswith("2026-05-15T12:00:00")
    # event_id is preserved.
    assert payload["event_id"] == env.event_id
    # Nested extraction is serialised as a dict with the enum coerced to its string value.
    assert payload["extraction"]["id"] == "ext_TEST00000000000000000000000"
    assert payload["extraction"]["status"] == "queued"


# ---------------------------------------------------------------------------
# Round-trip across the bus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    list(ALL_EVENT_TYPES),
)
def test_envelope_round_trips_through_serialise_parse(event_type: str) -> None:
    """Every event type re-parses correctly from its serialised dict."""
    env = EventEnvelope(
        event_type=event_type,
        extraction=_extraction(ExtractionStatus.SUCCEEDED),
    )
    raw = env.model_dump(mode="json")
    parsed = EventEnvelope.model_validate(raw)
    assert parsed.event_type == event_type
    assert parsed.event_id == env.event_id
    assert parsed.extraction.id == env.extraction.id
    assert parsed.extraction.status == ExtractionStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Result is populated only on success
# ---------------------------------------------------------------------------


def test_envelope_can_carry_full_result() -> None:
    """``result`` is null by default; completed-success events fill it."""
    result = ExtractionResult(
        id="ext_TEST00000000000000000000000",
        files=[],
        documents=[],
        pipeline=PipelineMeta(model="m", latency_ms=1),
    )
    env = EventEnvelope(
        event_type=EVENT_TYPE_EXTRACTION_COMPLETED,
        extraction=_extraction(ExtractionStatus.SUCCEEDED),
        result=result,
    )
    payload = envelope_for_publish(env)
    assert payload["result"]["id"] == "ext_TEST00000000000000000000000"
    assert payload["result"]["pipeline"]["model"] == "m"


def test_envelope_result_defaults_to_null() -> None:
    env = EventEnvelope(
        event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
        extraction=_extraction(),
    )
    assert env.result is None
    payload = envelope_for_publish(env)
    assert payload["result"] is None
