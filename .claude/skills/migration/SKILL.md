---
name: migration
description: Create an Alembic migration for Bronze with a working downgrade and the up/down/up proof, respecting the snapshots-additive and versioned-game-data rules.
argument-hint: "add sim_jobs and sim_results"
---

Create a migration for: `$ARGUMENTS`.

1. Read `.claude/rules/migrations.md` and plan §7. If the change touches
   `snapshots` and is not purely additive, stop: that is a stop-and-ask.
2. Update the SQLAlchemy models first, then
   `uv run alembic -c api/alembic.ini revision --autogenerate -m "<slug>"`.
   Read the generated file; autogenerate misses partial indexes, check
   constraints and JSONB defaults — write those by hand.
3. Write `downgrade()` so it fully reverses `upgrade()`. Never drop
   old-version rows from `game_*` tables.
4. Prove it against the local stack (`make up`):
   `make migrate && uv run alembic -c api/alembic.ini downgrade -1 && make migrate`.
   Paste the output in the PR body.
5. If the migration adds or changes a unique index, add a test that inserts
   a duplicate and asserts the database rejects it.
6. Never edit a migration that is already on `origin/main` (a hook blocks
   it); write a new revision.
