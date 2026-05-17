"""Branching on RFC 7807 ``code`` for graceful fallback.

Tries the sync extraction path first, falls back to the async job
queue when the service signals an extraction timeout, and surfaces
semantic validation errors in a way the caller can act on.

    uv run python sdks/python/examples/05_error_handling.py path/to/document.pdf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples_helpers import INVOICE_DOC_SPEC  # type: ignore[import-not-found]

from flydocs_sdk import (
    AsyncFlydocsClient,
    DocumentInput,
    ExtractionRequest,
    FlydocsClientError,
    FlydocsHTTPError,
    FlydocsTimeoutError,
    SubmitJobRequest,
)


async def main(path: Path) -> int:
    req = ExtractionRequest(
        documents=[DocumentInput.from_path(path)],
        docs=[INVOICE_DOC_SPEC],
    )
    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        try:
            result = await flydocs.extract(req)
            print(f"extracted in sync: latency={result.latency_ms}ms")
            return 0
        except FlydocsHTTPError as exc:
            if exc.code == "extraction_timeout":
                print("sync ceiling exceeded; falling back to async")
                submit = await flydocs.submit_job(SubmitJobRequest(**req.model_dump()))
                final = await flydocs.wait_for_completion(submit.job_id, timeout=600.0)
                print(f"async result: {final.status} {final.error_message or ''}")
                return 0 if str(final.status).startswith("SUCCE") else 1
            if exc.code == "document_too_large":
                print(f"413: {exc.detail}")
                return 2
            if exc.code == "invalid_request":
                print("422 invalid_request:")
                for issue in exc.payload.get("errors", []):
                    print(f"  - {issue}")
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
