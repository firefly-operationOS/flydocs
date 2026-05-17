# Copyright 2026 Firefly Software Solutions Inc
"""``ExtractionJob`` -- persistent state for asynchronous extraction jobs.

Stores everything the worker needs to resume a job after a restart and
everything callers can later query through ``GET /api/v1/jobs/{id}``.
We deliberately do NOT store the document bytes -- the payload is only
held in memory during processing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by every entity in this service."""


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        doc="Stable string UUID used as the public job id.",
    )
    # ``UNIQUE WHERE NOT NULL`` is enforced by the partial index below.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # ``String(24)`` -- fits ``PARTIAL_SUCCEEDED`` (17 chars) and ``REFINING_BBOXES`` (15);
    # the original ``String(16)`` truncated the former and crashed asyncpg on commit.
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Inputs.
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    options_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    callback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Outputs.
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Bookkeeping.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Bbox-refine leg state -- populated only when the caller enabled
    # ``options.stages.bbox_refine``. ``null`` for jobs that never asked
    # for grounding. See ``interfaces/enums/job_status.py::BboxRefineStatus``.
    bbox_refine_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    bbox_refine_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bbox_refine_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bbox_refine_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bbox_refine_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bbox_refine_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "uq_extraction_jobs_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=(idempotency_key.is_not(None)),
            sqlite_where=(idempotency_key.is_not(None)),
        ),
    )
