# wowlab — developer and agent entry points. Every target is safe to run in a
# git worktree; nothing here touches another checkout or a game install.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# User-installed tools live here on contributor machines without Homebrew.
export PATH := $(HOME)/.local/bin:$(PATH)
UV ?= uv
# Pinned selene for the lab-addon Lua lint (scripts/fetch_selene.py holds the
# version and checksums). Override with SELENE=<path> to use another binary.
SELENE ?= $(shell $(UV) run --frozen python scripts/fetch_selene.py --print)

.PHONY: help setup lint lint-lua selene format typecheck test test-fast test-clean test-parser hooks-test ci

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## One-time per checkout: sync deps, wire git hooks, create .env
	$(UV) sync --frozen
	git config core.hooksPath .githooks   # tracked hooks resolve per worktree; never `pre-commit install`
	$(UV) run --frozen pre-commit install-hooks
	@[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example"; }
	@# Pinned Lua linter into .tools/ (network here only). Warn-only so setup
	@# finishes offline; `make selene` is the strict fetch, `make lint-lua` needs it.
	@$(UV) run --frozen python scripts/fetch_selene.py || echo "warning: selene not fetched; run 'make selene' before 'make lint-lua'"

lint: ## ruff check + format check + mypy --strict (the commit gate)
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
	$(UV) run --frozen mypy
	$(UV) run --frozen mypy --python-version 3.10 --strict .claude/hooks

selene: ## Fetch the pinned selene binary into .tools/ (checksum-verified)
	$(UV) run --frozen python scripts/fetch_selene.py

lint-lua: ## Static Lua lint of lab/addon with pinned selene (offline; never runs Lua)
	@[ -x "$(SELENE)" ] || { echo "selene not found at $(SELENE); run 'make selene' first"; exit 1; }
	cd lab/addon && "$(SELENE)" WowLab

format: ## Apply ruff fixes and formatting
	$(UV) run --frozen ruff check --fix .
	$(UV) run --frozen ruff format .

typecheck: ## mypy only
	$(UV) run --frozen mypy

# Two passes (M13-18): everything not `serial` across all cores, then the
# `serial` tests (RSS and CPU-time budgets, marked in conftest.py) alone.
# A command-line -m replaces the one in addopts, so both keep `not live`.
# worksteal: a few review probes take 10-40 s, and an idle worker takes
# queued tests from a busy one instead of waiting at the end.
PYTEST_PARALLEL := -n auto --dist worksteal -m "not live and not serial"
PYTEST_SERIAL := -m "serial and not live"

test: ## Full pytest suite (excludes @live): parallel pass, then the serial pass
	$(UV) run --frozen pytest $(PYTEST_PARALLEL)
	$(UV) run --frozen pytest $(PYTEST_SERIAL)

# Local iteration (M13-81), never the proof of done: everything `make test`
# runs except the review probes under lab/core/tests/review/ and the `serial`
# pass, on at most 4 workers so the machine stays usable. `make test`,
# `make ci` and CI keep running everything; tests/repo/test_make_test_fast.py
# pins that this recipe leaves out nothing else.
TEST_FAST_WORKERS = $(shell $(UV) run --frozen python -c "import os; print(min(4, os.cpu_count() or 1))")

test-fast: ## Quick local run: no review probes, no serial pass, at most 4 workers (not the proof of done)
	$(UV) run --frozen pytest -n $(TEST_FAST_WORKERS) --dist worksteal -p no:cacheprovider -m "not live and not serial" --ignore=lab/core/tests/review

# The system python3 (3.9+), not `uv run`: `uv cache prune` waits for every
# uv process holding the cache, and `uv run` holds it while its child runs.
PYTHON3 ?= python3

test-clean: ## Remove pytest's temp dirs and .pytest_cache, prune the uv cache; print sizes before and after
	UV="$(UV)" $(PYTHON3) scripts/clean_test_artifacts.py

test-parser: ## Parser fixtures and round-trip suites — gate for luadata.py and every format parser
	$(UV) run --frozen pytest -m parser lab/core/tests/parser

hooks-test: ## Claude Code hook scripts under 3.12 and 3.9 (hooks run on the system interpreter)
	$(UV) run --frozen pytest tests/harness
	$(UV) run --python 3.9 --isolated --no-project --with pytest -- pytest tests/harness

# Every stage runs even when an earlier one fails, then a summary; the exit
# status is non-zero if any stage failed (M13-81).
CI_STAGES := lint lint-lua test test-parser hooks-test

ci: ## Everything CI runs: every stage, then a summary of each stage's result
	@failed=0; summary=(); \
	for stage in $(CI_STAGES); do \
	  echo "==> make $$stage"; \
	  if $(MAKE) --no-print-directory $$stage; then \
	    result="ok"; \
	  else \
	    failed=1; result="FAILED"; \
	    if [ "$$stage" = lint-lua ] && [ ! -x "$(SELENE)" ]; then \
	      result="FAILED: selene is missing (expected at $(SELENE)); run 'make selene'"; \
	    fi; \
	  fi; \
	  summary+=("$$(printf '  %-12s %s' "$$stage" "$$result")"); \
	done; \
	echo; echo "make ci summary:"; printf '%s\n' "$${summary[@]}"; \
	if [ $$failed -ne 0 ]; then echo "make ci: FAILED"; else echo "make ci: all stages passed"; fi; \
	exit $$failed
