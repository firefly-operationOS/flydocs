"""Submit an async extraction, poll until terminal, fetch the result envelope.

What it shows:
  * The new ``POST /api/v1/extractions`` endpoint (was ``POST /api/v1/jobs``).
  * The :class:`SubmitExtractionRequest` shape (file + types + callback).
  * The :class:`Client.extractions` sub-resource (``create`` /
    ``get`` / ``get_result``).
  * :meth:`AsyncClient.wait_for_completion` polling :class:`ExtractionStatus`
    until a terminal state is reached.
  * Reading the result envelope: ``envelope.result.pipeline.latency_ms``,
    ``envelope.result.documents[*].field_groups[*].fields``.

The legacy "PARTIAL_SUCCEEDED" / "REFINING_BBOXES" intermediate states are
gone in v1: an extraction reaches ``succeeded`` the moment the main pipeline
finishes, and bbox refinement runs as additive post-processing.

Run from the repo root::

    PYTHONPATH=sdks/python/examples \
        uv run python sdks/python/examples/03_async_extraction_with_wait.py path/to/document.pdf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples_helpers import INVOICE_DOCUMENT_TYPE, INVOICE_RULES  # type: ignore[import-not-found]

from flydocs_sdk import (
    AsyncClient,
    ExtractionStatus,
    FileInput,
    SubmitExtractionRequest,
)


async def main(path: Path) -> int:
    async with AsyncClient("http://localhost:8400", timeout=30.0) as flydocs:
        ext = await flydocs.extractions.create(
            SubmitExtractionRequest(
                files=[FileInput.from_path(path)],
                document_types=[INVOICE_DOCUMENT_TYPE],
                rules=INVOICE_RULES,
                callback_url="https://your-app.example.com/flydocs/webhook",
                metadata={"caller": "examples:03"},
            ),
            idempotency_key=f"examples:03:{path.name}",
        )
        print(f"queued {ext.id} ({ext.status.value})")

        final = await flydocs.wait_for_completion(
            ext.id,
            poll_interval=2.0,
            timeout=600.0,
        )

        if final.status == ExtractionStatus.SUCCEEDED:
            envelope = await flydocs.extractions.get_result(ext.id)
            result = envelope.result
            print(f"done: {len(result.documents)} document(s), {result.pipeline.latency_ms}ms")
            return 0
        err = final.error
        if err is not None:
            print(f"extraction did not succeed: {final.status.value} {err.code}: {err.message}")
        else:
            print(f"extraction did not succeed: {final.status.value}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "usage: python 03_async_extraction_with_wait.py path/to/document.pdf",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
