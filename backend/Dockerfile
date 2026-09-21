# Recluse backend.
#
# The image mirrors the repo layout (/srv/backend, /srv/data) so that the
# repo-root-relative path resolution in app/config.py behaves identically
# inside and outside a container.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/srv/backend/.venv

WORKDIR /srv/backend

# Dependencies first, so application edits do not re-resolve the ML stack.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY backend/ ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/srv/backend/.venv/bin:$PATH"

# Documentation only; the published port comes from docker-compose.yml.
EXPOSE 8000

# Migrate then serve, so `docker compose up` works against an empty volume.
# Host and port come from the environment -- nothing here is hardcoded.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host ${IDS_HOST:-0.0.0.0} --port ${IDS_PORT:-8000}"]
