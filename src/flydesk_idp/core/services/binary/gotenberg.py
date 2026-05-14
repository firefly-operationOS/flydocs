# Copyright 2026 Firefly Software Solutions Inc
"""Gotenberg HTTP adapter -- distroless-friendly Office → PDF.

Gotenberg (https://gotenberg.dev) is a Go service that wraps headless
LibreOffice + Chromium and exposes an HTTP API for document conversion.
By delegating to the sidecar we keep the application container truly
distroless: no ``soffice`` binary, no writable filesystem, no shell.

Endpoints used:

* ``POST /forms/libreoffice/convert``  -- DOC, DOCX, XLS, XLSX, PPT,
                                          PPTX, ODT, ODS, ODP, RTF.
* ``POST /forms/chromium/convert/html`` -- HTML.

A single multipart request returns the PDF in the response body. We
treat any non-2xx as a typed :class:`OfficeConversionError`.
"""

from __future__ import annotations

import logging
import time
from typing import Final

import httpx

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.observability import log_outbound
from flydesk_idp.core.services.binary.errors import OfficeConversionError
from flydesk_idp.core.services.binary.office_converter import (
    OFFICE_MEDIA_TYPES,
    OfficeConverter,
)

logger = logging.getLogger(__name__)


# Filename extension by MIME -- Gotenberg picks the right LibreOffice
# import filter from the upload's filename, so the extension matters.
_EXT: Final[dict[str, str]] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "text/html": "html",
}


class GotenbergConverter(OfficeConverter):
    """``OfficeConverter`` backed by a Gotenberg HTTP sidecar.

    Not decorated ``@service`` -- :class:`IDPCoreConfiguration` registers
    it (or :class:`LibreOfficeConverter`) as the ``OfficeConverter`` bean
    based on ``IDPSettings.office_converter``.
    """

    def __init__(self, settings: IDPSettings) -> None:
        self._base_url = settings.gotenberg_url.rstrip("/")
        self._timeout_s = settings.gotenberg_timeout_s

    @staticmethod
    def supports(media_type: str) -> bool:
        return media_type in OFFICE_MEDIA_TYPES

    async def convert(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> bytes:
        ext = _EXT.get(media_type)
        if ext is None:
            raise OfficeConversionError(
                f"unsupported office media type: {media_type}",
                filename=filename,
            )
        url = (
            f"{self._base_url}/forms/chromium/convert/html"
            if ext == "html"
            else f"{self._base_url}/forms/libreoffice/convert"
        )
        upload_name = (filename or "document").rsplit("/", 1)[-1]
        if not upload_name.lower().endswith("." + ext):
            stem = upload_name.rsplit(".", 1)[0] if "." in upload_name else upload_name
            upload_name = f"{stem}.{ext}"
        files = {"files": (upload_name, data, media_type)}
        # The HTML endpoint expects a single ``index.html`` upload key.
        if ext == "html":
            files = {"files": ("index.html", data, "text/html")}

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(url, files=files)
        except httpx.HTTPError as exc:
            raise OfficeConversionError(f"Gotenberg request failed: {exc}", filename=filename) from exc
        latency_ms = (time.monotonic() - started) * 1000
        if resp.status_code >= 400:
            err = resp.text[:500]
            log_outbound(
                "gotenberg",
                op=f"convert.{ext}",
                status="error",
                latency_ms=latency_ms,
                http_status=resp.status_code,
                error=err[:120],
            )
            raise OfficeConversionError(
                f"Gotenberg returned HTTP {resp.status_code}: {err or 'no body'}",
                filename=filename,
            )
        log_outbound(
            "gotenberg",
            op=f"convert.{ext}",
            status="ok",
            latency_ms=latency_ms,
            in_bytes=len(data),
            out_bytes=len(resp.content),
        )
        return resp.content
