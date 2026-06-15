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

"""Worker-mode HTTP health server (``flydocs.worker_health``).

The worker CLI modes serve ``/actuator/health/*`` so Kubernetes can probe
them over httpGet instead of ``exec`` shims. These tests pin the contract:
probe routes and status codes, secure-by-default endpoint exposure
(loggers/metrics 404), indicator discovery from the DI container, port
resolution (``FLYDOCS_WORKER_HEALTH_PORT`` -> ``FLYDOCS_PORT``; ``0`` =
disabled), the uvicorn server factory (bind ``0.0.0.0``, no access log,
lifespan off), and failure semantics (a bind failure surfaces as a normal
task exception so the worker process dies instead of limping on without
probes).
"""

from __future__ import annotations

import asyncio
import signal
import socket

import httpx
import pytest
from pyfly.actuator import HealthAggregator, HealthStatus, ProbeGroup
from pyfly.context.application_context import ApplicationContext
from pyfly.core.config import Config
from starlette.testclient import TestClient

from flydocs.config import IDPSettings
from flydocs.worker_health import (
    build_health_app,
    build_worker_health_server,
    make_health_server,
    resolve_health_port,
    serve_health,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _UpIndicator:
    async def health(self) -> HealthStatus:
        return HealthStatus(status="UP")


class _DownIndicator:
    async def health(self) -> HealthStatus:
        return HealthStatus(status="DOWN", details={"reason": "offline"})


def _client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# Probe routes
# ---------------------------------------------------------------------------


def test_probes_up_without_indicators() -> None:
    client = _client(build_health_app(ApplicationContext(Config({}))))
    for path in ("/actuator/health", "/actuator/health/liveness", "/actuator/health/readiness"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.json()["status"] == "UP"


def test_down_indicator_flips_probes_to_503() -> None:
    agg = HealthAggregator()
    agg.add_indicator("database_health", _DownIndicator())
    client = _client(build_health_app(ApplicationContext(Config({})), aggregator=agg))

    resp = client.get("/actuator/health/readiness")
    assert resp.status_code == 503
    assert resp.json()["components"]["database_health"]["status"] == "DOWN"
    assert client.get("/actuator/health").status_code == 503


def test_readiness_only_indicator_does_not_kill_liveness() -> None:
    agg = HealthAggregator()
    agg.add_indicator("database_health", _DownIndicator(), groups={ProbeGroup.READINESS})
    client = _client(build_health_app(ApplicationContext(Config({})), aggregator=agg))

    assert client.get("/actuator/health/readiness").status_code == 503
    resp = client.get("/actuator/health/liveness")
    assert resp.status_code == 200
    assert resp.json()["status"] == "UP"


# ---------------------------------------------------------------------------
# Endpoint exposure (secure by default)
# ---------------------------------------------------------------------------


def test_default_exposure_hides_loggers_and_metrics() -> None:
    client = _client(build_health_app(ApplicationContext(Config({}))))
    assert client.get("/actuator/health").status_code == 200
    assert client.get("/actuator/loggers").status_code == 404
    assert client.get("/actuator/metrics").status_code == 404


def test_exposure_opt_in_follows_pyfly_management_config() -> None:
    cfg = Config(
        {"pyfly": {"management": {"endpoints": {"web": {"exposure": {"include": "health,metrics"}}}}}}
    )
    context = ApplicationContext(cfg)
    client = _client(build_health_app(context))
    assert client.get("/actuator/health").status_code == 200
    assert client.get("/actuator/metrics").status_code == 200
    assert client.get("/actuator/loggers").status_code == 404


def test_worker_serves_admin_dashboard_when_enabled() -> None:
    # With a context that enables the admin dashboard, the worker's management
    # app exposes /admin alongside the actuator — same surface as the API.
    context = ApplicationContext(Config({"pyfly": {"admin": {"enabled": True}}}))
    client = _client(build_health_app(context))
    assert client.get("/actuator/health").status_code == 200
    assert client.get("/admin/").status_code in (200, 307, 308)


def test_worker_admin_dashboard_absent_when_disabled() -> None:
    context = ApplicationContext(Config({"pyfly": {"admin": {"enabled": False}}}))
    client = _client(build_health_app(context))
    assert client.get("/actuator/health").status_code == 200
    assert client.get("/admin/").status_code == 404


# ---------------------------------------------------------------------------
# Indicator discovery from the DI container
# ---------------------------------------------------------------------------


def test_indicators_scanned_from_context_container() -> None:
    context = ApplicationContext(Config({}))
    context.container.register_instance(_DownIndicator, _DownIndicator(), name="database_health")
    context.container.register_instance(_UpIndicator, _UpIndicator(), name="eda_health")

    client = _client(build_health_app(context))
    resp = client.get("/actuator/health/readiness")
    assert resp.status_code == 503
    components = resp.json()["components"]
    assert components["database_health"]["status"] == "DOWN"
    assert components["eda_health"]["status"] == "UP"


# ---------------------------------------------------------------------------
# Port resolution
# ---------------------------------------------------------------------------


def test_resolve_health_port_defaults_to_service_port() -> None:
    # worker_health_port passed explicitly: IDPSettings is a BaseSettings, so
    # a bare constructor would absorb ambient FLYDOCS_WORKER_HEALTH_PORT from
    # the developer's shell or .env and flake this assertion.
    assert resolve_health_port(IDPSettings(port=8080, worker_health_port=None)) == 8080


def test_resolve_health_port_override() -> None:
    assert resolve_health_port(IDPSettings(port=8080, worker_health_port=9090)) == 9090


def test_resolve_health_port_zero_disables() -> None:
    assert resolve_health_port(IDPSettings(port=8080, worker_health_port=0)) == 0


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def test_make_health_server_config() -> None:
    settings = IDPSettings(port=8080, worker_health_port=9090)
    server = make_health_server(build_health_app(ApplicationContext(Config({}))), settings=settings)
    assert server.config.host == "0.0.0.0"
    assert server.config.port == 9090
    assert server.config.access_log is False
    assert server.config.lifespan == "off"


def test_make_health_server_rejects_disabled_port() -> None:
    settings = IDPSettings(port=8080, worker_health_port=0)
    with pytest.raises(ValueError, match="disabled"):
        make_health_server(build_health_app(ApplicationContext(Config({}))), settings=settings)


def test_build_worker_health_server_none_when_disabled() -> None:
    assert (
        build_worker_health_server(
            ApplicationContext(Config({})), IDPSettings(port=8080, worker_health_port=0)
        )
        is None
    )


def test_build_worker_health_server_enabled() -> None:
    server = build_worker_health_server(
        ApplicationContext(Config({})), IDPSettings(port=8080, worker_health_port=9090)
    )
    assert server is not None
    assert server.config.port == 9090


# ---------------------------------------------------------------------------
# Lifecycle: real server round-trip, bind failure, graceful stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_server_serves_and_stops() -> None:
    port = _free_port()
    server = build_worker_health_server(
        ApplicationContext(Config({})), IDPSettings(port=port, worker_health_port=None)
    )
    assert server is not None
    task = asyncio.create_task(serve_health(server), name="health-server")
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                if task.done():  # bind failed -> surface the exception now
                    await task
                try:
                    resp = await client.get(f"http://127.0.0.1:{port}/actuator/health/liveness")
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.05)
            else:
                pytest.fail("health server never came up")
        assert resp.status_code == 200
        assert resp.json()["status"] == "UP"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_bind_failure_is_a_normal_task_exception() -> None:
    # uvicorn turns EADDRINUSE into SystemExit; serve_health must convert it
    # into a RuntimeError so asyncio.wait(FIRST_COMPLETED) in the CLI observes
    # a dead task and tears the whole worker process down.
    with socket.socket() as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("0.0.0.0", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]

        server = build_worker_health_server(
            ApplicationContext(Config({})), IDPSettings(port=port, worker_health_port=None)
        )
        assert server is not None
        with pytest.raises(RuntimeError, match="health server"):
            # wait_for bounds the test: if the server ever binds successfully
            # (wrong port resolution), this fails fast instead of hanging.
            await asyncio.wait_for(serve_health(server), timeout=30)


@pytest.mark.asyncio
async def test_sigterm_triggers_graceful_stop_callback() -> None:
    from flydocs.cli import _install_sigterm_handler

    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_sigterm_handler(stopped.set)
    try:
        signal.raise_signal(signal.SIGTERM)
        await asyncio.wait_for(stopped.wait(), timeout=5)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


@pytest.mark.asyncio
async def test_run_until_first_exit_drains_siblings_before_reraising() -> None:
    # When one task dies, the siblings must be stopped through their own exit
    # paths (so uvicorn closes its sockets) and awaited BEFORE the exception
    # propagates to the caller's pyfly shutdown.
    from flydocs.cli import _run_until_first_exit

    stop = asyncio.Event()
    sibling_finished = False

    async def fails() -> None:
        raise RuntimeError("boom")

    async def stop_aware() -> None:
        nonlocal sibling_finished
        await stop.wait()
        sibling_finished = True

    tasks = {
        asyncio.create_task(fails(), name="fails"),
        asyncio.create_task(stop_aware(), name="stop-aware"),
    }
    with pytest.raises(RuntimeError, match="boom"):
        await _run_until_first_exit(tasks, stop.set)
    assert sibling_finished


@pytest.mark.asyncio
async def test_run_until_first_exit_cancels_stragglers(monkeypatch: pytest.MonkeyPatch) -> None:
    from flydocs import cli

    monkeypatch.setattr(cli, "_SHUTDOWN_GRACE_S", 0.05)

    async def finishes() -> None:
        return None

    async def ignores_stop() -> None:
        await asyncio.sleep(3600)

    tasks = {
        asyncio.create_task(finishes(), name="finishes"),
        asyncio.create_task(ignores_stop(), name="ignores-stop"),
    }
    await cli._run_until_first_exit(tasks, lambda: None)
    assert all(task.done() for task in tasks)
