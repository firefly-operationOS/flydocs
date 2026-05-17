# Copyright 2026 Firefly Software Solutions Inc
"""Office document → PDF conversion via headless LibreOffice.

Covers DOCX, DOC, XLSX, XLS, PPTX, PPT, ODT, ODS, ODP, RTF, HTML.
Runs ``soffice --headless --convert-to pdf`` in a temporary directory
so concurrent conversions don't collide on a shared user profile.

For multilingual rendering the runtime image must ship the matching
font packs (``fonts-noto-cjk``, ``fonts-noto-color-emoji``,
``fonts-dejavu``, ``fonts-liberation``) -- otherwise non-Latin text
falls back to ``.notdef`` glyphs and the resulting PDF has no text
layer for those runs, breaking the bbox refiner downstream.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound
from flydocs.core.services.binary.errors import OfficeConversionError
from flydocs.core.services.binary.office_converter import (
    OFFICE_MEDIA_TYPES,
    OfficeConverter,
)

logger = logging.getLogger(__name__)


# Supported MIME → LibreOffice extension. The keys are a subset of
# OFFICE_MEDIA_TYPES; the values are what soffice expects on disk so it
# picks the right import filter.
_OFFICE_TYPES: dict[str, str] = {
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


class LibreOfficeConverter(OfficeConverter):
    """``OfficeConverter`` that shells out to local ``soffice``.

    Used when the runtime image bundles LibreOffice (the slim/dev
    Dockerfile path). For distroless deployments use
    :class:`GotenbergConverter` instead.

    Not decorated ``@service`` -- :class:`IDPCoreConfiguration` registers
    it (or :class:`GotenbergConverter`) as the ``OfficeConverter`` bean
    based on ``IDPSettings.office_converter``.
    """

    def __init__(self, settings: IDPSettings) -> None:
        self._binary = settings.binary_libreoffice_path
        self._timeout_s = settings.binary_libreoffice_timeout_s

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
        """Return the converted PDF bytes.

        Raises :class:`OfficeConversionError` on subprocess failure,
        timeout, or missing binary.
        """
        ext = _OFFICE_TYPES.get(media_type)
        if ext is None:
            raise OfficeConversionError(
                f"unsupported office media type: {media_type}",
                filename=filename,
            )

        if shutil.which(self._binary) is None:
            raise OfficeConversionError(
                f"LibreOffice binary {self._binary!r} not found on PATH",
                filename=filename,
            )

        # Per-call workdir so concurrent conversions don't share state.
        # LibreOffice writes a user profile alongside; sharing it across
        # concurrent runs deadlocks soffice.
        workdir = Path(tempfile.mkdtemp(prefix="flydocs-soffice-"))
        try:
            stem = uuid.uuid4().hex
            input_path = workdir / f"{stem}.{ext}"
            output_path = workdir / f"{stem}.pdf"
            input_path.write_bytes(data)

            profile_dir = workdir / "profile"
            profile_dir.mkdir()

            cmd = [
                self._binary,
                "--headless",
                "--norestore",
                "--nologo",
                "--nofirststartwizard",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(workdir),
                str(input_path),
            ]
            started = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout_s)
                except TimeoutError as exc:
                    proc.kill()
                    await proc.wait()
                    raise OfficeConversionError(
                        f"LibreOffice timed out after {self._timeout_s}s",
                        filename=filename,
                    ) from exc
            except FileNotFoundError as exc:
                raise OfficeConversionError(
                    f"LibreOffice binary not executable: {exc}", filename=filename
                ) from exc
            latency_ms = (time.monotonic() - started) * 1000

            if proc.returncode != 0 or not output_path.exists():
                err = (stderr or b"").decode("utf-8", errors="replace").strip()[-500:]
                log_outbound(
                    "libreoffice",
                    op=f"convert.{ext}",
                    status="error",
                    latency_ms=latency_ms,
                    rc=proc.returncode,
                    error=err[:120],
                )
                raise OfficeConversionError(
                    f"LibreOffice failed (rc={proc.returncode}): {err or 'no stderr'}",
                    filename=filename,
                )
            pdf_bytes = output_path.read_bytes()
            log_outbound(
                "libreoffice",
                op=f"convert.{ext}",
                status="ok",
                latency_ms=latency_ms,
                in_bytes=len(data),
                out_bytes=len(pdf_bytes),
            )
            return pdf_bytes
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
