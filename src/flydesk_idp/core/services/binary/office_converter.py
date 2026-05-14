# Copyright 2026 Firefly Software Solutions Inc
"""Office → PDF conversion behind a single ``OfficeConverter`` protocol.

Two concrete adapters ship in-tree:

* :class:`GotenbergConverter` -- HTTP client against a Gotenberg sidecar.
  The canonical choice for distroless deployments: the API + worker
  containers carry no ``soffice`` binary at all and just POST bytes to
  Gotenberg, which owns LibreOffice + headless Chromium internally in
  its own container.
* :class:`LibreOfficeConverter` -- in-container subprocess wrapper.
  Suitable for the slim/dev image (``python:3.13-slim`` + LibreOffice
  installed via apt). Kept as a drop-in fallback for development without
  the sidecar.

The active adapter is picked by ``IDPSettings.office_converter`` and
exposed as the ``OfficeConverter`` bean by
:class:`IDPCoreConfiguration`. Every consumer (today only
:class:`BinaryNormalizer`) injects the protocol -- the concrete classes
stay private to this package.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# Re-export the supported MIME set so the normalizer's ``supports`` check
# stays adapter-agnostic.
OFFICE_MEDIA_TYPES: frozenset[str] = frozenset({
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/rtf",
    "text/rtf",
    "text/html",
})


@runtime_checkable
class OfficeConverter(Protocol):
    """Convert Office bytes to PDF bytes.

    Implementations raise :class:`OfficeConversionError` on any failure.
    The contract is async because every realistic adapter is I/O-bound
    (subprocess wait or HTTP round-trip).
    """

    @staticmethod
    def supports(media_type: str) -> bool:
        ...

    async def convert(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> bytes:
        ...


def _supports(media_type: str) -> bool:
    return media_type in OFFICE_MEDIA_TYPES
