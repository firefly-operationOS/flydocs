# Copyright 2026 Firefly Software Solutions Inc
"""Synchronous extraction endpoint -- ``POST /api/v1/extract``."""

from __future__ import annotations

import asyncio
import base64
import logging

from pyfly.container import rest_controller
# Depend on the concrete bus class -- pyfly's container resolves by exact
# type and the CQRS auto-config registers ``DefaultCommandBus``, not the
# ``CommandBus`` Protocol.
from pyfly.cqrs import DefaultCommandBus
from pyfly.web import Body, Valid, post_mapping, request_mapping

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.extract import ExtractCommand
from flydesk_idp.interfaces.dtos.extract import ExtractionRequest, ExtractionResult

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1")
class ExtractController:
    """REST adapter for the synchronous extraction API.

    Blocks the HTTP connection while the pipeline runs the request.
    Beyond the per-stage timeouts enforced inside the orchestrator,
    the handler wraps the whole call in
    ``asyncio.wait_for(FLYDESK_IDP_SYNC_TIMEOUT_S)``; if that elapses
    the caller gets a 408 ``extraction_timeout`` problem-detail and is
    expected to retry through ``POST /api/v1/jobs``.
    """

    def __init__(self, commands: DefaultCommandBus, settings: IDPSettings) -> None:
        self._commands = commands
        self._settings = settings

    @post_mapping("/extract")
    async def extract(self, request: Valid[Body[ExtractionRequest]]) -> ExtractionResult:
        """Extract structured fields from a document.

        Runs the full pipeline -- multimodal extraction with normalised
        bounding boxes, optional structured validation, visual / content
        authenticity checks, LLM judge re-evaluation, and business-rule
        DAG evaluation -- and returns the assembled ``ExtractionResult``.

        Use this endpoint when you can wait for the answer
        (sub-minute, single document). For long-running or fire-and-forget
        workloads, prefer ``POST /api/v1/jobs``.

        Errors map to RFC 7807 problem-details:
        ``408 extraction_timeout`` (pipeline exceeded the sync ceiling),
        ``413 document_too_large`` (document over ``FLYDESK_IDP_MAX_BYTES``),
        ``422 invalid_base64`` (``content_base64`` failed strict parsing).
        """
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
