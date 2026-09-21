# Architecture Decision Records

Decisions already made. Read before proposing an alternative. If you believe one is wrong, open a new ADR superseding it rather than quietly deviating.

Format: Status / Context / Decision / Consequences. Status is `Proposed` until the repository owner flips it to `Accepted`; no agent does.

> **Note (2026-09-21).** This file is append-only, with one exception made on this date and authorised by the repository owner: it was rewritten when the repository stopped being Bronze and became the Lab (ADR-0025). ADRs that belonged to Bronze were removed, the three harness ADRs that carry over were revised in place and marked with a dated amendment, and ADR-0019 to ADR-0024 were reworded for a Lab-only repository without changing what they decide. The previous text of every ADR is at the git tag `bronze-final`. From here on the file is append-only again.

## Retired numbers

ADR-0001 to ADR-0011 and ADR-0015 to ADR-0018 belonged to Bronze, retired 2026-09-21 by ADR-0025, text at tag `bronze-final`. Numbers are not reused. A retired ADR binds nothing.

| ADR | Title at retirement |
|---|---|
| 0001 | Snapshots are immutable and append-only |
| 0002 | We do not compute item stats |
| 0003 | Self-host SimulationCraft rather than integrate Raidbots |
| 0004 | Sim results are cached on a deterministic profile hash |
| 0005 | The `/simc` string is the primary ingest path, not the Blizzard API |
| 0006 | Static game data comes from SimulationCraft's generated files |
| 0007 | Game data tables are versioned, never overwritten |
| 0008 | Postgres, not a document store |
| 0009 | The companion agent runs outside the game client |
| 0010 | Gap analysis must cite evidence |
| 0011 | Vault ranking is the first user-facing feature |
| 0015 | Great Vault candidates come from the `/simc` export taken with the vault open |
| 0016 | Launch posture: free and open, all specs, public pages, modest hosting, US/EU |
| 0017 | A public, documented, read-only API from the first release |
| 0018 | Collections data comes from AllTheThings, read as data through the companion agent |

---

## ADR-0012 — No live external calls from tests or CI

**Status:** Accepted

**Context:** Integration tests against a real service catch real breakage. The services this repository talks to are community-run (wago.tools), publish no rate limit, and owe us nothing; a CI loop hammering one is rude at best and gets the client blocked at worst. A test that needs the network also fails for reasons that have nothing to do with the change under review.

**Decision:** Record real responses as fixtures; replay them in CI. Live calls happen only from a manually run suite marked `@pytest.mark.live`, which the default pytest options exclude. The same rule covers the owner's game install: no test needs a real install to be present, and none touches one.

**Consequences:** CI is hermetic and fast. Fixture staleness is the tradeoff: recordings are refreshed deliberately when a service changes shape, and the change is logged in `docs/DATA_SOURCES.md`. A recorded response is a fixture like any other and carries a provenance row.

**Amendment (2026-09-21):** revised at the Bronze retirement (ADR-0025).

---

## ADR-0013 — Agent operating model: conductor, roles, independent review, autonomous merge

**Status:** Proposed (2026-09-09)

**Context:** The repository is built primarily by Claude Code agents. Without structure, a single session writes code, tests, and review, and grades its own work; parallel sessions collide in one checkout; and "done" drifts to "the agent said so". The owner runs a conductor model on another project and wants it here, adapted to this domain and to current Claude Code mechanisms.

**Decision:** The main session is a conductor that orchestrates and never implements. Work is agent-sized tickets in `docs/BACKLOG.md`; the frontier is computed from ticket status and merged PR titles. Roles live in `.claude/agents/`: `implementer`, `test-writer`, `code-reviewer`, `domain-reviewer`, `security-reviewer`, `pr-shepherd`, each in an isolated worktree. Every branch gets an independent, spec-first review that runs things. For the two load-bearing files (`lab/core/src/wowlab_core/luadata.py` and `lab/core/src/wowlab_core/guard.py`), graders are written first, by a different agent, as `xfail(strict=True)` tests. A PR merges autonomously when all required checks are green, every review thread is resolved, the reviewer verdicts are clean, and there are no conflicts. Subagents never edit the harness, ADRs, or the backlog; hooks enforce it. ADRs stay `Proposed` until the owner flips them.

**Consequences:** Parallelism is bounded only by dependencies, the harness's concurrency limit, and the wave boundary (ADR-0024). Review quality depends on the reviewer deriving expectations before reading the diff; the agent definitions insist on it. The owner's attention goes to decisions (ADR status, wave picks) and real fixtures rather than to every diff. The harness itself is code: hooks have tests, workflows are linted, and the `harness` CI job is required. The full workflow is documented in `docs/AGENT_WORKFLOW.md`.

**Amendment (2026-09-21):** revised at the Bronze retirement (ADR-0025).

---

## ADR-0014 — Toolchain: uv workspace, ruff, mypy strict, pytest; required CI checks

**Status:** Proposed (2026-09-09)

**Context:** The plan names Python 3.12 and its quality gates but not how the repository is assembled or what CI enforces. Contributor machines may lack a package manager; agents run in parallel worktrees and must not fight over environments.

**Decision:** One uv workspace at the root (`pyproject.toml`, `uv.lock`) whose members live under `lab/` (`lab/core` today; later apps are `lab/<app>`); dev tooling in the root `dev` group. Ruff for lint and format with a broad rule set; `mypy --strict` on `lab/core/src`; pytest with `parser` and `live` markers. Hooks under `.claude/hooks/` are Python 3.9-compatible stdlib scripts. Pre-commit runs ruff, uv-lock, gitleaks, actionlint. CI required checks, by exact name: `Lint, typecheck, tests`; `Parser fixtures and round-trip`; `Agent harness (hooks on 3.9 and 3.12, lockfile, workflows) (3.9)` and `(3.12)`; `Secret scan (gitleaks)`; `SAST (semgrep)`; `Dependency and config scan (trivy)`. A `lab (windows)` job runs the Lab suite on `windows-latest` and is not required. Squash-only merges, linear history, conversation resolution required, no force-push to `main`.

**Consequences:** `make ci` is the single local truth and matches CI. Version pins live in one lockfile; Dependabot bumps them under CI. Adding a workspace member is a one-line change. No other language or runtime is part of the toolchain; one arrives only by ADR (ADR-0020). Renaming a CI job means changing `.github/rulesets/main.json` and re-applying it, or every PR waits on a check that no longer reports.

**Amendment (2026-09-21):** revised at the Bronze retirement (ADR-0025).

---

## ADR-0019 — wowlab is a local-only personal toolchain

**Status:** Proposed (2026-09-20) — owner decision

**Context:** The owner wants tooling that reads a WoW install directly, explains every file, snapshots it, and modifies client configuration reversibly, as a base for character-customization and offline-character tools (`docs/LAB_PLAN.md` §1). That is only reasonable to build because it serves one person on their own machine: software that reads and rewrites a game directory is not something to hand to strangers, and a service that received such data would be a liability with no purpose.

**Decision:** wowlab runs only on the owner's machine, as the owner, against the owner's install. It is never distributed as a binary or a package, never deployed as a service, and never uploads anything it reads. The only network traffic is fetching public game tables (ADR-0022). The repository is public, so anything captured from a real install is scrubbed by a tool before it is committed, and the capture tool refuses output that still carries an identifier.

**Consequences:** No accounts, no authentication, no hosting, no release process, no telemetry. Fixtures need a scrub tool and a fixture policy before the first capture (M10-02). Anyone may read or fork the code; nothing here is built or supported for them. A later wish to share a tool with other players is a new ADR that revisits this one, not a quiet change.

---

## ADR-0020 — The core is a flavor-agnostic Python package in the uv workspace

**Status:** Proposed (2026-09-20)

**Context:** A Rust core with Python and Node bindings was considered for speed and single-binary distribution. The repository's toolchain, review agents, hooks and CI are Python-first (ADR-0014); the Lab has one user and no distribution requirement (ADR-0019); the hot paths in Wave 1 are a literal parser and file hashing. Separately, the Forever beta installs under a flavor folder that is reported as `_classic_beta_` and will probably change at launch, and the owner also plays retail.

**Decision:** The core library is `wowlab-core` (`lab/core/`, import name `wowlab_core`), a uv workspace member held to ruff, `mypy --strict` and pytest gates. Flavor folder, product code, build and interface version are always discovered from the install at run time and never appear as constants in library code. A second language is introduced only by a superseding ADR that carries measurements showing Python missing a stated target (`docs/LAB_PLAN.md` §6.4 sets the first such target).

**Consequences:** One toolchain for agents and CI. Local CASC reading, which has no mature pure-Python implementation, is deferred to the wave that needs models or textures and will be decided then (binding vs. a bridge to an existing exporter). Every parser must tolerate both retail and Forever captures from day one, which the fixture corpus (M10-03) has to reflect.

---

## ADR-0021 — Every write into a game install goes through one write gate

**Status:** Proposed (2026-09-20)

**Context:** The owner's requirement is to modify things at will and go back and forth. The client overwrites its configuration files on logout, can hold them open on Windows, and gives no warning when an external edit is lost. Several later tools (declarative config, profile switching, addon scaffolding, inbound SavedVariables for companion features) will all need to write.

**Decision:** `wowlab_core.guard` is the only code in the repository that writes inside an install. A write happens inside a transaction that refuses when the client is running or its state is unknown, accepts only paths under an allowlist (`WTF/`, `Interface/AddOns/`, `Fonts/`, loose `Interface/` overrides), takes a content-addressed snapshot first, journals before/after hashes, replaces files atomically, and rolls back on error. There is no flag that skips the snapshot or the client check. `guard.py` is load-bearing: graders are written by `test-writer` before the implementation, and `security-reviewer` reviews every change.

**Consequences:** Undo is a property of the platform, not of each tool. Tools cannot write while the game is open, by design. The snapshot store must be cheap enough to run on every transaction, which is why it deduplicates by content hash. A later tool that believes it needs to bypass the gate is mis-specified.

---

## ADR-0022 — Game data comes from wago.tools per-build exports, cached by build, never overwritten

**Status:** Proposed (2026-09-20)

**Context:** The Lab needs arbitrary client tables (customization options, items, maps, quest text) for whatever build is installed, on whichever product. wago.tools publishes every DB2 table for every build of every product as CSV over HTTP and is what the community's existing Forever tooling uses. Reading the same tables from the local install needs CASC access, which ADR-0020 defers. Game data changes with every build, and anything derived from a table is only meaningful against the build it came from.

**Decision:** `wowlab_core.gamedata` fetches tables from wago.tools by table name and full build string and caches them under the user data directory keyed by `(table, build)`. **A cached table is never overwritten:** a table for build A is never replaced by build B, a second fetch of the same key never replaces the file on disk, and nothing prunes old builds implicitly. URL, time, size and SHA-256 are recorded beside each file. The client is single-connection, identifies itself, backs off on 429/5xx and is never exercised live in CI (ADR-0012). `gamedata` is written against a `Source` protocol so a local CASC source can be added without changing callers.

**Consequences:** Anything computed against an old build stays reproducible; the cache grows per build, which is negligible at this size. The Lab depends on a community service staying up and keeping its URL shape; the recorded fixtures and the breakage log in `docs/DATA_SOURCES.md` are how a change is detected. Server-side hotfixes are not in these exports; overlaying `DBCache.bin` is a later idea. Column typing needs WoWDBDefs and arrives with the first wave that needs typed columns.

---

## ADR-0023 — Scope stops at files the client treats as user-editable

**Status:** Proposed (2026-09-20) — owner decision

**Context:** The owner is comfortable connecting tools directly to game files for personal use. Two facts set the limit. This repository is public and Apache-2.0 under the owner's name, so everything in it is a public statement of what the owner builds. And the client's anti-cheat acts on the account, not on the intent: it does not distinguish a curious read of process memory from a cheat, and the cost of being wrong is the account.

**Decision:** In this repository the Lab reads anything in the install and writes only what the client treats as user configuration: `WTF/`, `Interface/AddOns/`, `Fonts/` and loose `Interface/` texture overrides. Permanently out of scope here: reading or writing client process memory, library injection, packet capture or modification, input automation, modifying `Data/` or executables, and defeating integrity checks. Documentation labels each capability `A` (ordinary addon-user behaviour) or `B` (works, unsupported, may be reset by a patch). Experiments beyond this boundary, including private-server sandboxes, belong in a separate private repository and are not planned, ticketed or referenced from this one beyond this sentence.

**Consequences:** Every idea in `docs/LAB_IDEAS.md` is buildable without account risk. Real-time data out of the running client is limited to what the client writes to disk (SavedVariables on logout or `/reload`, combat and chat logs) or shows on screen. `code-reviewer` and `security-reviewer` treat any `psutil` use beyond process listing, any `ctypes`/FFI into the client, and any write outside the allowlist as a finding.

---

## ADR-0024 — Work proceeds in owner-selected waves; ideas are never pre-ticketed

**Status:** Proposed (2026-09-20) — owner decision

**Context:** The Lab has a long idea list (`docs/LAB_IDEAS.md`) and one fixed starting point, the core library. The conductor dispatches every eligible ticket in parallel (ADR-0013), so anything written as a ticket gets built. The owner wants to choose each next step after seeing what the last one produced.

**Decision:** Each wave is one milestone (`M10`, `M11`, …). The backlog holds tickets for the current wave only. When a wave's last ticket merges, the conductor writes `docs/handoffs/M<n>-review.md` (what shipped, what was learned, which ideas are now unblocked, a recommended next wave of at most three ideas) and stops dispatch. The owner picks; the conductor lands the plan section, any ADRs and the tickets in one docs PR; dispatch resumes when it merges.

**Consequences:** Throughput is gated on one owner decision per wave, deliberately. Between waves nothing is dispatched. `docs/LAB_IDEAS.md` stays a menu, and an agent found building from it without a ticket is off-task. Wave reviews are where estimates in the ideas file get corrected.

---

## ADR-0025 — Bronze is retired; this repository is the Lab

**Status:** Proposed (2026-09-21) — owner decision

**Context:** This repository began as Bronze, a hosted character workbench for many players: a FastAPI service over Postgres, self-hosted SimulationCraft workers, a Next.js front end and a Go companion agent, specified through milestones M0 to M5. On 2026-09-20 the Lab was added beside it as a local-only second track (ADR-0019 to ADR-0024 as first written). Running both meant one constitution serving two products with opposite trust models: Bronze's credibility rested on a companion agent that read one file and never wrote, while the Lab exists to read everything and write reversibly. Every Lab document had to defer to Bronze invariants that did not apply to it, and the owner's interest had moved to the Lab and to World of Warcraft: Forever.

**Decision:** Bronze is retired in full and this repository is the Lab. The last Bronze state is the git tag `bronze-final`; nothing of Bronze survives in the tree. Removed: `api/`, `worker/`, `tests/stack/`, the Compose stack, the Bronze spec and its product, competitive-landscape and `/simc` format documents, milestones M0 and M1, the README art, the path rules and skills for the API, worker, web, companion agent, migrations and `/simc` fixtures, the merged-migration protection in the hooks, and the `Web` CI job. ADR-0001 to ADR-0011 and ADR-0015 to ADR-0018 are retired; their numbers are not reused and **they bind nothing**: no agent should read them as constraints, precedents or defaults. Three harness ADRs carry over, revised: ADR-0012 (no live calls in tests), ADR-0013 (the conductor model; the load-bearing set is now `luadata.py` and `guard.py`) and ADR-0014 (the toolchain and required checks, now Python only). ADR-0019 to ADR-0024 keep their numbers and decisions and lose their references to Bronze; L1 to L8 become the hard invariants in `AGENTS.md`. Milestone `M10` and its ticket ids are unchanged. The package layout stays `lab/core/` so that later apps are `lab/<app>/`.

**Consequences:** One product, one constitution, one trust model. The required CI check names changed, so the branch ruleset must be re-applied by the owner before any PR can merge (`scripts/bootstrap-github.sh`). Ideas that Bronze would have owned, namely offline character planning and running simulations, become Lab ideas (`char-planner`, `simc-bridge` in `docs/LAB_IDEAS.md`) and wait for a wave like everything else. Anyone who wants Bronze back starts from the tag, in another repository.
