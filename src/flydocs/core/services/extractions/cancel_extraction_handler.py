# Copyright 2026 Firefly Software Solutions Inc
"""``CancelExtractionHandler`` -- cancels an extraction while still QUEUED.

The cancel is a single atomic ``UPDATE ... WHERE status='queued'``
against Postgres. If the row is no longer QUEUED (worker just claimed
it, the extraction is already terminal, or it never existed under that
id), the UPDATE matches zero rows and we surface the appropriate error.

This eliminates the previous TOCTOU window where a SELECT-then-UPDATE
pair could clobber a worker that claimed the extraction in between.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, command_handler

from flydocs.core.services.extractions._projector import row_to_extraction
from flydocs.interfaces.dtos.extraction import Extraction
from flydocs.models.repositories import ExtractionRepository


@dataclass(frozen=True)
class CancelExtractionCommand(Command[Extraction | None]):
    extraction_id: str


class ExtractionNotCancellable(RuntimeError):
    """Raised when the extraction is past the QUEUED state."""


@command_handler
@service
class CancelExtractionHandler(CommandHandler[CancelExtractionCommand, Extraction | None]):
    def __init__(self, repository: ExtractionRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, command: CancelExtractionCommand) -> Extraction | None:
        # Atomic compare-and-swap: ``mark_cancelled`` only succeeds when
        # the row's current status is QUEUED. A worker claiming the row
        # in the same instant moves the status to RUNNING with a single
        # UPDATE -- Postgres serialises the two UPDATEs by row-level lock
        # and exactly one of them matches its precondition.
        cancelled = await self._repository.mark_cancelled(command.extraction_id)
        if cancelled is not None:
            return row_to_extraction(cancelled)
        # mark_cancelled returned None -- either the extraction doesn't
        # exist or it's past QUEUED. Distinguish the two so the REST
        # layer can emit 404 vs 409 correctly.
        row = await self._repository.get(command.extraction_id)
        if row is None:
            return None
        raise ExtractionNotCancellable(f"Extraction {row.id!r} cannot be cancelled in status {row.status}")


__all__ = [
    "CancelExtractionCommand",
    "CancelExtractionHandler",
    "ExtractionNotCancellable",
]
