# Copyright 2026 Firefly Software Solutions Inc
"""CLI entry point for flydocs.

Four subcommands:

* ``flydocs serve``        -- run the FastAPI server on the configured port.
* ``flydocs worker``       -- run the EDA worker that consumes the job topic.
* ``flydocs bbox-worker``  -- run the second-stage EDA worker that grounds
                                  bboxes for jobs whose extraction finished in
                                  ``PARTIAL_SUCCEEDED``.
* ``flydocs migrate``      -- run ``alembic upgrade head`` against the DB.

``serve`` lets uvicorn import ``flydocs.main:app`` (pyfly drives the
lifecycle there). The workers boot minimal :class:`PyFlyApplication`
instances and pull their concrete worker classes out of the DI
container; they never construct the workers themselves, so the
container owns every dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from flydocs.config import get_settings

logger = logging.getLogger("flydocs.cli")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


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
    """Boot pyfly, run the :class:`JobWorker` and :class:`JobReaper` together.

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
        from flydocs.core.services.workers.job_reaper import JobReaper
        from flydocs.core.services.workers.job_worker import JobWorker
        from flydocs.models.repositories import ExtractionJobRepository

        pyfly_app = PyFlyApplication(FlydocsApplication)
        await pyfly_app.startup()
        worker: JobWorker | None = None
        reaper: JobReaper | None = None
        try:
            container = pyfly_app.context.container
            settings = container.resolve(IDPSettings)
            worker = JobWorker(
                orchestrator=container.resolve(PipelineOrchestrator),
                repository=container.resolve(ExtractionJobRepository),
                event_publisher=container.resolve(EventPublisher),
                webhook=container.resolve(WebhookPublisher),
                settings=settings,
            )
            reaper = JobReaper(
                repository=container.resolve(ExtractionJobRepository),
                event_publisher=container.resolve(EventPublisher),
                settings=settings,
            )
            worker_task = asyncio.create_task(worker.run_forever(), name="job-worker")
            reaper_task = asyncio.create_task(reaper.run_forever(), name="job-reaper")
            done, pending = await asyncio.wait(
                {worker_task, reaper_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            if worker is not None:
                worker.stop()
            if reaper is not None:
                reaper.stop()
            await pyfly_app.shutdown()

    asyncio.run(_run())
    return 0


def cmd_bbox_worker(_: argparse.Namespace) -> int:
    """Boot pyfly, run :class:`BboxRefineWorker` + :class:`BboxReaper` together."""

    async def _run() -> None:
        from pyfly.core import PyFlyApplication
        from pyfly.eda import EventPublisher

        from flydocs.app import FlydocsApplication
        from flydocs.config import IDPSettings
        from flydocs.core.services.bbox import BboxRefiner
        from flydocs.core.services.binary import BinaryNormalizer
        from flydocs.core.services.webhook import WebhookPublisher
        from flydocs.core.services.workers.bbox_reaper import BboxReaper
        from flydocs.core.services.workers.bbox_refine_worker import BboxRefineWorker
        from flydocs.models.repositories import ExtractionJobRepository

        pyfly_app = PyFlyApplication(FlydocsApplication)
        await pyfly_app.startup()
        worker: BboxRefineWorker | None = None
        reaper: BboxReaper | None = None
        try:
            container = pyfly_app.context.container
            settings = container.resolve(IDPSettings)
            worker = BboxRefineWorker(
                repository=container.resolve(ExtractionJobRepository),
                event_publisher=container.resolve(EventPublisher),
                webhook=container.resolve(WebhookPublisher),
                normalizer=container.resolve(BinaryNormalizer),
                refiner=container.resolve(BboxRefiner),
                settings=settings,
            )
            reaper = BboxReaper(
                repository=container.resolve(ExtractionJobRepository),
                event_publisher=container.resolve(EventPublisher),
                settings=settings,
            )
            worker_task = asyncio.create_task(worker.run_forever(), name="bbox-worker")
            reaper_task = asyncio.create_task(reaper.run_forever(), name="bbox-reaper")
            done, pending = await asyncio.wait(
                {worker_task, reaper_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            if worker is not None:
                worker.stop()
            if reaper is not None:
                reaper.stop()
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
