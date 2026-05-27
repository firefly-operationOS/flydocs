# flydocs Python SDK — Examples (v1 contract)

Runnable async-first scripts exercising every capability from the [TUTORIAL](../TUTORIAL.md). Each example is self-contained except for shared fixtures in `examples_helpers.py`.

| # | Script                                                                       | What it shows                                                                                       |
|---|------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| 1 | [`01_first_extraction.py`](./01_first_extraction.py)                          | Smallest async extraction with a hand-written `DocumentTypeSpec`.                                   |
| 2 | [`02_typed_schema_and_rules.py`](./02_typed_schema_and_rules.py)              | Realistic invoice schema with validators + business rules + dry-run `validate`.                     |
| 3 | [`03_async_extraction_with_wait.py`](./03_async_extraction_with_wait.py)      | Submit via `extractions.create`, poll `wait_for_completion`, fetch with `extractions.get_result`.   |
| 4 | [`04_webhook_receiver_fastapi.py`](./04_webhook_receiver_fastapi.py)          | FastAPI app that verifies `X-Flydocs-Signature` and dispatches on the four v1 event types.          |
| 5 | [`05_error_handling.py`](./05_error_handling.py)                              | Branching on RFC 7807 v1 codes (`timeout`, `file_too_large`, `validation_failed`) + sync→async fallback. |
| 6 | [`06_sync_facade.py`](./06_sync_facade.py)                                    | The synchronous facade (`Client`) for non-async callers.                                            |

## Running

```bash
task docker:up:test     # spin up flydocs + mock-llm at http://localhost:8400

# Then run any example. Examples 2/3/5/6 share fixtures via PYTHONPATH:
uv run python sdks/python/examples/01_first_extraction.py path/to/invoice.pdf
PYTHONPATH=sdks/python/examples \
    uv run python sdks/python/examples/02_typed_schema_and_rules.py path/to/invoice.pdf
PYTHONPATH=sdks/python/examples \
    uv run python sdks/python/examples/03_async_extraction_with_wait.py path/to/invoice.pdf
```

The mock LLM accepts any document and returns a fixed schema-compatible response, so the examples work end-to-end without an Anthropic / OpenAI key.

## v0 → v1 notes

If you migrated from the v0 SDK, every example here highlights the breaking changes:

* Request bodies use `files` (was `documents`) and `document_types` (was `docs`).
* Responses nest `model` / `latency_ms` / `trace` under `result.pipeline`.
* `documents[*].field_groups[*].fields` replaces the v0 `documents[*]["fields"][*]["fieldGroupFields"]` dict walk.
* Async endpoints are `POST /api/v1/extractions` etc. (was `/api/v1/jobs`).
* Statuses are lowercase (`queued`, `succeeded`, ...).
* Webhook payloads use a single `EventEnvelope` with a dotted `event_type` (`extraction.completed`, ...).
