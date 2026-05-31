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

"""Real-Postgres test: per-group advisory lock prevents duplicate dispatch.

Spins up two :class:`PostgresEventBus` instances sharing the same
``consumer_group`` and asserts that every published event is dispatched
exactly once across the pair -- not once per consumer. This is the
production scenario when ``--scale worker=N``: the bus is the gatekeeper
that picks a single winner per drain via ``pg_try_advisory_lock``.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from pyfly.eda.adapters.postgres import PostgresEventBus

_PG_URL = os.environ.get("FLYDOCS_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not _PG_URL, reason="FLYDOCS_TEST_PG_URL not set; skipping real-Postgres tests"
)


@pytest.mark.asyncio
async def test_two_buses_same_group_dispatch_each_event_exactly_once() -> None:
    """Two consumers, same group: each event goes to exactly one consumer."""
    received_a: list[str] = []
    received_b: list[str] = []
    lock = asyncio.Lock()

    bus_a = PostgresEventBus(
        dsn=_PG_URL,  # type: ignore[arg-type]
        channel="test_concurrency_lock_a",
        destinations=["concurrency.test"],
        group="concurrency-test-group",
        poll_interval_s=0.5,
    )
    bus_b = PostgresEventBus(
        dsn=_PG_URL,  # type: ignore[arg-type]
        channel="test_concurrency_lock_a",  # same channel
        destinations=["concurrency.test"],
        group="concurrency-test-group",  # same group -> same advisory lock
        poll_interval_s=0.5,
    )

    async def handler_a(envelope) -> None:
        async with lock:
            received_a.append(envelope.event_id)

    async def handler_b(envelope) -> None:
        async with lock:
            received_b.append(envelope.event_id)

    bus_a.subscribe("test.event", handler_a)
    bus_b.subscribe("test.event", handler_b)

    # Reset offset for our group so we read every event we publish here
    # rather than whatever the previous test run already advanced past.
    await bus_a.start()
    try:
        async with bus_a._pool.acquire() as conn:  # type: ignore[attr-defined]
            await conn.execute(
                "UPDATE pyfly_eda_offsets SET last_event_id = "
                "(SELECT COALESCE(MAX(id), 0) FROM pyfly_eda_outbox) "
                "WHERE consumer_group = $1",
                "concurrency-test-group",
            )

        await bus_b.start()
        try:
            # Publish 20 events; each must be delivered exactly once.
            for i in range(20):
                # Use bus_a as the producer; events land in the shared outbox.
                await bus_a.publish(
                    destination="concurrency.test",
                    event_type="test.event",
                    payload={"i": i},
                )
            # Wait for both buses to drain. The advisory lock means
            # whichever drains first wins; the other returns immediately.
            for _ in range(30):
                async with lock:
                    total = len(received_a) + len(received_b)
                if total >= 20:
                    break
                await asyncio.sleep(0.2)

            async with lock:
                all_received = received_a + received_b
            assert len(all_received) == 20, (
                f"expected 20 deliveries total, got {len(all_received)} "
                f"(bus_a={len(received_a)}, bus_b={len(received_b)})"
            )
            # No duplicates: the set has the same size as the list.
            assert len(set(all_received)) == 20, (
                f"DUPLICATE DELIVERY: {len(all_received)} deliveries but only "
                f"{len(set(all_received))} unique event ids"
            )
        finally:
            await bus_b.stop()
    finally:
        await bus_a.stop()
