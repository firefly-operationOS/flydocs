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

"""CLI entry point for flydocs.

Four subcommands:

* ``flydocs serve``        -- run the FastAPI server on the configured port.
* ``flydocs worker``       -- run the EDA worker that consumes the job topic.
* ``flydocs bbox-worker``  -- run the second-stage EDA worker that grounds
                                  bboxes out-of-band for extractions whose
                                  ``post_processing.bbox_refinement`` is pending.
* ``flydocs migrate``      -- run ``alembic upgrade head`` against the DB.

``serve`` lets uvicorn import ``flydocs.main:app`` (pyfly drives the
lifecycle there). The workers boot minimal :class:`PyFlyApplication`
instances and pull their concrete worker classes out of the DI
container; they never construct the workers themselves, so the
container owns every dependency.

Both worker modes also run an HTTP health server (``flydocs.worker_health``)
as a sibling asyncio task so Kubernetes can probe ``/actuator/health/*``;
it joins the same ``asyncio.wait(FIRST_COMPLETED)`` set as the worker and
reaper, so any of the three dying takes the whole process down for a clean
pod restart. SIGTERM stops all three gracefully via
:func:`_install_sigterm_handler`; SIGINT keeps asyncio's KeyboardInterrupt
behaviour for local Ctrl-C.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable

from flydocs.config import get_settings

logger = logging.getLogger("flydocs.cli")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _install_sigterm_handler(stop: Callable[[], None]) -> None:
    """Run *stop* on SIGTERM so the worker stack shuts down gracefully.

    Kubernetes stops pods with SIGTERM. *stop* flips every component's stop
    flag; the run tasks then return on their own and the CLI's done/pending
    handling cancels the rest and runs the pyfly shutdown. SIGINT is left to
    asyncio (KeyboardInterrupt) for local Ctrl-C.
    """
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop)


# Grace period for sibling tasks to exit on their own after the stop flags
# flip, before they are cancelled. Bounds worker shutdown: uvicorn needs a
# tick (~0.1 s) to notice ``should_exit`` and close its sockets; the worker
# and reaper return as soon as their stop event is observed.
_SHUTDOWN_GRACE_S = 5.0


async def _run_until_first_exit(tasks: set[asyncio.Task[None]], stop_all: Callable[[], None]) -> None:
    """Wait for the first task to finish, then drain the rest before returning.

    Flips the stop flags as soon as any task completes so the siblings shut
    down through their own exit paths (uvicorn closes its listening sockets,
    the worker and reaper run their cleanup); only stragglers that outlive
    the grace period are cancelled, and every task is awaited before the
    caller proceeds to the pyfly shutdown. The first observed exception is
    re-raised so the whole process dies when any task dies.
    """
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    stop_all()
    if pending:
        done_late, stragglers = await asyncio.wait(pending, timeout=_SHUTDOWN_GRACE_S)
        for task in stragglers:
            task.cancel()
        await asyncio.gather(*stragglers, return_exceptions=True)
        done |= done_late
    for task in done:
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            raise exc


def cmd_serve(_: argparse.Namespace) -> int:
    """Boot the PyFly application and serve the FastAPI app via uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "flydocs.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
    return 0


def cmd_worker(_: argparse.Namespace) -> int:
    """Boot pyfly, run the :class:`ExtractionWorker` and :class:`ExtractionReaper`.

    The reaper is colocated with the worker so a single container fulfils
    both responsibilities: drain the EDA outbox AND revive orphans whose
    triggering event was lost (worker crash, submit-publish crash,
    delayed-publish task killed before its sleep completed). Running the
    reaper in every worker replica is safe -- duplicate republishes are
    deduped at claim time by the atomic ``mark_running`` transition.
    """

    async def _run() -> None:
        from pyfly.core import PyFlyApplication
        from pyfly.eda import EventPublisher

        from flydocs.app import FlydocsApplication
        from flydocs.config import IDPSettings
        from flydocs.core.services.pipeline import PipelineOrchestrator
        from flydocs.core.services.webhook import WebhookPublisher
        from flydocs.core.services.workers.job_reaper import ExtractionReaper
        from flydocs.core.services.workers.job_worker import ExtractionWorker
        from flydocs.models.repositories import ExtractionRepository
        from flydocs.worker_health import build_worker_health_server, serve_health

        pyfly_app = PyFlyApplication(FlydocsApplication)
        await pyfly_app.startup()
        worker: ExtractionWorker | None = None
        reaper: ExtractionReaper | None = None
        health_server = None
        try:
            container = pyfly_app.context.container
            settings = container.resolve(IDPSettings)
            worker = ExtractionWorker(
                orchestrator=container.resolve(PipelineOrchestrator),
                repository=container.resolve(ExtractionRepository),
                event_publisher=container.resolve(EventPublisher),
                webhook=container.resolve(WebhookPublisher),
                settings=settings,
            )
            reaper = ExtractionReaper(
                repository=container.resolve(ExtractionRepository),
                event_publisher=container.resolve(EventPublisher),
                settings=settings,
            )
            tasks = {
                asyncio.create_task(worker.run_forever(), name="extraction-worker"),
                asyncio.create_task(reaper.run_forever(), name="extraction-reaper"),
            }
            health_server = build_worker_health_server(pyfly_app.context, settings)
            if health_server is not None:
                tasks.add(asyncio.create_task(serve_health(health_server), name="health-server"))
            else:
                logger.info("worker health server disabled (resolved port is 0)")

            def _stop_all() -> None:
                worker.stop()
                reaper.stop()
                if health_server is not None:
                    health_server.should_exit = True

            _install_sigterm_handler(_stop_all)
            await _run_until_first_exit(tasks, _stop_all)
        finally:
            if worker is not None:
                worker.stop()
            if reaper is not None:
                reaper.stop()
            if health_server is not None:
                health_server.should_exit = True
            await pyfly_app.shutdown()

    asyncio.run(_run())
    return 0


def cmd_bbox_worker(_: argparse.Namespace) -> int:
    """Boot pyfly, run :class:`BboxRefineWorker` + :class:`BboxReaper` together."""

    async def _run() -> None:
        from fireflyframework_agentic.content.binary import BinaryNormalizer
        from pyfly.core import PyFlyApplication
        from pyfly.eda import EventPublisher

        from flydocs.app import FlydocsApplication
        from flydocs.config import IDPSettings
        from flydocs.core.services.bbox import BboxRefiner
        from flydocs.core.services.webhook import WebhookPublisher
        from flydocs.core.services.workers.bbox_reaper import BboxReaper
        from flydocs.core.services.workers.bbox_refine_worker import BboxRefineWorker
        from flydocs.models.repositories import ExtractionRepository
        from flydocs.worker_health import build_worker_health_server, serve_health

        pyfly_app = PyFlyApplication(FlydocsApplication)
        await pyfly_app.startup()
        worker: BboxRefineWorker | None = None
        reaper: BboxReaper | None = None
        health_server = None
        try:
            container = pyfly_app.context.container
            settings = container.resolve(IDPSettings)
            worker = BboxRefineWorker(
                repository=container.resolve(ExtractionRepository),
                event_publisher=container.resolve(EventPublisher),
                webhook=container.resolve(WebhookPublisher),
                normalizer=container.resolve(BinaryNormalizer),
                refiner=container.resolve(BboxRefiner),
                settings=settings,
            )
            reaper = BboxReaper(
                repository=container.resolve(ExtractionRepository),
                event_publisher=container.resolve(EventPublisher),
                settings=settings,
            )
            tasks = {
                asyncio.create_task(worker.run_forever(), name="bbox-worker"),
                asyncio.create_task(reaper.run_forever(), name="bbox-reaper"),
            }
            health_server = build_worker_health_server(pyfly_app.context, settings)
            if health_server is not None:
                tasks.add(asyncio.create_task(serve_health(health_server), name="health-server"))
            else:
                logger.info("worker health server disabled (resolved port is 0)")

            def _stop_all() -> None:
                worker.stop()
                reaper.stop()
                if health_server is not None:
                    health_server.should_exit = True

            _install_sigterm_handler(_stop_all)
            await _run_until_first_exit(tasks, _stop_all)
        finally:
            if worker is not None:
                worker.stop()
            if reaper is not None:
                reaper.stop()
            if health_server is not None:
                health_server.should_exit = True
            await pyfly_app.shutdown()

    asyncio.run(_run())
    return 0


def cmd_migrate(_: argparse.Namespace) -> int:
    """Apply Alembic migrations."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    here = os.path.dirname(os.path.abspath(__file__))
    cfg = AlembicConfig(os.path.join(here, "..", "..", "alembic.ini"))
    settings = get_settings()
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flydocs", description="flydocs -- pure-multimodal IDP service")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub_serve = sub.add_parser("serve", help="Run the FastAPI server")
    sub_serve.set_defaults(func=cmd_serve)

    sub_worker = sub.add_parser("worker", help="Run the EDA worker")
    sub_worker.set_defaults(func=cmd_worker)

    sub_bbox_worker = sub.add_parser("bbox-worker", help="Run the second-stage bbox refinement worker")
    sub_bbox_worker.set_defaults(func=cmd_bbox_worker)

    sub_migrate = sub.add_parser("migrate", help="Apply database migrations")
    sub_migrate.set_defaults(func=cmd_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(get_settings().log_level)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
