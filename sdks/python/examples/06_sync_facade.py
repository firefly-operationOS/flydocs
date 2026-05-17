"""Synchronous facade -- for callers that can't run an event loop.

``FlydocsClient`` wraps ``AsyncFlydocsClient`` on a dedicated background
event loop. The API surface is identical, just without ``await``. Use
the async client whenever you can; this is the script / cron-job /
synchronous-codebase escape hatch.

    uv run python sdks/python/examples/06_sync_facade.py path/to/invoice.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

from examples_helpers import INVOICE_DOC_SPEC  # type: ignore[import-not-found]
from flydocs_sdk import (
    DocumentInput,
    ExtractionRequest,
    FlydocsClient,
)


def main(path: Path) -> int:
    with FlydocsClient("http://localhost:8400") as flydocs:
        result = flydocs.extract(
            ExtractionRequest(
                documents=[DocumentInput.from_path(path)],
                docs=[INVOICE_DOC_SPEC],
            )
        )
    print(f"sync: model={result.model}   latency={result.latency_ms}ms")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python 06_sync_facade.py path/to/document.pdf", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
