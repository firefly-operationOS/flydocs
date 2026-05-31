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

"""Hello, flydocs -- the smallest async-first runnable example (v1 contract).

What it shows:
  * Build a typed :class:`DocumentTypeSpec` for a single document type.
  * Submit one file via the v1 ``files`` / ``document_types`` keys.
  * Walk the new response shape (``documents[*].field_groups[*].fields``).

Run from the repo root, with a flydocs service reachable at
``http://localhost:8400`` (e.g. via ``task docker:up:test``)::

    uv run python sdks/python/examples/01_first_extraction.py path/to/invoice.pdf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from flydocs_sdk import (
    AsyncClient,
    DocumentTypeSpec,
    ExtractionRequest,
    Field,
    FieldGroup,
    FieldType,
    FileInput,
)


async def main(path: Path) -> int:
    invoice = DocumentTypeSpec(
        id="invoice",
        field_groups=[
            FieldGroup(
                name="totals",
                fields=[
                    Field(name="total_amount", type=FieldType.NUMBER, required=True),
                    Field(name="currency", type=FieldType.STRING, required=True),
                ],
            ),
        ],
    )

    async with AsyncClient("http://localhost:8400") as flydocs:
        result = await flydocs.extract(
            ExtractionRequest(
                files=[FileInput.from_path(path)],
                document_types=[invoice],
            )
        )

    # In v1, model + latency live under ``pipeline``.
    print(f"id={result.id}   model={result.pipeline.model}   latency={result.pipeline.latency_ms}ms")
    for doc in result.documents:
        for group in doc.field_groups:
            for field in group.fields:
                value = field.value if field.value is not None else "<missing>"
                print(f"  {field.name:>15} = {value!r:>20}   conf={field.confidence:.2f}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python 01_first_extraction.py path/to/document.pdf", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
