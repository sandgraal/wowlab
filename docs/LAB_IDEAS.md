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
| **addon-provenance** | For each installed addon, find where it came from (CurseForge, Wago or GitHub release), compare its file hashes to the upstream archive, and flag repacks or local edits | A | layout, snapshot hashing | upstream fetchers, hash compare | S |
| **addon-manager** | Install, update and pin addons from GitHub releases, CurseForge or Wago, with every change going through guard; undo reverts a bad update, including the addon's SavedVariables | A | guard, snapshot, toc | release fetchers, lockfile | M |
| **sv-health** | A doctor check that SavedVariables round-trip: lab-addon writes a login counter and wowlab checks it increments across a restart. Catches Forever's loader bug and any regression of it. Mostly built in Wave 2: the addon's `probe` and `wowlab sv merge`'s loader check (M11-09); what is left is a standalone `doctor` command | A | luadata, lab-addon | probe + check | S |
| **keybind-coach** | Fix keybinds from what you actually cast: flags frequently cast spells on hard keys, spells you cast with no bind (you click them), and binds to spells you never cast; suggests a layout and applies it through guard with undo. Bindings map keys to action-bar slots (`ACTIONBUTTON3`), not spells, and slot contents live on the server, so the slot-to-spell map comes from lab-addon | A | wtfconfig, combatlog (cast counts from `SPELL_CAST_SUCCESS`), lab-addon (action bars), guard | bindings writer, cast statistics, layout suggester | S–M |
| **macro-doctor** | Lint `macros-cache.txt`: spell names checked against your spellbook and ranks, bad conditional syntax, macros over 255 characters, macros on no action bar, duplicates; fixes through guard with undo. Whether an explicit rank (`Frostbolt(Rank 3)`) goes stale or fails once a higher rank is learned is **[verify]** (it is believed to keep casting that rank) | A | wtfconfig, lab-addon (spellbook, action bars), gamedata, guard | macros writer, rule set | S |
| **crash-forensics** | Link crashes to causes: parse the crash reports and error logs the client writes and correlate them with the addon set, CVars and zone from the latest snapshot. On Forever for macOS the client writes `<date>_<time>_Error_<pid>.txt` into the flavor folder's `Errors/` (seen 2026-09-28); whether it also writes to `~/Library/Logs/DiagnosticReports` is **[verify]**. The reports name the character, account, BattleTag and guild, so the tool reads them locally and no report becomes a fixture without the scrub tool learning that format first | A | layout, snapshot, wtfconfig | crash-report parsers, correlation | S |

## B. Game data

| Idea | What it is | Tier | Needs | New | Size |
|---|---|---|---|---|---|
| **db2lake** | DuckDB over cached DB2 tables with typed columns and a SQL REPL; cross-build joins | A | gamedata | WoWDBDefs typing, DuckDB loader | S |
| **build-diff** | Scheduled diff of chosen tables between builds with a readable changelog (beta builds move fast) | A | gamedata | diff + report, a scheduled workflow | S |
| **hotfix-reader** | Decode `Cache/ADB/DBCache.bin` and overlay hotfixes on tables | A | layout, gamedata | DBCache parser | M |
| **casc-source** | Read files straight from the install's `Data/` (needed by everything 3D) | A | install, gamedata `Source` | CASC binding or a wow.export CLI bridge | M |
| **azeroth-graph** | Item ↔ spell ↔ quest ↔ NPC ↔ zone relationships queryable as a graph | A | db2lake | relationship extraction | M |
| **quest-recorder** | lab-addon records givers, turn-ins, objectives and coordinates for Forever's new quests (the client tables only carry the quest IDs, which is why Questie is missing them); output as JSON, plus an export in the format Questie takes corrections in | A | lab-addon, luadata, db2lake | recorder schema, exporter | M (est.) |
| **market-memory** | Price history and supply shocks: keep your own auction-house scans over time (Auctionator's saved price data is already on the install) and cross them with build-diff, so a recipe change in a patch is linked to a price change; alert when datamined changes will move prices you care about. Whether the scan data holds seller names is **[verify]**; none are kept | A | luadata, build-diff, db2lake | price store, change-to-price linker | M |

## C. Character, offline (the owner's main interest)

| Idea | What it is | Tier | Needs | New | Size |
|---|---|---|---|---|---|
| **customization-sandbox** | Browse every race's customization options from the `ChrCustomization*` tables; save and compare looks; starts as data-only, gains a viewer when casc-source exists. The data-only half shipped in Wave 2 (M11-05 to M11-07); importing a character's current look waits on a capture, because the barber shop UI did not open in-world on Forever (2026-09-28, M11-23) | A | gamedata | option model; later a viewer | S → M |
| **look-from-image** | Match a concept image to your character: extract its palette and find the nearest customization choices and collected appearance sources. Not strictly data-only: colour matching needs the icon or texture images, from casc-source or an outside icon host **[verify]**. The collected-appearances half is blocked: `C_TransmogCollection.GetCategoryAppearances` crashed the Forever client (M11-20, 2026-09-28) and needs a safe source first | A | customization-sandbox, lab-addon (collections), casc-source | palette extraction, colour matching | S → M |
| **character-studio** | Your character rendered in the browser from your own game files, with gear and transmog; outfit designer | A | casc-source, gamedata, luadata | M2/BLP → glTF pipeline, three.js viewer | L |
| **photo-studio** | Poses, animation frames, lighting, transparent export, turntables | A | character-studio | scene tooling | M |
| **char-export** | Export to glTF, VRM (avatar use) and print-ready STL | A | character-studio | rigging map, mesh repair via Blender | M |
| **lab-addon** | The Lab's own data-broker addon: writes gear, talents, customization choices, collections, currencies and professions to SavedVariables on logout. The offline tools' feed. Works on any flavor with the modern API | A | luadata | addon, schema, fixtures | S |
| **alt-dashboard** | Local page over every character's state from lab-addon captures | A | lab-addon, luadata | UI | M |
| **digital-twin** | Timeline of a character built from dated snapshots, addon captures and screenshot timestamps | A | snapshot, lab-addon | timeline store + UI | M |
| **collection-router** | What is left to collect and the cheapest order to get it | A | lab-addon, db2lake | routing | M |
| **codex** | A printable book of a character: gear, talents, portrait, history; also a narrative journal of its adventures from digital-twin (firsts, zones, deaths, levels, notable fights) and an export as a roleplay profile for Forever's roleplay communities (absorbs the chronicle-book idea) | A | lab-addon, digital-twin (for the journal), photo-studio (optional) | PDF layout, journal writer, roleplay-profile export | S → M |
| **time-capsule** | Scheduled dated archive of full character state | A | snapshot, lab-addon | scheduler | S |
| **char-planner** | The owner's original goal: input or capture a character, view consolidated state offline, plan talent and gear builds; for Forever that means the `C_Traits` talent data and its new systems | A | lab-addon, db2lake | planner model + UI | M |
| **simc-bridge** | Export a character to a SimulationCraft profile and run a locally installed `simc` as a separate process, results stored beside the character. Depends on simc having data for the flavor | A | lab-addon, luadata | profile writer, process runner, result store | M |
| **drill-cards** | Spaced-repetition cards from your own data: spellbook, talents, boss abilities from the encounter journal tables, and what killed you in your logs; ten a night, weighted toward recent deaths. "What killed you" needs combat-log event meaning, which the Wave 1 tokenizer does not provide | A | lab-addon, gamedata, combatlog | card generator, death analysis, scheduler | S |
| **bag-triage** | Find dead weight across all characters: join Syndicator's inventory data with DB2 to find vendor trash, finished quest items, gear no alt can use and reagents no one on the account can craft with; says what to sell, delete or mail to which alt | A | luadata, db2lake, lab-addon (professions) | rules, per-alt advice | S–M |
| **crew-book** | Remember people worth grouping with again: who you grouped with, their role and level, your note and rating; warns when they are near. **Stores other players' names, which ADR-0026 forbids for the lab-addon and the repository's privacy rules keep out of fixtures: it needs its own privacy ADR (opt-in, local only, never exported, never in test data) before it can be picked** | A | lab-addon, luadata, privacy ADR | contact store, proximity check | S |
| **comp-builder** | Which groups are possible right now: from the online guild roster, propose viable dungeon groups by level band and role. **Same privacy ADR as crew-book (guild roster names)** | A | lab-addon, privacy ADR | group solver | S |

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
| **addon-audit** | Static scan of `Interface/AddOns` for Forever breakage: missing `16001` in the TOC, the build-number trap (`select(4, GetBuildInfo()) >= 100000`), removed Classic globals (`GetItemInfo`, `UnitAura`, `GetSpecialization`, …), and calls whose results go secret in combat. Reports per addon; fixes nothing | A | toc, layout, api-types | rule set, report | S |
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
- **sv-health**, **quest-recorder**, **keybind-coach**, **macro-doctor**,
  **drill-cards** and **bag-triage** all stand on **lab-addon**, which makes
  it the hub of most later waves; **addon-audit**
  needs **api-types** first. **addon-manager** and **addon-provenance** need
  only Wave 1 plus network fetchers for the addon sites.
- **crew-book** and **comp-builder** cannot be picked until a privacy ADR
  settles how other players' names may be stored locally.
- **keybind-coach** and **macro-doctor** need writers for the WTF text
  formats, which **wow-as-code** needs too; building those writers once serves
  all three.
