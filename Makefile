# wowlab — developer and agent entry points. Every target is safe to run in a
# git worktree; nothing here touches another checkout or a game install.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# User-installed tools live here on contributor machines without Homebrew.
export PATH := $(HOME)/.local/bin:$(PATH)
UV ?= uv

.PHONY: help setup lint format typecheck test test-parser hooks-test ci

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## One-time per checkout: sync deps, wire git hooks, create .env
	$(UV) sync --frozen
	git config core.hooksPath .githooks   # tracked hooks resolve per worktree; never `pre-commit install`
	$(UV) run --frozen pre-commit install-hooks
	@[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example"; }

lint: ## ruff check + format check + mypy --strict (the commit gate)
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

test-parser: ## Parser fixtures and round-trip suites — gate for luadata.py and every format parser
	$(UV) run --frozen pytest -m parser lab/core/tests/parser

hooks-test: ## Claude Code hook scripts under 3.12 and 3.9 (hooks run on the system interpreter)
	$(UV) run --frozen pytest tests/harness
	$(UV) run --python 3.9 --isolated --no-project --with pytest -- pytest tests/harness

ci: lint test test-parser hooks-test ## Everything CI runs
