# Backlog — M0 and M1

Agent-sized tickets. Each is independently reviewable and has a verifiable acceptance criterion. Dependencies are explicit; anything with no unmet dependency can be worked in parallel.

Estimates assume one agent per ticket. `S` = under half a day, `M` = about a day, `L` = multiple days.

---

# M0 — Foundation

## M0-01 — Verify Blizzard talent loadout availability
**Size:** S · **Depends on:** nothing · **Blocks:** M3 scoping

Obtain Blizzard API credentials. Run the client-credentials OAuth flow and query the Character Specializations endpoint for a real level-90 character. Determine whether the `loadouts` field is present in patch 12.1.

Record the result as an amendment to ADR-0005 in `docs/DECISIONS.md`, including the raw response shape. If loadouts are present, additionally test an **Evoker** character — Evoker and Priest loadout strings historically had a different shape from other classes.

**Acceptance:** ADR-0005 amended with a dated finding and the raw JSON captured to `api/tests/fixtures/blizzard/`.

*This is the first ticket. Do it before anything else — the answer changes M3's scope.*

---

## M0-02 — Repo scaffold and CI
**Size:** M · **Depends on:** nothing

Monorepo layout per `docs/IMPLEMENTATION_PLAN.md` §12. Python project under `api/` with Ruff, mypy strict, pytest. `Makefile` exposing `lint`, `test`, `test-parser`, `up`, `migrate`. GitHub Actions running lint and test on push and PR. Pre-commit hooks for Ruff.

**Acceptance:** a trivial PR runs CI green. `make lint` and `make test` work from a clean clone.

---

## M0-03 — Local development stack
**Size:** M · **Depends on:** M0-02

`docker-compose.yml` with Postgres 16, Redis, the API service, and one worker container. `.env.example` documenting every required variable with a comment explaining where to obtain it. `make up` brings the stack to a healthy state.

**Acceptance:** `cp .env.example .env && make up` gives a responding `/health` endpoint against a live database on a machine that has never run this project.

---

## M0-04 — Schema and migrations
**Size:** M · **Depends on:** M0-03

Alembic wired. Initial migration implementing the full schema from `docs/IMPLEMENTATION_PLAN.md` §7, including both partial unique indexes (snapshot content hash dedupe, sim job profile-hash cache). SQLAlchemy 2.0 models mirroring it.

**Acceptance:** `make migrate` applies cleanly to an empty database and `alembic downgrade base` reverses cleanly. A test asserts both unique indexes reject duplicates.

---

## M0-05 — Blizzard API client
**Size:** M · **Depends on:** M0-02, M0-01

Client-credentials OAuth with token caching and refresh-before-expiry. Typed wrappers for: character profile, equipment, specializations, mythic-keystone-profile, character-media. Rate limiter respecting documented per-client limits. Fixture-replay test harness.

**Acceptance:** unit tests pass against recorded fixtures with no network access. A manually-tagged live test successfully fetches a real character.

---

## M0-06 — Documentation seed
**Size:** S · **Depends on:** M0-02

Commit `CLAUDE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/DECISIONS.md`, `docs/GLOSSARY.md`, and this backlog. Create `docs/DATA_SOURCES.md` and `docs/SIMC_FORMAT.md` as stubs with headings.

**Acceptance:** files present, `README.md` links to them, `CLAUDE.md` is at the repo root.

---

# M1 — Ingest and character page

## M1-01 — Collect the SimC fixture corpus
**Size:** M · **Depends on:** M0-02 · **Blocks:** M1-02

Gather **real** `/simc` export strings — not constructed examples. Minimum coverage: one per class (13), plus specific edge cases: a character with crafted gear carrying `crafted_stats`, one with empty sockets, one with tertiary stats, one with items in the bag section, one Evoker, and one with a profession line.

Store under `api/tests/fixtures/simc/` with a `README.md` indexing what each fixture exercises and where it came from.

**Acceptance:** at least 18 real fixtures, indexed. Any fixture whose provenance is not a real export is rejected.

---

## M1-02 — SimC parser
**Size:** L · **Depends on:** M1-01

Implement `api/src/bronze_api/services/simc_parser.py` per `docs/IMPLEMENTATION_PLAN.md` §8.1.

Requirements: class is read from the *key* of the name line via a class-key map, never positionally. Slot sub-attribute keys are treated as an open set — unknown keys are preserved verbatim in the parsed payload, never dropped. Commented lines under `### Gear from Bags` parse into a separate collection. Output matches the canonical `parsed` shape in §8.1.

**Acceptance:** every fixture parses without error. A test asserts unknown sub-attribute keys survive a parse round-trip. Parser failures capture the failing input.

---

## M1-03 — Determinism and round-trip tests
**Size:** M · **Depends on:** M1-02

Two test suites, both gates on any parser or profile-builder change.

*Determinism:* generating a profile from the same snapshot and options 100 times produces byte-identical output. Explicitly exercise dict ordering and float formatting.

*Round-trip:* parse a fixture, regenerate a SimC profile from the parsed form, and assert the regenerated profile produces a sim result statistically indistinguishable from the original string's result. (This suite is skipped until M2 provides sim execution; write it now and mark it `xfail` with a reason.)

**Acceptance:** determinism suite green and wired into `make test-parser`. Round-trip suite written and correctly skipped.

---

## M1-04 — Snapshot ingest endpoint
**Size:** M · **Depends on:** M1-02, M0-04

`POST /v1/ingest/simc`. Parse, canonicalize, hash, create or find the character, insert the snapshot. Store `simc_raw` unconditionally. Idempotent by content hash — re-posting identical state returns the existing `snapshot_id` with HTTP 200.

Canonicalization must strip the export timestamp and comments from the string before hashing, or every paste creates a new row.

**Acceptance:** posting the same fixture twice yields one snapshot row and two 200s with the same id. Posting a modified fixture yields a second row.

---

## M1-05 — Static game data pipeline
**Size:** L · **Depends on:** M0-04

Ingest SimC's generated item and spell data for one pinned patch version into `game_items` and `game_spells`, keyed by `game_version`. Idempotent re-run. A CLI entry point that takes a SimC tag.

**Acceptance:** running the pipeline for 12.1 populates the tables; re-running changes nothing. A test asserts that ingesting a second version leaves the first version's rows untouched.

---

## M1-06 — Character read API
**Size:** M · **Depends on:** M1-04, M1-05

`GET /v1/characters/{region}/{realm}/{name}` returning the character plus its latest snapshot with item names and icons resolved from `game_items` at the snapshot's `game_version`. `GET /v1/characters/{id}/snapshots` cursor-paginated. `GET /v1/snapshots/{id}`.

**Acceptance:** a character page payload renders complete gear with resolved names for every fixture in the corpus.

---

## M1-07 — Snapshot diff
**Size:** M · **Depends on:** M1-06

`GET /v1/snapshots/{a}/diff/{b}`. Structural diff over equipped gear: slots added, removed, changed. A slot counts as changed if item id, bonus ids, gems, enchant, or crafted stats differ — not item id alone. Also report talent string change and item level delta.

**Acceptance:** two snapshots differing only in one item's bonus IDs produce a non-empty diff. Identical snapshots produce an empty one.

---

## M1-08 — Web character page
**Size:** L · **Depends on:** M1-06, M1-07

Next.js. Paste box for a `/simc` string. Character page showing equipped gear by slot with icons, item levels, gems, enchants; spec and class; snapshot history list; diff view between any two snapshots.

No auth in M1 — characters are public and unclaimed.

**Acceptance:** a person who has never seen the project can paste a string and understand their character page without explanation.

---

## M1-09 — Observability baseline
**Size:** M · **Depends on:** M1-04

Structured logging. Metrics for: ingest success rate by source, parser failure rate, endpoint latency. **Parser failures must capture the failing input string** to durable storage — this is the earliest warning of a patch changing the format, and losing the input makes the failure undiagnosable.

**Acceptance:** a deliberately malformed input produces a captured artifact and an incremented failure metric.

---

# Parallelization

After M0-02 lands, these can run concurrently:

- M0-03 → M0-04 (sequential)
- M0-05 (independent)
- M0-06 (independent)
- M1-01 (independent, and should start immediately — collecting real fixtures has human latency)

After M1-02 lands: M1-03, M1-04, and M1-05 are independent of each other.

M1-01 is the long pole on wall-clock time because it needs real exports from real characters across thirteen classes. Start it on day one regardless of what else is in flight.

---

# Definition of done

A ticket is done when: CI is green; `mypy --strict` passes on changed files; new external-format parsing is covered by a real fixture; any change to `simc_parser.py`, `profile_builder.py`, or `talent_codec.py` has `make test-parser` green; schema changes ship as a migration that downgrades cleanly; and anything contradicting an existing ADR ships with a superseding ADR rather than a silent deviation.
