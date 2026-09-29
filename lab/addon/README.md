# lab/addon/

The Lab's own addons: Lua that runs inside the game client, never in the Lab
(ADR-0026, `docs/LAB_PLAN.md` §13.1). One folder per addon; the first is
`WowLab/`. Nothing in the Lab loads, runs or evaluates these files (L3): they
are checked by a static linter, by Python tests that read their tokens, by
review, and later by the Python reader graded on the SavedVariables the addon
wrote (M11-04).

## WowLab

Records this character's equipped gear, spec, class talents, Legacy-tree candidates,
barber-shop choices, collections, currencies and professions into
`WowLabCharDB` (`## SavedVariablesPerCharacter`) as a versioned table
(`schema = 1`), written by the client at logout, `/reload` or a clean exit.
`/wowlab save` refreshes the tables in memory only; a crash writes nothing.
`/wowlab skip <section>` and `/wowlab unskip <section>` switch one section
off and on ("Switching a section off" below).
`WowLabDB` (`## SavedVariables`) holds only `{ schema = 1 }` until the M11-03
capture shows which data is account-wide.

- `WowLab.toc` is a **template**: `## Interface: @WOWLAB_INTERFACE@` is
  replaced at install time (`wowlab addon install lab`, M11-02) with the
  interface version discovered from the install (L6). The repository never
  holds a number.
- No names, realms, GUIDs, guild or chat of anyone, no text the owner typed,
  no Battle.net identity, no wall-clock time. Every unit token is `"player"`.
  A crafted item's link can carry the crafter's player GUID
  (`Player-<id>-<hex>`); every such run is blanked before the link is stored
  and the slot records `crafter_removed = true`.
- Availability is tested per API function, never by `WOW_PROJECT_ID`, the
  interface number or the flavor. A section the client cannot provide is
  `{ absent = "<reason>" }`.

### Table layout (schema 1)

```
WowLabCharDB = {
  schema = 1,
  probe = { loads = <n>, lost = true? },      -- carried across sessions, with customization; lost: this load only
  skip = { "<section key>", ... },            -- sections the owner switched off; left out when none (M11-21)
  client = { version, build, interface },     -- GetBuildInfo(); its date is not kept
  spec = { index, id?, api } | { absent },
  gear = { first_slot, last_slot, slots = { { slot, link, crafter_removed, item_level, item_level_api } },
          average = { overall (best items owned, bags included), equipped (the character-sheet figure), pvp } },
  talents = {
    class = { config = <config>, export, last_selected_config | last_selected_config_absent },
    legacy = { legacy_ui, player_level, skipped_types, configs = { <config + found_by> } },
  },
  customization = { as_of, recorded_at = "open"|"applied", recorded_load, carried = true?, choices = {…}, race_id, sex, chr_model_id }, -- kept across sessions until the next visit
  collections = {
    mounts = { collected, filtered = false },
    toys = { collected, filtered = true, filter = { collected_shown, uncollected_shown, unusable_shown } },
    pets = { species = { { species, count } }, filtered = true, default_filters },
    appearances = { absent = "not gathered: …" },  -- always absent (M11-20)
  },
  currencies = { list = { { id, quantity, max_quantity, max_weekly_quantity, earned_this_week, can_earn_per_week, total_earned, use_total_earned_for_max, account_wide } }, filtered = true, headers_collapsed, filter },
  professions = { list = { { position, skill_line, rank, max_rank, modifier } } },
}
<config> = { id, type, trees = { { id, system_id, currencies = { { id, quantity, max_quantity, spent } }, nodes = { { id, ranks_purchased, active_rank, current_rank, max_ranks, is_visible, entries, sub_tree, active_entry, active_entry_rank } } } } }
choices = { { option, choice_index, choice } }
```

Notes on the fields:

- `gear`: `first_slot`..`last_slot` is every slot number from the client's
  `INVSLOT_FIRST_EQUIPPED` to `INVSLOT_LAST_EQUIPPED`, so it covers whatever
  the client defines; empty slots are left out. The equipped average is
  `average.equipped`.
- `talents`: `active_rank` is the committed rank; `ranks_purchased` and
  `current_rank` may include changes staged in the talent UI but not applied
  **[verify]**. Tree currencies exclude staged changes
  (`GetTreeCurrencyInfo(config, tree, true)`). `last_selected_config` is kept
  raw and may be a negative sentinel (for example a starter build) rather
  than a config id.
- `talents.legacy` holds every trait config the client lists that is neither
  the active class config nor of type Combat or Profession, each with
  `found_by`; which of them is the Legacy system is decided from the M11-03
  capture. `skipped_types` names the types left out (Invalid, Combat,
  Profession).
- `customization`: the choices cover only the model being viewed
  (`chr_model_id`). `sex` is `Enum.UnitSex` 0/1, not `UnitSex()`'s 2/3
  **[verify]**. A record from an earlier session is carried with
  `carried = true` and its `recorded_load` (the `probe.loads` of the session
  that recorded it); it is dropped with a reason when `race_id` no longer
  matches the character (race only: a paid change that keeps the race is
  invisible to the addon). Without any record the section is
  `absent = "no barber-shop visit recorded with the addon enabled"`.
- `collections.appearances` is always
  `{ absent = "not gathered: asking the client for the appearance collection crashed the Forever client once (M11-03); the addon no longer asks" }`.
  On the first M11-03 login (build 1.60.1.70009), one call,
  `GetCategoryAppearances(9)` (9 is Waist on Retail), made about 4 s after
  entering the world, hit the client assertion `BC_ASSERT(this->m_has_value)`
  and crashed the client. Whether other categories were called before it is
  not known (the loop walked the enum with `pairs`). `pcall` cannot catch a
  C++ assertion. The addon now calls nothing in `C_TransmogCollection` on
  any client and registers no transmog events (2026-09-28, M11-20).
- `professions`: whether `GetProfessionInfo`'s rank and maximum are per
  expansion tier or overall on Forever is **[verify]**.
- `probe.lost` is set when `WowLabCharDB` loaded as a table without a probe,
  for that load only; a later load that finds the probe clears it. A first
  load and a file that failed to load both show `loads = 1`.

Any section may instead be `{ absent = "<reason>" }`, and carry
`events_unregistered = { ... }` when the client did not know one of its
change events. Schema 1 may change after M11-03; after M11-04 merges, any
change is schema 2.

### Switching a section off

A client assertion inside a section crashes the client, `pcall` cannot catch
it, and a crash writes nothing, so the addon cannot mark the culprit itself
(M11-20). The owner can (M11-21):

- `/wowlab skip <section>` switches the section off at once: its event
  handler is removed (an event no other section uses is unregistered), a
  pending gather is dropped, and what it gathered this session is forgotten.
  `/reload` or log out to save the switch; a crash writes nothing, so a
  switch typed just before a crash is lost.
- `/wowlab unskip <section>` switches it back on from the next `/reload` or
  login (never in the same session: the section may be the one that
  crashes). `/reload` or log out to save that too.
- `/wowlab skip` with no section lists the sections switched off, then every
  section key.
- A key that is not a section is refused, and the chat line lists the valid
  keys. The typed text is compared, never stored or printed.
- It is per character (`WowLabCharDB`): switching a section off on one
  character leaves it on for every other character.

Section keys (as in `docs/LAB_PLAN.md` §13.1), by the file that registers
them. A crash report names a file and line, not a section, and one file can
hold several sections:

- `Gear.lua`: `gear`
- `Talents.lua`: `spec`, `talents.class`, `talents.legacy`
- `Customization.lua`: `customization`
- `Collections.lua`: `collections.mounts`, `collections.toys`, `collections.pets`, `collections.appearances`
- `Currencies.lua`: `currencies`
- `Professions.lua`: `professions`
- `Core.lua` holds no section: events, tables and the slash command.

The list is saved as `WowLabCharDB.skip = { "<section key>", ... }` (section
order, left out when empty) and read at `ADDON_LOADED`, before any section is
carried, registers an event or is gathered. A section in it registers no
event and is never gathered, not on its events, not on entering the world,
not by `/wowlab save`, not at `PLAYER_LOGOUT`; it is written as
`{ absent = "switched off by the owner" }`. `collections.appearances` has no
gather at all (M11-20): it can be switched off, but it keeps its own absent
reason. A string in the saved list that is not a section key (for example
after a later version renames a section) is ignored and dropped at the next
save.

The 15 s window. The first on-world pass after each `ADDON_LOADED` (login or
`/reload`) runs 15 s after `PLAYER_ENTERING_WORLD`, on its own timer, and
the addon says so in chat at that moment. That event fires just before the
loading screen clears (**[verify]**), so a little less than 15 s is left
once the world is visible, less on a slow first login. The chat line is
`WowLab: recording in 15 s. To switch a section off first: /wowlab skip <section>  (/wowlab skip lists them)`.
Until that pass has run, change events gather nothing (what they would have
gathered is taken by the pass); after it, change events keep the 2 s
debounce. A `/wowlab skip` typed inside the 15 s takes effect for that pass.
A logout or `/reload` inside the 15 s still records: `PLAYER_LOGOUT` gathers
every section still waiting, the crashing one included unless it was
switched off first. `/wowlab save` inside the 15 s gathers at once, as
always. If the client has no `C_Timer`, there is no window: the pass runs at
once, as before. After the first pass, a section that crashes on a later
change event (2 s debounce) gives no window; switch it off at the next
login.

What the switch does not do:

- It stops the section's own gather only. Another section may call the same
  client API: `talents.class` asks for the spec too, so switching off `spec`
  does not keep the spec API from being called.
- Switching off `customization` drops the carried barber-shop record: the
  section is written absent, so after `unskip` it stays absent until the next
  barber-shop visit.
- It needs the addon loaded. Type it as soon as the world is visible,
  before the announced first recording pass; that pass runs every on-world
  section in one go. Without `C_Timer` there is no window. The one
  crash seen (M11-20) came about 4 s after entering the world on the old
  timing. If it cannot be typed in time, untick the addon at character
  select.

## Lint

```bash
make selene     # once per checkout: fetch the pinned selene into .tools/ (make setup does this)
make lint-lua   # selene on lab/addon/WowLab, offline
```

[selene](https://github.com/Kampfkarren/selene) 0.31.0, the `selene-light`
build, pinned by version and SHA-256 in `scripts/fetch_selene.py`. It is one
binary that parses Lua without running it, and the light build has no
Roblox library, so selene never touches the network. `wow_client.yml` is its
standard library: Lua 5.1 plus exactly the client globals the addon uses. A
global it lacks (for example `UnitName`) fails the lint as undefined.
`tests/addon/test_lab_addon.py` checks the TOC template, the privacy rules,
and that this file, `wow_client.yml` and the sources list the same APIs.

## API status

"confirmed by forever-addon-kit on 69893, re-verify in M11-03" means the
trait walker in https://github.com/Thunderz96/forever-addon-kit (MIT;
`addons/ForeverBeacon/FB_Spells.lua`) or its README findings exercise it on
the live Forever client, build 1.60.1.69893. It was read as a reference; no
code was copied. Everything else is **[verify]** for the owner's capture
(M11-03). "Baseline" says whether the function appears in that kit's
captured API list (`data/forever_api.json`, 69893); that is evidence the name
exists, not of what it returns.

| API | Used for | Status | Baseline |
|---|---|---|---|
| `C_Traits.GetConfigInfo` | talents: config type and tree ids (the `name` field is never read) | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `C_Traits.GetTreeNodes` | talents: node ids per tree | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `C_Traits.GetNodeInfo` | talents: node ranks and entries (`ID`, `ranksPurchased`, `maxRanks`, `entryIDs` exercised by the kit; `activeRank`, `currentRank`, `activeEntry`, `subTreeID` are [verify]) | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `C_Traits.GetTreeCurrencyInfo` | talents: points per tree, staged changes excluded (Legacy points spent and cap: [verify]) | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `C_Traits.GetSystemIDByTreeID` | talents: which system a tree belongs to | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `C_Traits.GetConfigsByType` | talents.legacy: configs per type | called by forever-addon-kit's walker on 69893; which call found the Legacy config is not recorded: [verify] in M11-03 | yes |
| `C_Traits.GetConfigIDBySystemID` | talents.legacy: configs per discovered system id (the `Constants` scan is [verify]) | called by forever-addon-kit's walker on 69893; which call found the Legacy config is not recorded: [verify] in M11-03 | yes |
| `C_ClassTalents.GetActiveConfigID` | talents.class: the active config | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `Enum.TraitConfigType` | talents.legacy: config types to ask for | called by forever-addon-kit's walker on 69893; which call found the Legacy config is not recorded: [verify] in M11-03 | n/a (table) |
| `ToggleLegacySystemUI` | talents.legacy: marker that the Legacy panel exists (never called) | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `UnitLevel` | talents.legacy: `player_level` (explains an empty list) | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `C_Timer.After` | debounce gathering after change events | confirmed by forever-addon-kit on 69893, re-verify in M11-03 | yes |
| `GetSpecialization` | spec: fallback only, if a client provides the global | confirmed by forever-addon-kit on 69893, re-verify in M11-03: **absent** on Forever | no |
| `GetSpecializationInfo` | spec: fallback only, with the global above | confirmed by forever-addon-kit on 69893, re-verify in M11-03: **absent** on Forever | no |
| `C_SpecializationInfo.GetSpecialization` | spec: index | [verify] | yes |
| `C_SpecializationInfo.GetSpecializationInfo` | spec: id (first return only) | [verify] | yes |
| `C_ClassTalents.GetLastSelectedSavedConfigID` | talents.class: last selected saved loadout (needs a spec id) | [verify] | yes |
| `C_Traits.GenerateImportString` | talents.class: loadout export string | [verify] | yes |
| `Constants` | talents.legacy: numeric `*SYSTEM_ID*` fields | [verify] | n/a (table) |
| `CreateFrame` | event frame | [verify] | yes |
| `C_EventUtils.IsEventValid` | skip events the client does not know | [verify] | yes |
| `GetBuildInfo` | client version, build, interface | [verify] | yes |
| `SlashCmdList` | `/wowlab` | [verify] | n/a (table) |
| `INVSLOT_FIRST_EQUIPPED` | gear: first slot (client constant) | [verify] | n/a (constant) |
| `INVSLOT_LAST_EQUIPPED` | gear: last slot (ranged slot on Forever: [verify]) | [verify] | n/a (constant) |
| `GetInventoryItemLink` | gear: item link per slot, crafter GUID blanked before storing (whether Forever fills it: [verify]) | [verify] | yes |
| `GetAverageItemLevel` | gear: overall (best items owned, bags included), equipped (character sheet), PvP | [verify] | yes |
| `ItemLocation.CreateFromEquipmentSlot` | gear: slot location for item level | [verify] | n/a (FrameXML mixin) |
| `C_Item.GetCurrentItemLevel` | gear: slot item level | [verify] | yes |
| `C_Item.GetDetailedItemLevelInfo` | gear: item level fallback from the link | [verify] | yes |
| `C_BarberShop.GetAvailableCustomizations` | customization: options, choices, current index | [verify] | yes |
| `C_BarberShop.GetCurrentCharacterData` | customization: `sex` only (name fields never read) | [verify] | yes |
| `C_BarberShop.GetViewingChrModel` | customization: chr model id | [verify] | yes |
| `UnitRace` | customization: race id (third return only) | [verify] | yes |
| `C_MountJournal.GetMountIDs` | mounts: every mount id, unfiltered | [verify] | yes |
| `C_MountJournal.GetMountInfoByID` | mounts: isCollected (11th return) | [verify] | yes |
| `C_ToyBox.GetNumFilteredToys` | toys: rows through the current filter | [verify] | yes |
| `C_ToyBox.GetToyFromIndex` | toys: item id per row | [verify] | yes |
| `C_ToyBox.GetCollectedShown` | toys: filter state, recorded | [verify] | yes |
| `C_ToyBox.GetUncollectedShown` | toys: filter state, recorded | [verify] | yes |
| `C_ToyBox.GetUnusableShown` | toys: filter state, recorded | [verify] | yes |
| `PlayerHasToy` | toys: owned | [verify] | yes |
| `C_PetJournal.GetNumPets` | pets: rows through the current filter | [verify] | yes |
| `C_PetJournal.GetPetInfoByIndex` | pets: species id and owned (returns 2 and 3; the GUID is never bound) | [verify] | yes |
| `C_PetJournal.GetNumCollectedInfo` | pets: count per species | [verify] | yes |
| `C_PetJournal.IsUsingDefaultFilters` | pets: filter state, recorded | [verify] | yes |
| `C_CurrencyInfo.GetCurrencyListSize` | currencies: panel rows | [verify] | yes |
| `C_CurrencyInfo.GetCurrencyListInfo` | currencies: header or currency per row | [verify] | yes |
| `C_CurrencyInfo.GetCurrencyListLink` | currencies: id fallback via link | [verify] | yes |
| `C_CurrencyInfo.GetCurrencyIDFromLink` | currencies: id fallback via link | [verify] | yes |
| `C_CurrencyInfo.GetCurrencyInfo` | currencies: quantity and caps | [verify] | yes |
| `C_CurrencyInfo.IsAccountWideCurrency` | currencies: account-wide flag fallback | [verify] | yes |
| `C_CurrencyInfo.GetCurrencyFilter` | currencies: filter state, recorded | [verify] | yes |
| `GetProfessions` | professions: indices | [verify] | yes |
| `GetProfessionInfo` | professions: rank, max, skill line, modifier | [verify] | yes |

Addon-defined globals: `WowLabCharDB`, `WowLabDB`, `SLASH_WOWLAB1`.

Events. `TRAIT_CONFIG_UPDATED`, `TRAIT_CONFIG_LIST_UPDATED`,
`TRAIT_TREE_CURRENCY_INFO_UPDATED`, `PLAYER_TALENT_UPDATE` and
`TRAIT_SYSTEM_INTERACTION_STARTED` are registered by forever-addon-kit's
walker on 69893, but its registration is wrapped in `pcall` and it also
registers `LEARNED_SPELL_IN_TAB`, which its README says does not exist; so
they are **[verify]** like every other event: `ACTIVE_COMBAT_CONFIG_CHANGED`, `PLAYER_LEVEL_UP`, `ADDON_LOADED`, `PLAYER_ENTERING_WORLD`,
`PLAYER_LOGOUT`, `PLAYER_EQUIPMENT_CHANGED`, `PLAYER_AVG_ITEM_LEVEL_UPDATE`,
`ACTIVE_PLAYER_SPECIALIZATION_CHANGED`, `PLAYER_SPECIALIZATION_CHANGED`,
`BARBER_SHOP_OPEN`, `BARBER_SHOP_APPEARANCE_APPLIED`, `NEW_MOUNT_ADDED`,
`COMPANION_LEARNED`, `NEW_TOY_ADDED`, `TOYS_UPDATED`, `NEW_PET_ADDED`,
`PET_JOURNAL_LIST_UPDATE`, `CURRENCY_DISPLAY_UPDATE`,
`SKILL_LINES_CHANGED`. An event the client does not know is skipped and
listed in the section's `events_unregistered`.

## M11-03 [verify] checklist

What the owner's capture must settle **in addition to** every **[verify]** in
`docs/LAB_PLAN.md` §13.1 (which the M11-03 table lists in full), from the
M11-01 reviews. Each item ends
up confirmed, contradicted (with what Forever returned) or still open in the
`docs/LAB_FORMATS.md` table M11-03 adds.

Capture steps beyond §13.1's:

- log in twice on one character, so `probe.loads` is seen rising;
- after the barber-shop logout, log in and out once more **without** a
  visit, so the carried customization record (`carried = true`,
  `recorded_load`) is captured;
- with the talent panel still open, stage a talent change without applying
  it, type `/wowlab save`, then `/reload`; compare `ranks_purchased` /
  `current_rank` / `active_rank` and the tree currencies with what the panel
  showed as applied;
- optional, costs gold: apply one barber-shop change, then log out (needed
  for item 12; without it item 12 stays open);
- open the character sheet before logging out, for item 13;
- watch for hitches after logging in or `/reload`: the mount, toy and pet
  rescans (collections) run once per session and on their events.

Items:

1. `C_SpecializationInfo.GetSpecialization` / `.GetSpecializationInfo`: what
   they return on Forever (index, id; paladin = 1486 per the kit).
2. Loadouts: `last_selected_config` (a config id, a negative sentinel or
   absent), `C_Traits.GenerateImportString` output, and whether any node has
   a `sub_tree`.
3. Which `found_by` entry finds the Legacy config in `talents.legacy`. If
   `configs` is empty at level 25 or above with `legacy_ui = true`, add the
   kit's numeric system-ID probe (`GetConfigIDBySystemID` over 1–120).
4. Whether `GetTreeCurrencyInfo` carries the Legacy points spent and the
   seasonal cap.
5. `events_unregistered` on every section: which events Forever does not
   know. `events_unregistered` shows only events the client refused; it does
   not prove that an accepted event ever fires.
6. Empty lists from the pet journal or toy box (journal
   not initialized, or filters hiding rows) versus real contents.
7. Currencies: whether `GetCurrencyInfo` has `isAccountWide`, or the
   `IsAccountWideCurrency` fallback was used; `headers_collapsed`.
8. `GetProfessionInfo` on Forever's 1–300 skill model: rank and maximum per
   tier or overall.
9. `probe.loads` rising across the two logins, and `probe.lost` absent.
10. Crafter GUIDs in item links: any `crafter_removed = true`, and no
    `Player-` left in any stored link.
11. `currentChoiceIndex` base: `choice` matches `choices[choice_index]`
    1-based (compare with the barber shop on screen). The race-mismatch drop
    cannot be tested without a paid race change; it stays open.
12. `BARBER_SHOP_APPEARANCE_APPLIED` firing: an applied change gives
    `recorded_at = "applied"`.
13. Gear: `first_slot` / `last_slot` values; whether slot 18 (ranged) ever
    holds an item; `item_level_api` per slot; `average.equipped` equals the
    character-sheet figure.
14. Customization: `sex` is 0 or 1; `chr_model_id` is present; no name field
    appears anywhere in the record.
15. `talents.legacy` on a character below level 25, if one exists: `configs`
    empty, `legacy_ui` true.
16. Whether the "recording in 15 s" line was on screen when the world
    appeared, how many seconds were left, and whether
    `LOADING_SCREEN_DISABLED` is a known event on Forever.
