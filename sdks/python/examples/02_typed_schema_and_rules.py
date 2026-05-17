"""A realistic invoice extraction: typed DocSpec, validators, rules, dry-run.

Run from the repo root::

    uv run python sdks/python/examples/02_typed_schema_and_rules.py path/to/invoice.pdf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples_helpers import INVOICE_DOC_SPEC, INVOICE_RULES  # type: ignore[import-not-found]

from flydocs_sdk import (
    AsyncFlydocsClient,
    DocumentInput,
    ExtractionOptions,
    ExtractionRequest,
    StageToggles,
)


async def main(path: Path) -> int:
    req = ExtractionRequest(
        documents=[DocumentInput.from_path(path)],
        docs=[INVOICE_DOC_SPEC],
        rules=INVOICE_RULES,
        options=ExtractionOptions(
            language_hint="es",
            stages=StageToggles(
                classifier=True,
                field_validation=True,
                judge=True,
                bbox_refine=True,
                rule_engine=True,
            ),
        ),
    )
    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        # Dry-run the semantic validator first.
        report = await flydocs.validate(req)
        if not report["ok"]:
            print("semantic validation failed:")
            for err in report["errors"]:
                print(f"  {err['path']}: {err['message']}")
            return 1

        result = await flydocs.extract(req, correlation_id="examples:02")

    print(f"model={result.model}   latency={result.latency_ms}ms")
    for rr in result.rule_results:
        print(f"  rule {rr['rule_id']:>20} = {rr['output']}   {rr.get('summary', '')}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python 02_typed_schema_and_rules.py path/to/invoice.pdf", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
