# Bronze — Constitution

This file wins over every other instruction in the repository. `CLAUDE.md`
imports it. Read it, then `docs/IMPLEMENTATION_PLAN.md`, before touching
anything.

## What this is

A persistent character workbench for World of Warcraft. Users hand us their
character state; we store it immutably, run SimulationCraft against it, and
give them planning tools on top. The differentiators are memory (snapshot
history), free unlimited sims (self-hosted SimC with aggressive caching), and
joining sim projections against real combat logs. `docs/PRODUCT.md` says who
it is for and what "good" looks like to a player.

## Hard invariants

Violating any of these is a bug even if tests pass.

**Snapshots are immutable.** There is no `UPDATE` on the `snapshots` table.
New character state is a new row. History is the product.

**We do not compute item stats.** Upgrade tracks, crafted quality tiers,
tertiary stats, set bonuses, and scaling curves are SimC's job. If you find
yourself writing arithmetic on item stats, stop — you are reimplementing a
solved problem incorrectly. We store and present; SimC computes.

**Profile generation must be byte-deterministic.** Same snapshot plus same
options must produce a byte-identical SimC profile every time. No
timestamps, no unordered dict iteration, no locale-dependent float
formatting. The sim cache — and therefore the entire cost model — depends on
this. There is a test for it. Do not weaken the test.

**Game data is versioned, never overwritten.** `game_items`, `game_spells`,
`game_talents` are keyed by `game_version`. A snapshot from three patches
ago must still render. Never write a migration that drops old-version rows.

**Preserve raw input unconditionally.** `snapshots.simc_raw` holds the
original string even when parsing succeeds. If the parser turns out to be
wrong, we reparse history.

**Unknown fields are preserved, not dropped.** The SimC string format gains
sub-attributes on patch releases. Unrecognized keys go into the parsed
payload verbatim. A parser that silently discards what it does not recognize
loses user data on patch day.

## Where truth lives

- `docs/IMPLEMENTATION_PLAN.md` — architecture, data model, milestones. The spec.
- `docs/DECISIONS.md` — ADRs. Read before proposing an alternative. Status is
  `Proposed` until the repository owner flips it to `Accepted`; no agent does.
- `docs/BACKLOG.md` — agent-sized tickets with acceptance criteria. Ticket
  headings carry status (`## [ ] M1-02 — …`). Implementers never edit it.
- `docs/GLOSSARY.md` — WoW domain terms. Several are counterintuitive.
- `docs/PRODUCT.md` — personas, the weekly flow, UX principles, quality bar.
- `docs/AGENT_WORKFLOW.md` — how work moves: roles, lifecycle, conventions.
- `docs/DATA_SOURCES.md` — per-API auth, limits, breakage log.
- `docs/SIMC_FORMAT.md` — parser reference and fixture index rules.
- `docs/SETUP.md` — machine setup. Machine-specific notes go in `CLAUDE.local.md` (gitignored).

## Operating mode: conductor

The main session **orchestrates; it does not implement** (ADR-0013). Work
starts with `/conduct next` (or ticket ids, or `milestone M1`). Feature code
is written by the `implementer` agent and graders by `test-writer`, each in
its own worktree; `code-reviewer` grades every branch spec-first;
`domain-reviewer` and `security-reviewer` are added by area; `pr-shepherd`
opens the PR, resolves every thread, and **merges autonomously** once the
required checks are green, threads are resolved, and reviews are clean.

- The conductor's only source edits are harness (`.claude/`), docs, backlog
  ticks, and handoffs. Everything else goes to an agent, including fixes for
  review comments (`SendMessage` to the implementer that has the context).
- Parallel is the default. Dispatch every independent eligible ticket at
  once; holding one back needs a named file or interface collision.
- Test-writer / implementer separation is mandatory for the four
  load-bearing files (`simc_parser.py`, `profile_builder.py`,
  `talent_codec.py`, `gap_analysis.py`) and for migrations. The session that
  writes the code never writes, edits, or weakens the tests that grade it.
- Subagents never edit `.claude/`, `AGENTS.md`, `CLAUDE.md`,
  `docs/DECISIONS.md`, or `docs/BACKLOG.md`; a hook enforces it. They report
  the need instead.

## Conventions

Python 3.12, FastAPI, SQLAlchemy 2.0 style, Pydantic v2. uv workspace, ruff
for lint and format, `mypy --strict` on `api/src/`. Next.js + TypeScript
strict under `web/` (pnpm). Go for `agent/`. Alembic for every schema change;
never edit a merged migration.

Tests use real fixtures, not generated examples. For anything parsing an
external format, the fixture must be a real capture from the real source,
with a provenance and consent row. A test that passes against a made-up
`/simc` string proves nothing.

- Branches: `m<milestone>/<nn>-<slug>` — `m1/02-simc-parser`, `m1/02-simc-parser-tests`.
- Commits: `type(scope): summary (M1-02)`. Squash-merged; small commits are fine.
- PRs: draft on first push, ready when reviews are clean, body from the
  template with proof per acceptance criterion and a `session:` footer.
- `.claude/rules/` carries area-specific rules that load when you open a file
  in that area. They extend this file; they never override it.

## Verification

```bash
make setup         # uv sync, pre-commit, .env
make lint          # ruff check + format --check + mypy (the commit gate)
make test          # pytest, all suites except @live
make test-parser   # parser fixtures + determinism + round-trip (run on any parser change)
make hooks-test    # harness hooks under 3.12 and 3.9
make ci            # all of the above
make up / migrate  # local stack (M0-03) / alembic upgrade head (M0-04)
```

A change to a load-bearing file requires `make test-parser` green before
review. A ticket is done when CI is green, acceptance criteria have pasted
proof, new external-format parsing has a real fixture, schema changes ship
as a migration that downgrades cleanly, and anything contradicting an ADR
ships with a superseding ADR rather than a silent deviation.

## Anti-patterns specific to this codebase

**Do not hit live external APIs from tests or CI.** Rate limits are shared,
finite, and per-client. Record responses as fixtures and replay them.

**Do not add a "refresh" that mutates a character row.** Refresh creates a
snapshot.

**Do not build features that require the Blizzard API to be up.** Talent
loadouts vanished from the Profile API in patch 11.2 and stayed gone. Every
core feature must work with zero Blizzard availability via `/simc` and the
companion agent.

**Do not attempt Raidbots sim submission.** Report *reading* is supported.
Submission would mean hitting undocumented internal endpoints.

**Do not execute Lua.** The companion agent parses SavedVariables as data
with a constrained literal parser — no interpreter, no `load()`. Reject
function definitions and metatables.

**Do not write confident analysis without evidence.** "Improve your uptime"
is not shippable. "Ebon Might uptime 84% vs sim 99%, estimated 6.2% of the
27% gap" is.

**Do not tune iteration counts to make a test pass.** Set `deterministic=1`
for comparison sims instead.

**Do not ship a number without its uncertainty.** Two options within noise
are a tie, not a ranking (`docs/PRODUCT.md`).

## Stop and ask

Escalate rather than deciding unilaterally when:

- A schema change would touch `snapshots` in a way that is not purely additive.
- An external API returns a shape that does not match recorded fixtures —
  usually a patch landed; the fix is a data-pipeline decision.
- The talent string decoder meets an unknown serialization version. Fail
  loudly; do not guess.
- A feature seems to require computing item stats ourselves. It almost
  certainly does not.
- Anything touches user credentials, the companion agent's filesystem scope,
  or binary distribution.
- An ADR needs to move from Proposed to Accepted, or be superseded.
- Reviewer and implementer still disagree after two fix rounds.

## Domain warning

Several things in WoW are named misleadingly and will be modeled wrong by
anyone reasoning from the names. "Item level" is not a level. "Bonus IDs"
are not bonuses; they are identity. "Loadout" and "spec" are different
things and neither is a stable property of a character. A "vault" refreshes
weekly and contains choices, not items. Read `docs/GLOSSARY.md` before
designing anything that touches these.
