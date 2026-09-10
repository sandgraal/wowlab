# Bronze — Agent Operating Context

Read this before touching anything. Then read `docs/IMPLEMENTATION_PLAN.md`.

## What this is

A persistent character workbench for World of Warcraft. Users hand us their character state; we store it immutably, run SimulationCraft against it, and give them planning tools on top. The differentiators are memory (snapshot history), free unlimited sims (self-hosted SimC with aggressive caching), and joining sim projections against real combat logs.

## Hard invariants

Violating any of these is a bug even if tests pass.

**Snapshots are immutable.** There is no `UPDATE` on the `snapshots` table. New character state is a new row. History is the product; anything that mutates it destroys the feature.

**We do not compute item stats.** Upgrade tracks, crafted quality tiers, tertiary stats, set bonuses, and scaling curves are SimC's job. If you find yourself writing arithmetic on item stats, stop — you are reimplementing a solved problem incorrectly. We store and present; SimC computes.

**Profile generation must be byte-deterministic.** Same snapshot plus same options must produce a byte-identical SimC profile every time. No timestamps, no unordered dict iteration, no locale-dependent float formatting. The sim cache — and therefore the entire cost model — depends on this. There is a test for it. Do not weaken the test.

**Game data is versioned, never overwritten.** `game_items`, `game_spells`, `game_talents` are keyed by `game_version`. A snapshot from three patches ago must still render. Never write a migration that drops old-version rows.

**Preserve raw input unconditionally.** `snapshots.simc_raw` holds the original string even when parsing succeeds. If the parser turns out to be wrong, we reparse history. Never store only the parsed form.

**Unknown fields are preserved, not dropped.** The SimC string format gains sub-attributes on patch releases. Unrecognized keys go into the parsed payload verbatim. A parser that silently discards data it doesn't recognize will lose user data on patch day.

## Where truth lives

- `docs/IMPLEMENTATION_PLAN.md` — architecture, data model, milestones. The spec.
- `docs/DECISIONS.md` — decisions already made, with reasoning. Read before proposing an alternative to something in the plan.
- `docs/GLOSSARY.md` — WoW domain terms. Read this if you have not played the game. Several concepts are counterintuitive and modeling them wrong is expensive.
- `docs/DATA_SOURCES.md` — per-API auth, rate limits, known breakage.
- `docs/SIMC_FORMAT.md` — the parser reference and fixture index.

## Conventions

Python 3.12, FastAPI, SQLAlchemy 2.0 style, Pydantic v2. Ruff for lint and format. Full type annotations; `mypy --strict` on `api/src/`.

Migrations via Alembic. Every schema change is a migration. Never edit a migration that has been merged.

Tests use real fixtures, not generated examples. For anything parsing external formats, the fixture must be a real capture from the real source. A test that passes against a made-up `/simc` string proves nothing.

Commits are scoped and describe intent. Branch naming: `m1/simc-parser`, `m2/sim-worker`.

## Verification

```bash
make lint          # ruff check + format --check + mypy
make test          # pytest, all suites
make test-parser   # parser fixtures + round-trip (run on any parser change)
make up            # docker compose local stack
make migrate       # alembic upgrade head
```

A change to `simc_parser.py`, `profile_builder.py`, or `talent_codec.py` requires `make test-parser` green before review. These three files are load-bearing.

## Anti-patterns specific to this codebase

**Do not hit live external APIs from tests or CI.** Rate limits are shared, finite, and per-client. Record responses as fixtures and replay them. Burning the Warcraft Logs point budget in CI breaks production ingest.

**Do not add a "refresh" that mutates a character row.** Refresh creates a snapshot. If you need current state, query the latest snapshot.

**Do not build features that require the Blizzard API to be up.** Blizzard has been progressively restricting API access; talent loadouts vanished from the Profile API in patch 11.2 and stayed gone. Every core feature must work with zero Blizzard availability via the `/simc` and companion paths.

**Do not attempt Raidbots sim submission.** There is no sanctioned programmatic path. Report *reading* is fine and supported. Submission would mean hitting undocumented internal endpoints.

**Do not execute Lua.** The companion agent parses SavedVariables as data. A constrained literal parser only — no interpreter, no `load()`, no eval-equivalent. Reject function definitions and metatables.

**Do not write confident analysis without evidence.** Any gap-analysis output must cite the specific numbers that produced it. "Improve your uptime" is not shippable. "Ebon Might uptime 84% vs sim 99%, estimated 6.2% of the 27% gap" is.

**Do not tune iteration counts to make a test pass.** Sim results have real variance. If a comparison test is flaky, set `deterministic=1` in the profile options — do not raise iterations until the flake goes away.

## Stop and ask

Escalate rather than deciding unilaterally when:

- A schema change would touch `snapshots` in a way that isn't purely additive.
- An external API returns a shape that doesn't match our recorded fixtures — this usually means a patch landed and the fix is a data-pipeline decision, not a code patch.
- The talent string decoder encounters an unknown serialization version. Fail loudly; do not guess at the format.
- A feature seems to require computing item stats ourselves. It almost certainly doesn't.
- Anything touches user credentials, the companion agent's filesystem access scope, or binary distribution.

## Domain warning

Several things in WoW are named misleadingly and will be modeled wrong by anyone reasoning from the names alone. "Item level" is not a level. "Bonus IDs" are not bonuses. "Loadout" and "spec" are different things. A character's "vault" refreshes weekly and contains choices, not items. Read `docs/GLOSSARY.md` before designing any schema that touches these.
