---
paths:
  - "api/migrations/**"
  - "api/alembic.ini"
---

# Migrations

- Every schema change is an Alembic revision with a working `downgrade()`. The up/down/up test must pass.
- **Never edit a revision that is on `origin/main`.** A hook blocks it; write a new revision.
- `snapshots` changes must be purely additive (new nullable columns, new indexes). Anything else is a stop-and-ask (`AGENTS.md`).
- `game_items` / `game_spells` / `game_talents` are keyed by `game_version`; never write a migration that deletes or rewrites old-version rows (ADR-0007).
- The two partial unique indexes (snapshot `content_hash` dedupe, `sim_jobs (profile_hash, simc_version) WHERE status='complete'`) are the product's cost model. A migration touching them needs a test that proves duplicates are still rejected.
