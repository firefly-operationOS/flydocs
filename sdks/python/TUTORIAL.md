# flydocs Python SDK — Tutorial

A complete walkthrough of the flydocs Python SDK. Each section is a small, runnable script.

> **Prerequisites**
> A flydocs service reachable at some base URL. For local development:
> ```bash
> task docker:up:test    # starts flydocs + a mock LLM at http://localhost:8400
> ```

---

## Table of contents

1. [Install](#1-install)
2. [Your first extraction](#2-your-first-extraction)
3. [Designing a schema with `DocSpec` + `FieldSpec`](#3-designing-a-schema-with-docspec--fieldspec)
4. [Tuning the pipeline with `StageToggles`](#4-tuning-the-pipeline-with-stagetoggles)
5. [Adding business rules](#5-adding-business-rules)
6. [Asynchronous extraction with `wait_for_completion`](#6-asynchronous-extraction-with-wait_for_completion)
7. [Webhook delivery + signature verification](#7-webhook-delivery--signature-verification)
8. [Error handling — RFC 7807 problem-details](#8-error-handling--rfc-7807-problem-details)
9. [Async-first usage](#9-async-first-usage)

---

## 1. Install

The SDK is published as a wheel on every `vX.Y.Z` GitHub Release. Install directly from the release URL with [`uv`](https://docs.astral.sh/uv/):

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

---

## 2. Your first extraction

```python
from flydocs_sdk import (
    DocumentInput,
    ExtractionRequest,
    FlydocsClient,
)

with FlydocsClient("http://localhost:8400") as flydocs:
    result = flydocs.extract(
        ExtractionRequest(
            documents=[DocumentInput.from_path("invoice.pdf")],
            docs=[
                # A minimal DocSpec the service understands. The next
                # section shows how to use the typed builders instead.
                {"docType": {"documentType": "invoice"},
                 "fieldGroups": [{
                     "fieldGroupName": "totals",
                     "fieldGroupFields": [
                         {"name": "total_amount", "type": "number"},
                         {"name": "currency",      "type": "string"},
                     ]}]}
            ],
        )
    )

print(f"model={result.model}   latency={result.latency_ms}ms")
for doc in result.documents:
    for group in doc["fields"]:
        for field in group["fieldGroupFields"]:
            print(f"  {field['name']:>15} = {field.get('value')!r:>20}   conf={field.get('confidence', 0):.2f}")
```

`FlydocsClient(...)` is a synchronous client; it opens a connection pool, sends one request, and closes the pool when the `with` block exits. Use it for scripts, batch tools, and webhook handlers.

---

## 3. Designing a schema with `DocSpec` + `FieldSpec`

Hand-built dicts get tedious past a few fields. The SDK ships fully typed request models with autocomplete-friendly factories:

```python
from flydocs_sdk import (
    DocSpec,
    DocType,
    FieldGroup,
    FieldItem,
    FieldSpec,
    FieldType,
    StandardFormat,
    StandardValidatorSpec,
    StandardValidatorType,
)

invoice = DocSpec(
    doc_type=DocType(
        document_type="invoice",
        description="Vendor invoice (paper or PDF)",
        country="ES",
    ),
    field_groups=[
        FieldGroup.of(
            "header",
            FieldSpec(field_name="invoice_number", field_type=FieldType.STRING, required=True),
            FieldSpec(field_name="invoice_date",    field_type=FieldType.STRING,
                      format=StandardFormat.DATE,    required=True),
            FieldSpec(field_name="supplier_vat",    field_type=FieldType.STRING,
                      standard_validators=[
                          StandardValidatorSpec(type=StandardValidatorType.VAT_ID,
                                                params={"country": "ES"})
                      ]),
        ),
        FieldGroup.of(
            "totals",
            FieldSpec(field_name="subtotal",      field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="tax_amount",    field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="total_amount",  field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="currency",      field_type=FieldType.STRING, required=True),
        ),
        # Repeating rows (array field):
        FieldGroup.of(
            "line_items_block",
            FieldSpec(
                field_name="line_items",
                field_type=FieldType.ARRAY,
                items=[
                    FieldItem(field_name="description", field_type=FieldType.STRING),
                    FieldItem(field_name="quantity",    field_type=FieldType.NUMBER),
                    FieldItem(field_name="unit_price",  field_type=FieldType.NUMBER),
                    FieldItem(field_name="line_total",  field_type=FieldType.NUMBER),
                ],
            ),
        ),
    ],
)
```

Plug the `DocSpec` straight into the request:

```python
req = ExtractionRequest(
    documents=[DocumentInput.from_path("invoice.pdf")],
    docs=[invoice],
)
```

> **Validator catalogue.** `StandardValidatorType` exposes the built-in checks the service ships with (IBAN, BIC, NIE, VAT_ID, PHONE_E164, …). Pass extras through `params` — for example country codes for region-specific validators.

---

## 4. Tuning the pipeline with `StageToggles`

The multimodal extractor is always on; every other stage is opt-in. Build your stage map with `StageToggles`:

```python
from flydocs_sdk import ExtractionOptions, StageToggles

options = ExtractionOptions(
    return_bboxes=True,
    language_hint="es",
    model="anthropic:claude-sonnet-4-6",
    stages=StageToggles(
        classifier=True,           # auto-route multiple files to the right DocSpec
        field_validation=True,     # run StandardValidators after extract
        judge=True,                # LLM re-evaluation of every field
        bbox_refine=True,          # fuzzy-match values against the document's real text
        rule_engine=True,          # evaluate the business-rule DAG
    ),
    escalation_threshold=0.25,     # rerun with the stronger model when >25% fields fail the judge
    escalation_model="anthropic:claude-opus-4-7",
)

req = ExtractionRequest(
    documents=[DocumentInput.from_path("invoice.pdf")],
    docs=[invoice],
    options=options,
)
```

Defaults match the service's defaults — an empty `StageToggles()` produces the same behaviour as omitting the field entirely.

---

## 5. Adding business rules

Rules are natural-language predicates evaluated against the extracted fields, validator outcomes, or upstream rule results. They form a DAG; the engine sorts and runs them in dependency order.

```python
from flydocs_sdk import (
    RuleFieldParent,
    RuleOutputSpec,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
)

rules = [
    RuleSpec(
        id="totals_consistent",
        predicate="subtotal + tax_amount equals total_amount within 0.01",
        parents=[RuleFieldParent(
            document_type="invoice",
            field_names=["subtotal", "tax_amount", "total_amount"],
        )],
    ),
    RuleSpec(
        id="vat_id_valid",
        predicate="The supplier VAT id passes the VAT_ID validator",
        parents=[RuleValidatorParent(
            document_type="invoice", validator_name="vat_id",
        )],
    ),
    RuleSpec(
        id="invoice_acceptable",
        predicate="totals are consistent AND the VAT id is valid",
        parents=[
            RuleRuleParent(rule_id="totals_consistent"),
            RuleRuleParent(rule_id="vat_id_valid"),
        ],
        output=RuleOutputSpec(type="boolean"),
    ),
]

req = ExtractionRequest(
    documents=[DocumentInput.from_path("invoice.pdf")],
    docs=[invoice],
    rules=rules,
    options=ExtractionOptions(stages=StageToggles(
        field_validation=True,
        rule_engine=True,
    )),
)
```

In the response, `result.rule_results` carries one entry per rule with `output`, `summary`, and optional `human_revision` instructions.

---

## 6. Asynchronous extraction with `wait_for_completion`

For long-running workloads, submit the job and let the service work in the background. The SDK ships a `wait_for_completion` helper that polls until a terminal status is reached:

```python
from flydocs_sdk import (
    DocumentInput,
    FlydocsClient,
    JobStatus,
    SubmitJobRequest,
)

with FlydocsClient("http://localhost:8400") as flydocs:
    submit = flydocs.submit_job(
        SubmitJobRequest(
            documents=[DocumentInput.from_path("big-batch.pdf")],
            docs=[invoice],
            callback_url="https://your-app.example.com/flydocs/webhook",
            metadata={"caller": "ingest-pipeline", "batch_id": "b-42"},
        ),
        idempotency_key="ingest-pipeline:b-42",   # safe to retry without dup jobs
    )
    print(f"queued {submit.job_id}")

    final = flydocs.wait_for_completion(
        submit.job_id,
        poll_interval=2.0,
        timeout=900.0,
    )

    if final.status == JobStatus.SUCCEEDED:
        result = flydocs.get_job_result(submit.job_id).result
        print(f"done: {len(result.documents)} document(s), {result.latency_ms}ms")
    elif final.status == JobStatus.PARTIAL_SUCCEEDED:
        result = flydocs.get_job_result(submit.job_id).result
        print(f"partial: {len(result.pipeline_errors)} non-fatal errors")
    else:
        print(f"job did not succeed: {final.status} {final.error_code} {final.error_message}")
```

`wait_for_completion` returns the final `JobStatusResponse` no matter the outcome (success or failure) so you can branch in one place. It only raises `TimeoutError` when the deadline elapses while the job is still in flight.

> **Idempotency.** Send the same `Idempotency-Key` to replay an existing submission instead of creating a duplicate job. The service indexes by key so retried submissions are cheap.

---

## 7. Webhook delivery + signature verification

When `callback_url` is set, the service POSTs a `JobWebhookPayload` to that URL when the job reaches a terminal status. It signs the body with HMAC-SHA256 in the `X-Flydocs-Signature` header (configured via `FLYDOCS_WEBHOOK_HMAC_SECRET` on the service).

```python
from flydocs_sdk import (
    JobStatus,
    JobWebhookPayload,
    WebhookVerificationError,
    WebhookVerifier,
)

verifier = WebhookVerifier(secret=os.environ["FLYDOCS_WEBHOOK_HMAC_SECRET"])

# Example with FastAPI; works the same with Starlette, Flask, Django.
from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI()

@app.post("/flydocs/webhook")
async def on_webhook(
    request: Request,
    x_flydocs_signature: str = Header(...),
) -> dict:
    body = await request.body()        # MUST be the raw bytes
    try:
        verifier.verify(body, x_flydocs_signature)
    except WebhookVerificationError:
        raise HTTPException(status_code=403, detail="bad signature")

    payload = JobWebhookPayload.model_validate_json(body)
    if payload.status == JobStatus.SUCCEEDED and payload.result is not None:
        for doc in payload.result.documents:
            # ... persist the extracted fields, kick off downstream work
            pass
    return {"ok": True}
```

**Important:** verify against the *raw* request body bytes. If your framework deserialised the JSON before you got the bytes, ask the framework for the original body — re-encoding the JSON will change the digest.

---

## 8. Error handling — RFC 7807 problem-details

Every non-2xx response decodes into a typed `FlydocsHTTPError` so you can branch on the service's `code`:

```python
from flydocs_sdk import (
    FlydocsClient,
    FlydocsClientError,
    FlydocsHTTPError,
    FlydocsTimeoutError,
)

try:
    result = flydocs.extract(req)
except FlydocsHTTPError as exc:
    if exc.code == "extraction_timeout":
        # service ran out of patience inside the sync ceiling — fall back to async
        submit = flydocs.submit_job(SubmitJobRequest(**req.model_dump()))
    elif exc.code == "document_too_large":
        # split the file or compress it
        raise
    elif exc.code == "invalid_request":
        # semantic validation found a rule referencing an unknown field, etc.
        # exc.payload carries the full list of issues
        for issue in exc.payload.get("errors", []):
            print(issue)
        raise
    else:
        raise
except FlydocsTimeoutError:
    # the HTTP request itself timed out on the wire (no service response)
    raise
except FlydocsClientError:
    # other transport failure (DNS, connect, TLS)
    raise
```

Common `code` values:

| `code`                  | Status | Meaning                                                          |
|-------------------------|--------|------------------------------------------------------------------|
| `extraction_timeout`    | 408    | Pipeline exceeded the sync ceiling. Retry via `submit_job`.      |
| `document_too_large`    | 413    | Document over `FLYDOCS_MAX_BYTES`.                               |
| `invalid_base64`        | 422    | `content_base64` failed strict parsing.                          |
| `invalid_request`       | 422    | Semantic validation found issues (rule refs unknown field, etc.).|
| `job_not_ready`         | 409    | Job exists but result is not available yet.                      |
| `job_not_cancellable`   | 409    | Job has already started; mid-flight cancellation isn't supported.|
| `JOB_NOT_FOUND`         | 404    | Unknown `job_id`.                                                |

---

## 9. Async-first usage

If you live on `asyncio` (FastAPI, Starlette, aiohttp client), use `AsyncFlydocsClient` directly — there's no extra cost:

```python
import asyncio
from flydocs_sdk import AsyncFlydocsClient, DocumentInput, ExtractionRequest

async def main() -> None:
    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        result = await flydocs.extract(
            ExtractionRequest(
                documents=[DocumentInput.from_path("invoice.pdf")],
                docs=[invoice],
            )
        )
        print(result.model, result.latency_ms)

asyncio.run(main())
```

Every method on `FlydocsClient` mirrors a coroutine on `AsyncFlydocsClient`. The sync client is just a thin wrapper that drives the async one on a dedicated event loop — share an `AsyncFlydocsClient` across your app to avoid per-request loop setup.

---

## Further reading

- [`docs/api-reference.md`](../../docs/api-reference.md) — full HTTP wire contract.
- [`docs/pipeline.md`](../../docs/pipeline.md) — stage DAG, opt-in flags, what each stage does.
- [`docs/rule-engine.md`](../../docs/rule-engine.md) — rule semantics and the DAG resolution.
- [`docs/standard-validators.md`](../../docs/standard-validators.md) — every built-in validator + parameters.
