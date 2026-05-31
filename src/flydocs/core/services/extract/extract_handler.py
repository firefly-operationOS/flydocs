# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``ExtractHandler`` -- pyfly command handler for the sync extract path."""

from __future__ import annotations

import asyncio
import logging

from pyfly.container import service
from pyfly.cqrs import CommandHandler, command_handler

from flydocs.config import IDPSettings
from flydocs.core.services.extract.extract_command import ExtractCommand
from flydocs.core.services.pipeline import PipelineOrchestrator
from flydocs.interfaces.dtos.extract import ExtractionResult

logger = logging.getLogger(__name__)


class ExtractionTimedOutError(RuntimeError):
    """Raised when sync extraction exceeds ``FLYDOCS_SYNC_TIMEOUT_S``.

    Subclasses :class:`RuntimeError` so the pyfly CQRS bus lets it
    propagate to the controller's exception handler (asyncio's
    :class:`TimeoutError` extends :class:`OSError` and the bus would
    otherwise wrap it as a generic ``COMMAND_PROCESSING_ERROR`` with
    HTTP 400).
    """

    def __init__(self, timeout_s: int) -> None:
        super().__init__(f"extraction did not finish within {timeout_s}s")
        self.timeout_s = timeout_s


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
        except TimeoutError as exc:
            logger.warning("Sync extraction timed out after %ds", self._settings.sync_timeout_s)
            raise ExtractionTimedOutError(self._settings.sync_timeout_s) from exc
