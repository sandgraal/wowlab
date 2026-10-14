# Roadmap

wowlab grows in **waves**. A wave is one milestone of small tickets with checkable acceptance criteria. **The repository owner picks each wave after reviewing the last one**, and nothing is built between waves. The lists below are a *menu*, not a promise.

## Done

### Wave 1: understand and protect the install

- **Discovery and inventory:** find the install and its flavors, explain every file, and inventory a flavor folder (`doctor`, `install show`, `tree`, `explain`).
- **Readers:** SavedVariables (a strict, lossless parser that never runs Lua), CVars, key bindings, macros, addon lists.
- **Snapshots and undo:** a content-addressed store, value-level diffs, restore and undo through the write gate.
- **Game data:** tables for your exact build from wago.tools, cached and keyed by version.
- **Combat log:** a streaming tokenizer and a live `tail`.

### Wave 2: the character, offline

- **The lab-addon** records a character's gear, talents, customization, collections, currencies and professions, and `wowlab char show` reads them back offline.
- **The customization sandbox:** browse every race's options from the game's tables, save and compare looks, import from a character, and view it all as a local page.
- **Profiles:** named UI setups you can apply and undo.
- **`sv merge`:** copy an addon's settings between characters or from a snapshot, with a loader-failure check.
- Plus a long tail of hardening found in review: the snapshot store, the write gate, and the addon (including an off switch per section after a client crash).

### Wave 3: trimmed

Picked on 2026-09-29 (db2lake, a talent planner, an alt-dashboard) and trimmed on 2026-10-14, when the owner decided wowlab should be usable by other players. Players already have good talent planners, so that work is deferred and can be picked again later.

- **On `main`:** asking for a game table that does not exist in your build now says so plainly, instead of a bare HTTP error (`wowlab db2 …`, see [Game Data](Game-Data)).
- **On `main`:** `wowlab char list`, every character the lab-addon recorded ([Lab-Addon and Characters](Lab-Addon-and-Characters#every-character-at-once)).
- **In progress:** a daily job that checks this wiki against the code and keeps it current.
- **Deferred:** SQL over the game tables (db2lake), the talent planner, and the alt-dashboard.

## Now: Wave 4, the first public release

wowlab is becoming something other players can install. **The promise does not change:** reading never writes; every change asks first, goes through the write gate, takes a snapshot first and can be undone with `wowlab undo`; the only network access is downloading public game tables; nothing is uploaded; no accounts, no server, no telemetry.

| Planned | What it will be |
|---|---|
| **Releases** | A tagged GitHub release with a wheel, a source archive and SHA-256 sums, installed with one `uv tool install` line. No installer, app store or signed binary for now. |
| **Windows, tested** | The test suite on Windows and macOS in CI, and a person running the first-run flow on a real install of each. A platform is called supported only when both are true; until then it says "untested". |
| **A first run that explains itself** | `wowlab doctor` finds your install (or says how to point it at one), says plainly what wowlab will and won't do, offers a first snapshot and verifies it. |
| **Bug reports without your names** | a redact option for the doctor command: output safe to paste into an issue, with no character, realm, account folder, BattleTag, guild or home path. |
| **macros check** | A read-only linter: macros over the length limit, spell names not in your spellbook, macros on no action bar, duplicates, unbalanced `[ ]`, a misplaced `#showtooltip`. Needs a new version of the lab-addon that records your spellbook, action bars and keybinds (out of combat only). |
| **macros fix** | Only after a test shows whether the game keeps a macro file edited while it is closed. If it does not, wowlab prints corrected text for you to paste in game instead of writing anything. |
| **binds coach** | From your own combat log: spells you cast often on awkward keys, spells you cast with no bind, binds you never use. Read-only, and the "awkward key" table is a default you can edit. May arrive in the second release. |

**The gate.** Thirty days after the first release, the owner looks at GitHub's own counts (downloads, issues and discussions from people other than the owner; no telemetry). With fewer than 50 downloads or fewer than 5 issues or discussions from others, the owner may freeze the public side: releases stay up, no new public features, and wowlab goes back to being a personal lab.

## Then: Wave 5, the wowlab UI

A local page over every command, served only to your own machine while you keep the command running, aimed at the second public release. Every command reachable from it, every change confirmed first and undoable, exactly as on the command line.

Anything 3D still waits on a spike into reading models from your game files.

## The wider menu

The full list of ideas lives in [`docs/LAB_IDEAS.md`](https://github.com/sandgraal/wowlab/blob/main/docs/LAB_IDEAS.md). By theme:

- **Your character, offline:** build planning, alt dashboards, collection routing, bag triage, class flashcards, a printable character book, look-from-image.
- **Your UI and settings:** your whole setup as a file (plan, apply, roll back, detect drift), keybind coaching and a macro linter (their read-only halves are in Wave 4), skin packs, a map of your install.
- **Addons:** safe install and update with undo, provenance checks, a Forever-compatibility scan, developer tooling.
- **Game data and the world:** SQL over the tables, build-to-build change reports, relationship graphs, lore answers with citations.
- **Live and fun:** event feeds for stream overlays and smart-home triggers, combat-log visualizations, crash forensics.
- **Social:** remembering good groups. This stores other players' names, so it waits for its own privacy decision (opt-in, local only, never exported).

## Out of scope, permanently

Process memory reads or writes, DLL injection, packet capture or modification, input automation, editing anything under `Data/` or any executable, and bypassing the client's integrity checks. See [Safety and Guarantees](Safety-and-Guarantees).

## How it is built

wowlab is written by Claude Code agents coordinated by a *conductor* session, while the owner makes every decision and captures the real test files from the game. Each change is a small ticket. Every branch gets an independent code review, plus security and WoW-accuracy reviews wherever they apply. The most critical files (the SavedVariables parser, the write gate, sv-merge and profiles) get their tests written first, by a different agent than the one that writes the code. Test files are real captures, scrubbed of names and identifiers before they are committed. Details: [`docs/AGENT_WORKFLOW.md`](https://github.com/sandgraal/wowlab/blob/main/docs/AGENT_WORKFLOW.md) and [`AGENTS.md`](https://github.com/sandgraal/wowlab/blob/main/AGENTS.md).
