# Copyright 2026 Firefly Software Solutions Inc
"""Request-scoped correlation id propagation.

Every LLM call that the IDP makes ends up inside
:func:`flydesk_idp.core.observability.outbound_log.timed_agent_run`,
which wraps :meth:`fireflyframework_agentic.FireflyAgent.run`. The
framework records a :class:`UsageRecord` per call into the global
:data:`default_usage_tracker`, keyed by ``correlation_id``. Aggregation
by ``correlation_id`` is the *only* way to recover the per-request
token / cost breakdown that we want to surface in the response.

Threading the ``correlation_id`` through every service signature would
touch 7+ files. Instead we put it in a :class:`contextvars.ContextVar`
that the orchestrator sets once at the top of ``execute()`` and the
agent wrapper reads on every call. ``contextvars`` are copy-on-task in
asyncio, so the value propagates correctly into every
``asyncio.gather`` fan-out without manual plumbing.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_correlation_id: ContextVar[str | None] = ContextVar(
    "flydesk_idp_correlation_id", default=None
)


def set_correlation_id(value: str | None) -> Token[str | None]:
    """Set the active correlation id and return a token for resetting.

    Use the token with :func:`reset_correlation_id` in a ``finally``
    block so the var is always cleared even when the pipeline raises.
    """
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)


def get_correlation_id() -> str | None:
    return _correlation_id.get()
