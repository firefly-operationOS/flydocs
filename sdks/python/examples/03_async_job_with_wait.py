"""Submit an async job, poll until terminal, fetch the result.

The same request shape as ``extract``, just driven through the queue.
Use this for long-running workloads, batches, or anywhere you'd like
the worker to deliver via webhook.

    uv run python sdks/python/examples/03_async_job_with_wait.py path/to/document.pdf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples_helpers import INVOICE_DOC_SPEC, INVOICE_RULES  # type: ignore[import-not-found]

from flydocs_sdk import (
    AsyncFlydocsClient,
    DocumentInput,
    JobStatus,
    SubmitJobRequest,
)


async def main(path: Path) -> int:
    async with AsyncFlydocsClient("http://localhost:8400", timeout=30.0) as flydocs:
        submit = await flydocs.submit_job(
            SubmitJobRequest(
                documents=[DocumentInput.from_path(path)],
                docs=[INVOICE_DOC_SPEC],
                rules=INVOICE_RULES,
                callback_url="https://your-app.example.com/flydocs/webhook",
                metadata={"caller": "examples:03"},
            ),
            idempotency_key=f"examples:03:{path.name}",
        )
        print(f"queued {submit.job_id} ({submit.status})")

        final = await flydocs.wait_for_completion(
            submit.job_id,
            poll_interval=2.0,
            timeout=600.0,
        )

        if final.status == JobStatus.SUCCEEDED:
            result = (await flydocs.get_job_result(submit.job_id)).result
            print(f"done: {len(result.documents)} document(s), {result.latency_ms}ms")
            return 0
        if final.status == JobStatus.PARTIAL_SUCCEEDED:
            result = (await flydocs.get_job_result(submit.job_id)).result
            print(
                f"partial: {len(result.documents)} document(s), "
                f"{len(result.pipeline_errors)} non-fatal errors"
            )
            for err in result.pipeline_errors:
                print(f"  - {err}")
            return 0
        print(f"job did not succeed: {final.status} {final.error_code} {final.error_message}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python 03_async_job_with_wait.py path/to/document.pdf", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
