#!/bin/sh
# Recluse backend container start: migrate, install the model release, seed the
# demo if asked and the database is empty, then serve.
#
# Each step is safe to repeat on every restart:
#   - migrations are idempotent;
#   - `app.release install` never replaces a model that is already serving,
#     so artifacts you trained and mounted in are left alone;
#   - `app.seed --if-empty` does nothing once the database holds alerts.
set -eu

alembic upgrade head
python -m app.release install

if [ "${IDS_SEED_ON_START:-false}" = "true" ]; then
    # A failed seed must not keep the API down: the dashboard's empty states
    # are honest, an unreachable API is not.
    python -m app.seed --if-empty || echo "seed failed; serving an empty database" >&2
fi

exec uvicorn app.main:app --host "${IDS_HOST:-0.0.0.0}" --port "${IDS_PORT:-8000}"
