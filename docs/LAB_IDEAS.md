# Lab — Idea menu

Candidates for waves after the core library. **Nothing here is a ticket.**
The owner picks a wave after each wave review (`docs/LAB_PLAN.md` §11,
ADR-0024); until then an agent building from this file is off-task.

Columns: **Tier** per ADR-0023 (`A` ordinary, `B` works but unsupported by
Blizzard). **Needs** = `wowlab_core` modules it stands on. **New** = the
capability it has to add. **Size** = rough, for one wave: `S` a few tickets,
`M` a wave, `L` more than one wave.

Two things from the original brainstorm are deliberately absent: anything
needing memory access, injection, packets or a modified `Data/` (ADR-0023),
and a private-server sandbox, which would live in a separate private
repository if the owner ever wants it.

## A. Understand and control the install

| Idea | What it is | Tier | Needs | New | Size |
|---|---|---|---|---|---|
| **explorer** | Local static site generated from an install: every file annotated from the file map, with inline decoders (SavedVariables tree view, CVar tables, TOC view, snapshot timeline) | A | layout, luadata, wtfconfig, toc, snapshot | site generator | M |
| **wow-as-code** | Desired-state file (YAML) for CVars, binds, macros, enabled addons and chosen addon settings; `plan` / `apply` / `rollback`, drift detection, import current state | A | wtfconfig, luadata serializer, guard | writers for the WTF text formats | M |
| **profiles** | Named whole-UI states (`raid`, `gamepad`, `clean`) switched with one command; built on snapshots restricted to chosen subtrees | A | snapshot, guard | subtree presets, merge rules | S |
| **sv-merge** | Three-way structural merge of SavedVariables (copy one addon's settings between characters or machines without clobbering the rest) | A | luadata | merge engine | S |
| **skin-packs** | Reversible font and base-texture override packs with install/uninstall | B | guard, snapshot | pack format | S |

## B. Game data

| Idea | What it is | Tier | Needs | New | Size |
|---|---|---|---|---|---|
| **db2lake** | DuckDB over cached DB2 tables with typed columns and a SQL REPL; cross-build joins | A | gamedata | WoWDBDefs typing, DuckDB loader | S |
| **build-diff** | Scheduled diff of chosen tables between builds with a readable changelog (beta builds move fast) | A | gamedata | diff + report, a scheduled workflow | S |
| **hotfix-reader** | Decode `Cache/ADB/DBCache.bin` and overlay hotfixes on tables | A | layout, gamedata | DBCache parser | M |
| **casc-source** | Read files straight from the install's `Data/` (needed by everything 3D) | A | install, gamedata `Source` | CASC binding or a wow.export CLI bridge | M |
| **azeroth-graph** | Item ↔ spell ↔ quest ↔ NPC ↔ zone relationships queryable as a graph | A | db2lake | relationship extraction | M |

## C. Character, offline (the owner's main interest)

| Idea | What it is | Tier | Needs | New | Size |
|---|---|---|---|---|---|
| **customization-sandbox** | Browse every race's customization options from the `ChrCustomization*` tables; save and compare looks; starts as data-only, gains a viewer when casc-source exists | A | gamedata | option model; later a viewer | S → M |
| **character-studio** | Your character rendered in the browser from your own game files, with gear and transmog; outfit designer | A | casc-source, gamedata, luadata | M2/BLP → glTF pipeline, three.js viewer | L |
| **photo-studio** | Poses, animation frames, lighting, transparent export, turntables | A | character-studio | scene tooling | M |
| **char-export** | Export to glTF, VRM (avatar use) and print-ready STL | A | character-studio | rigging map, mesh repair via Blender | M |
| **lab-addon** | The Lab's own data-broker addon: writes gear, talents, customization choices, collections, currencies and professions to SavedVariables on logout. The offline tools' feed. Works on any flavor with the modern API | A | luadata | addon, schema, fixtures | S |
| **alt-dashboard** | Local page over every character's state from lab-addon captures. Overlaps Bronze M4; decide whether it is a Bronze feature fed by the Lab instead | A | lab-addon, luadata | UI | M |
| **digital-twin** | Timeline of a character built from dated snapshots, addon captures and screenshot timestamps | A | snapshot, lab-addon | timeline store + UI | M |
| **collection-router** | What is left to collect and the cheapest order to get it | A | lab-addon, db2lake | routing | M |
| **codex** | A printable book of a character: gear, talents, portrait, history | A | lab-addon, photo-studio (optional) | PDF layout | S |
| **time-capsule** | Scheduled dated archive of full character state | A | snapshot, lab-addon | scheduler | S |

Bronze already owns sims, snapshots of `/simc` state, and build planning for
retail. Forever character planning (the reworked talent trees via `C_Traits`
data, Legacy systems) is a candidate Bronze extension rather than a Lab app;
raise it at a wave review instead of building a second planner.

## D. Bridges and live data

| Idea | What it is | Tier | Needs | New | Size |
|---|---|---|---|---|---|
| **event-bus** | Daemon that watches SavedVariables, combat log and chat log and publishes events on a local WebSocket | A | layout, luadata, combatlog | watcher, event schema | S |
| **overlay-sinks** | OBS text/browser sources, Stream Deck, Home Assistant, Discord presence driven by the bus | A | event-bus | one adapter each | S each |
| **companion-loop** | An offline mini-game or planner whose results are written into a SavedVariables file the lab-addon reads at next login | A | guard, luadata serializer, lab-addon | game/planner, inbound schema | M |
| **pixel-channel** | In-game addon paints a small data block; a desktop reader decodes it for a real-time out-channel that SavedVariables cannot give | B | lab-addon | encoder/decoder | S |

## E. Addon development

| Idea | What it is | Tier | Needs | New | Size |
|---|---|---|---|---|---|
| **addon-kit** | `wowlab addon new`: scaffold with the right TOC for the discovered flavor, luacheck, LuaLS config, link into `Interface/AddOns` through guard | A | install, toc, guard | templates | S |
| **api-types** | LuaLS type stubs generated from the flavor's exported `Blizzard_APIDocumentation` | A | layout | doc parser, stub emitter | S |
| **addon-harness** | Headless Lua test harness with a mock of the API surface addons touch, including secret-value behaviour | A | api-types | mock runtime (runs Lua under a real interpreter, outside `wowlab_core`; LAB_PLAN L3 governs parsing game files, not testing addon code) | M |
| **dev-console** | In-game REPL and snippet notebook addon; wrappers for `/fstack`, `/etrace`, `/tinspect`; explains why a value is secret or tainted | A | — | addon | S |
| **api-rag** | Retrieval over exported FrameXML and API docs for agent-written addons, flagging calls that break under 12.x restrictions | A | layout | index + prompt tooling | S |
| **lab-mcp** | MCP server exposing install inventory, SavedVariables, game data and snapshots to an agent; write tools go through guard | A | everything | MCP surface | S |
| **nameplate-designer** | Design nameplates and unit frames outside the game with live preview; export to an addon profile | A | luadata serializer, guard | preview renderer | M |

## F. Odd ones

| Idea | What it is | Tier | Needs | New | Size |
|---|---|---|---|---|---|
| **roguelike-me** | A terminal roguelike or idle game seeded by your real character's gear and talents | A | lab-addon | the game | M |
| **log2art** | Combat log to fight replay, generative art or sound | A | combatlog | event semantics, renderer | M |
| **zone-heatmap** | Zone maps from game files with a heatmap of where you have been | A | casc-source, lab-addon | tile pipeline, position sampling | M |
| **lore-companion** | Lore Q&A grounded only in quest, NPC and gossip text from the game tables, with citations | A | db2lake | retrieval | S |
| **play-lake** | DuckDB plus Grafana over sessions, snapshots, logs and screenshots | A | snapshot, combatlog, lab-addon | ETL, dashboards | S |
| **screenshot-organizer** | Watches `Screenshots/`, tags by character, zone and date, searchable gallery | A | layout, lab-addon | tagging | S |
| **tabletop-sheet** | Export a character as a D&D-style sheet | A | lab-addon | mapping, template | S |

## Natural wave orderings

These are observations about dependencies, not a plan.

- **lab-addon** unblocks most of section C and half of D and F. It is small.
- **casc-source** gates everything 3D. It is the largest unknown; a spike
  ticket (can we read one M2 and one BLP from the owner's install on macOS
  and Windows, by which route) should precede any 3D wave.
- **db2lake** and **customization-sandbox** (data-only) need nothing beyond
  Wave 1 and deliver the customization interest early.
- **wow-as-code** and **profiles** are the direct payoff of `guard` and the
  serializer.
