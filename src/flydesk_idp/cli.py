# Copyright 2026 Firefly Software Solutions Inc
"""CLI entry point for flydesk-idp.

Four subcommands:

* ``flydesk-idp serve``        -- run the FastAPI server on the configured port.
* ``flydesk-idp worker``       -- run the EDA worker that consumes the job topic.
* ``flydesk-idp bbox-worker``  -- run the second-stage EDA worker that grounds
                                  bboxes for jobs whose extraction finished in
                                  ``PARTIAL_SUCCEEDED``.
* ``flydesk-idp migrate``      -- run ``alembic upgrade head`` against the DB.

``serve`` lets uvicorn import ``flydesk_idp.main:app`` (pyfly drives the
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

from flydesk_idp.config import get_settings

logger = logging.getLogger("flydesk_idp.cli")


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
        "flydesk_idp.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
    return 0


def cmd_worker(_: argparse.Namespace) -> int:
    """Boot pyfly, resolve :class:`JobWorker` from the container, run forever."""

    async def _run() -> None:
        from pyfly.core import PyFlyApplication
        from pyfly.eda import EventPublisher

        from flydesk_idp.app import FlydeskIDPApplication
        from flydesk_idp.config import IDPSettings
        from flydesk_idp.core.services.pipeline import PipelineOrchestrator
        from flydesk_idp.core.services.webhook import WebhookPublisher
        from flydesk_idp.core.services.workers.job_worker import JobWorker
        from flydesk_idp.models.repositories import ExtractionJobRepository

        pyfly_app = PyFlyApplication(FlydeskIDPApplication)
        await pyfly_app.startup()
        try:
            container = pyfly_app.context.container
            worker = JobWorker(
                orchestrator=container.resolve(PipelineOrchestrator),
                repository=container.resolve(ExtractionJobRepository),
                event_publisher=container.resolve(EventPublisher),
                webhook=container.resolve(WebhookPublisher),
                settings=container.resolve(IDPSettings),
            )
            await worker.run_forever()
        finally:
            await pyfly_app.shutdown()

    asyncio.run(_run())
    return 0


def cmd_bbox_worker(_: argparse.Namespace) -> int:
    """Boot pyfly, resolve :class:`BboxRefineWorker`, run forever."""

    async def _run() -> None:
        from pyfly.core import PyFlyApplication
        from pyfly.eda import EventPublisher

        from flydesk_idp.app import FlydeskIDPApplication
        from flydesk_idp.config import IDPSettings
        from flydesk_idp.core.services.bbox import BboxRefiner
        from flydesk_idp.core.services.binary import BinaryNormalizer
        from flydesk_idp.core.services.webhook import WebhookPublisher
        from flydesk_idp.core.services.workers.bbox_refine_worker import BboxRefineWorker
        from flydesk_idp.models.repositories import ExtractionJobRepository

        pyfly_app = PyFlyApplication(FlydeskIDPApplication)
        await pyfly_app.startup()
        try:
            container = pyfly_app.context.container
            worker = BboxRefineWorker(
                repository=container.resolve(ExtractionJobRepository),
                event_publisher=container.resolve(EventPublisher),
                webhook=container.resolve(WebhookPublisher),
                normalizer=container.resolve(BinaryNormalizer),
                refiner=container.resolve(BboxRefiner),
                settings=container.resolve(IDPSettings),
            )
            await worker.run_forever()
        finally:
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
    parser = argparse.ArgumentParser(prog="flydesk-idp", description="Firefly Desk IDP service")
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
