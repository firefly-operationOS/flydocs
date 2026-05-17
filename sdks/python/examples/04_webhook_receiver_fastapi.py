"""A FastAPI app that receives flydocs webhooks and verifies them.

Run it::

    FLYDOCS_WEBHOOK_HMAC_SECRET=topsecret \
        uv run uvicorn sdks.python.examples.04_webhook_receiver_fastapi:app --port 9000

Then point your flydocs ``callback_url`` at ``http://your-host:9000/flydocs/webhook``.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from flydocs_sdk import (
    JobStatus,
    JobWebhookPayload,
    WebhookVerificationError,
    WebhookVerifier,
)

verifier = WebhookVerifier(secret=os.environ["FLYDOCS_WEBHOOK_HMAC_SECRET"])
app = FastAPI()


@app.post("/flydocs/webhook")
async def on_webhook(request: Request) -> JSONResponse:
    # IMPORTANT: verify against the raw body bytes -- re-encoding the
    # JSON will change the digest and break the signature check.
    body = await request.body()
    signature = request.headers.get("X-Flydocs-Signature", "")
    try:
        verifier.verify(body, signature)
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    payload = JobWebhookPayload.model_validate_json(body)
    if payload.status == JobStatus.SUCCEEDED and payload.result is not None:
        for doc in payload.result.documents:
            # persist extracted fields, kick off downstream work, ...
            print(f"  {doc.get('document_type')}: {len(doc.get('fields', []))} field groups")
    elif payload.status == JobStatus.FAILED:
        print(f"job {payload.job_id} failed: {payload.error_code} {payload.error_message}")

    return JSONResponse({"ok": True})
