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

## Next: Wave 3 (not picked yet)

The owner's stated interest is the offline character tools. Candidates on the table:

| Candidate | What it would be |
|---|---|
| **db2lake + char-planner** | SQL over the cached game tables with typed columns, and a planner that uses the class-talent data the addon already records to view a character's build offline and plan a new one. |
| **alt-dashboard** | A local page over every character's recorded state. |
| **crash-forensics + api-types** | Parse the client's own crash reports and correlate them with your addon set; generate type stubs from the flavor's API documentation so addon calls can be checked. |

Anything 3D waits on a spike into reading models from your game files.

## The wider menu

The full list of ideas lives in [`docs/LAB_IDEAS.md`](https://github.com/sandgraal/wowlab/blob/main/docs/LAB_IDEAS.md). By theme:

- **Your character, offline:** build planning, alt dashboards, collection routing, bag triage, class flashcards, a printable character book, look-from-image.
- **Your UI and settings:** your whole setup as a file (plan, apply, roll back, detect drift), keybind coaching, a macro linter, skin packs, a map of your install.
- **Addons:** safe install and update with undo, provenance checks, a Forever-compatibility scan, developer tooling.
- **Game data and the world:** SQL over the tables, build-to-build change reports, relationship graphs, lore answers with citations.
- **Live and fun:** event feeds for stream overlays and smart-home triggers, combat-log visualizations, crash forensics.
- **Social:** remembering good groups. This stores other players' names, so it waits for its own privacy decision (opt-in, local only, never exported).

## Out of scope, permanently

Process memory reads or writes, DLL injection, packet capture or modification, input automation, editing anything under `Data/` or any executable, and bypassing the client's integrity checks. See [Safety and Guarantees](Safety-and-Guarantees).

## How it is built

wowlab is written by Claude Code agents coordinated by a *conductor* session, while the owner makes every decision and captures the real test files from the game. Each change is a small ticket. Every branch gets an independent code review, plus security and WoW-accuracy reviews wherever they apply. The most critical files (the SavedVariables parser, the write gate, sv-merge and profiles) get their tests written first, by a different agent than the one that writes the code. Test files are real captures, scrubbed of names and identifiers before they are committed. Details: [`docs/AGENT_WORKFLOW.md`](https://github.com/sandgraal/wowlab/blob/main/docs/AGENT_WORKFLOW.md) and [`AGENTS.md`](https://github.com/sandgraal/wowlab/blob/main/AGENTS.md).
