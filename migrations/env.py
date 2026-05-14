# Copyright 2026 Firefly Software Solutions Inc
"""Alembic env -- sync runner that delegates to ``ExtractionJob`` metadata.

The application uses async SQLAlchemy, but Alembic always runs sync;
we strip the async driver suffix to make the URL synchronous for the
migration step. Production migrations are usually run as a one-shot
container, not from inside the API process.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make ``src`` importable.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from flydesk_idp.models.entities.extraction_job import Base  # noqa: E402

config = context.config

# Override sqlalchemy.url from the env var when present (and translate the
# async driver name back to a sync one Alembic can use).
url = os.environ.get("FLYDESK_IDP_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
if url:
    sync_url = url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")
    config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
