# Domain Glossary

For engineers who do not play World of Warcraft. Several of these are named misleadingly. Modeling them from the name alone produces a wrong schema. Wave 1 needs the **Client filesystem** section; the gear, talent and progression terms are here for the character tools of later waves (`docs/LAB_IDEAS.md` section C).

## Identity

**Region** — `us`, `eu`, `kr`, `tw`, `cn`. Separate data centers with separate character namespaces. The same character name can exist in every region and they are unrelated.

**Realm** — a server within a region. Character names are unique per realm, not per region. The API uses a slugified realm name (`area-52`, not `Area 52`).

**Character identity is therefore (region, realm, name), not name.** Characters can also transfer realms, which changes their identity key without changing the character. On disk a character is `WTF/Account/<ACCOUNT>/<Realm>/<Character>/`, so a transfer or rename leaves the old folder behind and starts a new one.

**Second name (Forever)** — Forever characters have a player-chosen first and second name. On disk the character folder is `WTF/Account/<ACCOUNT>/<digits>/<First>-<Second>/` (the digits folder is the realm's numeric id, **[verify]**), with a retail-style twin `<ACCOUNT>/<Realm>/<First>/` holding only `AddOns.txt`. The same first name can appear with several second names (owner's directory listing). The hyphen is a separator, not part of either name; the second name is not a realm.

**Class** — one of thirteen (Warrior, Evoker, Priest, etc.). Fixed at creation.

**Spec (specialization)** — a subdivision of a class that determines role and playstyle. A class has 2-4 (Demon Hunter has two). A character can freely switch between their class's specs at any time, at no cost. **A character's spec is not stable state** — it changes between activities. Record the spec with each capture; never treat it as a property of the character.

## Gear

**Slot** — head, neck, shoulder, back, chest, wrist, hands, waist, legs, feet, finger1, finger2, trinket1, trinket2, main_hand, off_hand. Sixteen equipped items.

**Item level (ilvl)** — the power rating of an individual item *as it is right now*. The range shifts every season (the numbers in any example here are illustrative). **It is not a character level, not a requirement, and not upgrade headroom** — a piece at the top of a low track is a dead end; a lower-ilvl piece on a higher track upgrades past it. Higher is generally better but not always — a lower-ilvl item with better-matched stats can outperform a higher one, which is exactly why sims exist. An item's current ilvl is read from the client (the in-game API reports it per equipped item); it is never derived from the base item record or from bonus-id arithmetic.

**Equipped item level** — the in-game average over equipped slots, with quirks (a two-hander counts twice, empty slots count as zero). The headline "how geared is this character" number. Read it from the client rather than computing it.

**Bonus IDs** — a list of integers attached to an item instance that modify what that item actually is: its item level, its upgrade track position, whether it has a socket, which affix variant it rolled. **The same base item ID with different bonus IDs is a materially different item.** You cannot identify an item by its item ID alone. This is the single most common modeling mistake for newcomers to WoW data.

**Upgrade track** — items drop at a base level and can be upgraded through a track (e.g. "Champion 3/8") using currency. Encoded in bonus IDs. An item's current track position is part of its identity.

**Gem / socket** — some items have sockets that accept gems. Sockets themselves are often granted by a bonus ID rather than being intrinsic to the item.

**Enchant** — a permanent stat modifier applied to certain slots. Independent of the item's own identity.

**Crafted item** — player-made gear with a quality tier (1-5 stars) and player-chosen stat allocation, carried on the item instance rather than the base item. Two crafted items with the same ID can have completely different stats.

**Embellishment** — a special effect applied to crafted gear. Characters are limited to a small number equipped at once, which makes them a constrained optimization problem rather than a simple "best item" pick.

**Tier set** — a matched set of items (usually 5 slots) granting bonuses at 2 and 4 pieces. **Set bonuses create discontinuities in gear value** — a lower-ilvl item that completes a 4-piece can beat a higher-ilvl item that breaks it. Any gear optimizer that ranks items independently will get this wrong, which is why a planner asks a simulator instead of adding up stats.

**Catalyst** — a system that converts a non-tier item into its tier equivalent, using limited weekly charges. Relevant to planning: "should I spend a charge, and on what."

## Stats

**Primary stat** — Intellect, Agility, or Strength depending on spec. Scales most damage directly.

**Secondary stats** — Critical Strike, Haste, Mastery, Versatility. These have non-linear, spec-dependent, and interacting value. Their relative worth is exactly what sims compute and exactly what static "stat priority" lists get wrong.

**Tertiary stats** — Leech, Avoidance, Speed. Random, small, and not simmed for throughput; Mythic+ players still value Leech and Speed, so they are shown, not hidden.

**Stat weights** — a sim output expressing marginal value per point of each stat. Useful, but only locally valid — they change as gear changes, so they are a snapshot property, not a character property.

## Talents

**Talent tree** — a node graph. Current WoW has three per character: a class tree, a spec tree, and a hero tree. Nodes have ranks; some are choice nodes with mutually exclusive options.

**Loadout** — a specific complete set of talent selections. **A character has many loadouts and switches between them by activity** (one for raid, one for dungeons, one for single-target). Loadout is not a property of a character; it is a property of a moment.

**Talent string / loadout code** — a bit-packed, base64-ish encoding of a loadout, used for import/export. The header includes a serialization version and a spec ID. **The spec ID in the header reflects the character's spec at export time, not the loadout's spec** — it can lie for off-spec exports.

**`C_Traits`** — the modern client API namespace for talent trees (configs, trees, nodes, entries, ranks). It replaced the old per-tab talent functions, and it is what Forever is reported to use as well (`docs/DATA_SOURCES.md`). An addon reads a character's talents through it; the tree definitions themselves are in the `Trait*` DB2 tables.

## Content and progression

**Raid** — organized group content, 10-30 players, weekly lockout. Difficulty tiers: LFR, Normal, Heroic, Mythic — ascending, with correspondingly higher item level rewards.

**Mythic+ (M+)** — timed 5-player dungeon content with escalating difficulty levels ("keys"). A character's M+ Rating is the standard measure of dungeon skill.

**Keystone** — the item that determines which dungeon and what level. Consumed on use.

**Great Vault** — a weekly reward chest. Completing content unlocks slots; at weekly reset the player is presented with up to **nine items and must choose exactly one.** It holds choices, not items, and it empties every week.

**Weekly reset** — the instant lockouts, vault progress, and several currencies reset. It differs per region in both day and hour (*verify* each row against Blizzard's published maintenance schedule before relying on it):

| Region | Day | UTC hour (*verify*) | Notes |
|---|---|---|---|
| `us` | Tuesday | 15:00 | Also covers Oceania and Latin America (same instant, different local time). |
| `eu` | Wednesday | 07:00 CET / 06:00 UTC | Follows CET/CEST changes. |
| `kr`, `tw` | Thursday | *verify* | |
| `cn` | Thursday | *verify* | |

Anything weekly is anchored to the character's region, never to the local machine's clock.

**Lockout** — per-boss or per-instance limits on how often you can receive loot. Constrains what content is worth running.

## Play data

**Sim** — a combat simulation: a program plays a modelled character against a modelled fight thousands of times and reports expected output with an error margin. The community's answer to "is this item or talent better for me", because stat value is non-linear and set bonuses are discontinuous. A sim projects; it does not observe.

**Combat log** — the text file the client appends to under `Logs/` while `/combatlog` is on: one timestamped event per line. The record of what actually happened, as opposed to what a sim projects. Format: `docs/LAB_FORMATS.md` §8.

**Parse** — a percentile ranking of a single logged performance against everyone else who did that encounter with that spec. Colloquially "I got a 95 parse." Nothing to do with text parsing.

## Client filesystem

**SavedVariables** — a Lua file per addon where it persists data. Written on logout or `/reload`, read on login or `/reload`. **This is the only I/O channel available to an addon — addons cannot make network requests.** Two consequences: data leaves the game only when an external process reads these files, and that process always sees the *previous* session, never the live one. The client also rewrites them on exit, so an external edit made while the game runs is lost without warning.

**WTF folder** — where configuration and SavedVariables live, one per flavor. Path shape, with the flavor folder discovered, never assumed:
`<install>/<flavor folder>/WTF/Account/<ACCOUNT>/SavedVariables/<AddonName>.lua`
(account-wide) and `…/<ACCOUNT>/<Realm>/<Character>/SavedVariables/<AddonName>.lua`
(per character). On a retail install the flavor folder is `_retail_`.

**Install, product, flavor** — one Battle.net install directory holds several *products* (retail, Classic, PTR, a beta), each in its own *flavor folder* (`_retail_`, `_classic_`, …) and all sharing one `Data/` store. The product code (`wow`, `wow_classic`, …) is what Battle.net and data sites key on; the flavor folder is what is on disk. Neither is stable across a beta-to-launch transition, so library code discovers both and hard-codes neither (L6).

**Build** — the last component of a client version (`12.1.5.65432` → `65432`). Game data tables are valid for exactly one full version string (ADR-0022). "Patch" is the first three components. The last component alone is not unique: the wago.tools recording (M10-08) lists `2.5.5.68575` and `2.5.6.68575` under one product with the same build config, so anything keyed by build uses the full four-part version string, never the integer.

**Interface version** — the integer an addon's TOC declares to say which client it supports (`120105`). Derived from the patch number, not the build. A mismatch marks the addon "out of date"; it does not change what API exists.

**TOC** — an addon's manifest (`Foo.toc`): metadata directives and the ordered list of files to load. One addon can ship several TOCs with suffixes so each flavor loads its own.

**CVar** — a client console variable: a named setting persisted in `Config.wtf` or a `config-cache.wtf`. Scope is machine, account or character depending on the variable.

**Account folder** — `WTF/Account/<NAME>/`. The name identifies a Battle.net account (a number with `#<n>`, e.g. `#1` or `#6`, or an old account name). It is personal data; fixtures pseudonymize it.

**CASC** — the content-addressed archive format of `Data/`. Files are addressed by content hash and by numeric *FileDataID*; names come from a community-maintained *listfile*, which has gaps. Some content is encrypted until Blizzard releases a key.

**DB2** — the client's static data tables (items, spells, customization options, maps, …), one schema per build. Community definitions (WoWDBDefs) give the columns names.

**Hotfix cache** — `Cache/ADB/<locale>/DBCache.bin`: row-level corrections to DB2 tables pushed by the server at login. Exports of DB2 tables do not include them.

**Secret value** — since 12.0, some API returns in combat or restricted content (unit health is the usual example) are opaque to addon code: they can be passed to Blizzard widgets and cannot be compared, added or branched on. This is why computation-heavy combat addons stopped working and why Lab tooling works on out-of-combat and on-disk data.

**Taint** — the client's tracking of which values and code paths insecure (addon) code has touched. Tainted execution cannot call protected functions (casting, targeting) in combat. Unrelated to secret values, often confused with them.

**Forever** — *World of Warcraft: Forever*: a separate, permanent level-60 product based on the original world with new content, announced 2026-09-12, in beta from 2026-09-17. Reported to run the modern (12.x) addon API rather than the Classic one. See `docs/DATA_SOURCES.md` for what is verified.

## Terms that mislead

| Term | Does not mean |
|---|---|
| Item level | A level requirement, or a character level |
| Bonus ID | An optional bonus; it is core identity |
| Loadout | A one-per-character setting |
| Spec | A permanent character property |
| Vault | Storage; it is a weekly choice of one from nine |
| Parse | Text parsing; it is a percentile rank |
| Key | An auth credential; it is a dungeon item |
| SavedVariables | A live view of the game; it is the previous session, written at logout or `/reload` and overwritten by the client on exit |
| Flavor folder | A stable identifier; it is a directory name that changes between beta and launch |
| Interface version | The API level; it is a compatibility label derived from the patch number |
| Secret value | Encrypted data; it is a number addon code may display but not compute with |
