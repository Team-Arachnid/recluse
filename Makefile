# Recluse — task runner.
#
# GNU make is not installed by default on Windows. `make.ps1` in this
# directory mirrors every target below, so `./make.ps1 dev` works in
# PowerShell without installing anything.

SHELL := /bin/sh
.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
DOCS     := docs
UV       := uv
NPM      := npm
COMPOSE  := docker compose

.PHONY: help env install dev backend frontend migrate revision test test-backend \
        test-frontend lint format typecheck gen-types build up down logs ps clean \
        docs docs-serve data data-fetch data-clean data-split data-fit \
        train train-rf train-lgbm evaluate ablation-port train-anomaly ablation-input \
        loao drift-reference drift retrain models seed release openapi calibrate

help: ## Show the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if absent
	@test -f .env || (cp .env.example .env && echo "created .env")

install: env ## Install backend and frontend dependencies
	cd $(BACKEND) && $(UV) sync
	cd $(FRONTEND) && $(NPM) install --no-fund

# Incremental installs, used as run-target prerequisites. Each sentinel is the
# file its installer writes on success; with the manifests as prerequisites, a
# changed lockfile (a git pull, a newly added dependency) leaves the sentinel
# stale and the next run target reinstalls before starting. Without this,
# `make frontend` ran vite against a node_modules that predated a freshly added
# dependency, and the CSS @import 404'd at resolve time.
$(FRONTEND)/node_modules/.package-lock.json: $(FRONTEND)/package.json $(FRONTEND)/package-lock.json
	cd $(FRONTEND) && $(NPM) install --no-fund
	@touch $@

$(BACKEND)/.venv/pyvenv.cfg: $(BACKEND)/pyproject.toml $(BACKEND)/uv.lock
	cd $(BACKEND) && $(UV) sync
	@touch $@

dev: env ## Run backend and frontend together (native, hot reload)
	python scripts/dev.py

backend: env $(BACKEND)/.venv/pyvenv.cfg ## Run the API only
	cd $(BACKEND) && $(UV) run python -m app

frontend: $(FRONTEND)/node_modules/.package-lock.json ## Run the dashboard only
	cd $(FRONTEND) && $(NPM) run dev

migrate: env ## Apply database migrations
	cd $(BACKEND) && $(UV) run alembic upgrade head

data-fetch: ## Phase 1: download CICIDS2017 into data/raw (Kaggle mirror)
	cd $(BACKEND) && $(UV) run python ../scripts/fetch_data.py

data: env ## Phase 1: clean, split and fit the preprocessing bundle in one run
	cd $(BACKEND) && $(UV) run python -m training.preprocess --all

data-clean: env ## Phase 1: clean data/raw CSVs into data/interim Parquet
	cd $(BACKEND) && $(UV) run python -m training.clean

data-split: env ## Phase 1: temporally split data/interim into data/processed
	cd $(BACKEND) && $(UV) run python -m training.split

data-fit: env ## Phase 1: fit the preprocessing bundle from data/processed/train.parquet
	cd $(BACKEND) && $(UV) run python -m training.preprocess

train: train-rf evaluate ## Phase 2: train the RandomForest baseline and report it

train-rf: env ## Phase 2: RandomForest baseline, tuned and thresholded on the validation day
	cd $(BACKEND) && $(UV) run python -m training.train_supervised --algorithm rf

# Re-evaluates after training: a promotion rewrites the model card, and without
# a fresh evaluation the API would serve LightGBM beside RandomForest's numbers.
train-lgbm: env ## Phase 2: LightGBM upgrade; promoted only if it beats the baseline, then re-evaluated
	cd $(BACKEND) && $(UV) run python -m training.train_supervised --algorithm lgbm
	cd $(BACKEND) && $(UV) run python -m training.evaluate

evaluate: env ## Phase 2: score the held-out test day into reports/phase2_supervised.md
	cd $(BACKEND) && $(UV) run python -m training.evaluate

ablation-port: env ## Phase 2: raw vs bucketed destination port, into reports/port_ablation.md
	cd $(BACKEND) && $(UV) run python -m training.train_supervised --port-ablation

train-anomaly: env ## Phase 3: benign-only autoencoder, tau_anom and the PyOD baselines
	cd $(BACKEND) && $(UV) run python -m training.train_autoencoder

ablation-input: env ## Phase 3: pick the Stage 2 input clip bound, into reports/input_ablation.md
	cd $(BACKEND) && $(UV) run python -m training.train_autoencoder --input-ablation

loao: env ## Phase 4: leave-one-attack-out, into reports/loao.md
	cd $(BACKEND) && $(UV) run python -m training.loao

drift-reference: env ## Phase 7: cut the PSI reference from the training split
	cd $(BACKEND) && $(UV) run python -m training.drift_reference

# Nightly in a real deployment. A job rather than a thread inside the API: a
# full scan of the sample table at 3am should not compete with the alert stream
# for the same event loop.
#
#   0 3 * * *  cd /app/backend && python -m training.drift_job
drift: env ## Phase 7: compute one PSI snapshot over the sampled window
	cd $(BACKEND) && $(UV) run python -m training.drift_job

retrain: env ## Phase 7: fit a challenger from analyst labels and gate it
	cd $(BACKEND) && $(UV) run python -m training.retrain --now

models: env ## Phase 8: install the committed model release (never over a model you trained)
	cd $(BACKEND) && $(UV) run python -m app.release install

# Replays the committed demo flows through the real pipeline into an empty
# database, then runs the drift job. `make seed ARGS=--reset` starts over.
seed: env migrate models ## Phase 8: fill an empty database with a real, replayed demo
	cd $(BACKEND) && $(UV) run python -m app.seed $(ARGS)

release: env ## Phase 8 (maintainers): rebuild backend/release from the serving model
	cd $(BACKEND) && $(UV) run python -m app.seed sample
	cd $(BACKEND) && $(UV) run python -m app.release build

# After a shadow-mode burn-in (POST /ingest/start with mode "shadow"): cut the
# local tau_anom from the network's own traffic. Alert mode is refused until
# this has run for the Stage 2 model that is serving.
calibrate: env ## Phase 9: local tau_anom from the shadow burn-in, into reports/phase9_live.md
	cd $(BACKEND) && $(UV) run python -m training.calibrate_live $(ARGS)

revision: ## Autogenerate a migration: make revision m="add drift table"
	cd $(BACKEND) && $(UV) run alembic revision --autogenerate -m "$(m)"

test: test-backend test-frontend ## Run every test

test-backend: ## Run the backend test suite
	cd $(BACKEND) && $(UV) run pytest

test-frontend: ## Run the dashboard test suite
	cd $(FRONTEND) && $(NPM) run test

lint: ## Lint backend and typecheck frontend
	cd $(BACKEND) && $(UV) run ruff check .
	cd $(FRONTEND) && $(NPM) run typecheck

format: ## Format the backend
	cd $(BACKEND) && $(UV) run ruff format .
	cd $(BACKEND) && $(UV) run ruff check --fix .

typecheck: ## Typecheck the frontend
	cd $(FRONTEND) && $(NPM) run typecheck

# The API contract: rewrite the committed OpenAPI snapshot from the code, then
# regenerate the dashboard's types from it. The contract tests on both sides
# fail until this has been run after a change to the wire format.
openapi: ## Phase 8: rewrite the API contract snapshot and regenerate the frontend types
	cd $(BACKEND) && $(UV) run python -m app.contract
	cd $(FRONTEND) && $(NPM) run gen:types -- --from ../backend/tests/snapshots/openapi.json

gen-types: ## Regenerate frontend API types from the running backend
	cd $(FRONTEND) && $(NPM) run gen:types

build: ## Build the production dashboard bundle
	cd $(FRONTEND) && $(NPM) run build

up: env ## Start the containerised stack
	$(COMPOSE) up --build

down: ## Stop the containerised stack
	$(COMPOSE) down

logs: ## Tail container logs
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

docs: ## Build the documentation site into docs/_site (needs Ruby + bundler)
	cd $(DOCS) && bundle install --quiet && bundle exec jekyll build

docs-serve: ## Preview the documentation site at http://localhost:4000/recluse/
	cd $(DOCS) && bundle install --quiet && bundle exec jekyll serve --livereload

clean: ## Remove build output, caches and the dev database
	rm -rf $(FRONTEND)/dist $(FRONTEND)/node_modules/.vite
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -f data/ids.db data/ids.db-wal data/ids.db-shm
