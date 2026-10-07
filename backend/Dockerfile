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

# LightGBM's native library links against the OpenMP runtime, which the slim
# image does not ship. Without it the API cannot load the Stage 1 model at all --
# invisible until a model is actually served, which is why a scaffold with no
# artifacts started fine without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

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

# Migrate, install the committed model release if nothing is serving, seed the
# demo when IDS_SEED_ON_START=true and the database is empty, then serve. Run
# through `sh` rather than as an executable, so a checkout that lost the file's
# exec bit (any Windows clone) still starts.
CMD ["sh", "/srv/backend/docker-entrypoint.sh"]
