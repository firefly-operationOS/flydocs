#!/usr/bin/env sh
# Copyright 2026 Firefly Software Solutions Inc
#
# Dispatcher for the flydocs container.
#
#   ./docker-entrypoint.sh serve        -- run the FastAPI server (default)
#   ./docker-entrypoint.sh worker       -- run the main EDA worker
#   ./docker-entrypoint.sh bbox-worker  -- run the second-stage bbox-refine worker
#   ./docker-entrypoint.sh migrate      -- run alembic upgrade head and exit
#
# When RUN_MIGRATIONS=true, migrate-then-serve is invoked.
set -eu

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "[entrypoint] RUN_MIGRATIONS=true -- running migrations first"
  alembic upgrade head
fi

cmd="${1:-serve}"
shift || true

case "${cmd}" in
  serve)
    exec flydocs serve "$@"
    ;;
  worker)
    exec flydocs worker "$@"
    ;;
  bbox-worker)
    exec flydocs bbox-worker "$@"
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    # Allow ad-hoc commands (sh, alembic <foo>, etc.)
    exec "${cmd}" "$@"
    ;;
esac
