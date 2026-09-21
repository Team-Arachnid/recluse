# Recluse frontend.
#
# `dev` is what docker compose runs: the Vite dev server with HMR, proxying
# /api to the backend service. `serve` is the static production image, used by
# the Phase 8 packaging target.

# ---------------------------------------------------------------------------
FROM node:24-alpine AS deps
WORKDIR /srv/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit

# ---------------------------------------------------------------------------
FROM deps AS dev
COPY frontend/ ./
# Containers must bind all interfaces or the published port reaches nothing.
ENV VITE_DEV_SERVER_HOST=0.0.0.0
EXPOSE 5173
CMD ["sh", "-c", "npm run dev -- --host ${VITE_DEV_SERVER_HOST:-0.0.0.0} --port ${VITE_DEV_SERVER_PORT:-5173}"]

# ---------------------------------------------------------------------------
FROM deps AS build
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
FROM nginx:1.29-alpine AS serve
COPY --from=build /srv/frontend/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
