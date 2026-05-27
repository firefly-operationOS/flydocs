# Copyright 2026 Firefly Software Solutions Inc
"""``Extraction`` -- persistent state for asynchronous extraction jobs.

Stores everything the worker needs to resume an extraction after a restart
and everything callers can later query through
``GET /api/v1/extractions/{id}``. We deliberately do NOT store the document
bytes -- the payload is only held in memory during processing (the
``schema_json`` column carries enough to re-render the request when
needed).

The v1 lifecycle collapses to ``queued | running | succeeded | failed |
cancelled`` on the main status column. Post-processing (bbox refinement
today) lives in columns prefixed ``post_processing_bbox_*`` -- still
atomic at the SQL level, projected to a ``post_processing`` JSON object
in the public DTO. Keeping them as columns lets ``UPDATE ... RETURNING``
remain race-safe without needing ``jsonb_set`` games.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by every entity in this service."""


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[str] = mapped_column(
        String(48),
        primary_key=True,
        default=lambda: f"ext_{uuid.uuid4().hex[:26].upper()}",
        doc="Public extraction id (prefixed: ``ext_<26-hex>``).",
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True, default="queued")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    options_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    callback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Post-processing: bbox refinement leg. ``null`` until/unless requested.
    post_processing_bbox_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    post_processing_bbox_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    post_processing_bbox_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    post_processing_bbox_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    post_processing_bbox_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    post_processing_bbox_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_extractions_status",
        ),
        CheckConstraint(
            "post_processing_bbox_status IS NULL OR "
            "post_processing_bbox_status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_extractions_post_processing_bbox_status",
        ),
        Index(
            "uq_extractions_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=(idempotency_key.is_not(None)),
            sqlite_where=(idempotency_key.is_not(None)),
        ),
    )
