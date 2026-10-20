# Domain Glossary

For engineers who do not play World of Warcraft. Several of these are named misleadingly. Modeling them from the name alone produces a wrong schema. Wave 1 needs the **Client filesystem** section; the gear, talent and progression terms are here for the character tools of later waves (`docs/LAB_IDEAS.md` section C).

## Identity

**Region** — `us`, `eu`, `kr`, `tw`, `cn`. Separate data centers with separate character namespaces. The same character name can exist in every region and they are unrelated.

**Realm** — a server within a region. Character names are unique per realm, not per region. The API uses a slugified realm name (`area-52`, not `Area 52`).

**Character identity is therefore (region, realm, name), not name.** Characters can also transfer realms, which changes their identity key without changing the character. On disk a character is `WTF/Account/<ACCOUNT>/<Realm>/<Character>/`, so a transfer or rename leaves the old folder behind and starts a new one.

**Second name (Forever)** — Forever characters have a player-chosen first and second name. On disk the character folder is `WTF/Account/<ACCOUNT>/<digits>/<First>-<Second>/` (the digits folder is the realm's numeric id, **[verify]**), with a retail-style twin `<ACCOUNT>/<Realm>/<First>/` holding only `AddOns.txt`. The same first name can appear with several second names (owner's directory listing). The hyphen is a separator, not part of either name; the second name is not a realm. Syndicator's data (2026-09-22) spells the same character `"<First> <Second>"` with a space and an empty realm, and DBM-Party-Vanilla (2026-09-28) keys it `"<First> <Second>"` with no realm at all. The combat log names the character `<First>-<Realm>-`, without the second name (`docs/LAB_FORMATS.md` §8). Whether other addons and the client API see it the same way is **[verify]**. Until then, do not match Forever addon records to folders by realm.

**GUID (unit GUID)** — the client's string id for a unit, used in the combat log and the addon API. `Player-<serverID>-<playerUID>` names one character. Other units have the shape `<Type>-0-<serverID>-<instanceID>-<zoneUID>-<ID>-<spawnUID>`, for types such as `Creature`, `Pet`, `Vehicle` and `GameObject`. **Only `<ID>` (the NPC or object id) is game data.** The other fields record where and when this copy of the unit spawned, so a spawn UID plus a timestamp can match one log to another player's log of the same fight. `0000000000000000` means "no unit". Field meanings are from community documentation, **[verify]** against captures. The scrub tool keeps `<Type>` and `<ID>` and replaces the rest with invented values (`docs/LAB_PLAN.md` §8, amendment 2026-09-24).

**Class** — one of thirteen (Warrior, Evoker, Priest, etc.). Fixed at creation.

**Spec (specialization)** — a subdivision of a class that determines role and playstyle. A class has 2-4 (Demon Hunter has two). A character can freely switch between their class's specs at any time, at no cost. **A character's spec is not stable state** — it changes between activities. Record the spec with each capture; never treat it as a property of the character.

## Gear

**Slot** — head, neck, shoulder, back, chest, wrist, hands, waist, legs, feet, finger1, finger2, trinket1, trinket2, main_hand, off_hand. Sixteen equipped items.

**Item level (ilvl)** — the power rating of an individual item *as it is right now*. The range shifts every season (the numbers in any example here are illustrative). **It is not a character level, not a requirement, and not upgrade headroom** — a piece at the top of a low track is a dead end; a lower-ilvl piece on a higher track upgrades past it. Higher is generally better but not always — a lower-ilvl item with better-matched stats can outperform a higher one, which is exactly why sims exist. An item's current ilvl is read from the client (the in-game API reports it per equipped item); it is never derived from the base item record or from bonus-id arithmetic. Forever (2026-09-28): the character sheet shows no item level (see "Equipped item level"); the lab-addon's per-slot figure is what `C_Item.GetCurrentItemLevel` returned (or `GetDetailedItemLevelInfo` as a fallback; `item_level_api` names which), and the sheet has no figure to check it against.

**Equipped item level** — the in-game average over equipped slots, with quirks (a two-hander counts twice, empty slots count as zero). The headline "how geared is this character" number. Read it from the client rather than computing it. Forever (build 1.60.1.70009, 2026-09-28): the character sheet shows no item level at all, only stats, so `GetAverageItemLevel`'s equipped value has no UI figure behind it there, and how the client computes it is open (`docs/LAB_FORMATS.md`, M11-03 amendment).

**Bonus IDs** — a list of integers attached to an item instance that modify what that item actually is: its item level, its upgrade track position, whether it has a socket, which affix variant it rolled. **The same base item ID with different bonus IDs is a materially different item.** You cannot identify an item by its item ID alone. This is the single most common modeling mistake for newcomers to WoW data.

**Upgrade track** — items drop at a base level and can be upgraded through a track (e.g. "Champion 3/8") using currency. Encoded in bonus IDs. An item's current track position is part of its identity.

**Gem / socket** — some items have sockets that accept gems. Sockets themselves are often granted by a bonus ID rather than being intrinsic to the item.

**Enchant** — a permanent stat modifier applied to certain slots. Independent of the item's own identity.

**Item quality** — the rarity tier an item is shown in (Poor grey, Common white, Uncommon green, Rare blue, Epic purple, and higher tiers). **It is a display colour, not a power rating**: a green item can out-level a blue one. An item link carries it only as its colour code (`|cnIQ2:` on Forever, `|cff1eff00` in the hex form other clients write, [verify]); the word for each code is community documentation, shipped in `gear_slots.toml` and **[verify]** on Forever (`docs/LAB_FORMATS.md`, 2026-10-18). Not the same as a crafted item's quality tier (stars).

**Item link** — the string the client gives for an item instance, `|c<colour>|Hitem:<fields>|h[<name>]|h|r`. The `:`-separated fields after the item id carry the instance's enchant, gems, bonus ids and more, so two links with the same item id can be different items (see Bonus IDs). The bracketed name is display text in the client's language, not an identifier, and it can hold markup such as colour codes. A crafted item's link can carry the crafter's player GUID, which is personal: the lab-addon blanks it before storing the link and records `crafter_removed` (ADR-0026).

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

**Trait tables** — the DB2 tables that define talent trees, joined by id: `TraitTree` (a tree, with a `TraitSystemID`) → `TraitNode` (a place in the tree, with `PosX`, `PosY`) → `TraitNodeXTraitNodeEntry` → `TraitNodeEntry` (what a node offers, with `MaxRanks`; a node with several entries is a choice) → `TraitDefinition` (names a `SpellID`) → `SpellName` (the text). `TraitEdge` joins two nodes, `TraitCond` holds gates (points spent, level, and others), `TraitCurrency` and `TraitTreeXTraitCurrency` are the point pools. The meaning of the enum columns is community documentation, **[verify]** on Forever. On the beta (build 1.60.1.70058) the tables are small: 17 trees, 558 nodes. `TraitSubTree` and the loadout tables are not served at that build (wago answers `400`, which cannot tell "absent from this game version" from "no such table").

**Trait system id** — `TraitTree.TraitSystemID`, also recorded per tree by the lab-addon as `system_id`. In the 70058 capture the class talent tree (1116) is system 10 and the four trees of the one Legacy candidate are system 45; nine trees in the table are 10 and four are 45. That 45 is *the* Legacy system is **[verify]**: the addon does not record which candidate is Legacy.

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

**Unit flags** — the hex number after each unit's name in a combat-log record (`0x511`). It describes the unit **relative to the player who wrote the log**, not the unit itself: affiliation (mine, party, raid, outsider), reaction, controller, and type (player, NPC, pet, guardian), plus bits for that player's current target and focus. Per community documentation **[verify]**, the same NPC logs different flags as it becomes and stops being the target (the one real log shows its NPC targeted on every line), and "mine" plus "player" means the logging player (no capture of a charmed or mind-controlled player exists). A pet or guardian is "mine" but not a player. `wowlab binds coach` picks the player's own casts by the affiliation, controller and type fields alone, never by a GUID or a name (M13-08). Bit values are community documentation, **[verify]** beyond what the one real log shows.

**Action slot, binding command** — the client has numbered action slots (the lab-addon walks slots 1 to 180, its own bound; how many slots the client has is **[verify]**); an action bar shows twelve of them, and what each holds is server state. A key binding does not name a spell: it names a command such as `ACTIONBUTTON3` or `MULTIACTIONBAR1BUTTON2`, and that command presses whichever slot its bar shows. In the default UI, button n of the bar showing page p is slot (p − 1) × 12 + n; the main bar changes page with forms, stances and paging, so the same key can cast different spells. Key to spell therefore needs both the bindings and the bars (`docs/LAB_PLAN.md` §15.2). A **rank** is a separate spell id per spell strength on clients that have ranks (Forever: Shadow Bolt is 686, 695 and 705), all under one name.

**Macro id, macro index** — two different numbers. The **macro id** is the hex number in a `macros-cache.txt` record's `VER 3` line (`0100000000000001`); the **macro index** is the number the client's macro calls and an action slot use for a macro. An action slot holding a macro keeps no name or body, only a number: with no subtype it is taken to be the macro index **[verify]**, and with a subtype it is the spell or item the macro shows now **[verify]**. Which record of the file an index names is **[verify]** (`docs/LAB_FORMATS.md`, 2026-10-16, item 7): `wowlab char macros` uses a candidate numbering (account macros 1 to 120, character macros from 121): the 120 offset is Retail's, from memory **[verify]**; counting records in file order is this tool's assumption **[verify]**. It calls every slot it gives "unconfirmed" until a capture settles it (`docs/handoffs/M14-macro-place.md`).

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

**Account folder** — `WTF/Account/<NAME>/`. One folder per game account that logged in on the machine: a number with `#<n>` (e.g. `#1` or `#6`), where the `#<n>` suffix numbers the game accounts under one Battle.net account **[verify]**, or an old account name. It is personal data; fixtures pseudonymize it. An old-style name counts as an account folder only while it holds a character folder (M13-50). `WTF/Account/SavedVariables/` is the client's own and not an account.

**CASC** — the content-addressed archive format of `Data/`. Files are addressed by content hash and by numeric *FileDataID*; names come from a community-maintained *listfile*, which has gaps. Some content is encrypted until Blizzard releases a key.

**Content key (CKey)** — the MD5 of a file's decoded bytes. It names *what* a file is, whatever its compression. The root file maps a file data id to a content key, and the content key is how a read is checked (a decoded file whose MD5 is not its content key is wrong). It is not where the file is.

**Encoding key (EKey)** — a hash that names a file's *stored* form (its BLTE, compressed and perhaps encrypted), so it names *how* the file is kept. For a file stored in chunks it is the MD5 of the BLTE header and chunk table, not of the whole stored file (settled on the owner's install at 70450, M15-04: `docs/LAB_FORMATS.md` §10, amendment of 2026-10-19; for a BLTE without a chunk table, none met, community documentation gives the MD5 of the whole BLTE, **[verify]**); the Lab checks a read by the content key and the per-block MD5s, not by this key. One content key may have several encoding keys (an encrypted and a plain copy, for one); the encoding file maps one to the other. The local index (`Data/data/*.idx`) holds only the **first nine bytes** of an encoding key, never a content key, so a lookup there takes an encoding key (`docs/LAB_FORMATS.md` §10).

**Bucket (CASC index)** — one of the sixteen `.idx` files a key's entry is filed in, chosen from the key's first nine bytes (XORed together, then the two nibbles of that XORed). Each bucket has its own versioned file (`<bucket:2><version:8>.idx`); the newest version is the one read (**[verify]**). The archives' own record headers are filed one bucket on from their key's (`docs/LAB_FORMATS.md` §10, amendment of 2026-10-19). Not related to any game term.

**Root file (CASC)** — the build's table from *file data id* to content key, in blocks; each block carries locale flags (which client languages want it) and content flags (which platforms load it, `LowViolence`, `Encrypted`, and bits whose meaning is not settled). A client reads only the blocks for its own locale and platform, so "is this file in the build" depends on both; the Lab takes the locale from `.build.info`'s `Tags` cell and the platform from the flavor folder (a `.app` bundle or an `.exe`), never from a constant. One id can have rows in two blocks with different content; at 70450 the client fetched only one of them (`docs/LAB_FORMATS.md` §10, amendment of 2026-10-19, M15-05).

**Build config** — the `key = value` text file `Data/config/<k[0:2]>/<k[2:4]>/<k>`, named by its own MD5 (the `Build Key` cell of the product's `.build.info` row). It names the build's root and encoding files and more. Per product, as are the CDN configs beside it in `Data/config/`; the index and the archives under `Data/data/` are shared by every product in the install. Written by the Battle.net app, never edited (L7).

**BLTE** — the stored form of almost every file in the archives (`data.NNN`): a header, usually a chunk table (each block's stored size, decoded size and MD5), then the blocks, each led by a mode byte: `N` plain, `Z` zlib, `E` encrypted, and `4` (LZ4) and `F` (a nested BLTE) in the format but not met in a sample of the install's archives. Almost every record in an archive is a 30-byte record header followed by one BLTE (segment headers have no BLTE after them, and one record in the owner's install, the build config's `patch` manifest, is stored unencoded; `docs/LAB_FORMATS.md` §10). The Lab decodes `N` and `Z` and refuses the rest by name.

**Encryption key name (TACT key)** — an encrypted (`E`) block names, in eight bytes, the key it needs; community tools print the name as sixteen hex digits, the bytes read as a little-endian number (CascLib and wow.export, from memory; **[verify]**). Blizzard ships some content encrypted and releases the key later, often through a hotfix the client fetches (from community documentation; **[verify]**), so the game may already show a file the Lab reports as encrypted: "encrypted" in the Lab means the Lab will not read it (it holds no key list and looks for none, ADR-0033), not that the client cannot. In the owner's install on 2026-10-19, 9 of 4,079 sampled archive records (every product's records the local index lists, not only 70450's) had an `E` block, under two key names; nothing ties either name to a Classic-era build.

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
