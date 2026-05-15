# Copyright 2026 Firefly Software Solutions Inc
"""Typed EDA event envelopes.

Coverage:

1. ``IDPJobSubmittedEvent`` defaults populate ``event_id`` (UUID),
   ``occurred_at`` (UTC datetime), ``version``, and ``event_type``.
2. ``envelope_for_publish`` produces a JSON-friendly dict suitable
   for ``EventPublisher.publish(payload=...)`` — datetimes serialise
   to ISO strings, the event id round-trips, and the enum discriminator
   matches the constant pyfly will route on.
3. The discriminated union (``IDPEvent``) round-trips through pydantic
   from raw dicts the EDA bus would deliver, so the consumer-side
   parse is loss-free.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import TypeAdapter

from flydesk_idp.interfaces.dtos.event import (
    IDPBboxRefineCompletedEvent,
    IDPBboxRefineRequestedEvent,
    IDPEvent,
    IDPJobCompletedEvent,
    IDPJobSubmittedEvent,
    envelope_for_publish,
)
from flydesk_idp.interfaces.enums.job_status import JobStatus


def test_submitted_event_defaults() -> None:
    """Constructor populates id + timestamp + version + discriminator."""
    ev = IDPJobSubmittedEvent(job_id="job-1")

    # event_id is a valid UUID4 string.
    parsed = uuid.UUID(ev.event_id)
    assert parsed.version == 4

    # occurred_at is timezone-aware UTC.
    assert isinstance(ev.occurred_at, datetime)
    assert ev.occurred_at.tzinfo == UTC

    # Type + version + attempt defaults.
    assert ev.event_type == "IDPJobSubmitted"
    assert ev.version == "1.0.0"
    assert ev.attempt == 1


def test_envelope_for_publish_is_json_friendly() -> None:
    """The serialiser used by publishers produces a primitive dict."""
    occurred = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    ev = IDPJobSubmittedEvent(
        job_id="job-1",
        occurred_at=occurred,
        correlation_id="cor-42",
    )
    payload = envelope_for_publish(ev)

    # Discriminator is preserved verbatim — pyfly routes on this.
    assert payload["event_type"] == "IDPJobSubmitted"
    assert payload["job_id"] == "job-1"
    assert payload["correlation_id"] == "cor-42"
    # Datetimes become ISO strings (mode='json').
    assert payload["occurred_at"].startswith("2026-05-15T12:00:00")
    # event_id is preserved.
    assert payload["event_id"] == ev.event_id


def test_discriminated_union_round_trips_every_type() -> None:
    """Every event type re-parses correctly from its serialised dict."""
    adapter: TypeAdapter[IDPEvent] = TypeAdapter(IDPEvent)
    events: list[IDPEvent] = [
        IDPJobSubmittedEvent(job_id="job-1"),
        IDPJobCompletedEvent(
            job_id="job-2",
            status=JobStatus.SUCCEEDED,
            started_at=datetime(2026, 5, 15, 10, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 5, 15, 10, 5, 0, tzinfo=UTC),
            attempts=2,
        ),
        IDPBboxRefineRequestedEvent(job_id="job-3", attempt=1),
        IDPBboxRefineCompletedEvent(
            job_id="job-4",
            status="succeeded",
            attempts=1,
        ),
    ]
    for ev in events:
        raw = ev.model_dump(mode="json")
        parsed = adapter.validate_python(raw)
        assert parsed.event_type == ev.event_type
        # event_id is stable across the round-trip.
        assert parsed.event_id == ev.event_id


def test_completed_event_serialises_status_enum() -> None:
    """JobStatus enum serialises to its string value in the payload."""
    ev = IDPJobCompletedEvent(
        job_id="job-9",
        status=JobStatus.PARTIAL_SUCCEEDED,
    )
    payload = envelope_for_publish(ev)
    assert payload["status"] == "PARTIAL_SUCCEEDED"
