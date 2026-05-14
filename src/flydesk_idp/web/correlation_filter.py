# Copyright 2026 Firefly Software Solutions Inc
"""``CorrelationHeadersMiddleware`` -- end-to-end traceability for every request.

PyFly already ships a :class:`TransactionIdFilter` that propagates
``X-Transaction-Id``. flydesk-idp extends the surface so a request can
be correlated across systems (caller -> API -> worker -> webhook
receiver) without the operator having to glue ids by hand:

  * ``X-Correlation-Id`` -- end-to-end identifier across service hops.
    Echoed verbatim when provided by the caller; generated as a UUID
    when missing.
  * ``X-Request-Id`` -- one identifier per HTTP call. Generated when
    missing.
  * ``X-Tenant-Id`` -- multi-tenant scope. Carried through to logs and
    outbound webhooks. Never generated server-side -- absent means
    "unscoped".
  * ``traceparent`` and ``tracestate`` -- W3C Trace Context for
    OpenTelemetry. Echoed when present so downstream services receive
    the trace chain unbroken.

All values land on ``request.state`` for handlers that can take a
``starlette.Request``, AND on a :class:`contextvars.ContextVar` so
deeply-nested command handlers (running inside ``CommandBus.send``)
can read them without changing their signatures.

Registered as a Starlette middleware directly on the FastAPI app
(see :func:`flydesk_idp.main`). It is NOT a pyfly ``@bean`` filter
because pyfly's auto-discovery only picks up beans that are already
instantiated at ``create_app`` time, which is before
``PyFlyApplication.startup()`` has run.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

CORRELATION_ID_HEADER = "X-Correlation-Id"
REQUEST_ID_HEADER = "X-Request-Id"
TENANT_ID_HEADER = "X-Tenant-Id"
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"


# Async-safe per-request context so command handlers (running inside
# CommandBus.send) can read the correlation surface without taking a
# starlette.Request in their constructor.
_CORRELATION_CTX: ContextVar[dict[str, str]] = ContextVar(
    "flydesk_idp_correlation", default={}
)


def current_correlation_context() -> dict[str, str]:
    """Read the active correlation context for the running request."""
    return dict(_CORRELATION_CTX.get())


def build_propagation_context(
    *,
    correlation_id: str,
    request_id: str,
    tenant_id: str = "",
    traceparent: str = "",
    tracestate: str = "",
) -> dict[str, str]:
    """Compact dict shape used to propagate context into outbound calls.

    The submit-job handler persists this on ``ExtractionJob.metadata_json``
    so the worker can echo it on the outbound webhook headers.
    """
    ctx: dict[str, str] = {
        CORRELATION_ID_HEADER: correlation_id,
        REQUEST_ID_HEADER: request_id,
    }
    if tenant_id:
        ctx[TENANT_ID_HEADER] = tenant_id
    if traceparent:
        ctx[TRACEPARENT_HEADER] = traceparent
    if tracestate:
        ctx[TRACESTATE_HEADER] = tracestate
    return ctx


class CorrelationHeadersMiddleware(BaseHTTPMiddleware):
    """Stamps every request/response with the full correlation surface."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Inbound: read each header, generate UUIDs for the IDs we own,
        # leave the rest empty when absent.
        headers = request.headers
        correlation_id = headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        request_id = headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        tenant_id = headers.get(TENANT_ID_HEADER) or ""
        traceparent = headers.get(TRACEPARENT_HEADER) or ""
        tracestate = headers.get(TRACESTATE_HEADER) or ""

        ctx = build_propagation_context(
            correlation_id=correlation_id,
            request_id=request_id,
            tenant_id=tenant_id,
            traceparent=traceparent,
            tracestate=tracestate,
        )

        # Expose via request.state for handlers that take a Request, and
        # via the ContextVar for everything else further down the stack.
        request.state.correlation_id = correlation_id
        request.state.request_id_header = request_id
        request.state.tenant_id = tenant_id
        request.state.traceparent = traceparent
        request.state.tracestate = tracestate
        request.state.correlation = ctx
        token = _CORRELATION_CTX.set(ctx)

        try:
            response: Response = await call_next(request)
        finally:
            _CORRELATION_CTX.reset(token)

        # Echo the headers we own so the caller can reuse them in logs
        # or retries.
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers[REQUEST_ID_HEADER] = request_id
        if tenant_id:
            response.headers[TENANT_ID_HEADER] = tenant_id
        if traceparent:
            response.headers[TRACEPARENT_HEADER] = traceparent
        if tracestate:
            response.headers[TRACESTATE_HEADER] = tracestate
        return response
