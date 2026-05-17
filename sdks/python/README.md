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

## Quickstart (sync)

```python
from flydocs_sdk import DocumentInput, ExtractionRequest, FlydocsClient

with FlydocsClient("http://localhost:8400") as flydocs:
    result = flydocs.extract(
        ExtractionRequest(
            documents=[DocumentInput.from_path("invoice.pdf")],
            docs=[
                {
                    "docType": {"documentType": "invoice"},
                    "groups": [
                        {
                            "fieldGroupName": "totals",
                            "fieldGroupFields": [
                                {"name": "total_amount", "type": "number"},
                                {"name": "currency",      "type": "string"},
                            ],
                        }
                    ],
                }
            ],
        )
    )

print(result.model, "latency:", result.latency_ms, "ms")
for doc in result.documents:
    for group in doc["fields"]:
        for field in group["fieldGroupFields"]:
            print(field["name"], "=", field.get("value"))
```

## Quickstart (async)

```python
import asyncio
from flydocs_sdk import AsyncFlydocsClient, DocumentInput, SubmitJobRequest

async def main() -> None:
    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        submit = await flydocs.submit_job(
            SubmitJobRequest(
                documents=[DocumentInput.from_path("invoice.pdf")],
                docs=[{"docType": {"documentType": "invoice"}}],
                callback_url="https://example.com/webhook",
                metadata={"caller": "my-app"},
            ),
            idempotency_key="my-app:invoice:42",
        )
        print("queued", submit.job_id)

        while True:
            status = await flydocs.get_job(submit.job_id)
            if status.status in {"SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            await asyncio.sleep(2)

        if status.status == "SUCCEEDED":
            result = await flydocs.get_job_result(submit.job_id)
            print("got", len(result.result.documents), "documents")

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

| SDK method | HTTP                                  | Returns                         |
|------------|---------------------------------------|---------------------------------|
| `extract`        | `POST /api/v1/extract`          | `ExtractionResult`              |
| `validate`       | `POST /api/v1/extract:validate` | `dict` (validation report)      |
| `submit_job`     | `POST /api/v1/jobs`             | `SubmitJobResponse`             |
| `get_job`        | `GET  /api/v1/jobs/{id}`        | `JobStatusResponse`             |
| `get_job_result` | `GET  /api/v1/jobs/{id}/result` | `JobResult`                     |
| `list_jobs`      | `GET  /api/v1/jobs`             | `JobListResponse`               |
| `cancel_job`     | `DEL  /api/v1/jobs/{id}`        | `JobStatusResponse`             |
| `version`        | `GET  /api/v1/version`          | `VersionInfo`                   |
| `health`         | `GET  /actuator/health/{probe}` | `dict`                          |

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
