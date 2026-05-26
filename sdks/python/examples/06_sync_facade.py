"""Synchronous facade -- for callers that can't run an event loop.

What it shows:
  * :class:`flydocs_sdk.Client` mirrors :class:`flydocs_sdk.AsyncClient`
    method-for-method without ``await``.
  * The new v1 request shape (``files`` + ``document_types``) and
    response shape (``result.pipeline.model`` / ``result.pipeline.latency_ms``).

``Client`` wraps ``AsyncClient`` on a dedicated background event loop.
The API surface is identical, just without ``await``. Use the async
client whenever you can; this is the script / cron-job /
synchronous-codebase escape hatch.

Run from the repo root::

    PYTHONPATH=sdks/python/examples \
        uv run python sdks/python/examples/06_sync_facade.py path/to/invoice.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

from examples_helpers import INVOICE_DOCUMENT_TYPE  # type: ignore[import-not-found]

from flydocs_sdk import (
    Client,
    ExtractionRequest,
    FileInput,
)


def main(path: Path) -> int:
    with Client("http://localhost:8400") as flydocs:
        result = flydocs.extract(
            ExtractionRequest(
                files=[FileInput.from_path(path)],
                document_types=[INVOICE_DOCUMENT_TYPE],
            )
        )
    print(f"sync: id={result.id}   model={result.pipeline.model}   latency={result.pipeline.latency_ms}ms")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python 06_sync_facade.py path/to/document.pdf", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
