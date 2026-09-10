# Bronze — developer and agent entry points. Every target is safe to run in a
# git worktree; nothing here touches another checkout.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# User-installed tools live here on contributor machines without Homebrew.
export PATH := $(HOME)/.local/bin:$(PATH)
UV ?= uv

# ─── per-worktree isolation ─────────────────────────────────────────────────
# Parallel agents run `make up` in different worktrees at the same time. A
# compose project name and a port block derived from the checkout path keep
# their containers and host ports apart. Anything already exported in your
# shell wins (`?=`); docker-compose.yml reads the same names.
WT_HASH     := $(shell printf '%s' "$(CURDIR)" | shasum -a 256 | cut -c1-8)
PORT_OFFSET := $(shell printf '%d' 0x$(shell printf '%s' "$(CURDIR)" | shasum -a 256 | cut -c1-2))
export COMPOSE_PROJECT_NAME ?= bronze-$(WT_HASH)
export DB_PORT    ?= $(shell echo $$((15432 + $(PORT_OFFSET))))
export REDIS_PORT ?= $(shell echo $$((16379 + $(PORT_OFFSET))))
export API_PORT   ?= $(shell echo $$((18000 + $(PORT_OFFSET))))
# Host-side tools (alembic, pytest, uvicorn outside Docker) reach this
# worktree's stack through the published ports.
export DATABASE_URL ?= postgresql+psycopg://bronze:bronze@localhost:$(DB_PORT)/bronze
export REDIS_URL    ?= redis://localhost:$(REDIS_PORT)/0

.PHONY: help setup lint format typecheck test test-parser hooks-test ci up down migrate env

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## One-time per checkout: sync deps, wire git hooks, create .env
	$(UV) sync --frozen
	git config core.hooksPath .githooks   # tracked hooks resolve per worktree; never `pre-commit install`
	$(UV) run --frozen pre-commit install-hooks
	@[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example"; }

lint: ## ruff check + format check + mypy (the commit gate)
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
	$(UV) run --frozen mypy
	$(UV) run --frozen mypy --python-version 3.10 --strict .claude/hooks

format: ## Apply ruff fixes and formatting
	$(UV) run --frozen ruff check --fix .
	$(UV) run --frozen ruff format .

typecheck: ## mypy only
	$(UV) run --frozen mypy

test: ## Full pytest suite (excludes @live)
	$(UV) run --frozen pytest

test-parser: ## Parser fixtures, determinism and round-trip suites — gate for load-bearing files
	$(UV) run --frozen pytest -m parser api/tests/parser

hooks-test: ## Claude Code hook scripts under 3.12 and 3.9 (hooks run on the system interpreter)
	$(UV) run --frozen pytest tests/harness
	$(UV) run --python 3.9 --isolated --no-project --with pytest -- pytest tests/harness

ci: lint test test-parser hooks-test ## Everything CI runs

env: ## Print the per-worktree compose values
	@echo COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT_NAME) DB_PORT=$(DB_PORT) REDIS_PORT=$(REDIS_PORT) API_PORT=$(API_PORT)
	@echo DATABASE_URL=$(DATABASE_URL) REDIS_URL=$(REDIS_URL)

up: env ## Local stack: Postgres 16, Redis, API, one SimC worker (first run compiles SimC)
	docker compose up -d --build --wait
	@echo "API: http://localhost:$(API_PORT)/health"

down: ## Stop the local stack for this worktree
	@[ -f docker-compose.yml ] && docker compose down || true

migrate: ## alembic upgrade head (M0-04 wires alembic)
	@[ -f api/alembic.ini ] || { echo "api/alembic.ini is not committed yet (ticket M0-04)"; exit 1; }
	$(UV) run --frozen alembic -c api/alembic.ini upgrade head
