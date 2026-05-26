"""A realistic invoice extraction: typed DocumentTypeSpec + validators + rules + dry-run.

What it shows:
  * The full v1 ``DocumentTypeSpec`` shape with validators on fields.
  * The new rule discriminator (``kind`` instead of ``parentType``).
  * The dry-run :meth:`AsyncClient.validate` returning a typed
    :class:`ValidationResponse` instead of a raw dict.
  * Reading per-rule results through the typed :class:`RuleResult`.

Run from the repo root::

    PYTHONPATH=sdks/python/examples \
        uv run python sdks/python/examples/02_typed_schema_and_rules.py path/to/invoice.pdf
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples_helpers import INVOICE_DOCUMENT_TYPE, INVOICE_RULES  # type: ignore[import-not-found]

from flydocs_sdk import (
    AsyncClient,
    ExtractionOptions,
    ExtractionRequest,
    FileInput,
    StageToggles,
)


async def main(path: Path) -> int:
    req = ExtractionRequest(
        files=[FileInput.from_path(path)],
        document_types=[INVOICE_DOCUMENT_TYPE],
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
    async with AsyncClient("http://localhost:8400") as flydocs:
        report = await flydocs.validate(req)
        if not report.ok:
            print("semantic validation failed:")
            for err in report.errors:
                print(f"  {err.get('path', '?')}: {err.get('message', err)}")
            return 1

        result = await flydocs.extract(req, correlation_id="examples:02")

    print(f"id={result.id}   model={result.pipeline.model}   latency={result.pipeline.latency_ms}ms")
    for rr in result.rule_results:
        suffix = f"   {rr.summary}" if rr.summary else ""
        print(f"  rule {rr.rule_id:>22} = {rr.output}{suffix}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python 02_typed_schema_and_rules.py path/to/invoice.pdf", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
