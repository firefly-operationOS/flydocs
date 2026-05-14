# Copyright 2026 Firefly Software Solutions Inc
"""Structured logging helper for outbound calls to external systems.

Every call the service makes outside its own process (LLM provider,
webhook receiver, queue broker) is logged through :func:`log_outbound`
so a single grep on ``outbound_call`` surfaces the full picture:
target, operation, status, latency, retries, plus any free-form
fields the caller wants stamped.

The shape is deliberately log-line-oriented (key=value pairs) so it
plays well with both stdout grep and structured loggers that ingest
the same format (Loki, Datadog, etc.).
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("flydesk_idp.outbound")


def log_outbound(
    target: str,
    *,
    op: str,
    status: str,
    latency_ms: float,
    **fields: Any,
) -> None:
    """Emit one ``outbound_call`` log line for an external operation.

    Parameters mirror the conventional dimensions of an outbound call:

    * ``target``     -- service identifier (``anthropic``, ``openai``,
                        ``webhook``, ``redis``, ``postgres``, ...).
    * ``op``         -- operation name (``agent.run``, ``deliver``,
                        ``publish``, ``select``, ...).
    * ``status``     -- ``ok`` / ``error`` / ``retry`` / ``permanent_failure``.
    * ``latency_ms`` -- elapsed time in milliseconds.
    * extras         -- any additional dimensions (``url``, ``model``,
                        ``attempt``, ``http_status``, ``job_id``, ...).
    """
    extras = " ".join(f"{k}={_format(v)}" for k, v in fields.items() if v is not None)
    logger.info(
        "outbound_call target=%s op=%s status=%s latency_ms=%.0f %s",
        target, op, status, latency_ms, extras,
    )


@contextmanager
def measure(target: str, op: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Context manager that times a block and emits one log line on exit.

    On exceptions the status becomes ``error`` and the exception class
    is captured under ``error``. The yielded dict can be mutated to add
    fields discovered during the block (e.g. an HTTP status code).

    ::

        async with measure("anthropic", "agent.run", model=model_id) as extras:
            result = await agent.run(content)
            extras["tokens"] = result.usage.total_tokens
    """
    started = time.monotonic()
    extras: dict[str, Any] = dict(fields)
    try:
        yield extras
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.monotonic() - started) * 1000
        log_outbound(
            target,
            op=op,
            status="error",
            latency_ms=latency_ms,
            error=type(exc).__name__,
            **extras,
        )
        raise
    latency_ms = (time.monotonic() - started) * 1000
    status = extras.pop("_status", "ok")
    log_outbound(target, op=op, status=status, latency_ms=latency_ms, **extras)


def _format(value: Any) -> str:
    """Quote values with whitespace so log lines stay parseable."""
    s = str(value)
    if any(ch in s for ch in (" ", "\t", "=")):
        return f'"{s}"'
    return s


async def timed_agent_run(agent: Any, content: Any, *, op: str, model: str) -> Any:
    """Run a :class:`FireflyAgent` and emit one ``outbound_call`` line.

    The provider name is derived from the model id (``anthropic:opus`` ->
    ``anthropic``). Exceptions still propagate -- the helper only adds
    the log line and never alters control flow.
    """
    target = model.split(":", 1)[0] if ":" in model else "llm"
    started = time.monotonic()
    try:
        result = await agent.run(content)
    except Exception as exc:
        latency_ms = (time.monotonic() - started) * 1000
        log_outbound(
            target, op=op, status="error", latency_ms=latency_ms,
            model=model, error=type(exc).__name__,
        )
        raise
    latency_ms = (time.monotonic() - started) * 1000
    log_outbound(target, op=op, status="ok", latency_ms=latency_ms, model=model)
    return result
