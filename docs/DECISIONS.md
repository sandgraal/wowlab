# Architecture Decision Records

Decisions already made. Read before proposing an alternative. If you believe one is wrong, open a new ADR superseding it rather than quietly deviating.

Format: Status / Context / Decision / Consequences.

---

## ADR-0001 — Snapshots are immutable and append-only

**Status:** Accepted

**Context:** Character state changes constantly. The obvious model is a mutable `characters` row updated on refresh.

**Decision:** Character state is stored as immutable `snapshots` rows keyed on `(character_id, captured_at)`. Nothing updates state; new state is a new row. Identical state is deduplicated by `content_hash`.

**Consequences:** History, diffing, and cache-correctness come free. Snapshots accumulate — all list endpoints must be cursor-paginated, and retention policy is an eventual concern. The `characters` table holds identity only.

---

## ADR-0002 — We do not compute item stats

**Status:** Accepted

**Context:** Ranking gear requires knowing what items actually do. Reconstructing that from raw data means modeling upgrade tracks, crafted quality, tertiary stats, set bonus discontinuities, and scaling curves.

**Decision:** SimulationCraft computes; we store and present. Any question of the form "how good is this item for this character" is answered by running a sim.

**Consequences:** We are dependent on SimC correctness and SimC release cadence. In exchange we avoid an entire class of subtly-wrong-answer bugs. Set bonus discontinuities alone would break any independent item-ranking approach.

---

## ADR-0003 — Self-host SimulationCraft rather than integrate Raidbots

**Status:** Accepted

**Context:** Raidbots is the incumbent sim service. Integrating would save building sim infrastructure.

**Decision:** Run our own SimC workers. Support importing completed Raidbots *reports* as an onboarding path.

**Consequences:** Raidbots has no documented public API for submitting simulations; the SPA uses internal JS-rendered endpoints and the only third-party wrapper is abandoned and never supported submission. Integration was never actually available. Self-hosting also makes sims free and unlimited for users, which is a product advantage, at the cost of owning CPU spend.

---

## ADR-0004 — Sim results are cached on a deterministic profile hash

**Status:** Accepted

**Context:** SimC is CPU-expensive. Free unlimited sims are only viable if repeated work is eliminated.

**Decision:** `sim_jobs` carries `profile_hash` (sha256 of generated profile text plus canonicalized options) and `simc_version`. A partial unique index on `(profile_hash, simc_version) WHERE status = 'complete'` enforces one execution per distinct profile.

**Consequences:** Profile generation must be byte-deterministic — no timestamps, no unordered iteration, no locale-dependent formatting. Non-determinism silently destroys the cache and the cost model with it, with no test failure to signal it. An explicit determinism test is mandatory. SimC version upgrades invalidate the cache by construction, which is correct since results are not comparable across engine versions.

---

## ADR-0005 — The `/simc` string is the primary ingest path, not the Blizzard API

**Status:** Accepted

**Context:** The Blizzard Profile API is the lower-friction input — no client involvement. But talent loadouts disappeared from the Character Specializations endpoint in patch 11.2 (August 2025) and were still absent a year later with no official response.

**Decision:** The `/simc` addon export is primary. The Blizzard API is a convenience layer for gear and identity, and for browsing characters the user does not own.

**Consequences:** Onboarding requires a copy-paste until the companion agent ships. In exchange, we get talent data, crafted item quality, gem placement, and upgrade track state that the API does not reliably expose. No core feature may depend on Blizzard API availability.

**Open:** verify the current 12.1 state of the loadouts field (see `docs/IMPLEMENTATION_PLAN.md` §6.1) and record the result here as an amendment.

---

## ADR-0006 — Static game data comes from SimulationCraft's generated files

**Status:** Accepted

**Context:** We need item, spell, and talent reference data. The Blizzard Game Data API exposes it per-item.

**Decision:** Ingest SimC's generated data files (`engine/dbc/generated/`), which are extracted from the client DB2s and tagged per patch. Fall back to wago.tools for tables SimC does not carry.

**Consequences:** Avoids tens of thousands of API calls against a 36k/hour budget to build a catalog that goes stale each patch. Data is already normalized for sim use, and patch tagging gives the pipeline a clean version boundary. We inherit SimC's release cadence for new-patch data.

---

## ADR-0007 — Game data tables are versioned, never overwritten

**Status:** Accepted

**Context:** Item and talent data change every patch. Overwriting is simpler.

**Decision:** `game_items`, `game_spells`, `game_talents` are keyed by `game_version`. Old rows are never dropped.

**Consequences:** Snapshots from prior patches remain renderable. Storage grows per patch, which is negligible at this data size. Every query against game data must carry a version.

---

## ADR-0008 — Postgres, not a document store

**Status:** Accepted

**Context:** Snapshot payloads are semi-structured, which suggests a document database.

**Decision:** Postgres with JSONB columns for payloads.

**Consequences:** Both load-bearing correctness mechanisms — snapshot dedupe and the sim cache — are unique indexes over computed columns. Relational is the right tool. JSONB covers the semi-structured payloads without giving up those guarantees.

---

## ADR-0009 — The companion agent runs outside the game client

**Status:** Accepted

**Context:** Automatic character sync would eliminate all onboarding friction. The obvious approach is an addon that posts to our API.

**Decision:** A companion addon writes to SavedVariables; a separate local agent watches that file and uploads.

**Consequences:** WoW addons cannot make HTTP requests — SavedVariables is their only I/O, written on logout or `/reload`. An external process is the only possible design. This means asking users to install a binary that reads their game directory, which imposes hard requirements: open source, reproducible builds with published hashes, read-only access to a single path pattern, upload-only, and a constrained Lua data parser that never executes.

---

## ADR-0010 — Gap analysis must cite evidence

**Status:** Accepted

**Context:** Joining sim projections against combat logs can produce plausible-sounding attributions that are wrong.

**Decision:** Every attribution line in a gap analysis carries the specific numbers that produced it, and residual unexplained gap is reported honestly as unattributed rather than assigned to an invented cause.

**Consequences:** The feature is harder to build and less impressive-looking in a demo. A confidently wrong analysis engine would destroy trust in the entire product, and users cannot easily verify these claims themselves — which is exactly why the bar has to be high. Requires hand-labeled test cases where the true cause is known.

---

## ADR-0011 — Vault ranking is the first user-facing feature

**Status:** Accepted

**Context:** M2 could ship any of several sim-backed features first.

**Decision:** Great Vault ranking ships before droptimizer, top gear, or talent comparison.

**Consequences:** It is a recurring weekly decision every player makes, currently unsupported by any tool, and it is the simplest possible sim job — nine candidate items against one baseline. High demand, low complexity, and it establishes a weekly return habit. Everything else in M3 is a generalization of the same machinery.

---

## ADR-0012 — No live external API calls from tests or CI

**Status:** Accepted

**Context:** Integration tests against real APIs catch real breakage.

**Decision:** Record real responses as fixtures; replay in CI. Live calls only from a manually-run, explicitly-tagged suite.

**Consequences:** External API rate limits are per-client and shared with production. The Warcraft Logs point budget in particular (roughly 3,600 points/hour, consumed unevenly — event queries are far more expensive than table queries) would be exhausted by a CI loop, breaking live ingest. Fixture staleness is the tradeoff; refresh them deliberately after each patch.

---

## ADR-0013 — Agent operating model: conductor, roles, independent review, autonomous merge

**Status:** Proposed (2026-09-09)

**Context:** The repository is built primarily by Claude Code agents. Without structure, a single session writes code, tests, and review, and grades its own work; parallel sessions collide in one checkout; and "done" drifts to "the agent said so". The owner runs a conductor model on another project and wants it here, adapted to this domain and to current Claude Code mechanisms.

**Decision:** The main session is a conductor that orchestrates and never implements. Work is agent-sized tickets in `docs/BACKLOG.md`; the frontier is computed from ticket status and merged PR titles. Roles live in `.claude/agents/`: `implementer`, `test-writer`, `code-reviewer`, `domain-reviewer`, `security-reviewer`, `pr-shepherd`, each in an isolated worktree. Every branch gets an independent, spec-first review that runs things. For the four load-bearing files (`simc_parser.py`, `profile_builder.py`, `talent_codec.py`, `gap_analysis.py`) and for migrations, graders are written first, by a different agent, as `xfail(strict=True)` tests. A PR merges autonomously when all required checks are green, every review thread is resolved, the reviewer verdicts are clean, and there are no conflicts. Subagents never edit the harness, ADRs, or the backlog; hooks enforce it. ADRs stay `Proposed` until the owner flips them.

**Consequences:** Parallelism is bounded only by dependencies and the harness's concurrency limit. Review quality depends on the reviewer deriving expectations before reading the diff; the agent definitions insist on it. The owner's attention goes to decisions (ADR status, product calls), credentials, and real fixtures rather than to every diff. The harness itself is code: hooks have tests, workflows are linted, and the `harness` CI job is required. The full workflow is documented in `docs/AGENT_WORKFLOW.md`.

---

## ADR-0014 — Toolchain: uv workspace, ruff, mypy strict, pytest; pnpm; Go; required CI checks

**Status:** Proposed (2026-09-09)

**Context:** The plan names Python 3.12, FastAPI, Ruff, and mypy but not how the monorepo is assembled or what CI enforces. Contributor machines may lack a package manager; agents run in parallel worktrees and must not fight over environments or ports.

**Decision:** One uv workspace at the root (`pyproject.toml`, `uv.lock`) with `api/` (and later `pipeline/`, `worker/`) as members; dev tooling in the root `dev` group. Ruff for lint and format with a broad rule set; `mypy --strict` on `api/src`; pytest with `parser` and `live` markers. Hooks under `.claude/hooks/` are Python 3.9-compatible stdlib scripts. `web/` uses pnpm with Node from `.nvmrc`; `agent/` uses Go. Pre-commit runs ruff, uv-lock, gitleaks, actionlint. CI required checks: API quality, parser suite, web quality, harness (3.9 and 3.12), gitleaks, semgrep, trivy. Squash-only merges, linear history, conversation resolution required, no force-push to `main`. The Makefile derives a compose project name and port block per checkout so worktrees can run local stacks concurrently.

**Consequences:** `make ci` is the single local truth and matches CI job for job. Version pins live in one lockfile; Dependabot bumps them under CI. The `web-quality` job runs as a real check from day one and becomes substantive when `web/` lands. Adding a workspace member is a one-line change. Docker Compose is a machine prerequisite documented in `docs/SETUP.md`.
