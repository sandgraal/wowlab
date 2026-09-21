# Backlog — M0, M1 and M10

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

*Built and merged in PR #5 (2026-09-10), not yet accepted. Proven there: the Compose file renders against the Compose spec for two per-worktree value sets that differ only in project name, published ports, network and volume; `tests/stack` (39 graders) covers services, loopback binds, healthchecks, the SimC pin and `.env.example`; `make ci` green. **Not executed:** `cp .env.example .env && make up` reaching a healthy `/health`, and two checkouts running `make up` at once, because this machine has no Docker Compose plugin (`docs/SETUP.md`) and CI does not build the images. The box stays open until the conductor runs both on a machine with the plugin and pastes the transcript into a `docs(backlog)` PR. M0-04 does not wait on this: its migration proof comes from CI's Postgres service (`docs/handoffs/M0-04.md`).*

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
**Size:** L · **Depends on:** M0-04 · **owner** (ADR-0007 within-patch hotfix re-ingest decision; blocks dispatch)

Ingest SimC's generated item and spell data for one pinned patch version into `game_items` and `game_spells`, keyed by `game_version`. Idempotent re-run. A CLI entry point that takes a SimC commit SHA (SimC no longer tags releases; ADR-0006 amendment 2026-09-10), derives the `game_version` patch triple by truncating `CLIENT_DATA_WOW_VERSION` in `client_data_version.inc` at that commit, and records the full build, hotfix date, and SHA in an additive per-version manifest. The within-patch hotfix re-ingest rule is an open owner decision on ADR-0007 (the **owner** marker above); do not dispatch until it is made. Lives in `pipeline/` as a workspace member.

**Acceptance:** running the pipeline for 12.1 populates the tables; re-running **at the same SimC SHA** changes nothing (a re-run at a newer SHA within the same patch follows the ADR-0007 decision, not this line). A test asserts that ingesting a second version leaves the first version's rows untouched.

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

# M10 — Lab core library (Wave 1)

Spec: `docs/LAB_PLAN.md`. Formats: `docs/LAB_FORMATS.md`. Paths: `docs/LAB_FILE_MAP.md`. Decisions: ADR-0019 to ADR-0024. Lab invariants L1–L8 (`docs/LAB_PLAN.md` §4) apply to every ticket below. This is the whole of Wave 1; nothing from `docs/LAB_IDEAS.md` is a ticket until the owner picks the next wave (ADR-0024).

## [ ] M10-01 — Lab scaffold and gates
**Size:** S · **Depends on:** nothing

Create `lab/core/` as uv workspace member `wowlab-core` (import `wowlab_core`, hatchling, `src/` layout, a `wowlab` console-script entry pointing at a Typer app that only implements `--version`). Wire it into the root: workspace `members`, root `dependencies`, `uv.sources`, mypy `files`, pytest `testpaths`, isort `known-first-party`. Extend `make test-parser` to also run `lab/core/tests/parser` under the existing `parser` marker, so the existing required check covers Lab parsers. Add `lab/README.md`, `lab/core/tests/fixtures/README.md` (index table: `file | kind | flavor | client_version | platform | captured_by | consent | scrub | edge_cases`), and `.gitignore` entries for Lab scratch. Add an architecture test asserting that nothing under `api/` or `worker/` imports `wowlab_core` and nothing under `lab/` imports `bronze_api` (AST scan, no imports executed). Add a non-required `lab (windows)` CI job that runs `pytest lab/core/tests` on `windows-latest`. Details and exact expected diffs: `docs/handoffs/M10-01.md`.

**Acceptance:** `make ci` green from a clean clone; `uv run wowlab --version` prints a version; `uv lock --check` clean; the architecture test fails when a forbidden import is added in a scratch commit (show the failing output, then remove it); the Windows job runs and is green. Reviewed by `security-reviewer` (touches `.github/`).

---

## [ ] M10-02 — Fixture capture and scrub tool
**Size:** M · **Depends on:** M10-01

`scripts/lab_capture.py`, standard library only, runnable as `uv run python scripts/lab_capture.py --root <install> --out lab/core/tests/fixtures/incoming/`. Copies the capture set in `docs/handoffs/M10-03.md` from a real install and scrubs it per `docs/LAB_PLAN.md` §8: stable pseudonyms for account folder, character and realm names (in paths and in file contents), identity CVars blanked, and a hard refusal (non-zero exit, nothing written for that file) if an email address, a BattleTag or an unmapped `Player-<n>-<hex>` GUID survives. Byte-level targeted replacement only; it never parses and re-serializes. Prints a provenance row per file ready to paste into the index. Opens the install read-only and writes only under `--out` (L1).

**Acceptance:** run against a synthetic install tree built in the test (our own layout, so constructed input is legitimate): pseudonyms are stable across files; untouched bytes are identical (assert on a diff of offsets); the three refusal cases refuse; `--dry-run` writes nothing. `gitleaks` config extended if the scrubbed output trips it for a benign reason, with the reason in the PR. Reviewed by `security-reviewer`.

---

## [ ] M10-03 — Capture the Lab fixture corpus
**Size:** S · **Depends on:** M10-02 · **owner** (needs the machine with the game installed)

Follow `docs/handoffs/M10-03.md`: log one character in and out on each installed flavor (retail; Forever beta if installed), run the capture tool, review the output, commit it with index rows. Record in `docs/DATA_SOURCES.md` (Local install section and breakage log) what the capture shows for every **[verify]** item in `docs/LAB_FORMATS.md` and `docs/LAB_FILE_MAP.md`: Forever's flavor folder, product code, version string, interface number, executable name, preferred TOC suffix, SavedVariables line endings per platform, and whether the Forever client writes a combat log.

**Acceptance:** the corpus covers the coverage list in the handoff; every file has an index row; `gitleaks` clean; `docs/LAB_FORMATS.md` has a dated amendment per **[verify]** item resolved or still open.

*Start this as soon as M10-02 merges. Everything that parses a client format waits on it.*

---

## [ ] M10-04T — luadata parser graders [TEST]
**Size:** M · **Depends on:** M10-03

Graders for `wowlab_core.luadata` parsing, derived from `docs/LAB_PLAN.md` §6.4, `docs/LAB_FORMATS.md` §4 and the real corpus: every real SavedVariables fixture parses; key order, key style, number source text and trailing comments are preserved; duplicates kept and flagged; `to_python()` behaviour; every rejection in §4.3 raises the typed error with line and column (constructed, labelled); depth, size and string bounds raise rather than truncate or crash; a 10 000-deep table raises without a `RecursionError` escaping. Marked `parser`, `xfail(strict=True, reason="M10-04 not implemented")`.

**Acceptance:** graders fail today for the asserted reason; `make test-parser` reports them as xfail; no implementation code.

---

## [ ] M10-04 — luadata parser [IMPL]
**Size:** L · **Depends on:** M10-04T

`lab/core/src/wowlab_core/luadata.py` per `docs/LAB_PLAN.md` §6.4 (parsing half). No Lua execution, no third-party parser (L3). Activate graders by deleting marker lines only.

**Acceptance:** `make test-parser` green with all M10-04T markers removed; parse time and peak RSS for the largest real fixture pasted in the PR, plus a generated 50 MB document (generator script in `lab/core/tests/`, labelled constructed) against the §6.4 target. If the target is missed, report numbers and stop (ADR-0020).

---

## [ ] M10-05 — Install discovery
**Size:** M · **Depends on:** M10-03

`wowlab_core.install` per `docs/LAB_PLAN.md` §6.1 and `docs/LAB_FORMATS.md` §1–§2. No flavor, product or version constants in library code (L6); a test greps the package for `_retail_`, `_classic` and `wow_classic` and fails on a hit outside comments.

**Acceptance:** real `.build.info` / `.flavor.info` fixtures from each captured platform resolve to the expected `Install`; unknown columns preserved; a flavor folder with no matching row is returned with `version=None`; `WOWLAB_WOW_ROOT` and explicit root override defaults; a root without `.build.info` raises the typed error; nothing is written anywhere (assert on a read-only temp tree).

---

## [ ] M10-06 — Layout walker, file map and TOC parser
**Size:** L · **Depends on:** M10-05

`wowlab_core.layout`, `wowlab_core.toc`, `wowlab_core.filemap` per `docs/LAB_PLAN.md` §6.2–§6.3, `docs/LAB_FORMATS.md` §3, `docs/LAB_FILE_MAP.md`. The file map is one data source shared by the doc and `classify()`; state in the PR which way it is kept in sync and add a test that fails when they drift.

**Acceptance:** against the captured tree: accounts, realms, characters, SavedVariables (scope and owning addon), addons with all their TOCs, WTF files and loose overrides are inventoried; every path in the captured tree gets a `classify()` result or an explicit `Unclassified` (list them in the PR; each becomes a file-map row or a stated omission); real TOC fixtures round-trip directive order and unknown directives; symlinks pointing outside the install are reported and not followed; 400 synthetic addon folders inventory in under two seconds.

---

## [ ] M10-07 — WTF text formats
**Size:** M · **Depends on:** M10-03

`wowlab_core.wtfconfig` per `docs/LAB_PLAN.md` §6.5 and `docs/LAB_FORMATS.md` §5–§7. Read-only, lossless.

**Acceptance:** for every real `Config.wtf`, `config-cache.wtf`, `bindings-cache.wtf` and `macros-cache.txt` fixture, joining `lines` reproduces the file byte for byte; case-insensitive CVar lookup with original case preserved; duplicate CVars reported with the last effective; multi-line macro bodies intact; unknown lines typed `Unknown` and preserved.

---

## [ ] M10-08 — Game data client
**Size:** M · **Depends on:** M10-01

`wowlab_core.gamedata` per `docs/LAB_PLAN.md` §6.6 and ADR-0022. Record the builds endpoint and one small table's CSV from wago.tools as fixtures (manual `@pytest.mark.live` capture run by the implementer, responses committed, no credentials involved); add the source to `docs/DATA_SOURCES.md` with the URL shapes the recordings show.

**Acceptance:** replayed tests cover: cache miss then hit; an existing cached build is never overwritten (L5); interrupted download leaves no partial file under the final name; sidecar records URL, time, size, SHA-256; 429 and 5xx back off and retry; `BuildNotPublished` for an unknown build; cache lives under the user data directory, never in the repo or an install. No live call in CI.

---

## [ ] M10-09 — Client process detection
**Size:** S · **Depends on:** M10-01

`wowlab_core.process` per `docs/LAB_PLAN.md` §6.7. `psutil` process listing only (ADR-0023).

**Acceptance:** with a fake process table injected: matches by executable path under an install root and by known names; access-denied yields `unknown`; returns pid, exe path and flavor folder when derivable. A test asserts the module calls nothing on `psutil.Process` beyond `pid`, `name`, `exe`, `cmdline`, `status`. Reviewed by `security-reviewer`.

---

## [ ] M10-10 — Snapshot store
**Size:** L · **Depends on:** M10-01

`wowlab_core.snapshot` per `docs/LAB_PLAN.md` §6.9. Works on any directory tree with explicit subtrees; integration with `layout` defaults happens in M10-14.

**Acceptance:** identical trees produce byte-identical manifests apart from id and timestamp fields (assert with those fixed); unchanged files are stored once across snapshots; `diff` reports added, removed, changed; `verify` detects a corrupted object; `gc` dry-run lists exactly the unreferenced objects and a real run removes only those; manifests are immutable (no API mutates one except the guarded label write); store path is under the user data directory and creating a snapshot writes nothing inside the source tree (assert on a read-only source).

---

## [ ] M10-11T — Write gate graders [TEST]
**Size:** M · **Depends on:** M10-09, M10-10

Graders for `wowlab_core.guard` from `docs/LAB_PLAN.md` §6.10 and ADR-0021, against a synthetic install in `tmp_path` with an injected process probe: refuses when the client is running and when its state is unknown; refuses every path outside the allowlist, including `Data/`, executables, `.build.info`, `.flavor.info`, the install root, `..` traversal, absolute paths, a symlink that escapes, and a case-variant of a forbidden path on a case-insensitive filesystem; takes a snapshot before the first write; journals before/after hashes; atomic replace (a simulated crash between temp write and rename leaves the original intact); an exception inside the transaction rolls every touched path back; `undo()` restores the pre-transaction bytes; dry-run touches nothing; no code path or flag skips the snapshot or the client check (assert on the public signature). `xfail(strict=True, reason="M10-11 not implemented")`.

**Acceptance:** graders fail today for the asserted reason; no implementation code.

---

## [ ] M10-11 — Write gate and restore [IMPL]
**Size:** L · **Depends on:** M10-11T

`lab/core/src/wowlab_core/guard.py`. Activate graders by deleting marker lines only. A repository-wide test greps `lab/` for `open(` with a write mode, `write_text`, `write_bytes`, `os.replace`, `shutil.copy*`, `shutil.move`, `unlink` and `rmtree` outside `guard.py`, `snapshot.py`, `gamedata.py` and tests, and fails on a hit that is not allowlisted with a reason (L2).

**Acceptance:** all M10-11T graders green; the write-site test green; on Windows CI the locked-file case reports a typed error and rolls back. Reviewed by `security-reviewer`.

---

## [ ] M10-12T — luadata serializer graders [TEST]
**Size:** S · **Depends on:** M10-04

Graders from `docs/LAB_PLAN.md` §6.4 (serializer half) and `docs/LAB_FORMATS.md` §4.2: `serialize(parse(x)) == x` byte for byte for every real SavedVariables fixture (parametrized over the index); after changing one leaf, every untouched sibling subtree's bytes are unchanged; a new positional entry, string key and number key are written in the client's format with the source document's line ending; 100 runs produce identical bytes, including under a non-C locale. `xfail(strict=True, reason="M10-12 not implemented")`.

**Acceptance:** graders fail today for the asserted reason.

---

## [ ] M10-12 — luadata serializer [IMPL]
**Size:** M · **Depends on:** M10-12T

**Acceptance:** `make test-parser` green with all M10-12T markers removed. Any real fixture that cannot round-trip is a finding against the parser's document model, reported rather than special-cased.

---

## [ ] M10-13 — Combat log tokenizer
**Size:** M · **Depends on:** M10-03

`wowlab_core.combatlog` per `docs/LAB_PLAN.md` §6.8 and `docs/LAB_FORMATS.md` §8. If M10-03 found that the Forever client writes no usable combat log, ship against retail fixtures and say so in the module docstring and the PR.

**Acceptance:** every line of each real fixture tokenizes or is yielded as `Unparsed` (report the count and the distinct unparsed shapes); quoted commas and nested `[...]`/`(...)` groups handled (`COMBATANT_INFO` fixture line); `follow()` yields appended records, survives truncation and a rotated file name.

---

## [ ] M10-14 — `wowlab` CLI
**Size:** M · **Depends on:** M10-04, M10-05, M10-06, M10-07, M10-08, M10-10, M10-11

Commands, flags and exit codes per `docs/LAB_PLAN.md` §6.11. `snap create` uses `layout` to resolve the default subtrees. `snap restore` and `undo` go through `guard`, print the plan, and ask unless `--yes`.

**Acceptance:** each command has a test through Typer's runner against the captured tree or a synthetic install; every data command's `--json` output validates against its Pydantic model; exit code 3 when guard refuses; `wowlab explain <path>` returns the file-map entry for ten representative paths. A transcript of `wowlab doctor`, `tree --explain`, `sv dump`, `snap create`, a guarded edit and `undo` on a synthetic install is pasted in the PR.

---

## [ ] M10-15 — Wave 1 review
**Size:** S · **Depends on:** M10-12, M10-13, M10-14 · **owner**

The conductor writes `docs/handoffs/M10-review.md` per `docs/LAB_PLAN.md` §11 and stops Lab dispatch. The owner runs the CLI against the real install (`wowlab doctor`, `wowlab snap create -m baseline`, one guarded change and `wowlab undo`), notes what was wrong or missing, and picks Wave 2.

**Acceptance:** the review file exists; the owner's pick is recorded in it; the next wave's plan, ADRs and tickets land in one docs PR.

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

M10 (Lab, `docs/LAB_PLAN.md` §10) is independent of M0 and M1 and runs alongside them. After M10-01: M10-02, M10-08, M10-09 and M10-10 are independent. M10-03 needs the owner; M10-04T, M10-05, M10-07 and M10-13 open when it lands. Lab dispatch stops after M10-15 until the owner picks the next wave (ADR-0024).

---

# Definition of done

A ticket is done when: CI is green on every required check; each acceptance criterion has pasted proof; `mypy --strict` passes; new external-format parsing is covered by a real fixture with a provenance row; any change to a load-bearing file has `make test-parser` green with graders activated by marker deletion only; schema changes ship as a migration that downgrades cleanly with the transcript attached; the independent review verdict is clean; every review thread was replied to and resolved; and anything contradicting an existing ADR ships with a superseding ADR (`Proposed`) rather than a silent deviation. Full lifecycle: `docs/AGENT_WORKFLOW.md`.
