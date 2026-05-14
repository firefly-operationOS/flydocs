# Copyright 2026 Firefly Software Solutions Inc
"""Async-job lifecycle states.

The state machine is:

    QUEUED -> RUNNING -> SUCCEEDED | FAILED
    QUEUED -> CANCELLED   (only while still QUEUED)

A job that has already started cannot be cancelled.
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
