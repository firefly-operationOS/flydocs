"""Hello, flydocs — the smallest async-first runnable example.

Run from the repo root, with a flydocs service reachable at
``http://localhost:8400`` (e.g. via ``task docker:up:test``)::

    uv run python sdks/python/examples/01_first_extraction.py path/to/invoice.pdf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from flydocs_sdk import (
    AsyncFlydocsClient,
    DocSpec,
    DocumentInput,
    ExtractionRequest,
    FieldGroup,
    FieldSpec,
    FieldType,
)


async def main(path: Path) -> int:
    invoice = DocSpec(
        doc_type={"documentType": "invoice"},
        field_groups=[
            FieldGroup.of(
                "totals",
                FieldSpec(field_name="total_amount", field_type=FieldType.NUMBER, required=True),
                FieldSpec(field_name="currency", field_type=FieldType.STRING, required=True),
            )
        ],
    )

    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        result = await flydocs.extract(
            ExtractionRequest(
                documents=[DocumentInput.from_path(path)],
                docs=[invoice],
            )
        )

    print(f"model={result.model}   latency={result.latency_ms}ms")
    for doc in result.documents:
        for group in doc["fields"]:
            for field in group["fieldGroupFields"]:
                print(
                    f"  {field['name']:>15} = {field.get('value')!r:>20}   "
                    f"conf={field.get('confidence', 0):.2f}"
                )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python 01_first_extraction.py path/to/document.pdf", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
