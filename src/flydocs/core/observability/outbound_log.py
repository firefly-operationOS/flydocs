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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("flydocs.outbound")


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
        target,
        op,
        status,
        latency_ms,
        extras,
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

    Reads the active request correlation id from
    :mod:`flydocs.core.observability.correlation` and threads it
    into the framework's :class:`AgentContext`, so the per-call
    :class:`UsageRecord`s the framework records are queryable later
    via ``default_usage_tracker.get_summary_for_correlation(...)``.

    Also extracts ``result.usage()`` and emits token + estimated cost
    on the log line, so a single grep can sum spend per request.

    The provider name is derived from the model id (``anthropic:opus`` ->
    ``anthropic``). Exceptions still propagate -- the helper only adds
    the log line and never alters control flow.
    """
    from fireflyframework_agentic.agents.context import AgentContext
    from pyfly.observability.correlation import get_correlation_id

    target = model.split(":", 1)[0] if ":" in model else "llm"
    correlation_id = get_correlation_id()
    agent_context = AgentContext(correlation_id=correlation_id or "")

    started = time.monotonic()
    try:
        result = await agent.run(content, context=agent_context)
    except Exception as exc:
        latency_ms = (time.monotonic() - started) * 1000
        log_outbound(
            target,
            op=op,
            status="error",
            latency_ms=latency_ms,
            model=model,
            error=type(exc).__name__,
            correlation_id=correlation_id,
        )
        raise

    latency_ms = (time.monotonic() - started) * 1000
    usage_fields = _extract_usage_fields(result, model)
    log_outbound(
        target,
        op=op,
        status="ok",
        latency_ms=latency_ms,
        model=model,
        correlation_id=correlation_id,
        **usage_fields,
    )
    return result


def _extract_usage_fields(result: Any, model: str) -> dict[str, Any]:
    """Pull tokens + estimated cost out of a pydantic-ai ``RunResult``.

    Best-effort: any failure returns an empty dict so the log line is
    still emitted with what we already have. Cost is computed via the
    framework's calculator so log lines and the response stay
    consistent.
    """
    try:
        usage = result.usage() if callable(getattr(result, "usage", None)) else None
    except Exception:  # noqa: BLE001
        return {}
    if usage is None:
        return {}
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or (input_tokens + output_tokens)
    cache_write = getattr(usage, "cache_write_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_tokens", 0) or 0
    fields: dict[str, Any] = {
        "in_tokens": input_tokens,
        "out_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if cache_write:
        fields["cache_write"] = cache_write
    if cache_read:
        fields["cache_read"] = cache_read
    try:
        # ImportError-tolerant: the cost resolver lives in
        # fireflyframework-agentic and is optional. ``genai_prices_cost``
        # consults the bundled ``genai-prices`` database and returns the
        # USD estimate, or ``None`` when the model is unknown.
        from fireflyframework_agentic.observability.cost import (  # pyright: ignore[reportMissingImports]
            CostContext,
            genai_prices_cost,
        )

        cost = genai_prices_cost(
            CostContext(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_tokens=cache_write,
                cache_read_tokens=cache_read,
            )
        )
        if cost is not None and cost > 0:
            fields["cost_usd"] = f"{cost:.6f}"
    except Exception:  # noqa: BLE001
        pass
    return fields
