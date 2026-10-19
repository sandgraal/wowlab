# Architecture Decision Records

Decisions already made. Read before proposing an alternative. If you believe one is wrong, open a new ADR superseding it rather than quietly deviating.

Format: Status / Context / Decision / Consequences. Status is `Proposed` until the repository owner flips it to `Accepted`; no agent does.

> **Note (2026-09-21).** This file is append-only, with one exception made on this date and authorised by the repository owner: it was rewritten when the project this repository used to hold was retired and the repository became the Lab (ADR-0025). That project's ADRs were removed, the three harness ADRs that carry over were revised in place and marked with a dated amendment, and ADR-0019 to ADR-0024 were reworded for a Lab-only repository without changing what they decide. The previous text of every ADR is at the git tag `bronze-final`. From here on the file is append-only again. On the same date the owner accepted ADR-0013, ADR-0014 and ADR-0019 to ADR-0025, and instructed the conductor session, explicitly and for this one occasion, to make the status edits; the rule that no agent flips a status stands.

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

**Status:** Accepted (2026-09-21; proposed 2026-09-09)

**Context:** The repository is built primarily by Claude Code agents. Without structure, a single session writes code, tests, and review, and grades its own work; parallel sessions collide in one checkout; and "done" drifts to "the agent said so". The owner runs a conductor model on another project and wants it here, adapted to this domain and to current Claude Code mechanisms.

**Decision:** The main session is a conductor that orchestrates and never implements. Work is agent-sized tickets in `docs/BACKLOG.md`; the frontier is computed from ticket status and merged PR titles. Roles live in `.claude/agents/`: `implementer`, `test-writer`, `code-reviewer`, `domain-reviewer`, `security-reviewer`, `pr-shepherd`, each in an isolated worktree. Every branch gets an independent, spec-first review that runs things. For the two load-bearing files (`lab/core/src/wowlab_core/luadata.py` and `lab/core/src/wowlab_core/guard.py`), graders are written first, by a different agent, as `xfail(strict=True)` tests. A PR merges autonomously when all required checks are green, every review thread is resolved, the reviewer verdicts are clean, and there are no conflicts. Subagents never edit the harness, ADRs, or the backlog; hooks enforce it. ADRs stay `Proposed` until the owner flips them.

**Consequences:** Parallelism is bounded only by dependencies, the harness's concurrency limit, and the wave boundary (ADR-0024). Review quality depends on the reviewer deriving expectations before reading the diff; the agent definitions insist on it. The owner's attention goes to decisions (ADR status, wave picks) and real fixtures rather than to every diff. The harness itself is code: hooks have tests, workflows are linted, and the `harness` CI job is required. The full workflow is documented in `docs/AGENT_WORKFLOW.md`.

**Amendment (2026-09-21):** revised at the Bronze retirement (ADR-0025).

**Amendment (2026-10-17, owner decision; Accepted 2026-10-17 — owner: "i accept all"; the status edit was made by the conductor on the owner's explicit instruction):** six guardrails loosened after a review of what each had cost. (1) The load-bearing set returns to the two files this ADR names, `luadata.py` and `guard.py`; the widening to `svmerge.py` and `profiles.py` (2026-09-29) and `wowlab_core.ui` (2026-10-15) is withdrawn, because it doubled every `ui` ticket, needed owner exemptions for M13-25 and M13-26, and blocked M13-28 over a one-field change in a report. Graders already on `main` for those files stay and are activated by marker deletion. (2) The conductor may make an XS fix directly on a branch when it touches only tests, workflow YAML, scripts or docs, nothing under `lab/*/src/` and no grader of a load-bearing file; `code-reviewer` still grades it before merge. (3) The `make lint` commit hook is unregistered; pre-commit's ruff hook and CI's `make lint` are the gate. (4) The `main` ruleset no longer requires review-thread resolution, and `pr-shepherd`'s merge rule changes with it: every thread still gets a reply, a thread the owner opened is answered before merge, but an open thread from the automated review or another bot no longer holds a merge; the independent `code-reviewer` verdict is the gate. (5) Shell writes into `docs/BACKLOG.md` and `docs/DECISIONS.md` are blocked for subagents only; `.claude/`, `AGENTS.md` and `CLAUDE.md` stay shell-blocked for everyone (ADR-0031). (6) `domain-reviewer` is dispatched for format, file-map and discovery changes, not for CLI wording alone, and `security-reviewer` for fixtures only when `scripts/lab_capture.py` changes in the same PR; a ticket's own reviewer line still wins. Everything else in this ADR stands.

**Amendment (2026-10-17, later, owner decision; Accepted 2026-10-17 — owner: "i accept all"; the status edit was made by the conductor on the owner's explicit instruction):** to item (4). When the automated review raises a finding that every independent reviewer dispatched for the branch (`code-reviewer` alone, or with `security-reviewer` and `domain-reviewer` where dispatched) already saw and chose not to count, the conductor checks that against the reviewers' reports and tells `pr-shepherd` so; only then does the shepherd reply on the thread that it was seen by the reviewers and is not held, and merge. No implementer fix and no reviewer re-check are spent on it. Without that word from the conductor the shepherd routes the thread as before. A bot finding neither reviewer saw is routed as before (fixed if XS by the shepherd's own rule, otherwise to the implementer with a re-check). Four more rules from the same review, all about spending less reviewer time on what was already graded, none touching what is graded: (a) a reviewer runs `make ci` once, on the first round, and on a re-check runs only the suites the fix touches and its probes, since the PR's required checks run the full suite on every push; (b) a re-check is narrow: the same reviewer, the named fixes, one short report; (c) after a rebase the shepherd compares the branch's own patch before and after with `git range-diff <old-base>..<old-head> origin/main..HEAD`, which keeps every line in place: a range-diff in which every commit is unchanged (`=`) merges without a re-check, while any changed commit gets a re-check on its differing hunks only; (d) a defect the branch neither introduces nor makes worse, whose fix lives outside the branch's files (an older wording the branch only reaches, a doc already stale on `main`, a follow-up), is reported as "Off the branch", does not change the verdict, and is filed by the conductor; a caller the branch broke, a doc the branch made stale, or an unlisted user-facing change is a finding as before. The reviewer set, the load-bearing rule, the first-round full run and `security-reviewer`'s scope are unchanged.

---

## ADR-0014 — Toolchain: uv workspace, ruff, mypy strict, pytest; required CI checks

**Status:** Accepted (2026-09-21; proposed 2026-09-09)

**Context:** The plan names Python 3.12 and its quality gates but not how the repository is assembled or what CI enforces. Contributor machines may lack a package manager; agents run in parallel worktrees and must not fight over environments.

**Decision:** One uv workspace at the root (`pyproject.toml`, `uv.lock`) whose members live under `lab/` (`lab/core` today; later apps are `lab/<app>`); dev tooling in the root `dev` group. Ruff for lint and format with a broad rule set; `mypy --strict` on `lab/core/src`; pytest with `parser` and `live` markers. Hooks under `.claude/hooks/` are Python 3.9-compatible stdlib scripts. Pre-commit runs ruff, uv-lock, gitleaks, actionlint. CI required checks, by exact name: `Lint, typecheck, tests`; `Parser fixtures and round-trip`; `Agent harness (hooks on 3.9 and 3.12, lockfile, workflows) (3.9)` and `(3.12)`; `Secret scan (gitleaks)`; `SAST (semgrep)`; `Dependency and config scan (trivy)`. A `lab (windows)` job runs the Lab suite on `windows-latest` and is not required. Squash-only merges, linear history, conversation resolution required, no force-push to `main`.

**Consequences:** `make ci` is the single local truth and matches CI. Version pins live in one lockfile; Dependabot bumps them under CI. Adding a workspace member is a one-line change. No other language or runtime is part of the toolchain; one arrives only by ADR (ADR-0020). Renaming a CI job means changing `.github/rulesets/main.json` and re-applying it, or every PR waits on a check that no longer reports.

**Amendment (2026-09-21):** revised at the Bronze retirement (ADR-0025).

**Superseded by (in part):** ADR-0030 (accepted 2026-09-30) replaces "No other language or runtime is part of the toolchain" with one language per purpose, each admitted by an ADR. Lua (ADR-0026) and inline page templates (ADR-0027) are already admitted that way.

**Amendment (2026-09-30, owner decision):** the non-required `lab (windows)` job is removed from CI. The required checks listed above are unchanged. `docs/LAB_PLAN.md` §9 records what stops being exercised.

**Superseded by (in part):** ADR-0031 (accepted 2026-10-14) brings the `lab (windows)` job back and makes it a required check once it is green (M13-02). The list of required checks above then gains it.

**Amendment (2026-10-15):** `lab (windows)` was green on `main` with M13-02 (#173), so it is added to the required checks in `.github/rulesets/main.json`; the owner re-applies the ruleset with `scripts/bootstrap-github.sh`. A `lab (macos)` job runs too and is not required (`docs/LAB_PLAN.md` §15.3 E3). The Windows job takes about 20 minutes, which every PR now waits on.

**Amendment (2026-10-15, later, owner decision):** waiting on Windows took too long (15 to 30 minutes per PR, plus queueing for a runner), so `lab (windows)` is not a required check after all and leaves the PR path. It moves to its own workflow, `.github/workflows/windows.yml`, which runs after every merge to `main`, nightly, and on demand for a branch (`gh workflow run windows.yml --ref <branch>`). A red run on `main` is a bug to fix next, filed as a ticket. The required checks are again the seven listed above, without `lab (windows)`; the owner re-applies the ruleset with `scripts/bootstrap-github.sh`. ADR-0031's "Windows, tested" still holds: the suite runs on `windows-latest` in CI, and a human runs the first-run flow on a real Windows install (M13-02).

**Amendment (2026-10-17, owner decision; Accepted 2026-10-17 — owner: "i accept all"; the status edit was made by the conductor on the owner's explicit instruction):** off the PR path, the Windows job went red on `main` twice in two days (M13-36 from #203/#204, M13-43 from #235/#237), each time from a test that assumed a POSIX host and that nobody saw until after the merge. `windows.yml` therefore also runs on a pull request that touches `lab/**`, `uv.lock`, the root `pyproject.toml` or `conftest.py` (which decide what is installed and which tests run serially), or the workflow itself, still as a non-required check: a red run is visible on the PR before merge, but nothing waits on it. The merge-to-`main`, nightly and on-demand runs stay. The required checks are unchanged.

---

## ADR-0019 — wowlab is a local-only personal toolchain

**Status:** Accepted (2026-09-21; proposed 2026-09-20) — owner decision

**Context:** The owner wants tooling that reads a WoW install directly, explains every file, snapshots it, and modifies client configuration reversibly, as a base for character-customization and offline-character tools (`docs/LAB_PLAN.md` §1). That is only reasonable to build because it serves one person on their own machine: software that reads and rewrites a game directory is not something to hand to strangers, and a service that received such data would be a liability with no purpose.

**Decision:** wowlab runs only on the owner's machine, as the owner, against the owner's install. It is never distributed as a binary or a package, never deployed as a service, and never uploads anything it reads. The only network traffic is fetching public game tables (ADR-0022). The repository is public, so anything captured from a real install is scrubbed by a tool before it is committed, and the capture tool refuses output that still carries an identifier.

**Consequences:** No accounts, no authentication, no hosting, no release process, no telemetry. Fixtures need a scrub tool and a fixture policy before the first capture (M10-02). Anyone may read or fork the code; nothing here is built or supported for them. A later wish to share a tool with other players is a new ADR that revisits this one, not a quiet change.

**Superseded by (in part):** ADR-0031 (accepted 2026-10-14) replaces "never distributed as a binary or a package": wowlab may be installed by other players from a tagged GitHub Release and runs on each player's own machine, against their own install. No hosting, no service, no accounts, no telemetry and no upload all stand.

**Amended by:** ADR-0032 (accepted 2026-10-18, both stages): "never uploads anything it reads" and "the only network traffic is fetching public game tables" gain exactly one exception, `wowlab char advise --send`, opt-in, configured by the player with their own key, redacted and shown before sending. Nothing else changes; no telemetry, no service, no accounts.

**Amendment (2026-10-17, owner decision; Accepted 2026-10-17 — owner: "i accept all"; the status edit was made by the conductor on the owner's explicit instruction):** what counts as an identifier for the public repository is narrowed. Character names and realm names are visible to anyone in the game and on the Armory, so they may appear as they are in fixtures, tests, docs, PR text and the wiki; the scrub no longer rewrites them, and a capture is no longer refused because one of them lands inside a longer word. What stays scrubbed and refused, because it identifies an account rather than a character: the Battle.net account folder (the `<digits>#<n>` name), any email address or BattleTag, the owner's real name, a home path that carries the owner's login, the owner's own `Player-<n>-<hex>` GUIDs (kept outside the repository as before), other players' GUIDs in combat logs together with the unit name beside each (a GUID plus a name identifies an account), and the identity CVars on the deny-list (`docs/LAB_FORMATS.md` §5). `wowlab doctor --redact` is unchanged: it redacts a player's data for a public report, and what another player hides is their choice. Existing fixtures keep their pseudonyms; nothing is re-captured for this. The conductor's own rule of keeping the owner's log dates and times out of PR text is dropped with it. M13-49 applies this to `scripts/lab_capture.py`, `wowlab_core.redact` and `scripts/check_wiki.py`.

**Amendment (2026-10-18, M13-55; Proposed):** the set that stays scrubbed and refused, as listed in the 2026-10-17 amendment, also covers the value of every `accountName` CVar the capture tool reads: rewritten with an account pseudonym, refused glued into a longer word, and the capture stops on a value it cannot hunt. `accountList` entries follow once their separator is known (M13-62). Character and realm names stay kept as they are; nothing changes in what may appear in the repository.

**Amendment (2026-10-19, M15-02; Proposed):** a *player-clicked link* to an outside page is not an upload and not network traffic of the Lab's. `wowlab ui` may show a link whose address the Lab builds from public game identifiers only (item ids, bonus ids, race, body-type and class ids; never a character, realm or account name, never a path, never the token) and which opens in the player's own browser when, and only when, the player clicks it. The Lab's server makes no request to that address, fetches nothing from it, and embeds nothing from it: the UI's CSP is unchanged (`default-src 'none'`, `connect-src 'self'`), so the page itself cannot reach the outside site. The first such link is the Character tab's dressing-room button (`docs/LAB_PLAN.md` §16.3, 2026-10-19 amendment; `docs/LAB_IDEAS.md` **dressing-room-link**). ADR-0031's trust-model sentence "the only network traffic is fetching public game tables" gains the same clarification: a link the player opens is the browser's traffic, not the Lab's.

---

## ADR-0020 — The core is a flavor-agnostic Python package in the uv workspace

**Status:** Accepted (2026-09-21; proposed 2026-09-20)

**Context:** A Rust core with Python and Node bindings was considered for speed and single-binary distribution. The repository's toolchain, review agents, hooks and CI are Python-first (ADR-0014); the Lab has one user and no distribution requirement (ADR-0019); the hot paths in Wave 1 are a literal parser and file hashing. Separately, the Forever beta installs under a flavor folder that is reported as `_classic_beta_` and will probably change at launch, and the owner also plays retail.

**Decision:** The core library is `wowlab-core` (`lab/core/`, import name `wowlab_core`), a uv workspace member held to ruff, `mypy --strict` and pytest gates. Flavor folder, product code, build and interface version are always discovered from the install at run time and never appear as constants in library code. A second language is introduced only by a superseding ADR that carries measurements showing Python missing a stated target (`docs/LAB_PLAN.md` §6.4 sets the first such target).

**Consequences:** One toolchain for agents and CI. Local CASC reading, which has no mature pure-Python implementation, is deferred to the wave that needs models or textures and will be decided then (binding vs. a bridge to an existing exporter). Every parser must tolerate both retail and Forever captures from day one, which the fixture corpus (M10-03) has to reflect.

**Superseded by (in part):** ADR-0030 (accepted 2026-09-30) replaces the rule for admitting a second language. The measurement requirement stays for languages added for performance in the core.

---

## ADR-0021 — Every write into a game install goes through one write gate

**Status:** Accepted (2026-09-21; proposed 2026-09-20)

**Context:** The owner's requirement is to modify things at will and go back and forth. The client overwrites its configuration files on logout, can hold them open on Windows, and gives no warning when an external edit is lost. Several later tools (declarative config, profile switching, addon scaffolding, inbound SavedVariables for companion features) will all need to write.

**Decision:** `wowlab_core.guard` is the only code in the repository that writes inside an install. A write happens inside a transaction that refuses when the client is running or its state is unknown, accepts only paths under an allowlist (`WTF/`, `Interface/AddOns/`, `Fonts/`, loose `Interface/` overrides), takes a content-addressed snapshot first, journals before/after hashes, replaces files atomically, and rolls back on error. There is no flag that skips the snapshot or the client check. `guard.py` is load-bearing: graders are written by `test-writer` before the implementation, and `security-reviewer` reviews every change.

**Consequences:** Undo is a property of the platform, not of each tool. Tools cannot write while the game is open, by design. The snapshot store must be cheap enough to run on every transaction, which is why it deduplicates by content hash. A later tool that believes it needs to bypass the gate is mis-specified.

---

## ADR-0022 — Game data comes from wago.tools per-build exports, cached by build, never overwritten

**Status:** Accepted (2026-09-21; proposed 2026-09-20)

**Context:** The Lab needs arbitrary client tables (customization options, items, maps, quest text) for whatever build is installed, on whichever product. wago.tools publishes every DB2 table for every build of every product as CSV over HTTP and is what the community's existing Forever tooling uses. Reading the same tables from the local install needs CASC access, which ADR-0020 defers. Game data changes with every build, and anything derived from a table is only meaningful against the build it came from.

**Decision:** `wowlab_core.gamedata` fetches tables from wago.tools by table name and full build string and caches them under the user data directory keyed by `(table, build)`. **A cached table is never overwritten:** a table for build A is never replaced by build B, a second fetch of the same key never replaces the file on disk, and nothing prunes old builds implicitly. URL, time, size and SHA-256 are recorded beside each file. The client is single-connection, identifies itself, backs off on 429/5xx and is never exercised live in CI (ADR-0012). `gamedata` is written against a `Source` protocol so a local CASC source can be added without changing callers.

**Consequences:** Anything computed against an old build stays reproducible; the cache grows per build, which is negligible at this size. The Lab depends on a community service staying up and keeping its URL shape; the recorded fixtures and the breakage log in `docs/DATA_SOURCES.md` are how a change is detected. Server-side hotfixes are not in these exports; overlaying `DBCache.bin` is a later idea. Column typing needs WoWDBDefs and arrives with the first wave that needs typed columns.

**Amendment (2026-09-29, proposed with ADR-0028):** the first wave that needs typed columns is Wave 3 (M12), and ADR-0028 proposes that it types them by inference from the build's own data rather than from WoWDBDefs, because the tables it needs are small and wago's headers already carry the column names. If ADR-0028 is accepted, the last sentence of the Consequences above no longer describes Wave 3; WoWDBDefs typing (foreign keys, exact widths) waits for a wave that needs them. Nothing else in this ADR changes.

**Amendment (2026-09-30):** the owner accepted ADR-0028 on 2026-09-30, so the last sentence of the Consequences above no longer describes Wave 3: it types columns by inference from the build's data, and WoWDBDefs typing waits for a wave that needs foreign keys or exact widths.

---

## ADR-0023 — Scope stops at files the client treats as user-editable

**Status:** Accepted (2026-09-21; proposed 2026-09-20) — owner decision

**Context:** The owner is comfortable connecting tools directly to game files for personal use. Two facts set the limit. This repository is public and Apache-2.0 under the owner's name, so everything in it is a public statement of what the owner builds. And the client's anti-cheat acts on the account, not on the intent: it does not distinguish a curious read of process memory from a cheat, and the cost of being wrong is the account.

**Decision:** In this repository the Lab reads anything in the install and writes only what the client treats as user configuration: `WTF/`, `Interface/AddOns/`, `Fonts/` and loose `Interface/` texture overrides. Permanently out of scope here: reading or writing client process memory, library injection, packet capture or modification, input automation, modifying `Data/` or executables, and defeating integrity checks. Documentation labels each capability `A` (ordinary addon-user behaviour) or `B` (works, unsupported, may be reset by a patch). Experiments beyond this boundary, including private-server sandboxes, belong in a separate private repository and are not planned, ticketed or referenced from this one beyond this sentence.

**Consequences:** Every idea in `docs/LAB_IDEAS.md` is buildable without account risk. Real-time data out of the running client is limited to what the client writes to disk (SavedVariables on logout or `/reload`, combat and chat logs) or shows on screen. `code-reviewer` and `security-reviewer` treat any `psutil` use beyond process listing, any `ctypes`/FFI into the client, and any write outside the allowlist as a finding.

---

## ADR-0024 — Work proceeds in owner-selected waves; ideas are never pre-ticketed

**Status:** Accepted (2026-09-21; proposed 2026-09-20) — owner decision

**Context:** The Lab has a long idea list (`docs/LAB_IDEAS.md`) and one fixed starting point, the core library. The conductor dispatches every eligible ticket in parallel (ADR-0013), so anything written as a ticket gets built. The owner wants to choose each next step after seeing what the last one produced.

**Decision:** Each wave is one milestone (`M10`, `M11`, …). The backlog holds tickets for the current wave only. When a wave's last ticket merges, the conductor writes `docs/handoffs/M<n>-review.md` (what shipped, what was learned, which ideas are now unblocked, a recommended next wave of at most three ideas) and stops dispatch. The owner picks; the conductor lands the plan section, any ADRs and the tickets in one docs PR; dispatch resumes when it merges.

**Consequences:** Throughput is gated on one owner decision per wave, deliberately. Between waves nothing is dispatched. `docs/LAB_IDEAS.md` stays a menu, and an agent found building from it without a ticket is off-task. Wave reviews are where estimates in the ideas file get corrected.

---

## ADR-0025 — Bronze is retired; this repository is the Lab

**Status:** Accepted (2026-09-21) — owner decision

**Context:** This repository began as Bronze, a hosted character workbench for many players: a FastAPI service over Postgres, self-hosted SimulationCraft workers, a Next.js front end and a Go companion agent, specified through milestones M0 to M5. On 2026-09-20 the Lab was added beside it as a local-only second track (ADR-0019 to ADR-0024 as first written). Running both meant one constitution serving two products with opposite trust models: Bronze's credibility rested on a companion agent that read one file and never wrote, while the Lab exists to read everything and write reversibly. Every Lab document had to defer to Bronze invariants that did not apply to it, and the owner's interest had moved to the Lab and to World of Warcraft: Forever.

**Decision:** Bronze is retired in full and this repository is the Lab. The last Bronze state is the git tag `bronze-final`; nothing of Bronze survives in the tree. Removed: `api/`, `worker/`, `tests/stack/`, the Compose stack, the Bronze spec and its product, competitive-landscape and `/simc` format documents, milestones M0 and M1, the README art, the path rules and skills for the API, worker, web, companion agent, migrations and `/simc` fixtures, the merged-migration protection in the hooks, and the `Web` CI job. ADR-0001 to ADR-0011 and ADR-0015 to ADR-0018 are retired; their numbers are not reused and **they bind nothing**: no agent should read them as constraints, precedents or defaults. Three harness ADRs carry over, revised: ADR-0012 (no live calls in tests), ADR-0013 (the conductor model; the load-bearing set is now `luadata.py` and `guard.py`) and ADR-0014 (the toolchain and required checks, now Python only). ADR-0019 to ADR-0024 keep their numbers and decisions and lose their references to Bronze; L1 to L8 become the hard invariants in `AGENTS.md`. Milestone `M10` and its ticket ids are unchanged. The package layout stays `lab/core/` so that later apps are `lab/<app>/`.

**Consequences:** One product, one constitution, one trust model. The required CI check names changed, so the branch ruleset must be re-applied by the owner before any PR can merge (`scripts/bootstrap-github.sh`). Ideas that Bronze would have owned, namely offline character planning and running simulations, become Lab ideas (`char-planner`, `simc-bridge` in `docs/LAB_IDEAS.md`) and wait for a wave like everything else. Anyone who wants Bronze back starts from the tag, in another repository.

---

## ADR-0026 — The lab-addon is Lua that runs in the game client, never in the Lab

**Status:** Accepted (2026-09-28; proposed 2026-09-28) — owner decision; the status edit was made by the conductor on the owner's explicit instruction

**Context:** The owner picked the lab-addon for Wave 2 (M11, 2026-09-28): the Lab's own data-broker addon, which records gear, talents, customization choices, collections, currencies and professions into SavedVariables at logout, so the offline tools have a feed. An addon is Lua by necessity: it is loaded by the client's own Lua 5.1 environment. ADR-0020 admits a second language only with measurements showing Python missing a target, which is a rule for the core library and does not describe this case. L3 forbids executing Lua in the Lab.

**Decision:** Lua code is allowed in exactly one place: the addon sources under `lab/addon/` (one folder per addon, first `WowLab`). It is data-producing code for the game client only. The Lab never loads, runs or evaluates it: no Lua interpreter in `wowlab_core`, in tests or in CI jobs that grade Lab behaviour (L3 stands). The addon is verified three ways: a static Lua linter in CI on `lab/addon/` only (the tool is chosen in its ticket; a single pinned binary or a pinned rock, no network at test time); the Python reader in `wowlab_core` graded on real SavedVariables captures the addon wrote (L8, captured and scrubbed with `scripts/lab_capture.py`); and review. The addon stores no player names, realm names, GUIDs, guild names or chat of anyone, the owner included: the character is identified by the folder the client puts the file in. Its TOC `## Interface:` value is filled at install time from the discovered version by the patch-number rule (L6); the repository holds a template, never a number. Installing and removing it goes through `guard` (ADR-0021). A headless test harness that runs addon code under a real interpreter is a separate later idea (`addon-harness`) and needs its own ADR.

**Consequences:** One new language, fenced to one folder, with no runtime of its own in the Lab. Addon behaviour that depends on the live API (for example whether the player's own customization choices can be read at all) is only proven by the owner's capture, so M11 has an owner capture ticket like M10-03. The linter adds one CI step and one pinned tool.

---

## ADR-0027 — Generated local pages are self-contained static HTML

**Status:** Accepted (2026-09-28; proposed 2026-09-28) — owner decision; the status edit was made by the conductor on the owner's explicit instruction

**Context:** The owner asked for customization-sandbox (Wave 2) to have a local web page besides the CLI (2026-09-28). ADR-0020 keeps the repository Python-only and ADR-0019 keeps the Lab local, with no server and no upload. `docs/LAB_IDEAS.md` has more page-shaped ideas (explorer, alt-dashboard) that would repeat this choice.

**Decision:** A page the Lab shows is one self-contained `.html` file generated by Python from data it already holds, with inline CSS and plain JavaScript written in the Python package as template text. No build step, no npm, no framework, no bundler, no external request of any kind (no CDN, fonts or analytics; a Content-Security-Policy meta tag forbids them), no server: the owner opens the file. Pages are written only under the user data directory or a path the owner names, never into an install. Tests grade the generated file's data (parsed back from an embedded JSON block) and its CSP, not the browser behaviour.

**Consequences:** Pages cost one template and one generator per feature and cannot leak data off the machine. Interactivity stays modest; anything needing a real front-end toolchain needs a superseding ADR.

**Superseded by (in part):** ADR-0029 (accepted 2026-09-30) replaces the "no server" clause for `wowlab ui` only. Every other page stays a static file.

---

## ADR-0028 — db2lake is SQLite over the cached tables, with column types inferred from the build's data

**Status:** Accepted (2026-09-30; proposed 2026-09-29) — owner decision; the status edit was made by the conductor on the owner's explicit instruction

**Context:** The owner picked db2lake for Wave 3 (M12, 2026-09-29; `docs/LAB_PLAN.md` §14). `docs/LAB_IDEAS.md` names DuckDB and WoWDBDefs typing, and ADR-0022 says column typing needs WoWDBDefs and arrives with the first wave that needs typed columns. A live check by hand on 2026-09-29 (`wowlab db2 head`, build 1.60.1.70058, §14.1) found the tables Wave 3 needs are small: the whole talent dataset is under 100 KB, and the largest table looked at (`SpellMisc`) is 3 MB. wago's CSV headers already carry column names, including WoWDBDefs placeholder names such as `Field_10_0_0_45697_006`. A SQL prompt runs text typed by the owner, or by a tool acting for the owner, against a database, so the surface must not write files, load code or reach the network (L1, L3, ADR-0019). ADR-0020 keeps the core to the runtime dependencies in `docs/LAB_PLAN.md` §6 and to one language.

**Decision:** `wowlab_core.db2lake` uses the standard library's `sqlite3`; it adds no dependency. There is one database file per full build string, under the user data directory and never inside an install, derived from the CSV tables `gamedata` has cached (ADR-0022); a table already loaded into it is never replaced (L5), and deleting the file loses nothing. Column names are wago's, unchanged. Column types are inferred from that build's data by a fixed rule: a column whose every non-empty cell is a canonical decimal integer (an optional minus, no leading zero except for `0`, within 64 bits) is INTEGER; one whose every non-empty cell is another finite decimal number is REAL; every other column is TEXT; an empty cell is NULL in a numeric column and the empty string in a TEXT column. The cached CSV stays the authority and the schema command shows the inferred type of each column. WoWDBDefs typing (foreign keys, exact widths) is deferred to a wave that needs it. SQL the Lab runs for the owner is read-only: the connection is opened read-only, an authorizer admits only reads (SELECT, READ, FUNCTION, RECURSIVE and a short allowlist of PRAGMAs), extension loading is off, the owner's text cannot ATTACH (cross-build attachments are made by the Lab, read-only), and every statement runs under an operation budget, a wall-clock deadline and a row cap.

**Consequences:** No new dependency, and the surface that runs owner-typed SQL is small enough to review adversarially. SQLite's dynamic typing means a column typed differently in two builds compares by SQLite's rules; the schema output is where that is visible. Inference can type a text column INTEGER when a build happens to hold only numbers in it; the CSV is unaffected. DuckDB (faster columnar scans, richer SQL) stays an alternative: adopting it later needs its own ADR, because its SQL can read and write files and fetch extensions unless external access is switched off, and because it adds a large compiled dependency. `docs/LAB_PLAN.md` §6.6 and ADR-0022's last sentence are amended by reference to this ADR.

---

## ADR-0029 — `wowlab ui`: a local UI server over the CLI

**Status:** Accepted (2026-09-30; proposed 2026-09-30) — owner decision; the status edit was made by the conductor on the owner's explicit instruction

**Supersedes:** ADR-0027, in part: its "no server" clause, for `wowlab ui` only. Every other page stays a static file.

**Context:** On 2026-09-30 the owner asked for a UI over everything the Lab does, and for every new feature to reach it. They chose a local app over a static console page, and scheduled it as Wave 4, after M12. A static page (ADR-0027) can show everything but cannot run anything. The owner wants the write commands (`snap restore`, `undo`, `profile apply`, `addon install|remove lab`, `sv merge`) to be usable from the UI too.

The constraints that shape it:
- ADR-0019 forbids deploying a service or uploading anything.
- ADR-0021 and L2 keep every write in `guard`.
- ADR-0020 and ADR-0030 keep the code to one language per purpose; D3 in `docs/LAB_PLAN.md` §14.5 keeps rules out of untested JavaScript.
- The runtime dependencies are limited by `docs/LAB_PLAN.md` §6.
- About 45 commands exist today, each with `--json` backed by a Pydantic model (§6, "Every command that prints data has `--json`"). A UI that hand-codes each one would drift from them.

**Decision:**
- **The server.** `wowlab ui` starts an HTTP server from the standard library (`http.server.ThreadingHTTPServer`), bound to `127.0.0.1` on a free port, and opens the browser at it. It runs only while the owner keeps the command running. It is never started by anything else, never bound to another interface, and never reachable from another machine.
- **Every request is checked.** The server generates a random token (`secrets.token_urlsafe(32)`) at start and puts it only in the URL it opens; every request must carry it. It rejects any request whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>`, and any request whose `Origin`, when present, is not that same origin. It sends no CORS headers. Every page carries a Content-Security-Policy that allows the server itself and nothing else.
- **What it runs.** The UI builds its commands from the CLI's own Click tree at start, so a new command appears with no UI code. It runs a command only as a subprocess of the `wowlab` it was started from, with `--json`. The argument vector is built only from the parameters that command declares, with values passed as separate arguments and never through a shell. Nothing else can be executed.
- **Writes.**
  - The server itself writes nothing inside an install. Every write is a CLI write command, so it goes through `guard` as the CLI does, and there is no second path (L2).
  - A command is treated as a write when it has a `--yes` option. The UI runs it first without `--yes`, to show `guard`'s plan, and runs it with `--yes` only after the owner confirms.
  - The result offers `wowlab undo`. A refusal (exit 3) is shown as the CLI prints it.
- **The front end.** Plain HTML, CSS and JavaScript, written as template text inside the Python package as ADR-0027 already allows. No build step, npm, framework or external request. The JavaScript draws forms and renders results; it holds no game rules.
- **Parity.** A test in the required `quality` job asserts that every leaf command is reachable in the UI or appears in an exclusion list with a written reason, and that every `--yes` command goes through the confirm step.
- **Review.** The server module is written graders-first by `test-writer`, and `security-reviewer` reviews every change to it.

**Consequences:**
- The UI covers new commands automatically, and the parity test keeps it that way.
- Because the UI shells out to the CLI, it behaves exactly as the CLI does, including refusals and exit codes. The cost is one process per action, which is acceptable for one local user.
- A local port is a new attack surface. The token, the Host and Origin checks and the fixed argument vector are there for DNS rebinding and for other local pages and processes; `security-reviewer` owns them.
- Richer views, such as the looks, talents and character pages, are embedded or linked rather than rebuilt.
- Anything that needs a front-end toolchain, or a server that another machine can reach, still needs its own ADR, and the latter also revisits ADR-0019.
- ADR-0027 carries a "Superseded by (in part)" note.

**Amendment (2026-09-30):** `--yes` marks a command that asks before changing files, not only one that writes into an install. On `main` there are seven: `snap restore`, `undo`, `profile apply`, `addon install`, `addon remove`, `sv merge` and `snap gc`. `snap gc` changes only the Lab's snapshot store; it runs under `guard.store_lock`, not a `guard` transaction. The UI's confirm flow applies to all seven, which is what the decision above intends. "Every write is a CLI write command" means every change the UI causes, to an install or to the store. Found while checking M12-13 against the Click tree. The decision itself is unchanged.

**Amendment (2026-10-15, Accepted 2026-10-15 — owner decision; the status edit was made by the conductor on the owner's explicit instruction):** On 2026-10-14 the owner pulled `wowlab ui` forward from Wave 5 (M14) into Wave 4 (M13) and put it in v0.1. Its first view is the install explorer (`docs/LAB_PLAN.md` §15.6). The UI ships in two stages:
- **v0.1 (M13-21 to M13-26):** read-only commands only. The server, the token, Host and Origin checks, the fixed argument vector and the parity test are as decided above. Every leaf command that declares `--yes`, and `ui` itself, sits in the parity test's exclusion list, with the reason "confirm flow: M14" for the `--yes` commands. The set is derived from the Click tree, not from a count. On 2026-10-15 it holds eight commands: the seven named in the amendment above and `doctor`, whose `--yes` (from M13-04) takes a first snapshot into the store. While they are excluded, the server refuses to run them.
- **Wave 5 (M14):** the confirm flow for those `--yes` commands, after which they leave the exclusion list, plus the embedded looks, talents and character pages.

The decision itself is unchanged. This amendment only stages it, so that the first public build of a localhost server has no path that changes files.

*Note (2026-10-15, later):* the explorer view was split in two (owner: "get there sooner"), so v0.1 is now M13-21 to M13-27. M13-27 is read-only too, and nothing above changes.

**Owner rulings (2026-10-15), from the M13-24T review; the conductor recorded them on the owner's instruction:**
- **What "read-only" means for v0.1.** v0.1 keeps out every command that declares `--yes`, and anything that writes into an install. It may run commands that write only to wowlab's own store or user data directory, such as `snap create`, `profile save` and `profile delete`, `looks save` and `looks import-char`, `looks page` to its default place under the data directory's `pages/`, and `db2 fetch` (the cache). `looks page --out` can create a file anywhere outside an install, so the UI refuses `--out` in v0.1. "No path that changes files" above therefore reads "no path that changes the game's files". The parity test and the server keep the `--yes` set exactly as stated.
- **The token in the terminal.** When the browser cannot be opened, `wowlab ui` may print the token URL once, on its own stdout. It is never written through logging, never into a file, and never into a child's argv or environment. Everywhere else, "only in the URL it opens" stands.
- **Settled with the security review, not new decisions:**
  - every page sends `frame-ancestors 'none'`;
  - an `Origin`, when present, must equal `http://` plus the request's `Host`;
  - `log tail --follow` is refused in v0.1, because a run must end;
  - the subprocess is the running interpreter, `sys.executable -m wowlab_core.cli` or an equivalent absolute path, never a PATH lookup.

**Owner rulings (2026-10-16), from the M13-24 reviews; the conductor recorded them on the owner's instruction:**
- **One permitted FFI site.** `lab/core/src/wowlab_core/ui/_winjob.py` may use `ctypes` on Windows. It may do so only to put the server's own child runs in a Job Object, resume them and terminate them. It calls only on the job handle it creates and on the run's own process handle. It never opens another process, never touches memory and never aims at the client. This is the only FFI in the Lab. Any other site, or any widening of this one, needs a new owner decision. ADR-0023 stands: no FFI aimed at the client. The two enforcers name this file only. `lab/core/tests/test_write_sites.py`, the L2 write-site scanner and not a `ui` grader, gets an allowlist row for it with a reason; M13-24 added that row, and both reviewers accepted it. `.claude/agents/code-reviewer.md` names the exception.
- **The token on Linux.** When `wowlab ui` opens the browser on Linux, `xdg-open` and a newly started browser carry the token URL in their command line for a while. Other accounts on the same machine can read it. This is accepted for v0.1 as a known risk on shared Linux machines. The README states it, as an acceptance criterion of M13-10. macOS and Windows are not affected. A single-use opened link remains an option for a later release.
- **Recorded residual.** On POSIX, a process that a run started outlives a run that exits on its own. A run's process group is killed only at a timeout, at the output cap, or when the server closes. None of the commands the UI offers starts a process.

**Amendment (2026-10-18; Accepted 2026-10-18 — owner decision; the status edit was made by the conductor on the owner's explicit instruction):** the 2026-10-15 amendment names "the embedded looks, talents and character pages" for Wave 5. The character is built instead as a native tab of the UI, drawn from CLI `--json` models (`wowlab char gear`, `char binds`, `char macros`) as the explorer is (`docs/LAB_PLAN.md` §16.3, decision F-A), because the editing stage planned after it needs the server and a static page would duplicate the explorer's JSON rendering. The looks page stays embedded (M14-04, by the route §16.3 gives, with no response header loosened). The talents page waits for char-planner (§14.8). "Richer views … are embedded or linked rather than rebuilt" is read as "a view that exists as a page is embedded; a view that does not yet exist may be a tab". The decision itself is unchanged.

---

## ADR-0030 — Languages: one per purpose, each admitted by an ADR

**Status:** Accepted (2026-09-30; proposed 2026-09-30) — owner decision; the status edit was made by the conductor on the owner's explicit instruction

**Supersedes:** ADR-0020, in part: its sentence "A second language is introduced only by a superseding ADR that carries measurements…". ADR-0014's "No other language or runtime is part of the toolchain" is replaced by the rule below.

**Context:** ADR-0020 was written for the core library, where the only reason to add a language was speed. Since then, two languages have arrived for reasons that have nothing to do with speed:
- ADR-0026 (2026-09-28) admitted Lua under `lab/addon/`, because an addon is Lua by necessity.
- ADR-0027 (2026-09-28) admitted inline HTML, CSS and JavaScript as template text, because a page is a browser document.

`AGENTS.md` ("There is no other language or runtime in this repository") and ADR-0014 were never updated, so the constitution now contradicts what is on `main`. An agent reading it literally would flag the addon or the looks page as a violation, and a reviewer reading ADR-0020 would demand performance measurements for a language admitted for a different reason. The owner asked on 2026-09-30 for decisions that limit the project to be reviewed. This one limits by being inaccurate, not by being strict.

**Decision:**
- **The rule.** Each language in this repository is admitted by an ADR that names its purpose, the one place it lives, how it is verified, and what it may not do.
- **The current set.**
  - **Python 3.12:** the core, the CLI, the scripts and the tests (ADR-0014, ADR-0020). The hooks stay stdlib-only Python and must also run on 3.9.
  - **Lua 5.1:** only under `lab/addon/`. It runs only in the game client and is never executed by the Lab (ADR-0026, L3).
  - **HTML, CSS and plain JavaScript:** only as template text inside the Python package. No build step, and no rules that the Python does not also enforce (ADR-0027, ADR-0029).
- **Adding one.** A new language, a runtime or a build toolchain (npm, a bundler, a compiler) needs a new ADR stating the same four things.
- **Performance.** When the reason is performance in the core, ADR-0020's rule stands: the ADR carries measurements showing Python missing a stated target.

**Consequences:**
- The constitution matches the tree again. `AGENTS.md` "Conventions" says "one language per purpose, each admitted by an ADR (ADR-0030)" instead of "no other language".
- Adding a language stays exactly as hard as before; only the reason that has to be given changes with the purpose.
- A later wave that wants TypeScript, a 3D viewer or a CASC binding writes an ADR naming where it lives and how it is tested. The owner decides.

**Amended by (2026-10-19; Proposed):** ADR-0034 admits one vendored third-party JavaScript library (three.js, pinned) under its own rules; ADR-0033 keeps the CASC reader in Python, so no binding is admitted.

---

## ADR-0031 — wowlab may be installed by other players; the trust model is the product

**Status:** Accepted (2026-10-14; proposed 2026-10-14) — owner decision of 2026-10-13; the status edit was made by the conductor on the owner's explicit instruction

**Supersedes:** ADR-0019, in part: the clause "It is never distributed as a binary or a package", and the words that exist only because of it (listed below under Decision, "Who runs it", and Consequences, "Wording"). Everything else in ADR-0019 stands: no hosting, no service, no accounts, no telemetry, and nothing read from an install is ever uploaded. Also ADR-0014's amendment of 2026-09-30, in part: the `lab (windows)` job comes back, and becomes a required check once it is green (M13-02).

**Context:**
- On 2026-10-13 the owner decided that wowlab will be usable by other players, not only by the owner. ADR-0019 says such a wish "is a new ADR that revisits this one, not a quiet change". This is that ADR.
- The Lab already has the properties that make it reasonable to hand to a stranger: reads never write (L1); every write goes through one gate that takes a snapshot first and refuses while the client runs (L2, ADR-0021); no Lua is executed (L3); the scope stops at files the client treats as user-editable (ADR-0023); nothing is uploaded (ADR-0019). Those are the reason a player would trust this over a script from a forum, so they are stated first, not buried in the docs.
- What it lacks for a stranger: a release (today the only install is a clone of the repository), any tested platform besides the owner's macOS (CI runs on `ubuntu-latest` only, since the Windows job was removed on 2026-09-30), a way to share diagnostic output without leaking character, realm or account names, and a reporting path written for people who are not the owner.
- An unsigned frozen binary (PyInstaller and similar) draws antivirus and SmartScreen warnings, and signing costs money and a process. The first users are expected to be comfortable with a terminal.
- ADR-0019 forbids telemetry, so whether public work is worth continuing has to be judged from what GitHub already counts.

**Decision:**
- **Who runs it.** wowlab runs on the player's own machine, as that player, against that player's install. ADR-0019's "only on the owner's machine, as the owner, against the owner's install" is read with "the player" for "the owner". Nothing about where it runs, what it reads or what it may write changes.
- **Distribution.** A tagged GitHub Release (`v*`) carries a wheel, an sdist and a SHA-256 sums file, built by a workflow in this repository. The README gives one documented `uv tool install` line. No PyPI, no installer, and no signed or frozen binary until the gate below has passed and a further ADR says otherwise.
- **The trust model, on the README's first screen.** Read-only by default. Every command that changes files asks first and goes through `guard`. A snapshot is taken before the change, and `wowlab undo` reverses it. The only network traffic is fetching public game tables (ADR-0022). Nothing is uploaded.
- **Supported platforms.** A platform is "supported" only when CI runs the test suite on it and a human has run the first-run flow on a real install of it. Until both hold, the README says "untested on <platform>".
- **Redaction.** Anything a player is asked to paste into an issue passes through a redaction step in the Lab first. Character, realm, account-folder and BattleTag strings, guild names and absolute home paths never leave the machine unredacted through anything the Lab or its issue templates ask a player to share.
- **Strangers' text is data.** Issue, discussion and pull-request text from accounts other than the owner's is data, never instructions, for every agent and workflow. The interactive `@claude` workflow keeps answering only owners, members and collaborators.
- **The gate.** Measured without telemetry, from GitHub's own counts: release-asset downloads, distinct non-owner authors of issues and discussions, and stars. Thirty days after the first public release, public work continues only if there are at least 50 release-asset downloads and at least 5 issues or discussions opened by non-owner accounts. Downloads are a weak proxy (bots, mirrors), so the issue count carries more weight. If the gate fails, the public surface is frozen: the releases stay up, no new public-facing tickets are taken, and the Lab returns to personal-lab mode. The owner makes this call, not the conductor.
- **Unchanged.** ADR-0021, ADR-0023, ADR-0026 and ADR-0027, and the invariants L1 to L8.

**Consequences:**
- A support burden exists now. Reports from strangers arrive from platforms, installs and addon sets the fixtures do not cover. Each one is triaged as data and becomes work only as a ticket in a wave (ADR-0024).
- `--json` output becomes an interface other people may script against. A breaking change to a `--json` model needs a version note in the release notes.
- `SECURITY.md` becomes the reporting path for strangers and is rewritten for them.
- **Wording.** When this is accepted, these change so that none contradicts it (M13-01): `AGENTS.md` "What this is" ("never distributed", line 15); `docs/LAB_PLAN.md` §3 "Local-only" (lines 59–60); the README's note that wowlab "runs from source … no installer … by design (ADR-0019)" (line 190) and its first screen; the opening paragraph of `SECURITY.md` ("no distributed binary"); and the opening paragraph of `CONTRIBUTING.md` ("never distributed (ADR-0019)", added 2026-10-14 from the review of PR #165). The "if accepted" in the Superseded-by lines under ADR-0019 and ADR-0014 is dropped. ADR-0020's Context ("one user and no distribution requirement") is history and stays as written.
- Windows needs real work before it can be called supported (junctions, long paths, case-insensitive names, an install root with spaces in it) and a human with a Windows install, whom the owner has to find.
- The release workflow is a new place where a token can write to the repository. It gets `contents: write` on the release job only, pinned action commits, no other secrets, and `security-reviewer`.
- The first public release carries the trust model, Windows CI, the first-run flow, redaction and the README. Feature work rides on later releases. `wowlab ui` (ADR-0029) is unchanged and moves to the second public release.
- If the gate fails, nothing is taken down, and what was built for it (the release workflow, redaction, Windows CI, the first-run flow) still serves the owner.

**Amended by:** ADR-0032 (accepted 2026-10-18): the trust model's "nothing is uploaded" and "the only network traffic is fetching public game tables" gain the one opt-in exception named there, `wowlab char advise --send`, and the README's first screen says so when that command ships.

**Amended by (2026-10-19, M15-02; Proposed):** ADR-0019's amendment of the same date: a player-clicked link to an outside page, built from public game identifiers only, is the browser's traffic, not the Lab's; the trust model's sentence is read that way.

---

## ADR-0032 — A model may advise on a character's bindings and macros; how it sees the data

**Status:** Accepted (2026-10-18; proposed 2026-10-18) — owner decision, both stages ("Both stages", in answer to the conductor's question); the status edit was made by the conductor on the owner's explicit instruction. Stage two is built after stage one, off until configured; the README trust sentence and `doctor`'s report change when stage two's ticket ships; the ADR-0019 and ADR-0031 cross-notes and the "accepted, not yet built" sentences in `AGENTS.md` and `.claude/rules/lab.md` were added on acceptance.

**Context:** On 2026-10-18 the owner asked for the player's character in `wowlab ui` (gear, key bindings, macros), read-only first, with the intention that a later wave changes and optimizes bindings and macros from the UI "using AI to help improve and add to them" (`docs/LAB_PLAN.md` §16.5). A model can only advise on data it has seen, and the data is the player's own bindings, macros, spellbook and action bars, read from their install. ADR-0019 says wowlab "never uploads anything it reads"; ADR-0031 kept that clause unchanged and put it on the README's first screen as the trust model. Sending a character's data to a hosted model is an upload under that sentence, whoever presses the button. ADR-0012 forbids a live service in tests or CI. The runtime dependency list (`docs/LAB_PLAN.md` §6) has no HTTP client beyond `gamedata`'s. ADR-0031 also says text from strangers is data, never instructions; a model's reply is text from a stranger in that sense.

**Decision:** Two stages. The first changes no accepted decision; the second amends ADR-0019 narrowly and takes effect only if the owner accepts this ADR as a whole.

1. **Advice without upload (stage one).** `wowlab char advise` writes an *advice packet*: one self-contained text file under the user data directory holding the character's gear, keys and macros reports in compact form, the coach's and the checker's findings, the cost table in use, and the grammar of a *proposal* with worked examples. The player takes it to whichever assistant they use, by hand, and brings a proposal back. The Lab makes no network request, holds no key and knows no provider. The only path from any suggestion, human or model, to a change is the proposal validator (`docs/LAB_PLAN.md` §16.5 F3) and then a `guard` transaction behind `--yes` (L2, ADR-0021), or the print-only form if the owner's persistence tests say the client replaces the file. A proposal is data, never instructions, for every agent and every tool.
2. **Advice with a hosted model, opt-in (stage two).** `wowlab char advise --send` posts the same packet, after `wowlab_core.redact` (character, realm and account placeholders; the packet holds no GUID and no path), to one hosted model the player configured in the user config directory with their own key. It is off until configured; it shows the exact bytes it will send and asks before sending; it sends nothing else, ever, and never on a timer or at start; the key is never read from a child run's environment, never logged, never printed; the request uses the standard library (`urllib`) with certificate verification on, no SDK dependency; the reply is parsed as a proposal and validated like one typed by hand, and a reply that is not a valid proposal is shown as text and plans nothing. No test and no workflow ever calls the service (ADR-0012); the sender is graded against a recorded reply. The README's trust sentence changes to: "Nothing is uploaded, with one exception you turn on yourself: `wowlab char advise --send` sends the advice packet it shows you to the model you configured." `wowlab doctor` reports whether the switch is on, and the sentence "the only network traffic is fetching public game tables" (ADR-0019, ADR-0022, ADR-0031's trust model, `.claude/rules/lab.md` "the only network client is `gamedata`") gains the same exception wherever it appears.

Stage two amends ADR-0019 ("never uploads anything it reads" and "the only network traffic is fetching public game tables") and the matching clauses of ADR-0031 by exactly that exception and no other. A second provider, a second kind of data in the packet, or any send the player did not initiate from a command needs a new ADR.

**Consequences:**
- Stage one gives the full modify loop (advise, propose, validate, confirm, undo) with the trust model untouched, so the validator and the writers are built and graded before any upload exists. The cost is a copy-and-paste step for the player.
- Stage two adds the first outbound request that carries install data, a key on disk in the user config directory, and a provider API that will change under us. It is one command, one switch and one sentence in the README, and `security-reviewer` owns it.
- The proposal validator becomes a load-bearing file in the ADR-0013 sense if the owner says so: it stands between a model's text and a `guard` write. The conductor raises that when F3 is ticketed.
- If the owner accepts stage one only, the `--send` option is not built and the README sentence does not change. If the owner leaves this ADR Proposed, §16.5 F4 is not ticketed and the read-only character view (M14) is unaffected.

---

## ADR-0033 — The Lab reads the install's game data archives; how their parsers are graded

**Status:** Proposed (2026-10-19). The owner chose the direction in session ("1 yes do this. 2 all three.") before this text was refined from the research pass; acceptance of the text is the owner's word on the plan PR.

**Context:** On 2026-10-19 the owner picked **character-studio** as the next wave (`docs/LAB_IDEAS.md`): the player's character rendered in the browser from the player's own game files. The models and textures live in the client's CASC archives under `Data/`, which nothing in the Lab has opened: every module so far reads the configuration tree, the lab-addon's record and the public game tables (ADR-0022). ADR-0023 already says the Lab "reads anything in the install" and writes only what the client treats as user configuration, and L7 forbids editing anything under `Data/` or bypassing the client's integrity checks. Reading the archives therefore needs no change of scope, but it needs three decisions: how they are read, where what is read goes, and how parsers of formats whose real files are Blizzard's assets are graded under L8, which wants real captures in the repository.

The owner asked whether `Data/` could also be edited. The conductor recommended against it, and the owner's instruction was to go ahead with the recommendations: what is verifiable: every file has an MD5 content key (TACT), so a changed file no longer matches it, and Battle.net's Scan and Repair, run by the player, and patches restore such files (whether the client checks the key on load is **[verify]**); what is from memory and marked so: that the modern client has no override mechanism for models or textures **[verify]**, that Warden or another client check treats a changed archive as tampering **[verify]**, and that Blizzard's EULA forbids modifying the game client **[verify]**; that the Lab is installed by other players (ADR-0031) and a tool that patches game files is a different product; and that what the owner wants is reachable without it, by rendering gear and customization whose files are local, equipped or not, and through the game's own systems. L7 stands as written.

**Decision:**
1. **A pure-Python CASC reader, read-only.** `wowlab_core.casc` opens the install's `Data/` with the same rules as every other module (L1): files are opened read-only, for the length of one read, and closed; no handle or memory map outlives a command (on Windows an open handle or mapping would block the agent or the client from replacing a file, which is a lock in practice); no temp file, cache, lock or index is written inside the install; nothing is decrypted (an encrypted block is reported by its key name; no key list and no scraping of `DBCache.bin`, which would sit on ADR-0023's integrity line), and nothing is fetched from Blizzard's CDN: a file the client's local files lack is reported as *not in your client's local files* or *not part of this game version's files* (§17.1's four states) and no network source is added (ADR-0019, ADR-0022). It resolves a file data id through the build's configuration, root, encoding and index files to the archive bytes and decodes BLTE. There is no bridge to an outside tool (wow.export or CascLib): that would be a second runtime, and ADR-0030 would want its own ADR; a later ADR may add one with measurements if Python misses a stated target (§6.4).
2. **Extracted files live under the user data directory, keyed by product and build (L5).** Anything the Lab decodes from the archives (a model, a texture, a glTF it writes) is stored under the data directory in a folder keyed by product and build and is never overwritten by another build; the one exemption is `characters/`, derived output that `char model` re-makes on request (§17.1). Nothing extracted is ever uploaded (ADR-0019, ADR-0031) and nothing extracted is ever redistributed by the Lab: the files stay on the player's machine, and the Lab's own package ships none of them.
3. **Grading parsers of asset formats: an L8 exception, in three layers.** Game assets (models, skins, textures, the archive index and root files) are Blizzard's and never enter the repository, so for the formats this wave parses (the build config under `Data/config/`; CASC's index, encoding and root files; BLTE; M2 and its skin files; BLP) L8's "real capture with a provenance row" is met in three ways together: (a) **constructed** files, small and labelled as such, for structure, boundaries and hostile input; (b) **derived** fixtures: for each real file a parser is graded on, a JSON record of what was read from it, never the bytes: its file data id, size, chunk table, counts, hashes of its sections, and the decoded values a test asserts, with a provenance row naming the build and the capture date, produced by a script in `scripts/` from the owner's install; (c) **live** tests marked `@pytest.mark.live` that open the owner's install and are run by hand, never in CI (ADR-0012's rule for wago.tools applies). A parser is not done until it has all three. No other format gets this exception without a new owner decision.

**Consequences:**
- L1 and L7 are untouched: the reader opens the archives read-only and the Lab still writes only to the user-configuration subtrees through `guard`.
- `AGENTS.md`'s L8 gains a sentence naming this exception for asset formats, and `docs/LAB_PLAN.md` §9 gains the derived-fixture script.
- The reader is the first module whose correctness cannot be shown in CI on real data. The derived fixtures carry that weight; the conductor keeps the live tests on the owner's TODO list after any CASC change, as `make test-parser` is for `luadata.py`.
- The first wave on this base (§17) is data-only: no file from the archives reaches a browser until the viewer ADR (ADR-0034) is built on.

---

## ADR-0034 — A pinned three.js, vendored inside the package and served by the UI, draws the character in `wowlab ui`

**Status:** Proposed (2026-10-19). The owner chose the direction in session ("3 three.js … go ahead with all of your suggestions") before this text was refined from the research pass; acceptance of the text is the owner's word on the plan PR.

**Context:** ADR-0030 admits HTML, CSS and plain JavaScript only as template text inside the Python package with no build step, and says a 3D viewer needs an ADR naming where it lives, how it is tested and what it may not do. Rendering a character needs a WebGL scene graph, materials, a glTF loader and camera controls; writing those by hand is weeks of code that an MIT-licensed library already provides.

**Decision:**
1. **What is admitted.** One pinned release of **three.js** (MIT), **r185** (0.185.0), the last release that ships official minified builds (`build/three.core.min.js`, 385 KB, and `build/three.module.min.js`, 366 KB; r186 ships none), with `GLTFLoader.js` (115 KB), `OrbitControls.js` (41 KB) and the two files the loader imports, `BufferGeometryUtils.js` (38 KB) and `SkeletonUtils.js` (12 KB), from its `examples/jsm` (about 955 KB in all), copied unchanged into the package under `wowlab_core/ui/templates/vendor/` with its LICENSE file beside it. No bundler, no npm at build or run time, no CDN at run time. The add-ons import the bare specifier `'three'` and each other by relative path, and the vendored files are never edited, so the page carries an **import map** (one inline script, hashed in the CSP like the page's own script) that maps `three` to the served module file. The UI server serves the files under a route it names itself. **How the CSP admits them is [verify] in the first viewer ticket, in a real browser:** the intended mechanism is `script-src` hashes matched against `integrity` metadata on the module script and import-map entries, which keeps the policy hash-only; if a browser does not honour that for module imports, the fallback is `'self'` scoped by a dedicated vendor path, and that loosening is an owner ruling recorded here. They are not template text in ADR-0027's sense (the Lab writes none of them), which is why this ADR exists; ADR-0030's current set gains the line "vendored third-party JavaScript, only under ADR-0034".
2. **How it is verified.** A test holds the SHA-256 of each vendored file and the release tag it came from; `scripts/check_vendor.py` compares the files with the upstream release artefact when run by hand (never in CI, ADR-0012). Upgrading the pin is a PR that changes the hashes and says why.
3. **What it may not do.** The vendored code is never edited. It loads only what the Lab's server serves (glTF and textures the Lab wrote under the data directory, read through token-checked routes the server builds; never a request parameter path), makes no request outside the server, and the page's own code keeps the §14.5 D3 rule: no game rule in JavaScript; which geosets are shown, which textures are composited and which items attach is decided in Python and written into the glTF.
4. **Python writes the glTF.** `wowlab_core.gltf` writes standard glTF 2.0 (`.glb`) with stdlib only; the browser side converts nothing.

**Consequences:**
- The package grows by the library's size (about 1 MB); the wheel stays well under PyPI's limits. The README names the licence.
- The CSP gains the import map's hash and the vendored files' hashes (or the scoped `'self'`, if the owner rules so after the browser check) and nothing else; `frame-ancestors 'none'` and the token rules of ADR-0029 are unchanged.
- A later wave that needs animation or a different library writes an amendment here.
