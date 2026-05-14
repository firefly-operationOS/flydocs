# Copyright 2026 Firefly Software Solutions Inc
"""ZIP / 7z / TAR / GZIP expansion.

Returns one ``(filename, bytes)`` tuple per member. Compressed-only
formats (gzip/bz2 of a single file) yield one tuple; container formats
(zip/7z/tar) yield as many as they hold.

Per-call hard limits guard against zip-bombs:

* recursion depth (handled by the normalizer above us, not here)
* total expanded files per archive (enforced here)
* total uncompressed bytes per archive (enforced here)
"""

from __future__ import annotations

import gzip
import io
import logging
import tarfile
import time
import zipfile
from collections.abc import Iterator

from pyfly.container import service

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.observability import log_outbound
from flydesk_idp.core.services.binary.errors import ArchiveExtractionError

logger = logging.getLogger(__name__)

# Bytes-per-archive ceiling. Independent of the per-input expansion
# limit so a single ZIP can't exhaust memory by holding one giant member
# under the file-count limit.
_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024  # 256 MiB

_ARCHIVE_TYPES = {
    "application/zip",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
}


@service
class ArchiveUnpacker:
    """Yield one (path, bytes) per archive member."""

    def __init__(self, settings: IDPSettings) -> None:
        self._max_files = settings.binary_max_expanded_files

    @staticmethod
    def supports(media_type: str) -> bool:
        return media_type in _ARCHIVE_TYPES

    def unpack(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> list[tuple[str, bytes]]:
        """Return every member as ``(path, bytes)``.

        Skips directories and zero-byte members. Raises
        :class:`ArchiveExtractionError` on corrupt archives, password-
        protected members, or fan-out / size limit breach.
        """
        started = time.monotonic()
        try:
            members = list(self._iter_members(data, media_type, filename))
        except ArchiveExtractionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArchiveExtractionError(f"archive could not be opened: {exc}", filename=filename) from exc
        log_outbound(
            "archive",
            op=f"unpack.{media_type.split('/')[-1]}",
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            members=len(members),
        )
        return members

    # ------------------------------------------------------------------

    def _iter_members(
        self,
        data: bytes,
        media_type: str,
        filename: str | None,
    ) -> Iterator[tuple[str, bytes]]:
        if media_type == "application/zip":
            yield from self._iter_zip(data, filename)
        elif media_type == "application/x-7z-compressed":
            yield from self._iter_7z(data, filename)
        elif media_type == "application/x-tar":
            yield from self._iter_tar(data, filename)
        elif media_type == "application/gzip":
            yield from self._iter_gz(data, filename)
        else:
            raise ArchiveExtractionError(f"unsupported archive type: {media_type}", filename=filename)

    def _iter_zip(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ArchiveExtractionError(f"corrupt ZIP: {exc}", filename=filename) from exc
        with zf:
            total_bytes = 0
            yielded = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:  # password bit
                    raise ArchiveExtractionError(
                        f"ZIP member {info.filename!r} is password-protected",
                        filename=filename,
                    )
                if info.file_size <= 0:
                    continue
                total_bytes += info.file_size
                self._enforce_limits(yielded + 1, total_bytes, filename)
                try:
                    member_bytes = zf.read(info.filename)
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    raise ArchiveExtractionError(
                        f"ZIP member {info.filename!r} could not be read: {exc}",
                        filename=filename,
                    ) from exc
                yielded += 1
                yield info.filename, member_bytes

    def _iter_7z(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            import py7zr  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover -- runtime dep guard
            raise ArchiveExtractionError("py7zr is required for 7-Zip input", filename=filename) from exc
        try:
            with py7zr.SevenZipFile(io.BytesIO(data), mode="r") as sf:
                if sf.needs_password():
                    raise ArchiveExtractionError("7z archive is password-protected", filename=filename)
                # py7zr returns a dict[str, BytesIO]; ``readall`` is the
                # documented API, type stubs in some releases miss it.
                contents: dict[str, io.BytesIO] = sf.readall() or {}  # pyright: ignore[reportAttributeAccessIssue]
        except ArchiveExtractionError:
            raise
        except Exception as exc:  # noqa: BLE001
            # py7zr.exceptions.PasswordRequired isn't always exported via
            # ``py7zr.exceptions``; fall back to substring check on the
            # exception message which is stable across releases.
            if "password" in str(exc).lower():
                raise ArchiveExtractionError("7z archive is password-protected", filename=filename) from exc
            raise ArchiveExtractionError(f"7z extraction failed: {exc}", filename=filename) from exc

        total_bytes = 0
        yielded = 0
        for path, buf in contents.items():
            payload = buf.getvalue()
            if not payload:
                continue
            total_bytes += len(payload)
            self._enforce_limits(yielded + 1, total_bytes, filename)
            yielded += 1
            yield path, payload

    def _iter_tar(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:  # noqa: SIM117
                total_bytes = 0
                yielded = 0
                for info in tf:
                    if not info.isfile() or info.size <= 0:
                        continue
                    total_bytes += info.size
                    self._enforce_limits(yielded + 1, total_bytes, filename)
                    fp = tf.extractfile(info)
                    if fp is None:
                        continue
                    yielded += 1
                    yield info.name, fp.read()
        except ArchiveExtractionError:
            raise
        except tarfile.TarError as exc:
            raise ArchiveExtractionError(f"corrupt tar: {exc}", filename=filename) from exc

    def _iter_gz(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        # gzip wraps a single file. Keep the original member name when present.
        try:
            decoded = gzip.decompress(data)
        except OSError as exc:
            raise ArchiveExtractionError(f"gzip decompression failed: {exc}", filename=filename) from exc
        if len(decoded) > _MAX_UNCOMPRESSED_BYTES:
            raise ArchiveExtractionError(
                f"gzip exceeds {_MAX_UNCOMPRESSED_BYTES} byte ceiling",
                filename=filename,
            )
        member_name = (filename or "payload").rstrip(".gz") or "payload"
        yield member_name, decoded

    def _enforce_limits(self, count: int, total_bytes: int, filename: str | None) -> None:
        if count > self._max_files:
            raise ArchiveExtractionError(
                f"archive expansion exceeds max {self._max_files} files",
                filename=filename,
            )
        if total_bytes > _MAX_UNCOMPRESSED_BYTES:
            raise ArchiveExtractionError(
                f"archive expansion exceeds {_MAX_UNCOMPRESSED_BYTES} byte ceiling",
                filename=filename,
            )
