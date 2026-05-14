# Copyright 2026 Firefly Software Solutions Inc
"""Job queue abstraction with in-memory and Redis Streams backends."""

from flydesk_idp.core.services.queue.job_queue import (
    InMemoryJobQueue,
    JobQueue,
    JobQueueMessage,
    RedisStreamJobQueue,
    create_job_queue,
)

__all__ = [
    "InMemoryJobQueue",
    "JobQueue",
    "JobQueueMessage",
    "RedisStreamJobQueue",
    "create_job_queue",
]
