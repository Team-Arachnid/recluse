# Recluse — task runner.
#
# GNU make is not installed by default on Windows. `make.ps1` in this
# directory mirrors every target below, so `./make.ps1 dev` works in
# PowerShell without installing anything.

SHELL := /bin/sh
.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
UV       := uv
NPM      := npm
COMPOSE  := docker compose

.PHONY: help env install dev backend frontend migrate revision test test-backend \
        test-frontend lint format typecheck gen-types build up down logs ps clean \
        wiki wiki-check hooks hooks-uninstall

help: ## Show the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if absent
	@test -f .env || (cp .env.example .env && echo "created .env")

install: env ## Install backend and frontend dependencies
	cd $(BACKEND) && $(UV) sync
	cd $(FRONTEND) && $(NPM) install --no-fund

dev: env ## Run backend and frontend together (native, hot reload)
	python scripts/dev.py

backend: env ## Run the API only
	cd $(BACKEND) && $(UV) run uvicorn app.main:app --reload

frontend: ## Run the dashboard only
	cd $(FRONTEND) && $(NPM) run dev

migrate: env ## Apply database migrations
	cd $(BACKEND) && $(UV) run alembic upgrade head

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

wiki: ## Publish wiki/ to the GitHub wiki (no-op when unchanged)
	python scripts/publish_wiki.py

wiki-check: ## Report whether the GitHub wiki is behind wiki/, push nothing
	python scripts/publish_wiki.py --check

hooks: ## Install the git hooks, including post-commit wiki publishing
	python scripts/install_hooks.py

hooks-uninstall: ## Remove the git hooks this repo installed
	python scripts/install_hooks.py --uninstall

clean: ## Remove build output, caches and the dev database
	rm -rf $(FRONTEND)/dist $(FRONTEND)/node_modules/.vite
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -f data/ids.db data/ids.db-wal data/ids.db-shm
