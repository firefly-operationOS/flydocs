# Copyright 2026 Firefly Software Solutions Inc
"""Cross-cutting observability helpers.

The correlation surface lives upstream in :mod:`pyfly.observability.correlation`
now (see ``docs/audits/2026-05-14-pyfly-eda-probes-tracing.md``). This
module re-exports the parts the IDP services already import, so the
existing call sites compile unchanged.
"""

from pyfly.observability.correlation import (
    current_correlation_context,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

from flydesk_idp.core.observability.agent_middleware import (
    DEFAULT_MIDDLEWARE,
    PROMPT_CACHE_MIDDLEWARE,
)
from flydesk_idp.core.observability.outbound_log import (
    log_outbound,
    measure,
    timed_agent_run,
)

__all__ = [
    "DEFAULT_MIDDLEWARE",
    "PROMPT_CACHE_MIDDLEWARE",
    "current_correlation_context",
    "get_correlation_id",
    "log_outbound",
    "measure",
    "reset_correlation_id",
    "set_correlation_id",
    "timed_agent_run",
]
