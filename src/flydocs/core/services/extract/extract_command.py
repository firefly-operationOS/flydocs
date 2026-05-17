# Copyright 2026 Firefly Software Solutions Inc
"""``ExtractCommand`` -- shipped through pyfly's :class:`CommandBus`."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.cqrs import Command

from flydocs.interfaces.dtos.extract import ExtractionRequest, ExtractionResult


@dataclass(frozen=True)
class ExtractCommand(Command[ExtractionResult]):
    """One sync extraction request."""

    request: ExtractionRequest
