# Copyright 2026 Firefly Software Solutions Inc
"""CLI entry point for flydesk-idp.

Three subcommands:

* ``flydesk-idp serve``    -- run the FastAPI server on the configured port.
* ``flydesk-idp worker``   -- run the EDA worker that consumes the job topic.
* ``flydesk-idp migrate``  -- run ``alembic upgrade head`` against the DB.

``serve`` lets uvicorn import ``flydesk_idp.main:app`` (pyfly drives the
lifecycle there). ``worker`` boots a minimal :class:`PyFlyApplication`
and pulls the :class:`JobWorker` out of the DI container; it never
constructs the worker itself, so the container owns every dependency.
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

        from flydesk_idp.app import FlydeskIDPApplication
        from flydesk_idp.core.services.workers.job_worker import JobWorker

        pyfly_app = PyFlyApplication(FlydeskIDPApplication)
        await pyfly_app.startup()
        try:
            worker = pyfly_app.context.container.resolve(JobWorker)
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
