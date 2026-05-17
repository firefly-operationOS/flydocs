# flydocs Python SDK — Tutorial

A complete, async-first walkthrough of the flydocs Python SDK. Every snippet is runnable as-is. A synchronous facade is shown at the end for callers that can't run an event loop.

> **Prerequisites**
>
> - Python ≥ 3.11
> - A flydocs service reachable on some base URL. For local development:
>
>   ```bash
>   task docker:up:test    # starts flydocs + mock LLM at http://localhost:8400
>   ```

---

## Table of contents

1. [Install](#1-install)
2. [Hello, flydocs — your first extraction](#2-hello-flydocs--your-first-extraction)
3. [The mental model](#3-the-mental-model)
4. [Designing a schema with `DocSpec` + `FieldSpec`](#4-designing-a-schema-with-docspec--fieldspec)
5. [Tuning the pipeline with `StageToggles`](#5-tuning-the-pipeline-with-stagetoggles)
6. [Adding business rules](#6-adding-business-rules)
7. [Built-in validators (`StandardValidatorSpec`)](#7-built-in-validators-standardvalidatorspec)
8. [Dry-running the request (`validate`)](#8-dry-running-the-request-validate)
9. [Multi-file requests + the classifier](#9-multi-file-requests--the-classifier)
10. [Long-running work: async jobs + `wait_for_completion`](#10-long-running-work-async-jobs--wait_for_completion)
11. [Listing, cancelling, and resuming jobs](#11-listing-cancelling-and-resuming-jobs)
12. [Webhook delivery + signature verification](#12-webhook-delivery--signature-verification)
13. [Error handling — RFC 7807 problem-details](#13-error-handling--rfc-7807-problem-details)
14. [Production patterns](#14-production-patterns)
15. [Synchronous facade (when async isn't an option)](#15-synchronous-facade-when-async-isnt-an-option)

---

## 1. Install

The wheel is attached to every `vYY.MM.PP` GitHub Release. Install directly from the release URL with [`uv`](https://docs.astral.sh/uv/):

```bash
uv add https://github.com/firefly-operationOS/flydocs/releases/download/v26.05.01/flydocs_sdk-26.5.1-py3-none-any.whl
```

…or pin it in your `pyproject.toml`:

```toml
[project]
dependencies = ["flydocs-sdk"]

[tool.uv.sources]
flydocs-sdk = { url = "https://github.com/firefly-operationOS/flydocs/releases/download/v26.05.01/flydocs_sdk-26.5.1-py3-none-any.whl" }
```

> **CalVer + PEP 440.** The git tag is `v26.05.01` (`YY.MM.PP`). PEP 440 normalises this to `26.5.1` for the wheel filename, which is why the URL above mixes both forms.

The SDK depends only on `httpx` and `pydantic`.

---

## 2. Hello, flydocs — your first extraction

```python
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

async def main() -> None:
    async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
        result = await flydocs.extract(
            ExtractionRequest(
                documents=[DocumentInput.from_path("invoice.pdf")],
                docs=[invoice],
            )
        )

    print(f"model={result.model}   latency={result.latency_ms}ms")
    for doc in result.documents:
        for group in doc["fields"]:
            for field in group["fieldGroupFields"]:
                print(f"  {field['name']:>15} = {field.get('value')!r:>20}   conf={field.get('confidence', 0):.2f}")

asyncio.run(main())
```

`AsyncFlydocsClient` is the **primary** integration surface — async-first, designed to drop into any modern Python app (FastAPI, Starlette, aiohttp). Use it as an async context manager so the underlying connection pool closes cleanly.

---

## 3. The mental model

flydocs takes a **document + a schema + (optional) rules** and returns **extracted fields with bounding boxes**, plus optional validation, judge, authenticity, and rule-engine outputs. The SDK is a thin, typed shim around the HTTP API:

```
   Your code                                Service
   ─────────                                ───────
   ExtractionRequest  ─POST /api/v1/extract→  pipeline
        │                                       │
        │   ←──────── ExtractionResult ─────────┘
        ▼
   result.documents[*].fields[*].fieldGroupFields[*]
       .name, .value, .confidence, .bbox,
       .judge, .field_validation
```

Two integration modes share the same request shape:

| Mode  | When to use                                            | SDK coroutine                                                  |
|-------|--------------------------------------------------------|----------------------------------------------------------------|
| Sync extraction | Sub-minute single-document workloads        | `await extract(req)`                                            |
| Async jobs      | Long-running, fire-and-forget, batch, webhook-delivered | `await submit_job(req)` + `await wait_for_completion(job_id)` |

---

## 4. Designing a schema with `DocSpec` + `FieldSpec`

Inline dicts get tedious past a few fields. The SDK ships typed request models with autocomplete-friendly factories.

```python
from flydocs_sdk import (
    DocSpec,
    DocType,
    FieldGroup,
    FieldItem,
    FieldSpec,
    FieldType,
    StandardFormat,
)

invoice = DocSpec(
    doc_type=DocType(document_type="invoice", description="Vendor invoice", country="ES"),
    field_groups=[
        FieldGroup.of(
            "header",
            FieldSpec(field_name="invoice_number", field_type=FieldType.STRING, required=True),
            FieldSpec(
                field_name="invoice_date",
                field_type=FieldType.STRING,
                format=StandardFormat.DATE,
                required=True,
            ),
            FieldSpec(field_name="supplier_name", field_type=FieldType.STRING, required=True),
        ),
        FieldGroup.of(
            "totals",
            FieldSpec(field_name="subtotal",     field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="tax_amount",   field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="total_amount", field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="currency",     field_type=FieldType.STRING, required=True),
        ),
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

You can also pass a **dict** for `docs` / `rules` / `options` — useful for forward compatibility against new server-side fields the SDK hasn't surfaced yet.

---

## 5. Tuning the pipeline with `StageToggles`

The multimodal extractor is always on; every other stage is opt-in.

```python
from flydocs_sdk import ExtractionOptions, StageToggles

options = ExtractionOptions(
    return_bboxes=True,
    language_hint="es",
    model="anthropic:claude-sonnet-4-6",
    stages=StageToggles(
        classifier=True,
        field_validation=True,
        judge=True,
        bbox_refine=True,
        rule_engine=True,
        judge_escalation=True,
    ),
    escalation_threshold=0.25,
    escalation_model="anthropic:claude-opus-4-7",
)
```

| Stage                | Default | What it does                                                          |
|----------------------|---------|-----------------------------------------------------------------------|
| `splitter`           | off     | LLM page-range splitter for interleaved multi-doc PDFs                |
| `classifier`         | **on**  | Routes each input file to one of the declared `DocSpec`s              |
| `field_validation`   | **on**  | Runs `StandardValidatorSpec`s, regex `pattern`, `format`, `enum`, ... |
| `visual_authenticity`| off     | LLM visual checks (signature presence, watermark, ...)                 |
| `content_authenticity`| off    | LLM cross-doc consistency checks                                       |
| `judge`              | off     | Per-field LLM re-evaluation with confidence + evidence                |
| `judge_escalation`   | off     | Re-runs extract+judge with the stronger model when the judge fails too often |
| `bbox_refine`        | off     | Replaces LLM bbox with grounded coordinates from the real text layer  |
| `rule_engine`        | off     | Evaluates the business-rule DAG                                        |
| `transform`          | off     | Applies `transformations` (entity resolution, free-form LLM transforms) |

---

## 6. Adding business rules

Rules are natural-language predicates over extracted fields, validator outcomes, or upstream rule outputs. The rule engine sorts them topologically.

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
        parents=[RuleValidatorParent(document_type="invoice", validator_name="vat_id")],
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

async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
    result = await flydocs.extract(
        ExtractionRequest(
            documents=[DocumentInput.from_path("invoice.pdf")],
            docs=[invoice],
            rules=rules,
            options=ExtractionOptions(stages=StageToggles(
                field_validation=True,
                rule_engine=True,
            )),
        )
    )

for rr in result.rule_results:
    print(f"  {rr['rule_id']:>20} = {rr['output']}   {rr.get('summary', '')}")
```

---

## 7. Built-in validators (`StandardValidatorSpec`)

Attach pure-Python validators directly to a `FieldSpec`:

```python
from flydocs_sdk import StandardValidatorSpec, StandardValidatorType

supplier_vat = FieldSpec(
    field_name="supplier_vat",
    field_type=FieldType.STRING,
    required=True,
    standard_validators=[
        StandardValidatorSpec(
            type=StandardValidatorType.VAT_ID,
            params={"country": "ES"},
        ),
    ],
)
```

Catalogue: `EMAIL`, `URI`, `URL`, `IPV4`, `IPV6`, `DOMAIN`, `SLUG`, `IBAN`, `BIC`, `CREDIT_CARD`, `PHONE_E164`, `VAT_ID`, `NIF`, `NIE`, `DNI`, `UUID`, `DATE`, `DATE_TIME`. Pass `params={"country": "ES"}` for region-specific validators; set `severity="warning"` for soft warnings.

---

## 8. Dry-running the request (`validate`)

Before submitting, run the **semantic** validator (rules reference real fields, no duplicate ids, no cycles, ...) without touching the LLM:

```python
async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
    report = await flydocs.validate(req)
    if not report["ok"]:
        for err in report["errors"]:
            print(f"ERROR  {err['path']}: {err['message']}")
        raise SystemExit(1)
```

The service runs the same validator as the first gate of `/api/v1/extract`; running it client-side lets you fail fast in CI / a UI without spending an LLM call.

---

## 9. Multi-file requests + the classifier

```python
req = ExtractionRequest(
    documents=[
        DocumentInput.from_path("invoice-front.pdf"),
        DocumentInput.from_path("invoice-back.pdf"),
        DocumentInput.from_path("passport-id.jpg", document_type="passport"),  # caller-pinned
    ],
    docs=[invoice, passport],
    options=ExtractionOptions(stages=StageToggles(classifier=True)),
)
```

Per-file outputs live in `result.files`; per-document-type outputs (one per resolved `DocSpec`) live in `result.documents`. Unmatched files land in `result.additional_documents` with `document_type="unmatched"`.

---

## 10. Long-running work: async jobs + `wait_for_completion`

```python
from flydocs_sdk import JobStatus, SubmitJobRequest

async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
    submit = await flydocs.submit_job(
        SubmitJobRequest(
            documents=[DocumentInput.from_path("big-batch.pdf")],
            docs=[invoice],
            callback_url="https://your-app.example.com/flydocs/webhook",
            metadata={"caller": "ingest-pipeline", "batch_id": "b-42"},
        ),
        idempotency_key="ingest-pipeline:b-42",
    )
    print(f"queued {submit.job_id}")

    final = await flydocs.wait_for_completion(
        submit.job_id,
        poll_interval=2.0,
        timeout=900.0,
    )

    if final.status == JobStatus.SUCCEEDED:
        result = (await flydocs.get_job_result(submit.job_id)).result
        print(f"done: {len(result.documents)} document(s)")
    elif final.status == JobStatus.PARTIAL_SUCCEEDED:
        result = (await flydocs.get_job_result(submit.job_id)).result
        print(f"partial: {len(result.pipeline_errors)} non-fatal errors")
    else:
        print(f"failed: {final.error_code} {final.error_message}")
```

`wait_for_completion` returns the final `JobStatusResponse` regardless of outcome. It raises `TimeoutError` only when the deadline elapses while the worker is still in flight.

---

## 11. Listing, cancelling, and resuming jobs

```python
listing = await flydocs.list_jobs(
    status=["SUCCEEDED", "PARTIAL_SUCCEEDED"],
    limit=25,
)
for job in listing.items:
    print(job.job_id, job.submitted_at, job.status)

found = await flydocs.list_jobs(idempotency_key="ingest-pipeline:b-42")
if found.items and found.items[0].status == JobStatus.SUCCEEDED:
    result = (await flydocs.get_job_result(found.items[0].job_id)).result

try:
    await flydocs.cancel_job("job-abc")
except FlydocsHTTPError as e:
    if e.code == "job_not_cancellable":
        # worker has started; let it finish or fail
        ...
```

---

## 12. Webhook delivery + signature verification

```python
import os
from flydocs_sdk import (
    JobStatus,
    JobWebhookPayload,
    WebhookVerificationError,
    WebhookVerifier,
)
from fastapi import FastAPI, Header, HTTPException, Request

verifier = WebhookVerifier(secret=os.environ["FLYDOCS_WEBHOOK_HMAC_SECRET"])
app = FastAPI()

@app.post("/flydocs/webhook")
async def on_webhook(request: Request, x_flydocs_signature: str = Header(...)) -> dict:
    body = await request.body()       # raw bytes -- do not re-encode
    try:
        verifier.verify(body, x_flydocs_signature)
    except WebhookVerificationError:
        raise HTTPException(status_code=403, detail="bad signature")
    payload = JobWebhookPayload.model_validate_json(body)
    if payload.status == JobStatus.SUCCEEDED and payload.result is not None:
        for doc in payload.result.documents:
            # persist, fan out downstream work, ...
            ...
    return {"ok": True}
```

> **Verify against the raw bytes.** If your framework deserialised the JSON before you got the bytes, re-encoding will change the digest. Ask the framework for the original body bytes.

---

## 13. Error handling — RFC 7807 problem-details

```python
from flydocs_sdk import (
    FlydocsClientError,
    FlydocsHTTPError,
    FlydocsTimeoutError,
)

try:
    result = await flydocs.extract(req)
except FlydocsHTTPError as exc:
    if exc.code == "extraction_timeout":
        submit = await flydocs.submit_job(SubmitJobRequest(**req.model_dump()))
    elif exc.code == "invalid_request":
        for issue in exc.payload.get("errors", []):
            print(issue)
        raise
    else:
        raise
except FlydocsTimeoutError:
    raise   # HTTP request itself timed out on the wire
except FlydocsClientError:
    raise   # other transport failure (DNS, connect, TLS)
```

| `code`                  | Status | Meaning                                                          |
|-------------------------|--------|------------------------------------------------------------------|
| `extraction_timeout`    | 408    | Pipeline exceeded the sync ceiling. Retry via `submit_job`.      |
| `document_too_large`    | 413    | Document over `FLYDOCS_MAX_BYTES`.                               |
| `invalid_base64`        | 422    | `content_base64` failed strict parsing.                          |
| `invalid_request`       | 422    | Semantic validation found issues.                                |
| `job_not_ready`         | 409    | Job exists but result isn't available yet.                       |
| `job_not_cancellable`   | 409    | Worker has started; mid-flight cancellation isn't supported.     |
| `JOB_NOT_FOUND`         | 404    | Unknown `job_id`.                                                |

---

## 14. Production patterns

**Reuse a client.** Construct `AsyncFlydocsClient` once per application and share it. The underlying connection pool is the most expensive part to set up.

**Correlation ids.** Pass `correlation_id="..."` on `extract` / `submit_job`. The service stamps it on every internal log line and the webhook payload.

**Custom timeouts.** Default is 60s. `AsyncFlydocsClient("...", timeout=120.0)`.

**Custom headers.** `default_headers={"X-Tenant-Id": "..."}` adds headers to every request.

**Bring your own httpx client.** Pass `http_client=your_httpx_async_client` to share a connection pool with the rest of your app. The SDK does not close transports it did not create.

**Health checks.** `await flydocs.health("readiness")` from your deploy verification.

---

## 15. Synchronous facade (when async isn't an option)

For scripts, batch tools, and callers that can't run an event loop, `FlydocsClient` wraps `AsyncFlydocsClient` on a dedicated background loop. The API surface is identical, just without `await`:

```python
from flydocs_sdk import FlydocsClient

with FlydocsClient("http://localhost:8400") as flydocs:
    result = flydocs.extract(req)
```

Use the async client whenever you can — the sync wrapper costs you a dedicated event loop per instance.

---

## Further reading

- [`examples/`](./examples/) — runnable scripts for every section above.
- [`docs/api-reference.md`](../../docs/api-reference.md) — full HTTP wire contract.
- [`docs/pipeline.md`](../../docs/pipeline.md) — stage DAG, what each stage does.
- [`docs/rule-engine.md`](../../docs/rule-engine.md) — rule semantics + DAG resolution.
- [`docs/standard-validators.md`](../../docs/standard-validators.md) — every built-in validator + parameters.
