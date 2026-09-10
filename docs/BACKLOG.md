# Backlog — M0 and M1

Agent-sized tickets. Each is independently reviewable and has a verifiable acceptance criterion. Dependencies are explicit; anything with no unmet dependency can be worked in parallel.

Estimates assume one agent per ticket. `S` = under half a day, `M` = about a day, `L` = multiple days.

**Status lives in the heading:** `## [ ] M1-02 — …` is open, `## [x] …` is done. A ticket also counts as done when a merged PR title carries its id in parentheses. Implementers never edit this file; the conductor ticks headings in a batched `docs(backlog)` PR. A grader ticket carries a `T` suffix (`M1-02T`, **[TEST]**, test-writer) and lands before its implementation twin (`M1-02`, **[IMPL]**, implementer) — see ADR-0013. Tickets marked **owner** need the repository owner for credentials, real captures, or a decision.

---

# M0 — Foundation

## [ ] M0-01 — Verify Blizzard talent loadout availability
**Size:** S · **Depends on:** nothing · **Blocks:** M3 scoping · **owner** (Blizzard client credentials)

Obtain Blizzard API credentials. Run the client-credentials OAuth flow and query the Character Specializations endpoint for a real level-90 character. Determine whether the `loadouts` field is present in patch 12.1.

Record the result as an amendment to ADR-0005 in `docs/DECISIONS.md` (`/adr amend 0005 "…"`), including the raw response shape. If loadouts are present, additionally test an **Evoker** character — Evoker and Priest loadout strings historically had a different shape from other classes. Strip the bearer token before saving any capture.

**Acceptance:** ADR-0005 amended with a dated finding and the raw JSON (token-free) captured to `api/tests/fixtures/blizzard/`. A dated row in the `docs/DATA_SOURCES.md` breakage log.

*This is the first ticket. Do it before anything else — the answer changes M3's scope.*

---

## [x] M0-02 — Repo scaffold and CI
**Size:** M · **Depends on:** nothing

Monorepo layout per `docs/IMPLEMENTATION_PLAN.md` §12. Python project under `api/` with Ruff, mypy strict, pytest. `Makefile` exposing `lint`, `test`, `test-parser`, `up`, `migrate`. GitHub Actions running lint and test on push and PR. Pre-commit hooks for Ruff.

**Acceptance:** a trivial PR runs CI green. `make lint` and `make test` work from a clean clone.

*Done in the harness PR, together with the agent harness (`.claude/`, `tests/harness/`, `.github/`) — ADR-0013, ADR-0014.*

---

## [ ] M0-03 — Local development stack
**Size:** M · **Depends on:** M0-02

`docker-compose.yml` with Postgres 16, Redis, the API service, and one worker container. `.env.example` documents every required variable with a comment explaining where to obtain it. `make up` brings the stack to a healthy state. Honour the per-worktree `COMPOSE_PROJECT_NAME`, `DB_PORT`, `REDIS_PORT`, `API_PORT` the Makefile exports (`make env`) so two worktrees can run stacks concurrently.

**Acceptance:** `cp .env.example .env && make up` gives a responding `/health` endpoint against a live database on a machine that has never run this project. Two checkouts can run `make up` at the same time.

*Blocked on this machine until the Docker Compose plugin is installed (`docs/SETUP.md`); CI cannot verify it either. The implementer writes it against the Compose spec and the conductor verifies locally once the plugin exists.*

---

## [ ] M0-04 — Schema and migrations
**Size:** M · **Depends on:** M0-03

Alembic wired (`/migration`). Initial migration implementing the full schema from `docs/IMPLEMENTATION_PLAN.md` §7, including both partial unique indexes (snapshot content hash dedupe, sim job profile-hash cache). SQLAlchemy 2.0 models mirroring it.

**Acceptance:** `make migrate` applies cleanly to an empty database and `alembic downgrade base` reverses cleanly; the up/down/up transcript is in the PR. A test asserts both unique indexes reject duplicates.

---

## [ ] M0-05 — Blizzard API client
**Size:** M · **Depends on:** M0-02, M0-01

Client-credentials OAuth with token caching and refresh-before-expiry. Typed wrappers for: character profile, equipment, specializations, mythic-keystone-profile, character-media. Rate limiter respecting documented per-client limits. Fixture-replay test harness.

**Acceptance:** unit tests pass against recorded fixtures with no network access. A manually-tagged `@pytest.mark.live` test successfully fetches a real character.

---

## [x] M0-06 — Documentation seed
**Size:** S · **Depends on:** M0-02

Commit `AGENTS.md`/`CLAUDE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/DECISIONS.md`, `docs/GLOSSARY.md`, and this backlog. Create `docs/DATA_SOURCES.md` and `docs/SIMC_FORMAT.md`.

**Acceptance:** files present, `README.md` links to them, `CLAUDE.md` is at the repo root.

*Done in the harness PR; `DATA_SOURCES.md` and `SIMC_FORMAT.md` are seeded, not stubs. Added `docs/PRODUCT.md`, `docs/AGENT_WORKFLOW.md`, `docs/SETUP.md`.*

---

## [x] M0-07 — Repository settings and required checks
**Size:** S · **Depends on:** M0-02 · **owner** (repo admin, API key)

Apply `scripts/bootstrap-github.sh` (squash-only, ruleset with the required checks from `.github/rulesets/main.json`, secret scanning and push protection, Dependabot security updates, labels). Add the `ANTHROPIC_API_KEY` repository secret so `claude.yml` and `claude-review.yml` run.

**Acceptance:** `gh api repos/sandgraal/wowlab/rulesets` lists an active `main` ruleset; a PR shows every required check; a push to `main` is rejected; the automated review comments on a newly opened PR.

---

# M1 — Ingest and character page

## [ ] M1-01 — Collect the SimC fixture corpus
**Size:** M · **Depends on:** M0-02 · **Blocks:** M1-02T · **owner** (real exports)

Gather **real** `/simc` export strings — not constructed examples — through `/fixture`, which records provenance and consent and scans for secrets. Minimum coverage: one per class (13), plus specific edge cases: a character with crafted gear carrying `crafted_stats`, one with empty sockets, one with tertiary stats, one with items in the bag section, one Evoker, one with a profession line, **one exported with the Great Vault window open** (`### Weekly Reward Choices`, ADR-0015), one with `### Saved Loadouts`, one healer, one tank.

Consent per fixture: `owner`, `explicit`, or `public-post`. Names and realms may be rewritten (`--rewrite-identity`); nothing else may. SimulationCraft's own `profiles/` directory may seed profile-format tests but does **not** count toward this corpus — those are not addon exports.

**Acceptance:** at least 20 real fixtures indexed in `api/tests/fixtures/simc/README.md`; `make test-parser` green; any fixture whose provenance is not a real export is rejected.

*The long pole on wall-clock time. Start on day one; the owner's own characters first, then guildmates with explicit consent.*

---

## [ ] M1-02T — Parser graders [TEST]
**Size:** M · **Depends on:** M1-01 (at least the owner's fixtures) · **Blocks:** M1-02

`test-writer` derives graders from `docs/IMPLEMENTATION_PLAN.md` §8.1, `docs/SIMC_FORMAT.md`, and the fixtures: class read from the key of the name line for every class key; slot sub-attributes as an open set with unknown keys preserved; bag, vault, saved-loadout and additional-info sections parsed into separate collections; `_raw` per slot; header treated as advisory; parser failures capture the input. All as `xfail(strict=True)` with one marker line each.

**Acceptance:** `make test-parser` reports the graders as expected failures with zero errors; stubs (if any) raise `NotImplementedError("M1-02")`.

---

## [ ] M1-02 — SimC parser [IMPL]
**Size:** L · **Depends on:** M1-02T (graders merged)

Implement `api/src/bronze_api/services/simc_parser.py` per `docs/IMPLEMENTATION_PLAN.md` §8.1 and `docs/SIMC_FORMAT.md`.

Requirements: class is read from the *key* of the name line via a class-key map, never positionally. Slot sub-attribute keys are treated as an open set — unknown keys are preserved verbatim in the parsed payload, never dropped. Comment sections (`### Gear from Bags`, `### Weekly Reward Choices`, `### Saved Loadouts`, `### Additional Character Info`) parse into `parsed.bags`, `parsed.vault`, `parsed.loadouts`, `parsed.extra` (shape proposed in ADR-0015). Output matches the canonical `parsed` shape in §8.1 plus those additions. Legacy keys (`covenant`, `soulbind`) are tolerated and preserved.

**Acceptance:** every fixture parses without error; graders activated by marker deletion only; a test asserts unknown sub-attribute keys survive a parse round-trip; parser failures capture the failing input.

---

## [ ] M1-03 — Determinism and round-trip tests
**Size:** M · **Depends on:** M1-02

Two test suites, both gates on any parser or profile-builder change (written by `test-writer`; profile builder implemented by a different agent).

*Determinism:* generating a profile from the same snapshot and options 100 times produces byte-identical output. Explicitly exercise dict ordering and float formatting under a non-C locale.

*Round-trip:* parse a fixture, regenerate a SimC profile from the parsed form, and assert the regenerated profile produces a sim result statistically indistinguishable from the original string's result. (Skipped until M2 provides sim execution; write it now and mark it `xfail` with a reason.)

**Acceptance:** determinism suite green and wired into `make test-parser`. Round-trip suite written and correctly skipped.

---

## [ ] M1-04 — Snapshot ingest endpoint
**Size:** M · **Depends on:** M1-02, M0-04

`POST /v1/ingest/simc`. Parse, canonicalize, hash, create or find the character, insert the snapshot. Store `simc_raw` unconditionally. Idempotent by content hash — re-posting identical state returns the existing `snapshot_id` with HTTP 200. Bound the request size.

Canonicalization must strip the export timestamp and comments from the string before hashing (`docs/SIMC_FORMAT.md`), or every paste creates a new row.

**Acceptance:** posting the same fixture twice yields one snapshot row and two 200s with the same id. Posting a modified fixture yields a second row. An oversized body is rejected with 413.

---

## [ ] M1-05 — Static game data pipeline
**Size:** L · **Depends on:** M0-04

Ingest SimC's generated item and spell data for one pinned patch version into `game_items` and `game_spells`, keyed by `game_version`. Idempotent re-run. A CLI entry point that takes a SimC commit SHA (SimC no longer tags releases; ADR-0006 amendment 2026-09-10) and derives `game_version` from `client_data_version.inc` at that commit. Lives in `pipeline/` as a workspace member.

**Acceptance:** running the pipeline for 12.1 populates the tables; re-running changes nothing. A test asserts that ingesting a second version leaves the first version's rows untouched.

---

## [ ] M1-06 — Character read API
**Size:** M · **Depends on:** M1-04, M1-05

`GET /v1/characters/{region}/{realm}/{name}` returning the character plus its latest snapshot with item names and icons resolved from `game_items` at the snapshot's `game_version`. `GET /v1/characters/{id}/snapshots` cursor-paginated. `GET /v1/snapshots/{id}`. Every response carries the snapshot's `source` and `captured_at` so the UI can show fidelity.

**Acceptance:** a character page payload renders complete gear with resolved names for every fixture in the corpus.

---

## [ ] M1-07 — Snapshot diff
**Size:** M · **Depends on:** M1-06

`GET /v1/snapshots/{a}/diff/{b}`. Structural diff over equipped gear: slots added, removed, changed. A slot counts as changed if item id, bonus ids, gems, enchant, or crafted stats differ — not item id alone. Also report talent string change and item level delta. `domain-reviewer` required.

**Acceptance:** two snapshots differing only in one item's bonus IDs produce a non-empty diff. Identical snapshots produce an empty one.

---

## [ ] M1-08 — Web character page
**Size:** L · **Depends on:** M1-06, M1-07

Next.js under `web/` (pnpm, TypeScript strict; this ticket also makes the `web-quality` CI job substantive). Paste box on the landing page whose copy tells the player to open the Great Vault before typing `/simc`. Character page showing equipped gear by slot with icons, item levels, gems, enchants (Wowhead tooltips); spec and class; snapshot history list with source and age; diff view between any two snapshots. Dark theme; class and quality color tokens with accessible variants; Open Graph card per page.

No auth in M1 — characters are public and unclaimed.

**Acceptance:** a person who has never seen the project can paste a string and understand their character page without explanation. Every item of the `docs/PRODUCT.md` quality bar (states, uncertainty where applicable, accessibility, mobile, shareable, copy, fidelity) is checked by `domain-reviewer`. A parser failure shows the offending line and a report action.

---

## [ ] M1-09 — Observability baseline
**Size:** M · **Depends on:** M1-04

Structured logging. Metrics for: ingest success rate by source, parser failure rate, endpoint latency. **Parser failures must capture the failing input string** to durable storage — this is the earliest warning of a patch changing the format, and losing the input makes the failure undiagnosable.

**Acceptance:** a deliberately malformed input produces a captured artifact and an incremented failure metric.

---

## [ ] M1-10 — Public read-only API surface
**Size:** M · **Depends on:** M1-06, M1-07

ADR-0017. Serve the OpenAPI document for the read endpoints (character, snapshots list and detail, diff) and link it from the web footer. Add an unauthenticated per-IP rate limit and an optional free API key that raises it; keys are opaque, revocable rows, not user accounts. Every response keeps the `source` and `captured_at` fidelity fields. An `/api` page restates the attribution requirements Bronze inherits from Blizzard, Wowhead and Raider.IO. Write endpoints are excluded from the published document.

**Acceptance:** `GET /openapi.json` lists only read endpoints; a burst over the limit returns 429 with a `Retry-After`; a request with a valid key gets the higher limit; the `/api` page renders attribution text. Reviewed by `security-reviewer` (rate limits and key handling).

---

# Parallelization

After M0-02 (done), these can run concurrently:

- M0-03 → M0-04 (sequential; M0-03 needs the Compose plugin on the verifying machine)
- M0-05 (after the owner completes M0-01)
- M0-07 (owner)
- M1-01 (owner; start immediately — collecting real fixtures has human latency)

After M1-02 [IMPL] lands: M1-03, M1-04, and M1-05 are independent of each other.

M1-10 follows M1-06 and M1-07 and is independent of M1-08 and M1-09.

M1-01 is the long pole on wall-clock time because it needs real exports from real characters across thirteen classes. Start it on day one regardless of what else is in flight.

---

# Definition of done

A ticket is done when: CI is green on every required check; each acceptance criterion has pasted proof; `mypy --strict` passes; new external-format parsing is covered by a real fixture with a provenance row; any change to a load-bearing file has `make test-parser` green with graders activated by marker deletion only; schema changes ship as a migration that downgrades cleanly with the transcript attached; the independent review verdict is clean; every review thread was replied to and resolved; and anything contradicting an existing ADR ships with a superseding ADR (`Proposed`) rather than a silent deviation. Full lifecycle: `docs/AGENT_WORKFLOW.md`.
