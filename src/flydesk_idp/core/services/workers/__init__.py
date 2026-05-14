# Copyright 2026 Firefly Software Solutions Inc
"""Background worker -- consumes the job queue and runs the pipeline."""

from flydesk_idp.core.services.workers.job_worker import JobWorker

__all__ = ["JobWorker"]
