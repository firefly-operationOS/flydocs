# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

from flydocs.core.observability.agent_middleware import (
    DEFAULT_MIDDLEWARE,
    IDP_MODEL_SETTINGS,
    PROMPT_CACHE_MIDDLEWARE,
)
from flydocs.core.observability.outbound_log import (
    log_outbound,
    measure,
    timed_agent_run,
)

__all__ = [
    "DEFAULT_MIDDLEWARE",
    "IDP_MODEL_SETTINGS",
    "PROMPT_CACHE_MIDDLEWARE",
    "current_correlation_context",
    "get_correlation_id",
    "log_outbound",
    "measure",
    "reset_correlation_id",
    "set_correlation_id",
    "timed_agent_run",
]
