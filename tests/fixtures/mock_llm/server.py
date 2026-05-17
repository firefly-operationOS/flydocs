# Copyright 2026 Firefly Software Solutions Inc
"""OpenAI-compatible mock LLM used by integration tests.

Exposes ``POST /v1/chat/completions`` and returns canned multimodal
responses keyed by the SHA-256 of the last attachment's bytes. Falls
back to a generic response when no canned answer is registered.

This lets the integration test stack run end-to-end (API + worker +
LLM call) without depending on a real provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Response

app = FastAPI(title="flydocs mock LLM")
_CANNED_DIR = Path(os.environ.get("MOCK_LLM_CANNED_DIR", "/canned"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    body = await request.json()
    response_payload = _select_response(body)
    return Response(
        content=json.dumps(response_payload),
        media_type="application/json",
        status_code=200,
    )


def _select_response(body: dict) -> dict:
    """Return the canned response that best matches the incoming request."""
    attachment_hash = _last_attachment_hash(body)
    if attachment_hash:
        canned_path = _CANNED_DIR / f"{attachment_hash}.json"
        if canned_path.exists():
            return _wrap_chat_completion(json.loads(canned_path.read_text()))
    default = _CANNED_DIR / "default.json"
    if default.exists():
        return _wrap_chat_completion(json.loads(default.read_text()))
    return _wrap_chat_completion(_generic_extraction(body))


def _last_attachment_hash(body: dict) -> str | None:
    for message in reversed(body.get("messages", [])):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in reversed(content):
            url = part.get("image_url", {}).get("url") if isinstance(part, dict) else None
            if not url or "," not in url:
                continue
            _, b64 = url.split(",", 1)
            try:
                decoded = base64.b64decode(b64)
            except Exception:  # noqa: BLE001
                continue
            return hashlib.sha256(decoded).hexdigest()[:16]
    return None


def _wrap_chat_completion(json_payload: dict) -> dict:
    """Wrap an arbitrary JSON payload as an OpenAI chat-completion response."""
    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "flydocs-mock",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(json_payload)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _generic_extraction(_: dict) -> dict:
    """Bare fallback when no canned response is available."""
    return {
        "fields": [],
        "documents": [],
        "validations": [],
        "checks": [],
        "rule_results": [],
    }
