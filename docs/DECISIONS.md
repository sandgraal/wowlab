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

---

## ADR-0015 — Great Vault candidates come from the `/simc` export taken with the vault open

**Status:** Proposed (2026-09-09) — owner decision

**Context:** M2's wedge feature ranks the player's vault choices, but the plan does not say where those choices come from. The Blizzard API does not expose vault contents, and a manual item picker (search item, choose track and item level) is slow, error-prone, and exactly the friction the product exists to remove. The SimulationCraft addon appends a `### Weekly Reward Choices` block (item lines for each vault choice) when `/simc` is run while the Great Vault window is open; this is how Raidbots' vault mode is fed today. *Hypothesis until a fixture confirms it — see `docs/SIMC_FORMAT.md`.*

**Decision:** Vault candidates are read from that comment block in the `/simc` export. The parser captures it into `parsed.vault_choices[]` (named for what it is: choices, not items); the ingest flow and the paste-box copy tell the player to open the vault first. No manual picker ships in M2. The canonical `parsed` shape in plan §8.1 gains `vault_choices[]`, `loadouts[]` (from `### Saved Loadouts`), and `extra{}` (from `### Additional Character Info`). Fallback if a 12.1 fixture shows the block absent or unusable: the player pastes vault item links from chat, scoped as a new ticket, and this ADR is amended — the format is never guessed.

**Consequences:** M1-01 must include a real export captured with the vault open, and M1-02 must parse the block; both are in the backlog. The vault candidate pool the sim sees includes the catalysed variant of any tier-slot choice, so set-bonus boundaries are visible. Because the block is state, snapshot canonicalization keeps comment sections: the same character pasted with and without the vault open is two snapshots (plan §7, amended). Saved loadouts arriving for free seed the M3 loadout library without a second import path.

---

## ADR-0016 — Launch posture: free and open, all specs, public pages, modest hosting, US/EU

**Status:** Proposed (2026-09-09) — owner decision; answers plan §17

**Context:** Plan §17 leaves five product questions open. Several block engineering: the Blizzard API terms restrict commercial use, hosting shape affects `infra/`, and spec breadth affects the fixture corpus and M2 validation.

**Decision:**
1. **Monetization:** none at launch. Free and open (Apache-2.0). This sidesteps the Blizzard non-commercial restriction and matches the "free unlimited sims" positioning. Revisit only with a separate ADR and a terms review.
2. **Spec scope:** accept every class and spec via `/simc` from day one (the fixture corpus covers all 13 classes), but validate deeply on three before M2 ships: Augmentation Evoker (support-spec edge cases), one melee DPS, and one healer — the healer to design the non-sim path in `docs/PRODUCT.md`, since SimulationCraft has no maintained healing model.
3. **Public pages:** yes, read-only. Equipped gear and talents are shown as the Armory shows them; bag alternates, vault choices, saved loadouts and currencies are visible only because the player pasted them, and claiming the character lets them hide those or the whole page.
4. **Hosting:** through M3, managed Postgres (Supabase), Vercel for `web/`, and one small always-on host for the API and a SimC worker. Move workers to spot capacity only when measured sim volume justifies it; the cache hit rate is instrumented from M2.
5. **Regions:** US and EU at launch. KR/TW/CN add realm-slug, reset-time and locale work with no early demand; `cn` also needs a separate API gateway.

**Consequences:** No billing, entitlement, or quota code in M0–M3; per-user concurrency limits still apply. `infra/` starts as a compose file and a deployment note, not Terraform. Launch legal review (§15) is limited to attribution and terms of use. Adding a region later is a data change (realm list, reset row), not a schema change.

---

## ADR-0017 — A public, documented, read-only API from the first release

**Status:** Proposed (2026-09-10)

**Context:** Raider.IO, Lorrgs and SaddleBag Exchange each grew a Discord-bot and guild-tool ecosystem on a documented public API; Raidbots, with none, is wrapped by abandoned third-party libraries. Bronze's API surface (plan §9) is served by FastAPI, which already emits an OpenAPI document, and every read endpoint returns only what a public character page shows (ADR-0016 §3). `docs/COMPETITIVE_LANDSCAPE.md` item 8.

**Decision:** The read endpoints in plan §9 (character, snapshots, diff, sim results, vault ranking) are public from M1: the OpenAPI document is served and linked, responses carry the same fidelity fields as the pages, an unauthenticated per-IP rate limit applies, and an optional free API key raises it. Write endpoints (ingest, sim submission, agent upload) are never part of the public surface; they stay behind the paste flow's abuse limits and the companion agent's device token. Attribution requirements inherited from upstream sources (Blizzard, Warcraft Logs, Raider.IO, Wowhead) are restated on the API page so a consumer cannot launder them away.

**Consequences:** Response shapes become a compatibility contract from M1, so breaking changes need a version prefix, not an edit. Rate limiting lands in M1 (ticket M1-10) rather than "later". A public API makes third-party Discord bots possible before Bronze ships its own (plan §3.15), which is the intended outcome. Terms of use for API consumers join the launch legal review (plan §15).

## ADR-0018 — Collections data comes from AllTheThings, read as data through the companion agent

**Status:** Proposed (2026-09-10) — scope for after M5; recorded now so nobody builds it another way

**Context:** "All things WoW" includes mounts, pets, toys and transmog. The Blizzard collections endpoints exist but depend on API availability (ADR-0005 forbids core features that do), and Blizzard's game-data endpoints would need tens of thousands of calls to describe what each item unlocks. The AllTheThings addon maintains, under the MIT licence, the mapping from items, quests and NPCs to collection categories and account completion, and writes account state to SavedVariables. `docs/COMPETITIVE_LANDSCAPE.md` item 10.

**Decision:** Collection completion, when built, is fed by the companion agent reading AllTheThings SavedVariables with the same constrained literal parser used for Bronze's own addon (ADR-0009: data, never executed Lua), stored as a versioned snapshot of collection state, and rendered against a versioned copy of AllTheThings' mapping data ingested per game version like `game_items` (ADR-0007). The Blizzard collections endpoints are a convenience path with the lower-fidelity label, never the only source. Bronze does not compute drop rates, source availability or "obtainable" status itself; it presents what the mapping says.

**Consequences:** A new versioned table family and a new fixture family (real SavedVariables captures with provenance and consent) before any UI. The agent's filesystem scope widens to a second addon's file, which is a stop-and-ask item in `AGENTS.md` and must ship with a scope disclosure in the agent's README. AllTheThings' MIT licence is compatible with Apache-2.0; its attribution travels with the data. Nothing here ships before M5, and the ADR can be superseded if AllTheThings changes licence or format.
