"""A FastAPI app that receives flydocs webhooks and verifies them.

What it shows:
  * Verifying an incoming HMAC-signed body with :class:`WebhookVerifier`.
  * Parsing the typed :class:`EventEnvelope` returned by ``verifier.verify``.
  * Switching on the four v1 event types
    (``extraction.submitted`` / ``extraction.completed`` /
    ``extraction.post_processing.requested`` /
    ``extraction.post_processing.completed``).
  * Reading the v1 ``Extraction`` + nested ``ExtractionResult`` shape.

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
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    ExtractionStatus,
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
        envelope = verifier.verify(body, signature)
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    ext = envelope.extraction
    if envelope.event_type == EVENT_TYPE_EXTRACTION_SUBMITTED:
        print(f"submitted: {ext.id}")
    elif envelope.event_type == EVENT_TYPE_EXTRACTION_COMPLETED:
        if ext.status == ExtractionStatus.SUCCEEDED and envelope.result is not None:
            for doc in envelope.result.documents:
                groups = doc.field_groups
                print(f"  succeeded {ext.id}: {doc.type} -> {len(groups)} field groups")
        elif ext.status == ExtractionStatus.FAILED and ext.error is not None:
            print(f"  failed {ext.id}: {ext.error.code} {ext.error.message}")
        elif ext.status == ExtractionStatus.CANCELLED:
            print(f"  cancelled {ext.id}")
    elif envelope.event_type == EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED:
        print(f"post-processing requested for {ext.id}")
    elif envelope.event_type == EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED:
        print(f"post-processing completed for {ext.id}")

    return JSONResponse({"ok": True})
