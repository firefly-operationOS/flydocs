# flydocs Python SDK

Official Python client for [flydocs](https://github.com/firefly-operationOS/flydocs) — the pure-multimodal Intelligent Document Processing service from Firefly OperationOS.

- **Async-first** over `httpx` with a synchronous wrapper.
- **Typed** with Pydantic v2 — forward-compatible by design (unknown fields are preserved).
- **Typed errors** mapping the service's RFC 7807 problem-details.
- **Webhook verification** with constant-time HMAC.

## Install

The wheel is attached to every `vX.Y.Z` GitHub Release of [firefly-operationOS/flydocs](https://github.com/firefly-operationOS/flydocs). There is no PyPI publish; install the wheel directly from the release URL with [`uv`](https://docs.astral.sh/uv/):

```bash
uv add https://github.com/firefly-operationOS/flydocs/releases/download/v0.1.0/flydocs_sdk-0.1.0-py3-none-any.whl
```

Or pin it in your `pyproject.toml`:

```toml
[project]
dependencies = ["flydocs-sdk"]

[tool.uv.sources]
flydocs-sdk = { url = "https://github.com/firefly-operationOS/flydocs/releases/download/v0.1.0/flydocs_sdk-0.1.0-py3-none-any.whl" }
```

The SDK depends only on `httpx` and `pydantic`.

## Quickstart (sync, with typed builders)

```python
from flydocs_sdk import (
    DocSpec,
    DocumentInput,
    ExtractionOptions,
    ExtractionRequest,
    FieldGroup,
    FieldSpec,
    FieldType,
    FlydocsClient,
    StageToggles,
)

invoice = DocSpec(
    doc_type={"documentType": "invoice"},
    field_groups=[
        FieldGroup.of(
            "totals",
            FieldSpec(field_name="total_amount", field_type=FieldType.NUMBER, required=True),
            FieldSpec(field_name="currency",      field_type=FieldType.STRING, required=True),
        )
    ],
)

with FlydocsClient("http://localhost:8400") as flydocs:
    result = flydocs.extract(
        ExtractionRequest(
            documents=[DocumentInput.from_path("invoice.pdf")],
            docs=[invoice],
            options=ExtractionOptions(stages=StageToggles(judge=True, bbox_refine=True)),
        )
    )

print(result.model, "latency:", result.latency_ms, "ms")
for doc in result.documents:
    for group in doc["fields"]:
        for field in group["fieldGroupFields"]:
            print(field["name"], "=", field.get("value"))
```

> **See [TUTORIAL.md](./TUTORIAL.md) for the full walkthrough** — schemas, rules, async jobs, webhooks, errors.

## Quickstart (async, with `wait_for_completion`)

```python
import asyncio
from flydocs_sdk import (
    AsyncFlydocsClient,
    DocumentInput,
    JobStatus,
    SubmitJobRequest,
)

async def main() -> None:
    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        submit = await flydocs.submit_job(
            SubmitJobRequest(
                documents=[DocumentInput.from_path("invoice.pdf")],
                docs=[invoice],   # typed DocSpec from the sync example above
                callback_url="https://example.com/webhook",
                metadata={"caller": "my-app"},
            ),
            idempotency_key="my-app:invoice:42",
        )
        print("queued", submit.job_id)

        final = await flydocs.wait_for_completion(
            submit.job_id, poll_interval=2.0, timeout=600.0
        )
        if final.status == JobStatus.SUCCEEDED:
            result = await flydocs.get_job_result(submit.job_id)
            print("got", len(result.result.documents), "documents")
        else:
            print("job did not succeed:", final.status, final.error_message)

asyncio.run(main())
```

## Webhook verification

```python
from flydocs_sdk import WebhookVerifier, WebhookVerificationError, JobWebhookPayload

verifier = WebhookVerifier(secret=os.environ["FLYDOCS_WEBHOOK_HMAC_SECRET"])

# In your web framework's webhook handler:
raw_body: bytes = await request.body()
signature_header: str = request.headers.get("X-Flydocs-Signature", "")
try:
    verifier.verify(raw_body, signature_header)
except WebhookVerificationError:
    return 403, "invalid signature"

payload = JobWebhookPayload.model_validate_json(raw_body)
# ... handle payload.status, payload.result, etc.
```

## API surface

| SDK method            | HTTP                                  | Returns                         |
|-----------------------|---------------------------------------|---------------------------------|
| `extract`             | `POST /api/v1/extract`                | `ExtractionResult`              |
| `validate`            | `POST /api/v1/extract:validate`       | `dict` (validation report)      |
| `submit_job`          | `POST /api/v1/jobs`                   | `SubmitJobResponse`             |
| `get_job`             | `GET  /api/v1/jobs/{id}`              | `JobStatusResponse`             |
| `get_job_result`      | `GET  /api/v1/jobs/{id}/result`       | `JobResult`                     |
| `list_jobs`           | `GET  /api/v1/jobs`                   | `JobListResponse`               |
| `cancel_job`          | `DEL  /api/v1/jobs/{id}`              | `JobStatusResponse`             |
| `wait_for_completion` | polls `GET /api/v1/jobs/{id}`         | `JobStatusResponse` (terminal)  |
| `version`             | `GET  /api/v1/version`                | `VersionInfo`                   |
| `health`              | `GET  /actuator/health/{probe}`       | `dict`                          |

## Typed request models

| Type                       | Purpose                                                                       |
|----------------------------|-------------------------------------------------------------------------------|
| `StageToggles`             | Opt-in switches for every optional pipeline stage.                            |
| `ExtractionOptions`        | Per-request knobs (model, language hint, stages, escalation, transformations).|
| `DocSpec` + `DocType`      | One expected document type plus its field schema and validators.              |
| `FieldGroup`, `FieldSpec`, `FieldItem` | Field schema (recursive: array fields nest items).                |
| `StandardValidatorSpec`    | Built-in field validator (IBAN, BIC, VAT_ID, …) attached to a `FieldSpec`.    |
| `RuleSpec` + `RuleFieldParent` / `RuleValidatorParent` / `RuleRuleParent` | Business-rule DAG. |

## Errors

Every error subclasses `FlydocsError`:

- `FlydocsTimeoutError` — the HTTP request itself timed out on the wire.
- `FlydocsClientError`  — other transport problems (DNS, connect, TLS).
- `FlydocsHTTPError`    — the service answered with a 4xx/5xx. Carries `status_code`, `code`, `title`, `detail`, and the raw `payload` dict.

The service emits RFC 7807-ish bodies with `code` / `title` / `detail`; the SDK decodes those onto the typed exception so you can branch:

```python
try:
    flydocs.extract(req)
except FlydocsHTTPError as e:
    if e.code == "extraction_timeout":
        # fall back to async
        flydocs.submit_job(req)
```

## Development

```bash
cd sdks/python
pip install -e ".[dev]"
pytest
ruff check src tests
```

## License

Apache-2.0. Copyright © 2026 Firefly Software Solutions Inc.
