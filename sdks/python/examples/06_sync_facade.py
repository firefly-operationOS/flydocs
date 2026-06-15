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
    with Client("http://localhost:8080") as flydocs:
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
