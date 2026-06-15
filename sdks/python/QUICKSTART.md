# flydocs Python SDK — Quickstart (v1)

The fastest path from zero to your first extracted invoice. Five minutes, end to end. This document covers the **v1 contract** released in `26.6.0`.

---

## 1. Install (30 s)

```bash
uv add https://github.com/firefly-operationOS/flydocs/releases/download/v26.06.00/flydocs_sdk-26.6.0-py3-none-any.whl
```

The SDK depends only on `httpx` and `pydantic`.

## 2. Spin up a local flydocs (1 min)

From the repo root:

```bash
task docker:up:test     # serves http://localhost:8080 backed by a mock LLM
```

If you already have a running flydocs deployment, point `base_url` at it and skip this step.

## 3. Extract (3 min)

```python
# quickstart.py
import asyncio
from flydocs_sdk import (
    AsyncClient,
    DocumentTypeSpec,
    ExtractionRequest,
    Field,
    FieldGroup,
    FieldType,
    FileInput,
)


async def main() -> None:
    # 1. Describe what you want extracted. The DocumentTypeSpec carries
    #    the schema; the FieldGroup bundles related fields under one name.
    invoice = DocumentTypeSpec(
        id="invoice",
        field_groups=[
            FieldGroup(
                name="totals",
                fields=[
                    Field(name="total_amount", type=FieldType.NUMBER, required=True),
                    Field(name="currency",     type=FieldType.STRING, required=True),
                ],
            ),
        ],
    )

    # 2. Build the request -- one or more files + one or more document types.
    #    v1 keys: ``files`` (was ``documents``) and ``document_types`` (was ``docs``).
    request = ExtractionRequest(
        files=[FileInput.from_path("invoice.pdf")],
        document_types=[invoice],
    )

    # 3. Call the service. AsyncClient is the primary integration surface;
    #    close it as a context manager.
    async with AsyncClient("http://localhost:8080") as flydocs:
        result = await flydocs.extract(request)

    # 4. Read the response. v1 nests model + latency under ``result.pipeline``;
    #    each Document has ``field_groups``, each with ``fields``.
    print(f"id={result.id}   model={result.pipeline.model}   "
          f"latency={result.pipeline.latency_ms} ms")
    for doc in result.documents:
        for group in doc.field_groups:
            for field in group.fields:
                print(
                    f"  {field.name:>15} = {field.value!r:>20}  "
                    f"conf={field.confidence:.2f}"
                )


asyncio.run(main())
```

```bash
uv run python quickstart.py
# id=ext_a1b2c3   model=anthropic:claude-sonnet-4-6   latency=412 ms
#    total_amount =              1234.56  conf=0.97
#        currency =                 'EUR'  conf=0.99
```

That's it — you've extracted structured data from a document.

---

## What next

- **[TUTORIAL.md](./TUTORIAL.md)** — the full payload composition reference: every field, every option, every variant, with constraints and worked examples.
- **[examples/](./examples/)** — six runnable scripts: typed schema + rules, async extractions with `wait_for_completion`, webhook receiver, error handling, sync facade.
- **[README.md](./README.md)** — feature matrix, API surface table, error model.
- **[docs/migration-v0-to-v1.md](../../docs/migration-v0-to-v1.md)** — full table of v0 → v1 renames if you are upgrading.

## Need a synchronous API?

If you can't run an event loop:

```python
from flydocs_sdk import Client

with Client("http://localhost:8080") as flydocs:
    result = flydocs.extract(request)
```

`Client` mirrors `AsyncClient` method-for-method, just without `await`. Prefer the async client whenever you can.
