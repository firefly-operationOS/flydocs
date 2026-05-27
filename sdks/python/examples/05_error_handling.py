"""Branching on RFC 7807 ``code`` for graceful fallback (v1 codes).

What it shows:
  * Catching :class:`FlydocsHttpError` and branching on the v1 ``code``
    field (``timeout`` instead of v0 ``extraction_timeout``,
    ``file_too_large`` instead of v0 ``document_too_large``, ...).
  * Falling back from the sync ``extract`` endpoint to the async
    ``extractions.create`` queue when the pipeline hits the sync timeout.
  * Surfacing the ``validation_failed`` 422 body so callers can show
    the validator's findings in their UI.
  * Distinguishing transport timeouts (:class:`FlydocsTimeoutError`)
    from server-side ``timeout`` problem-details.

Run from the repo root::

    PYTHONPATH=sdks/python/examples \
        uv run python sdks/python/examples/05_error_handling.py path/to/document.pdf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples_helpers import INVOICE_DOCUMENT_TYPE  # type: ignore[import-not-found]

from flydocs_sdk import (
    AsyncClient,
    ExtractionRequest,
    ExtractionStatus,
    FileInput,
    FlydocsClientError,
    FlydocsHttpError,
    FlydocsTimeoutError,
    SubmitExtractionRequest,
)


async def main(path: Path) -> int:
    req = ExtractionRequest(
        files=[FileInput.from_path(path)],
        document_types=[INVOICE_DOCUMENT_TYPE],
    )
    async with AsyncClient("http://localhost:8400") as flydocs:
        try:
            result = await flydocs.extract(req)
            print(f"extracted in sync: latency={result.pipeline.latency_ms}ms")
            return 0
        except FlydocsHttpError as exc:
            if exc.code == "timeout":
                print("sync ceiling exceeded; falling back to async")
                submit_payload = SubmitExtractionRequest(**req.model_dump())
                ext = await flydocs.extractions.create(submit_payload)
                final = await flydocs.wait_for_completion(ext.id, timeout=600.0)
                err = final.error
                print(f"async result: {final.status.value} {(err.message if err else '')}".rstrip())
                return 0 if final.status == ExtractionStatus.SUCCEEDED else 1
            if exc.code == "file_too_large":
                print(f"413: {exc.detail}")
                return 2
            if exc.code in ("validation_failed", "invalid_request"):
                print(f"422 {exc.code}:")
                for issue in exc.payload.get("errors", []):
                    print(f"  - {issue}")
                return 2
            if exc.code == "invalid_base64":
                print(f"422 invalid_base64: {exc.detail}")
                return 2
            print(f"HTTP {exc.status_code} {exc.code}: {exc.detail}")
            return 1
        except FlydocsTimeoutError:
            print("the HTTP request itself timed out on the wire -- retry with backoff")
            return 3
        except FlydocsClientError as exc:
            print(f"transport error: {exc}")
            return 3


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python 05_error_handling.py path/to/document.pdf", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
