# Copyright 2026 Firefly Software Solutions Inc
"""Synchronous extraction endpoint -- ``POST /api/v1/extract``."""

from __future__ import annotations

import asyncio
import base64
import logging

from pyfly.container import rest_controller
from pyfly.cqrs import CommandBus
from pyfly.web import Body, Valid, post_mapping, request_mapping

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.extract import ExtractCommand
from flydesk_idp.interfaces.dtos.extract import ExtractionRequest, ExtractionResult

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1")
class ExtractController:
    """One-shot extract endpoint. Blocks until the orchestrator finishes
    (or until ``FLYDESK_IDP_SYNC_TIMEOUT_S`` elapses, which yields a 408)."""

    def __init__(self, commands: CommandBus, settings: IDPSettings) -> None:
        self._commands = commands
        self._settings = settings

    @post_mapping("/extract")
    async def extract(self, request: Valid[Body[ExtractionRequest]]) -> ExtractionResult:
        _enforce_size_limits(request, max_bytes=self._settings.max_bytes)
        try:
            return await self._commands.send(ExtractCommand(request=request))
        except asyncio.TimeoutError as exc:
            raise _http_problem(408, "extraction_timeout", "Extraction timed out", str(exc)) from exc


def _enforce_size_limits(request: ExtractionRequest, *, max_bytes: int) -> None:
    encoded = request.document.content_base64
    decoded_size = (len(encoded) * 3) // 4
    if decoded_size > max_bytes:
        raise _http_problem(
            413,
            "document_too_large",
            "Document too large",
            f"document is {decoded_size} bytes (max {max_bytes})",
        )
    try:
        base64.b64decode(encoded, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise _http_problem(422, "invalid_base64", "Invalid base64 content", str(exc)) from exc


def _http_problem(status_code: int, code: str, title: str, detail: str) -> Exception:
    from fastapi import HTTPException

    return HTTPException(
        status_code=status_code,
        detail={"code": code, "title": title, "detail": detail},
    )
