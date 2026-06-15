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

"""HTTP health server for the worker CLI modes.

``flydocs worker`` and ``flydocs bbox-worker`` are pure-asyncio processes
with no web stack of their own, yet Kubernetes probes them over httpGet.
This module assembles pyfly's actuator into a minimal Starlette app served
by uvicorn as one more asyncio task inside the worker process:

* ``GET /actuator/health`` -- full composite over every scanned indicator.
* ``GET /actuator/health/liveness`` / ``readiness`` -- Kubernetes probes.
  pyfly group semantics apply: an indicator registered without a probe
  group (the scan default -- ``database_health``, ``eda_health``)
  participates in BOTH probes, identical to the API process. A broker or
  DB outage therefore flips liveness too; see ``docs/deployment.md``.
* Every other actuator endpoint honours pyfly's secure-by-default web
  exposure: only ``health`` and ``info`` are mounted unless
  ``pyfly.management.endpoints.web.exposure.include`` opts more in, so
  ``/actuator/loggers`` and ``/actuator/metrics`` are 404s by default.

The server binds ``0.0.0.0`` -- the kubelet probes the pod IP, never
loopback. The port is ``FLYDOCS_WORKER_HEALTH_PORT`` when set, falling
back to ``FLYDOCS_PORT``; ``0`` disables the server entirely (dev setups
running ``serve`` and ``worker`` on one host). The ``0`` gate lives here
because uvicorn itself treats port 0 as "bind an ephemeral port".

Signal handling stays with the worker CLI: the server is created with
uvicorn's signal capture disabled (uvicorn would otherwise install
process-global SIGTERM/SIGINT handlers and re-raise the signal with the
default disposition restored, killing the process before the worker's
cleanup runs). ``flydocs.cli`` installs its own SIGTERM handler for
graceful shutdown.

Indicator discovery uses :func:`pyfly.actuator.install_health_indicators`,
which only sees beans that are already instantiated -- callers must invoke
:func:`build_health_app` after ``PyFlyApplication.startup()``, and
indicator beans must not be ``@lazy``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING

import uvicorn
from starlette.applications import Starlette

if TYPE_CHECKING:
    from pyfly.actuator import HealthAggregator
    from pyfly.context.application_context import ApplicationContext

    from flydocs.config import IDPSettings


def resolve_health_port(settings: IDPSettings) -> int:
    """Port the worker health server listens on; ``0`` means disabled.

    ``worker_health_port`` (env ``FLYDOCS_WORKER_HEALTH_PORT``) wins when
    set; otherwise the service port (``FLYDOCS_PORT``) is reused so the
    platform only has to configure one port for all three workloads.
    """
    if settings.worker_health_port is not None:
        return settings.worker_health_port
    return settings.port


def build_health_app(
    context: ApplicationContext,
    aggregator: HealthAggregator | None = None,
) -> Starlette:
    """Management app for a worker process: actuator + admin dashboard.

    Returns pyfly's full management app (``create_management_app``) so a worker
    pod exposes the **same management surface as the API** on its health port —
    the actuator endpoints **and** the ``/admin`` dashboard (beans, health, env,
    config, loggers, metrics, scheduled, runtime, server, overview) — plus
    pyfly's structured request access log. Indicator/exposure config is carried
    by *context*; callers must invoke this after ``PyFlyApplication.startup()``.
    """
    from pyfly.web.adapters.starlette.management_app import create_management_app

    admin_enabled = str(context.config.get("pyfly.admin.enabled", "false")).lower() in ("true", "1", "yes")
    trace_collector = None
    if admin_enabled:
        from pyfly.admin.middleware.trace_collector import TraceCollectorFilter

        trace_collector = TraceCollectorFilter()
    return create_management_app(
        context,
        health_agg=aggregator,
        http_exchange_recorder=None,
        admin_trace_collector=trace_collector,
        actuator_active=True,
        admin_enabled=admin_enabled,
        base_path="",
    )


class _NoSignalServer(uvicorn.Server):
    """uvicorn server that leaves process signal handling to the worker CLI."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def make_health_server(app: Starlette, *, settings: IDPSettings) -> uvicorn.Server:
    """uvicorn server for *app*: ``0.0.0.0``, quiet logs, no lifespan.

    ``access_log=False`` because probes fire every 10-30 s; ``lifespan="off"``
    because the worker CLI owns the pyfly lifecycle; ``log_config=None`` so
    uvicorn's loggers propagate into the logging configuration the CLI set up
    instead of installing their own handlers.
    """
    port = resolve_health_port(settings)
    if port == 0:
        raise ValueError("worker health server is disabled (resolved port is 0)")
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        access_log=False,
        lifespan="off",
        log_config=None,
    )
    return _NoSignalServer(config)


def build_worker_health_server(
    context: ApplicationContext | None,
    settings: IDPSettings,
) -> uvicorn.Server | None:
    """Health server for a worker process, or ``None`` when disabled."""
    if resolve_health_port(settings) == 0:
        return None
    return make_health_server(build_health_app(context), settings=settings)


async def serve_health(server: uvicorn.Server) -> None:
    """Run *server*, surfacing startup failures as ordinary exceptions.

    uvicorn converts a failed bind into ``SystemExit`` -- a ``BaseException``
    that would propagate through the event loop around the sibling worker
    tasks instead of completing this task. Re-raising it as ``RuntimeError``
    lets the CLI's ``asyncio.wait(FIRST_COMPLETED)`` observe a dead task and
    shut the whole process down through its normal error path.
    """
    try:
        await server.serve()
    except SystemExit as exc:
        raise RuntimeError(f"health server exited with code {exc.code} (failed to bind?)") from exc
