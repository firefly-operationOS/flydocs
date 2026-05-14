# Copyright 2026 Firefly Software Solutions Inc
"""``ExtractHandler`` -- pyfly command handler for the sync extract path."""

from __future__ import annotations

import asyncio
import logging

from pyfly.container import service
from pyfly.cqrs import CommandHandler, command_handler

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.extract.extract_command import ExtractCommand
from flydesk_idp.core.services.pipeline import PipelineOrchestrator
from flydesk_idp.interfaces.dtos.extract import ExtractionResult

logger = logging.getLogger(__name__)


@command_handler
@service
class ExtractHandler(CommandHandler[ExtractCommand, ExtractionResult]):
    def __init__(self, orchestrator: PipelineOrchestrator, settings: IDPSettings) -> None:
        super().__init__()
        self._orchestrator = orchestrator
        self._settings = settings

    async def do_handle(self, command: ExtractCommand) -> ExtractionResult:
        try:
            return await asyncio.wait_for(
                self._orchestrator.execute(command.request),
                timeout=self._settings.sync_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            logger.warning("Sync extraction timed out after %ds", self._settings.sync_timeout_s)
            raise TimeoutError(
                f"extraction did not finish within {self._settings.sync_timeout_s}s"
            ) from exc
