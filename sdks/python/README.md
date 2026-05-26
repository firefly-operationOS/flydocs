# flydocs Python SDK

Official Python client for [flydocs](https://github.com/firefly-operationOS/flydocs) — the pure-multimodal Intelligent Document Processing service from Firefly OperationOS. **v1 contract** (snake_case everywhere, `Extraction` lifecycle, single `EventEnvelope` for EDA + webhooks).

- **Async-first** over `httpx` with a synchronous wrapper.
- **Typed** with Pydantic v2 — forward-compatible (`extra="allow"` everywhere).
- **Typed errors** mapping the service's RFC 7807 problem-details (`code`, `title`, `detail`, `instance`, `extensions`).
- **Webhook verification** with constant-time HMAC; returns a typed `EventEnvelope`.

## Install

The wheel is attached to every `vX.Y.Z` GitHub Release of [firefly-operationOS/flydocs](https://github.com/firefly-operationOS/flydocs). There is no PyPI publish; install the wheel directly from the release URL with [`uv`](https://docs.astral.sh/uv/):

```bash
uv add https://github.com/firefly-operationOS/flydocs/releases/download/v26.06.00/flydocs_sdk-26.6.0-py3-none-any.whl
```

The SDK depends only on `httpx` and `pydantic`.

## Quickstart (sync, with typed builders)

```python
from flydocs_sdk import (
    Client,
    DocumentTypeSpec,
    ExtractionOptions,
    ExtractionRequest,
    Field,
    FieldGroup,
    FieldType,
    FileInput,
    StageToggles,
)

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

with Client("http://localhost:8400") as flydocs:
    result = flydocs.extract(
        ExtractionRequest(
            files=[FileInput.from_path("invoice.pdf")],
            document_types=[invoice],
            options=ExtractionOptions(
                stages=StageToggles(judge=True, bbox_refine=True),
            ),
        )
    )

print(result.pipeline.model, "latency:", result.pipeline.latency_ms, "ms")
for doc in result.documents:
    for group in doc.field_groups:
        for field in group.fields:
            print(field.name, "=", field.value)
```

> **See [TUTORIAL.md](./TUTORIAL.md) for the full walkthrough** — schemas, rules, async extractions, webhooks, errors.

## Quickstart (async, with `wait_for_completion`)

```python
import asyncio
from flydocs_sdk import (
    AsyncClient,
    ExtractionStatus,
    FileInput,
    SubmitExtractionRequest,
)

async def main() -> None:
    async with AsyncClient("http://localhost:8400") as flydocs:
        ext = await flydocs.extractions.create(
            SubmitExtractionRequest(
                files=[FileInput.from_path("invoice.pdf")],
                document_types=[invoice],  # typed DocumentTypeSpec from above
                callback_url="https://example.com/webhook",
                metadata={"caller": "my-app"},
            ),
            idempotency_key="my-app:invoice:42",
        )
        print("queued", ext.id)

        final = await flydocs.wait_for_completion(
            ext.id, poll_interval=2.0, timeout=600.0
        )
        if final.status == ExtractionStatus.SUCCEEDED:
            envelope = await flydocs.extractions.get_result(ext.id)
            print("got", len(envelope.result.documents), "documents")
        elif final.error is not None:
            print("did not succeed:", final.status.value, final.error.code, final.error.message)

asyncio.run(main())
```

## Webhook verification

```python
import os
from flydocs_sdk import EVENT_TYPE_EXTRACTION_COMPLETED, WebhookVerificationError, WebhookVerifier

verifier = WebhookVerifier(secret=os.environ["FLYDOCS_WEBHOOK_HMAC_SECRET"])

# In your web framework's webhook handler:
raw_body: bytes = await request.body()
signature_header: str = request.headers.get("X-Flydocs-Signature", "")
try:
    envelope = verifier.verify(raw_body, signature_header)   # typed EventEnvelope
except WebhookVerificationError:
    return 403, "invalid signature"

if envelope.event_type == EVENT_TYPE_EXTRACTION_COMPLETED and envelope.result is not None:
    for doc in envelope.result.documents:
        ...  # persist, fan out downstream work
```

## API surface

| SDK method                                      | HTTP                                                   | Returns                       |
|-------------------------------------------------|--------------------------------------------------------|-------------------------------|
| `client.extract(req)`                           | `POST /api/v1/extract`                                  | `ExtractionResult`            |
| `client.validate(req)`                          | `POST /api/v1/extract:validate`                         | `ValidationResponse`          |
| `client.extractions.create(req, idempotency_key=...)` | `POST /api/v1/extractions`                        | `Extraction` (202)            |
| `client.extractions.list(...)`                  | `GET  /api/v1/extractions`                              | `ExtractionListResponse`      |
| `client.extractions.get(id)`                    | `GET  /api/v1/extractions/{id}`                         | `Extraction`                  |
| `client.extractions.get_result(id, wait_for_bboxes=, timeout=)` | `GET /api/v1/extractions/{id}/result`   | `ExtractionResultEnvelope`    |
| `client.extractions.cancel(id)`                 | `DELETE /api/v1/extractions/{id}`                       | `Extraction`                  |
| `client.wait_for_completion(id, ...)`           | polls `GET /api/v1/extractions/{id}`                    | `Extraction` (terminal)       |
| `client.version()`                              | `GET  /api/v1/version`                                  | `VersionInfo`                 |
| `client.health()`                               | `GET  /actuator/health/{probe}`                         | `dict`                        |

## Typed request models

| Type                           | Purpose                                                                                              |
|--------------------------------|------------------------------------------------------------------------------------------------------|
| `StageToggles`                 | Opt-in switches for every optional pipeline stage.                                                   |
| `ExtractionOptions`            | Per-request knobs (`model`, `language_hint`, `stages`, `escalation`, `transformations`).             |
| `EscalationConfig`             | Replaces v0 `escalation_threshold` + `escalation_model` (nested under `ExtractionOptions.escalation`). |
| `DocumentTypeSpec`             | Flattened v0 `DocSpec` + `DocType` (`id` / `description` / `country` are top-level fields).          |
| `FieldGroup`, `Field`          | Single recursive `Field` (arrays via `items`, objects via `fields`) replaces v0 `FieldSpec` + `FieldItem`. |
| `ValidatorSpec`                | Built-in field validator (`iban`, `vat_id`, ...); dispatch key is `name` (was `type` in v0).         |
| `VisualCheck`                  | One visual check; lives on `DocumentTypeSpec.visual_checks` (was nested under `ValidatorsSpec.visual`). |
| `RuleSpec` + `Rule{Field,Validator,Rule}Parent` | Business-rule DAG; parent discriminator is `kind` (was `parentType`).               |

## Errors

Every error subclasses `FlydocsError`:

- `FlydocsTimeoutError` — the HTTP request itself timed out on the wire.
- `FlydocsClientError`  — other transport problems (DNS, connect, TLS).
- `FlydocsHttpError`    — the service answered with a 4xx/5xx. Carries `status_code`, `code`, `title`, `detail`, `type`, `instance`, `extensions`, and the raw `payload` dict.

The service emits RFC 7807 bodies with `code` / `title` / `detail`. The v1 codes are: `not_found`, `not_ready`, `not_cancellable`, `timeout`, `file_too_large`, `unsupported_file`, `validation_failed`, `invalid_base64`, `invalid_request`, `encrypted_pdf`, `office_conversion_failed`, `archive_extraction_failed`, `image_conversion_failed`, `unauthorized`. The SDK doesn't pin to that set; it just exposes whatever the server sends.

```python
try:
    flydocs.extract(req)
except FlydocsHttpError as e:
    if e.code == "timeout":
        # fall back to async
        flydocs.extractions.create(req)
```

## Migrating from v0

Read [`docs/migration-v0-to-v1.md`](../../docs/migration-v0-to-v1.md) for the complete rename / reshape table, or jump to:

- `DocumentInput` → `FileInput`; `documents` → `files`; `document_type` → `expected_type`.
- `DocSpec` + `DocType` → `DocumentTypeSpec` (flat); `docs` → `document_types`.
- `FieldSpec` + `FieldItem` → single recursive `Field`.
- `StandardValidatorSpec` → `ValidatorSpec`; dispatch key `type` → `name`.
- `VisualValidatorSpec` + `ValidatorsSpec.visual` → `VisualCheck` + `DocumentTypeSpec.visual_checks`.
- `JobStatus` → `ExtractionStatus`; values are lowercase; `PARTIAL_SUCCEEDED` / `REFINING_BBOXES` are gone.
- `SubmitJobRequest`/`JobStatusResponse`/`SubmitJobResponse`/`JobResult`/`JobListResponse` → `SubmitExtractionRequest`/`Extraction`/`ExtractionResultEnvelope`/`ExtractionListResponse`.
- `JobWebhookPayload` → `EventEnvelope` (carries the event-type discriminator and the typed `Extraction` snapshot).
- Endpoints: `/api/v1/jobs/*` → `/api/v1/extractions/*`.
- Response: top-level `model`/`latency_ms`/`trace`/`pipeline_errors`/`usage` collapse into `pipeline: PipelineMeta`.

## Development

```bash
cd sdks/python
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

## License

Apache-2.0. Copyright © 2026 Firefly Software Solutions Inc.
