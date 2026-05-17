# flydocs Python SDK — Examples

Runnable async-first scripts exercising every capability from the [TUTORIAL](../TUTORIAL.md). Each example is self-contained except for shared fixtures in `examples_helpers.py`.

| # | Script                                                            | What it shows                                                    |
|---|-------------------------------------------------------------------|------------------------------------------------------------------|
| 1 | [`01_first_extraction.py`](./01_first_extraction.py)               | Smallest async extraction, hand-written `DocSpec`.               |
| 2 | [`02_typed_schema_and_rules.py`](./02_typed_schema_and_rules.py)   | Realistic invoice schema with validators + business rules + dry-run validate. |
| 3 | [`03_async_job_with_wait.py`](./03_async_job_with_wait.py)         | Async job submission + `wait_for_completion` + `get_job_result`. |
| 4 | [`04_webhook_receiver_fastapi.py`](./04_webhook_receiver_fastapi.py) | FastAPI app that verifies `X-Flydocs-Signature` and unpacks the payload. |
| 5 | [`05_error_handling.py`](./05_error_handling.py)                   | RFC 7807 typed errors and sync→async fallback on `extraction_timeout`. |
| 6 | [`06_sync_facade.py`](./06_sync_facade.py)                         | The synchronous facade (`FlydocsClient`) for non-async callers.  |

## Running

```bash
task docker:up:test     # spin up flydocs + mock-llm at http://localhost:8400

# Then run any example. Examples 2/3/5/6 share fixtures via PYTHONPATH:
uv run python sdks/python/examples/01_first_extraction.py path/to/invoice.pdf
PYTHONPATH=sdks/python/examples \
    uv run python sdks/python/examples/02_typed_schema_and_rules.py path/to/invoice.pdf
PYTHONPATH=sdks/python/examples \
    uv run python sdks/python/examples/03_async_job_with_wait.py path/to/invoice.pdf
```

The mock LLM accepts any document and returns a fixed schema-compatible response, so the examples work end-to-end without an Anthropic / OpenAI key.
