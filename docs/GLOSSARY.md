# Domain Glossary

For engineers who do not play World of Warcraft. Several of these are named misleadingly. Modeling them from the name alone produces a wrong schema.

## Identity

**Region** — `us`, `eu`, `kr`, `tw`, `cn`. Separate data centers with separate character namespaces. The same character name can exist in every region and they are unrelated.

**Realm** — a server within a region. Character names are unique per realm, not per region. The API uses a slugified realm name (`area-52`, not `Area 52`).

**Character identity is therefore (region, realm, name), not name.** Characters can also transfer realms, which changes their identity key without changing the character. We do not attempt to track this; a transferred character is a new character to us.

**Class** — one of thirteen (Warrior, Evoker, Priest, etc.). Fixed at creation.

**Spec (specialization)** — a subdivision of a class that determines role and playstyle. A class has 3-4. A character can freely switch between their class's specs at any time, at no cost. **A character's spec is not stable state** — it changes between activities. Snapshot the spec at capture time; never treat it as a property of the character.

## Gear

**Slot** — head, neck, shoulder, back, chest, wrist, hands, waist, legs, feet, finger1, finger2, trinket1, trinket2, main_hand, off_hand. Sixteen equipped items.

**Item level (ilvl)** — the power rating of an individual item. Typical current-content range is roughly 600-720. **It is not a character level and not a requirement.** Higher is generally better but not always — a lower-ilvl item with better-matched stats can outperform a higher one, which is exactly why sims exist.

**Equipped item level** — the average across the sixteen slots. The headline "how geared is this character" number.

**Bonus IDs** — a list of integers attached to an item instance that modify what that item actually is: its item level, its upgrade track position, whether it has a socket, which affix variant it rolled. **The same base item ID with different bonus IDs is a materially different item.** You cannot identify an item by its item ID alone. This is the single most common modeling mistake for newcomers to WoW data.

**Upgrade track** — items drop at a base level and can be upgraded through a track (e.g. "Champion 3/8") using currency. Encoded in bonus IDs. An item's current track position is part of its identity.

**Gem / socket** — some items have sockets that accept gems. Sockets themselves are often granted by a bonus ID rather than being intrinsic to the item.

**Enchant** — a permanent stat modifier applied to certain slots. Independent of the item's own identity.

**Crafted item** — player-made gear with a quality tier (1-5 stars) and player-chosen stat allocation. Encoded separately in the SimC string as `crafted_stats`. Two crafted items with the same ID can have completely different stats.

**Embellishment** — a special effect applied to crafted gear. Characters are limited to a small number equipped at once, which makes them a constrained optimization problem rather than a simple "best item" pick.

**Tier set** — a matched set of items (usually 5 slots) granting bonuses at 2 and 4 pieces. **Set bonuses create discontinuities in gear value** — a lower-ilvl item that completes a 4-piece can beat a higher-ilvl item that breaks it. Any gear optimizer that ranks items independently will get this wrong. This is a primary reason we delegate to SimC rather than computing gains ourselves.

**Catalyst** — a system that converts a non-tier item into its tier equivalent, using limited weekly charges. Relevant to planning: "should I spend a charge, and on what."

## Stats

**Primary stat** — Intellect, Agility, or Strength depending on spec. Scales most damage directly.

**Secondary stats** — Critical Strike, Haste, Mastery, Versatility. These have non-linear, spec-dependent, and interacting value. Their relative worth is exactly what sims compute and exactly what static "stat priority" lists get wrong.

**Tertiary stats** — Leech, Avoidance, Speed. Random, small, and mostly ignorable.

**Stat weights** — a sim output expressing marginal value per point of each stat. Useful, but only locally valid — they change as gear changes, so they are a snapshot property, not a character property.

## Talents

**Talent tree** — a node graph. Current WoW has three per character: a class tree, a spec tree, and a hero tree. Nodes have ranks; some are choice nodes with mutually exclusive options.

**Loadout** — a specific complete set of talent selections. **A character has many loadouts and switches between them by activity** (one for raid, one for dungeons, one for single-target). Loadout is not a property of a character; it is a property of a moment.

**Talent string / loadout code** — a bit-packed, base64-ish encoding of a loadout, used for import/export. The header includes a serialization version and a spec ID. **The spec ID in the header reflects the character's spec at export time, not the loadout's spec** — it can lie for off-spec exports.

Note: talent loadouts disappeared from the Blizzard Profile API in patch 11.2 and were still missing a year later. Do not assume this data source exists.

## Content and progression

**Raid** — organized group content, 10-30 players, weekly lockout. Difficulty tiers: LFR, Normal, Heroic, Mythic — ascending, with correspondingly higher item level rewards.

**Mythic+ (M+)** — timed 5-player dungeon content with escalating difficulty levels ("keys"). A character's M+ Rating is the standard measure of dungeon skill.

**Keystone** — the item that determines which dungeon and what level. Consumed on use.

**Great Vault** — a weekly reward chest. Completing content unlocks slots; at weekly reset the player is presented with up to **nine items and must choose exactly one.** This is a recurring, high-stakes, poorly-supported decision that every player makes every week. It is our wedge feature.

**Weekly reset** — Tuesday in US regions, Wednesday in EU. Lockouts, vault progress, and several currencies reset. All weekly planning is anchored to this.

**Lockout** — per-boss or per-instance limits on how often you can receive loot. Constrains what content is worth running.

## Tooling

**SimulationCraft (SimC)** — an open-source combat simulator. The authority on "how much damage will this character do." Takes a profile text file, emits JSON.

**APL (Action Priority List)** — SimC's model of a rotation: an ordered list of conditional ability uses. Maintained per spec by the SimC community.

**`/simc`** — an in-game addon command that exports the character's complete state as a text blob SimC consumes directly. The highest-fidelity character export available, because the addon has access to client data the API does not expose.

**Droptimizer** — a sim mode that answers "which items from this instance would improve me, and by how much." Ranks a candidate pool against a baseline.

**Top Gear** — a sim mode that searches combinations of items you already own for the best configuration. Combinatorial; needs a ceiling.

**Parse** — a percentile ranking of a single performance against everyone else who did that encounter with that spec. Colloquially "I got a 95 parse."

**Log** — an uploaded combat log, analyzed on Warcraft Logs. The record of what actually happened, as opposed to what a sim projects.

## Client filesystem

**SavedVariables** — a Lua file per addon where it persists data. Written on logout or `/reload`, read on login or `/reload`. **This is the only I/O channel available to an addon — addons cannot make network requests.** Every "sync my character to a website" workflow in WoW is therefore a copy-paste, unless an external process reads this file. That external process is our companion agent.

**WTF folder** — where SavedVariables live. Path shape:
`<install>/_retail_/WTF/Account/<ACCOUNT>/SavedVariables/<AddonName>.lua`

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
