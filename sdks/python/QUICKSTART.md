# flydocs Python SDK — Quickstart

The fastest path from zero to your first extracted invoice. Five minutes, end to end.

---

## 1. Install (30 s)

```bash
uv add https://github.com/firefly-operationOS/flydocs/releases/download/v26.05.01/flydocs_sdk-26.5.1-py3-none-any.whl
```

The SDK depends only on `httpx` and `pydantic`.

## 2. Spin up a local flydocs (1 min)

From the repo root:

```bash
task docker:up:test     # serves http://localhost:8400 backed by a mock LLM
```

If you already have a running flydocs deployment, point `base_url` at it and skip this step.

## 3. Extract (3 min)

```python
# quickstart.py
import asyncio
from flydocs_sdk import (
    AsyncFlydocsClient,
    DocSpec,
    DocumentInput,
    ExtractionRequest,
    FieldGroup,
    FieldSpec,
    FieldType,
)


async def main() -> None:
    # 1. Describe what you want extracted. The DocSpec carries the field
    #    schema; the FieldGroup bundles related fields under one name.
    invoice = DocSpec(
        doc_type={"documentType": "invoice"},
        field_groups=[
            FieldGroup.of(
                "totals",
                FieldSpec(field_name="total_amount", field_type=FieldType.NUMBER, required=True),
                FieldSpec(field_name="currency",     field_type=FieldType.STRING, required=True),
            )
        ],
    )

    # 2. Build the request -- one or more files + one or more DocSpecs.
    request = ExtractionRequest(
        documents=[DocumentInput.from_path("invoice.pdf")],
        docs=[invoice],
    )

    # 3. Call the service. AsyncFlydocsClient is the primary integration
    #    surface; close it as a context manager.
    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        result = await flydocs.extract(request)

    # 4. Read the response. Each ExtractedDocument has fieldGroups, each
    #    with extracted fields carrying value / confidence / bbox.
    print(f"model={result.model}   latency={result.latency_ms} ms")
    for doc in result.documents:
        for group in doc["fields"]:
            for field in group["fieldGroupFields"]:
                print(
                    f"  {field['name']:>15} = {field.get('value')!r:>20}  "
                    f"conf={field.get('confidence', 0):.2f}"
                )


asyncio.run(main())
```

```bash
uv run python quickstart.py
# model=anthropic:claude-sonnet-4-6   latency=412 ms
#    total_amount =              1234.56  conf=0.97
#        currency =                 'EUR'  conf=0.99
```

That's it — you've extracted structured data from a document.

---

## What next

- **[TUTORIAL.md](./TUTORIAL.md)** — the full payload composition reference: every field, every option, every variant, with constraints and worked examples.
- **[examples/](./examples/)** — six runnable scripts: typed schema + rules, async jobs with `wait_for_completion`, webhook receiver, error handling, sync facade.
- **[README.md](./README.md)** — feature matrix, API surface table, error model.

## Need a synchronous API?

If you can't run an event loop:

```python
from flydocs_sdk import FlydocsClient

with FlydocsClient("http://localhost:8400") as flydocs:
    result = flydocs.extract(request)
```

`FlydocsClient` mirrors `AsyncFlydocsClient` method-for-method, just without `await`. Prefer the async client whenever you can.
