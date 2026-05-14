# Copyright 2026 Firefly Software Solutions Inc
"""``JobQueue`` -- abstraction over Redis Streams + in-memory backends.

The API surface is tiny by design:

- ``await queue.publish(job_id)``
- ``async for message in queue.consume(consumer_id):  await queue.ack(message)``

Backends:

- :class:`InMemoryJobQueue` -- single process, used in tests and when
  ``FLYDESK_IDP_EDA_ADAPTER=memory``.
- :class:`RedisStreamJobQueue` -- Redis Streams with consumer groups so
  multiple worker pods share the load and surviving messages are
  re-delivered on restart.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class JobQueueMessage:
    """One queue entry."""

    job_id: str
    delivery_id: str        # backend-specific (in-memory uuid, redis stream id)
    attempts: int = 0
    backend_payload: dict[str, Any] | None = None


class JobQueue(ABC):
    """Minimal contract every adapter implements."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def publish(self, job_id: str) -> None: ...

    @abstractmethod
    def consume(self, consumer_id: str) -> AsyncIterator[JobQueueMessage]: ...

    @abstractmethod
    async def ack(self, message: JobQueueMessage) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation -- single process
# ---------------------------------------------------------------------------


class InMemoryJobQueue(JobQueue):
    """In-process queue. Useful for tests and single-process dev."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[JobQueueMessage] = asyncio.Queue()
        self._closed = False

    async def start(self) -> None:
        self._closed = False

    async def stop(self) -> None:
        self._closed = True

    async def publish(self, job_id: str) -> None:
        if self._closed:
            raise RuntimeError("Queue closed")
        await self._queue.put(
            JobQueueMessage(job_id=job_id, delivery_id=f"mem-{time.monotonic_ns()}")
        )

    async def consume(self, consumer_id: str) -> AsyncIterator[JobQueueMessage]:
        while not self._closed:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            yield message

    async def ack(self, message: JobQueueMessage) -> None:
        # In-memory has no separate ack -- popping is enough.
        return


# ---------------------------------------------------------------------------
# Redis Streams implementation
# ---------------------------------------------------------------------------


class RedisStreamJobQueue(JobQueue):
    """Redis Streams consumer-group queue.

    Stream key: ``flydesk:idp:jobs`` (overridable). Consumer group:
    ``flydesk-idp-workers``. Each worker passes its own ``consumer_id``
    (typically hostname) so claim / re-delivery works correctly.
    """

    def __init__(
        self,
        *,
        url: str,
        stream_key: str = "flydesk:idp:jobs",
        consumer_group: str = "flydesk-idp-workers",
        block_ms: int = 5000,
    ) -> None:
        from redis import asyncio as redis_asyncio  # type: ignore[import-not-found]

        self._stream_key = stream_key
        self._consumer_group = consumer_group
        self._block_ms = block_ms
        self._client = redis_asyncio.Redis.from_url(url, decode_responses=True)
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._started:
            return
        try:
            await self._client.xgroup_create(
                name=self._stream_key,
                groupname=self._consumer_group,
                id="$",
                mkstream=True,
            )
        except Exception as exc:  # noqa: BLE001
            # BUSYGROUP: group already exists -- safe to ignore
            if "BUSYGROUP" not in str(exc):
                raise
        self._started = True
        self._closed = False

    async def stop(self) -> None:
        self._closed = True
        await self._client.close()

    async def publish(self, job_id: str) -> None:
        if not self._started:
            await self.start()
        await self._client.xadd(self._stream_key, {"job_id": job_id})

    async def consume(self, consumer_id: str) -> AsyncIterator[JobQueueMessage]:
        if not self._started:
            await self.start()
        while not self._closed:
            response = await self._client.xreadgroup(
                groupname=self._consumer_group,
                consumername=consumer_id,
                streams={self._stream_key: ">"},
                count=1,
                block=self._block_ms,
            )
            if not response:
                continue
            for _stream, entries in response:
                for stream_id, payload in entries:
                    job_id = payload.get("job_id")
                    if not job_id:
                        await self.ack(
                            JobQueueMessage(job_id="", delivery_id=stream_id)
                        )
                        continue
                    yield JobQueueMessage(
                        job_id=str(job_id),
                        delivery_id=stream_id,
                        backend_payload=payload,
                    )

    async def ack(self, message: JobQueueMessage) -> None:
        await self._client.xack(
            self._stream_key, self._consumer_group, message.delivery_id
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_job_queue(adapter: str, *, redis_url: str, stream_key: str) -> JobQueue:
    if adapter == "memory":
        return InMemoryJobQueue()
    if adapter == "redis":
        return RedisStreamJobQueue(url=redis_url, stream_key=stream_key)
    raise ValueError(f"Unsupported queue adapter: {adapter!r} (memory/redis)")
