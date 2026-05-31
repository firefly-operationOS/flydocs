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

"""``TextAnchor`` protocol -- optional pre-extraction text rendering.

For sync/async extraction the multimodal LLM normally sees only the
raw bytes through :class:`BinaryContent`. For long, scanned, or
multilingual PDFs that single channel is a real handicap: the model
occasionally misreads diacritics, transposes digits in dense tables,
or truncates long arrays.

A ``TextAnchor`` produces a **structured text view** of the same
document (Markdown for the default Docling adapter) and the extractor
splices it into the user message alongside the binary content. The
LLM then has two modalities to cross-reference -- the image of the
document and a cleaned-up textual representation of what's on it.

The protocol is intentionally cheap to satisfy: every implementation
returns either a non-empty string or ``None``. ``None`` means "skip
the anchor for this document" and the extractor falls back to its
binary-only behaviour. The default :class:`NoOpTextAnchor` always
returns ``None`` so production deployments that don't install the
``docling`` extra pay zero cost.

Costs to be aware of when enabling :class:`DoclingTextAnchor`:

* CPU + RAM for the layout model (Heron) + the configured OCR
  backend on first call (cold start).
* Extra prompt tokens proportional to the anchor length. The
  ``max_chars`` knob is the user-facing knob for this trade-off.
* Anthropic prompt caching: the user message bytes shift between
  requests, so the cache key changes and a fresh cache write happens
  per document. Keep the anchor short or pin its position via the
  framework's cache hints if hit rate matters.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any, Protocol, runtime_checkable

from pyfly.container import service

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound

logger = logging.getLogger(__name__)


@runtime_checkable
class TextAnchor(Protocol):
    """Render a structured text view of a document for the extractor."""

    def produce(
        self,
        data: bytes,
        *,
        media_type: str,
        max_chars: int | None = None,
    ) -> str | None: ...


@service
class NoOpTextAnchor:
    """Default :class:`TextAnchor` -- always returns ``None``.

    Selected when ``IDPSettings.extraction_text_anchor`` is the empty
    string / ``"none"``. Keeps the slim runtime image free of the
    Docling dependency.
    """

    def produce(
        self,
        data: bytes,
        *,
        media_type: str,
        max_chars: int | None = None,
    ) -> str | None:
        return None


class DoclingTextAnchor:
    """:class:`TextAnchor` backed by Docling's Markdown export.

    Selected when ``IDPSettings.extraction_text_anchor == "docling"``.
    Lazy-imports ``docling`` so the missing-dep failure mode is the
    same as :class:`DoclingOcrEngine`: a clear ``RuntimeError`` on
    first use rather than a process-wide ImportError at boot.

    The converter instance is shared with :class:`DoclingOcrEngine`
    in production via DI when both are enabled -- both classes load
    the same models, so building one of them per role is wasteful.
    For now each builds its own; consolidating into a shared bean
    is a follow-up once the configuration shape settles.
    """

    _SUPPORTED_MEDIA: frozenset[str] = frozenset(
        {
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/tiff",
            "image/bmp",
            "image/webp",
        }
    )

    def __init__(self, settings: IDPSettings) -> None:
        self._default_max_chars = settings.extraction_text_anchor_max_chars
        self._converter: Any = None

    def produce(
        self,
        data: bytes,
        *,
        media_type: str,
        max_chars: int | None = None,
    ) -> str | None:
        if not data or media_type not in self._SUPPORTED_MEDIA:
            return None
        limit = max_chars if max_chars is not None else self._default_max_chars
        if limit <= 0:
            return None
        # Initialisation failures (missing optional dep, broken install)
        # are *configuration* errors -- raise so the operator notices.
        # Only runtime failures inside the model are degraded silently:
        # a single hard-to-parse document must not block extraction.
        converter = self._get_converter()
        document_stream_cls = _load_document_stream()
        started = time.monotonic()
        try:
            ext = _ext_for(media_type)
            source = document_stream_cls(name=f"flydocs-anchor{ext}", stream=io.BytesIO(data))
            result = converter.convert(source)
        except Exception as exc:  # noqa: BLE001 -- never block extract on a degraded anchor
            logger.warning("docling text-anchor: convert() raised %s", exc)
            log_outbound(
                "docling",
                op="anchor",
                status="error",
                latency_ms=(time.monotonic() - started) * 1000,
                error=type(exc).__name__,
            )
            return None
        doc = getattr(result, "document", None)
        if doc is None:
            return None
        try:
            markdown = doc.export_to_markdown()
        except Exception as exc:  # noqa: BLE001 -- defensive against API drift
            logger.warning("docling text-anchor: export_to_markdown() raised %s", exc)
            return None
        if not isinstance(markdown, str):
            return None
        trimmed = markdown.strip()
        if not trimmed:
            return None
        if len(trimmed) > limit:
            # Cut on a paragraph boundary when one is in reach, otherwise
            # hard-truncate. The "..." sentinel makes truncation visible
            # to the LLM so it knows the anchor was clipped.
            cut = trimmed[:limit]
            soft_cut = cut.rsplit("\n\n", 1)[0]
            if len(soft_cut) >= int(limit * 0.6):
                cut = soft_cut
            trimmed = cut.rstrip() + "\n\n... [anchor truncated]"
        log_outbound(
            "docling",
            op="anchor",
            status="ok",
            latency_ms=(time.monotonic() - started) * 1000,
            chars=len(trimmed),
        )
        return trimmed

    # ------------------------------------------------------------------

    def _get_converter(self) -> Any:
        if self._converter is not None:
            return self._converter
        try:
            from docling.document_converter import (  # pyright: ignore[reportMissingImports]
                DocumentConverter,
            )
        except ImportError as exc:  # pragma: no cover -- guarded by tests
            raise RuntimeError(
                "docling is not installed; install the optional extra "
                "(``pip install flydocs[docling]``) to enable "
                "FLYDOCS_EXTRACTION_TEXT_ANCHOR=docling"
            ) from exc
        self._converter = DocumentConverter()
        return self._converter


def _load_document_stream() -> type:
    try:
        from docling.datamodel.base_models import (  # pyright: ignore[reportMissingImports]
            DocumentStream,
        )
    except ImportError as exc:  # pragma: no cover -- guarded by tests
        raise RuntimeError(
            "docling is not installed; install the optional extra "
            "(``pip install flydocs[docling]``) to enable "
            "FLYDOCS_EXTRACTION_TEXT_ANCHOR=docling"
        ) from exc
    return DocumentStream


def _ext_for(media_type: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/tiff": ".tiff",
        "image/bmp": ".bmp",
        "image/webp": ".webp",
    }.get(media_type, ".pdf")


__all__ = ["DoclingTextAnchor", "NoOpTextAnchor", "TextAnchor"]
