# Copyright 2026 Firefly Software Solutions Inc
"""Email unpacking -- EML (RFC 822) and Outlook MSG.

Returns one ``(filename, bytes)`` tuple per attachment. Plain-text /
HTML email bodies are surfaced as ``body.html`` / ``body.txt`` so the
caller can extract from them too if they want; the normalizer above us
treats them as text/html (LibreOffice path) or text/plain (passthrough).

The header block itself is never returned -- callers care about the
content, not the routing metadata.
"""

from __future__ import annotations

import contextlib
import email
import email.message
import email.policy
import io
import logging
import time
from collections.abc import Iterator

from pyfly.container import service

from flydocs.core.observability import log_outbound
from flydocs.core.services.binary.errors import BinaryNormalizationError

logger = logging.getLogger(__name__)

_EMAIL_TYPES = {
    "message/rfc822",
    "application/vnd.ms-outlook",
}


@service
class EmailUnpacker:
    """Extract attachments + bodies from an email envelope."""

    @staticmethod
    def supports(media_type: str) -> bool:
        return media_type in _EMAIL_TYPES

    def unpack(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> list[tuple[str, bytes]]:
        """Return every attachment + the email body as ``(name, bytes)``.

        For MSG (Outlook) files, requires the ``extract-msg`` package
        bundled in the default deps.
        """
        started = time.monotonic()
        try:
            if media_type == "message/rfc822":
                items = list(self._iter_eml(data, filename))
            else:
                items = list(self._iter_msg(data, filename))
        except BinaryNormalizationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BinaryNormalizationError(f"email could not be parsed: {exc}", filename=filename) from exc
        log_outbound(
            "email",
            op=f"unpack.{media_type.split('/')[-1]}",
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            items=len(items),
        )
        return items

    # ------------------------------------------------------------------

    def _iter_eml(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        msg = email.message_from_bytes(data, policy=email.policy.default)
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = (part.get_content_disposition() or "").lower()
            ctype = part.get_content_type()
            raw_payload = part.get_payload(decode=True)
            # ``decode=True`` returns ``bytes`` for non-multipart parts.
            # The stub union with ``Message`` covers nested-message parts
            # which is_multipart() already filtered out -- the runtime
            # value is bytes for everything that reaches here.
            if not isinstance(raw_payload, (bytes, bytearray)):
                continue
            payload: bytes = bytes(raw_payload)
            if not payload:
                continue
            if disposition == "attachment" or part.get_filename():
                name = part.get_filename() or _default_name(ctype)
                yield name, payload
                continue
            # Inline body parts (text/plain, text/html). Surface so callers
            # can extract from them too.
            if ctype == "text/plain":
                yield "body.txt", payload
            elif ctype == "text/html":
                yield "body.html", payload

    def _iter_msg(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            import extract_msg  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover -- runtime dep guard
            raise BinaryNormalizationError(
                "extract-msg is required for Outlook .msg input",
                filename=filename,
            ) from exc

        try:
            msg = extract_msg.openMsg(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            raise BinaryNormalizationError(f"MSG could not be opened: {exc}", filename=filename) from exc

        try:
            for att in msg.attachments:
                payload = att.data
                if not isinstance(payload, (bytes, bytearray)):
                    continue
                name = att.longFilename or att.shortFilename or "attachment"
                yield name, bytes(payload)
            # ``body`` / ``htmlBody`` are documented MSGFile attributes;
            # extract-msg's type stubs miss them in the version we ship.
            raw_body: object = getattr(msg, "body", None)
            raw_html: object = getattr(msg, "htmlBody", None)
            body = (
                (raw_body or "").encode("utf-8", errors="replace")
                if isinstance(raw_body, str)
                else (bytes(raw_body) if isinstance(raw_body, (bytes, bytearray)) else b"")
            )
            html = (
                raw_html
                if isinstance(raw_html, bytes)
                else (raw_html.encode("utf-8", errors="replace") if isinstance(raw_html, str) else b"")
            )
            if body.strip():
                yield "body.txt", body
            if html.strip():
                yield "body.html", html
        finally:
            with contextlib.suppress(Exception):
                msg.close()


def _default_name(content_type: str) -> str:
    suffix = content_type.split("/")[-1] if "/" in content_type else "bin"
    return f"attachment.{suffix}"
