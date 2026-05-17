# Copyright 2026 Firefly Software Solutions Inc
"""Persistence layer -- SQLAlchemy entities + async repositories."""

from flydocs.models.entities.extraction_job import Base, ExtractionJob

__all__ = ["Base", "ExtractionJob"]
