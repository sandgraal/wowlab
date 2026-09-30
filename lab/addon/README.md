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
(`schema = 2` since M11-29), written by the client at logout, `/reload` or a
clean exit. `/wowlab save` refreshes the tables in memory only; a crash
writes nothing.
`/wowlab skip <section>` and `/wowlab unskip <section>` switch one section
off and on ("Switching a section off" below).
`WowLabDB` (`## SavedVariables`) holds only `{ schema = 2 }` until a capture
shows which data is account-wide. The addon writes one schema number into
both variables.

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

### Table layout (schema 2)

```
WowLabCharDB = {
  schema = 2,
  probe = { loads = <n>, lost = true? },      -- carried across sessions, with customization; lost: this load only
  skip = { "<section key>", ... },            -- sections the owner switched off; left out when none (M11-21)
  client = { version, build, interface },     -- GetBuildInfo(); its date is not kept
  spec = { index, id?, api } | { absent },
  gear = { first_slot, last_slot, slots = { { slot, link, crafter_removed, item_level, item_level_api } },
          average = { overall (best items owned, bags included), equipped (GetAverageItemLevel's second value; Forever's character sheet shows none), pvp } },
  talents = {
    class = { config = <config>, export | export_absent, last_selected_config | last_selected_config_absent },
    legacy = { legacy_ui, player_level, skipped_types, configs = { <config + found_by> } },
  },
  customization = { as_of, recorded_at = "open"|"applied", recorded_load, carried = true?, choices = {…}, race_id, sex, chr_model_id | chr_model_id_absent, events_received }, -- kept across sessions until the next visit; the last two keys are schema 2's
  collections = {
    mounts = { collected, filtered = false },
    toys = { collected, filtered = true, filter = { collected_shown, uncollected_shown, unusable_shown } },
    pets = { species = { { species, count } }, filtered = true, default_filters },
    appearances = { absent = "not gathered: …" },  -- always absent (M11-20)
  },
  currencies = { list = { { id, quantity, max_quantity, max_weekly_quantity, earned_this_week, can_earn_per_week, total_earned, use_total_earned_for_max, account_wide } }, filtered = true, headers, headers_collapsed, rows, filter },
  professions = { list = { { position, skill_line, rank, max_rank, modifier } } },
}
<config> = { id, type, trees = { { id, system_id, currencies = { { id, quantity, max_quantity, spent } }, nodes = { { id, ranks_purchased, active_rank, current_rank, max_ranks, is_visible, entries, sub_tree, active_entry, active_entry_rank } } } } }
choices = { { option, choice_index, choice } }
```

Schema 2 (M11-29) is schema 1 with `chr_model_id_absent` and
`events_received` on `customization` (also `events_received` on an absent
customization record); nothing else changed. It is the first change to the
format after M11-04, so it is a new schema (`docs/LAB_PLAN.md` §13.1). The
Lab still reads schema 1 files (the M11-03 and M11-23 captures) exactly as
before: it refuses `chr_model_id_absent` in one (text in an unknown key) and
keeps `events_received` as an unknown key, as it always did. It refuses a
schema it does not know. The first logout with the
M11-29 addon rewrites a schema-1 file as schema 2; `probe.loads` keeps
rising across that write, because the addon reads the probe, the skip list
and the carried customization record back by key, never by schema.

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
- `talents.class` holds exactly one of `export` and `export_absent`, and
  exactly one of `last_selected_config` and `last_selected_config_absent`
  (M11-22). `last_selected_config_absent` is
  `"the client returned no last-selected loadout for this spec"` when
  `GetLastSelectedSavedConfigID` returned nil (nil is taken, from Retail
  behaviour, to mean no saved loadout is selected for this spec
  **[verify]**); the other reasons say the function is missing, there was
  no spec id to ask with, the call raised an error, or it returned no
  number. `export_absent` says `GenerateImportString` is missing, raised an
  error, returned an empty string, or returned no string. Both calls go
  through `pcall` directly, so an error is not taken for nil. The literals:
  - `"C_ClassTalents.GetLastSelectedSavedConfigID missing"`
  - `"no spec id to ask with"`
  - `"C_ClassTalents.GetLastSelectedSavedConfigID raised an error"`
  - `"the client returned no last-selected loadout for this spec"`
  - `"C_ClassTalents.GetLastSelectedSavedConfigID returned no number"`
  - `"C_Traits.GenerateImportString missing"`
  - `"C_Traits.GenerateImportString raised an error"`
  - `"C_Traits.GenerateImportString returned an empty string"`
  - `"C_Traits.GenerateImportString returned no string"`
- `currencies` (M11-22): `rows` is the count `GetCurrencyListSize` gave; it
  counts header rows and leaves out rows under a collapsed header
  (community documentation, **[verify]**). `headers` counts every header
  row seen, so `rows − headers − #list` is the rows the addon could not
  read an id from. `rows` is missing when the call raised an error or
  returned no number, or when the file predates M11-22 (the M11-03
  fixtures).
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
- `customization` in schema 2 (M11-29). In the M11-23 capture
  (1.60.1.70058), after one applied change, the record's last write came
  from an open (`recorded_at = "open"`), and it had no `chr_model_id`; the
  file could not say why. Confirmed by that capture: `BARBER_SHOP_OPEN` fires
  and reaches the section; `GetAvailableCustomizations`,
  `GetCurrentCharacterData` and `UnitRace` ran; no name field was stored.
  Still **[verify]**: that `BARBER_SHOP_APPEARANCE_APPLIED` ever reaches the
  section (the client did not refuse it, yet the record's last write came
  from an open), what `GetViewingChrModel` returns, and `currentChoiceIndex`
  being 1-based (consistent with the tables, not proven). The addon changes
  only so that the next capture can say why ("M11-29 capture step" below):
  - A gathered record holds exactly one of `chr_model_id` and
    `chr_model_id_absent`. The reason records which outcome the call had
    when the section gathered (at the open or the applied event); none of
    them says why the client gave no id. The call goes through `pcall`
    directly, so an error is not taken for nil. `carry` keeps only the
    number, so a carried record may hold neither. The literals, and how each
    reads:
    - `"C_BarberShop.GetViewingChrModel missing"`: the function is not on
      this client.
    - `"C_BarberShop.GetViewingChrModel raised an error"`: it exists but
      refused this call.
    - `"C_BarberShop.GetViewingChrModel returned nil"`: it named no model at
      that moment (this also covers a call that returned no value at all).
    - `"C_BarberShop.GetViewingChrModel returned no number"`: it returned
      something other than a number or nil.

    In every case the Lab keeps taking the body type from `sex`, and why
    there was no id stays **[verify]**. A hypothesis, not a finding: the
    function names a model only while the shop shows an alternate form (set
    with `SetViewingChrModel`), and returns nil for the character's own form.
    forever-addon-kit's API list for 69893 has `C_BarberShop.HasAlteredForm`,
    `IsViewingAlteredForm`, `SetViewingAlteredForm` and `SetViewingChrModel`,
    which is consistent with that reading but does not prove it.
  - `events_received`: every event the section registered this session, with
    how many times it reached the section's handler (0 when it never did).
    With `recorded_at` it tells "the applied event never came" from "an open
    came after it". It is written on whatever record is written for the
    section, like `events_unregistered`, and is this session's: `carry` drops
    it. An event the client refused has no entry.
  - Count-only events (**[verify]** on Forever): registered on the same
    handler and counted, nothing else; no event argument is read (for
    example `BARBER_SHOP_RESULT`'s success flag on Retail). The record still
    comes only from `BARBER_SHOP_OPEN` and `BARBER_SHOP_APPEARANCE_APPLIED`,
    never at close. A count-only event the client refuses is left out of
    `events_received` and is not added to `events_unregistered`, which stays
    the list of refused change events. Switching `customization` off removes
    these handlers too.
    - `BARBER_SHOP_RESULT`, `BARBER_SHOP_CLOSE`,
      `BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE`: Retail names for events a
      client may fire around an Accept or on leaving the chair.
    - `BARBER_SHOP_COST_UPDATE`: Retail's event as previewed choices change
      the price; a count shows the addon saw shop activity between the open
      and the Accept.
    - `BARBER_SHOP_SUCCESS`: the name before 9.0; unlikely to exist, cheap to
      count.
    - `WOWLAB_CONTROL_NOT_A_REAL_EVENT`: made up on purpose, a control. If
      it has no entry, this client refuses a name it does not know, so every
      other entry, 0 included, is a name this client knows (0 means it did
      not reach the section in that session), and a missing entry means a
      name it does not know. If it has an entry at 0, the client accepts any
      name, so "not refused" proves nothing about a name being known. The
      reading holds whether or not `C_EventUtils.IsEventValid` exists,
      because the control tests the addon's whole gate (that check, then
      `RegisterEvent`). Registering a name the client does not know has never
      been exercised on Forever (every `events_unregistered` on 70058 is
      `{}`). It happens at `ADDON_LOADED`, before `/wowlab skip` can be
      typed, so if it ever crashed the client, untick WowLab at character
      select ("Switching a section off" above), or with the client closed
      `wowlab undo` the install.
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

Any section may instead be `{ absent = "<reason>" }`. A section that
registered its events this session carries `events_unregistered` on
whatever record is written for it (a gathered record, a carried
customization record, and the never-gathered
`{ absent = "<not_gathered reason>" }`): the change events the client
refused, or an empty list when it accepted all of them (M11-22). An empty
list is the answer; a record with no key at all was written before M11-22
(the M11-03 fixtures, or any file from before the owner reinstalled) and
says nothing about the client's events. On a carried customization record
the list is this session's, not the recorded visit's: `carry` drops the
saved list. `customization` also gets `events_received` the same way
(schema 2). A section switched off (at load, or by `/wowlab skip` this
session) is written with its plain "switched off by the owner" reason and
nothing else. `collections.appearances` has no events, so its reason gets
nothing added. After M11-04, any change to the format is a new schema:
M11-29's is schema 2, and the next one is schema 3.

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

**A new call stays [verify] until a capture shows it ran** (2026-09-29). `pcall`
catches Lua errors, not client assertions: `C_TransmogCollection.GetCategoryAppearances`
passed review and crashed the Forever client on its first login (M11-20). Any
API added later is listed here as **[verify]**, lives in a section
`/wowlab skip` can switch off, and is trusted only after an owner capture in
which it ran; `docs/AGENT_WORKFLOW.md` has the rule.

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
| `C_BarberShop.GetAvailableCustomizations` | customization: options, choices, current index | ran on 70058 (M11-23 capture: seven options with choices); `currentChoiceIndex` 1-based fits the tables, not proven: [verify] | yes |
| `C_BarberShop.GetCurrentCharacterData` | customization: `sex` only (name fields never read) | ran on 70058 (M11-23 capture: `sex` 0, no name stored); `sex` 1 not yet seen: [verify] | yes |
| `C_BarberShop.GetViewingChrModel` | customization: chr model id, or `chr_model_id_absent`: how the call went (schema 2, M11-29) | [verify]: no number on 70058 (M11-23 capture); why stays open | yes |
| `UnitRace` | customization: race id (third return only) | ran on 70058 (M11-23 capture: `race_id` 5); the drop on a race change is untested: [verify] | yes |
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
listed in the section's `events_unregistered` (an empty list when none was
refused).

Barber-shop events on 70058 (M11-23 capture): `BARBER_SHOP_OPEN` fired and
reached the section (the record says `"open"`).
`BARBER_SHOP_APPEARANCE_APPLIED` was not refused, yet after an applied change
the record's last write came from an open, so whether it ever reaches the
section is still **[verify]**. "Not refused" means `C_EventUtils.IsEventValid`,
when the client has it, did not say no, and `RegisterEvent` raised no error;
the addon does not read `RegisterEvent`'s return value. forever-addon-kit's
API list for 69893 has `C_EventUtils.IsEventValid`, so the check most likely
ran on Forever, but the file does not record whether it did on 70058. Three
limits on reading "not refused":

- every section's `events_unregistered` on 70058 is `{}`, so this client has
  never been seen refusing any name, and "not refused" is not yet evidence
  that a name is known (the control name below settles that);
- a known name means only that it is in the client's event table: an engine
  shared across flavors may know events this flavor never fires;
- only a count of 1 or more in `events_received` shows that an event fires.

The six count-only events of M11-29 (`BARBER_SHOP_RESULT`,
`BARBER_SHOP_CLOSE`, `BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE`,
`BARBER_SHOP_COST_UPDATE`, `BARBER_SHOP_SUCCESS` and the made-up control
`WOWLAB_CONTROL_NOT_A_REAL_EVENT`) are **[verify]**: they are registered by
`customization`, only counted, and a refused one is left out of
`events_received` rather than listed in `events_unregistered`.

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
   absent; since M11-22 an absent one carries `last_selected_config_absent`
   with the reason), `C_Traits.GenerateImportString` output (or
   `export_absent`), and whether any node has a `sub_tree`. If the talents
   panel offers saved loadouts, save and select one on a character, then
   `/reload`: `last_selected_config` should become a number.
3. Which `found_by` entry finds the Legacy config in `talents.legacy`. If
   `configs` is empty at level 25 or above with `legacy_ui = true`, add the
   kit's numeric system-ID probe (`GetConfigIDBySystemID` over 1–120).
4. Whether `GetTreeCurrencyInfo` carries the Legacy points spent and the
   seasonal cap.
5. `events_unregistered` on every section: which events Forever does not
   know. Since M11-22 it is always written (an empty list when none was
   refused), a never-gathered section included, so a login without a
   barber-shop visit settles whether `BARBER_SHOP_OPEN` and
   `BARBER_SHOP_APPEARANCE_APPLIED` are known. It shows only events the
   client refused; it does not prove that an accepted event ever fires.
   For `customization`, `events_received` (M11-29) does.
6. Empty lists from the pet journal or toy box (journal
   not initialized, or filters hiding rows) versus real contents.
7. Currencies: whether `GetCurrencyInfo` has `isAccountWide`, or the
   `IsAccountWideCurrency` fallback was used; `headers_collapsed`; `rows`
   against `headers` plus the length of `list` (a difference is rows whose
   id could not be read), and whether collapsing a header lowers `rows`.
8. `GetProfessionInfo` on Forever's 1–300 skill model: rank and maximum per
   tier or overall.
9. `probe.loads` rising across the two logins, and `probe.lost` absent.
10. Crafter GUIDs in item links: any `crafter_removed = true`, and no
    `Player-` left in any stored link.
11. `currentChoiceIndex` base: `choice` matches `choices[choice_index]`
    1-based (compare with the barber shop on screen). The race-mismatch drop
    cannot be tested without a paid race change; it stays open.
12. `BARBER_SHOP_APPEARANCE_APPLIED` firing: an applied change gives
    `recorded_at = "applied"`. Not so on 70058 (M11-23); the M11-29 capture
    step below settles it.
13. Gear: `first_slot` / `last_slot` values; whether slot 18 (ranged) ever
    holds an item; `item_level_api` per slot; `average.equipped` and
    `average.overall` are plausible for the gear worn (Forever's character
    sheet shows no item level to compare against, 2026-09-28).
14. Customization: `sex` is 0 or 1; `chr_model_id` is present; no name field
    appears anywhere in the record. On 70058 (M11-23) `sex` was 0, no name
    was stored, and `chr_model_id` was missing; since M11-29 (schema 2)
    `chr_model_id_absent` says how the call went, not why.
15. `talents.legacy` on a character below level 25, if one exists: `configs`
    empty, `legacy_ui` true.
16. Whether the "recording in 15 s" line was on screen when the world
    appeared, how many seconds were left, and whether
    `LOADING_SCREEN_DISABLED` is a known event on Forever.

## M11-29 capture step

One applied barber-shop change, for checklist items 11, 12 and 14: why, in
the M11-23 capture, the record's last write came from an open after an
applied change, and why it had no `chr_model_id`. The count-only events are
new client events for the addon, so they are **[verify]** until this capture
shows the addon ran with them; `/wowlab skip customization` switches them off
with the rest of the section.

Before you start:

- It costs gold: have enough for the price the shop shows on Accept.
- The new colour stays. It is a server-side change that `wowlab undo` cannot
  reverse (the undo in step 1 covers only the addon install). Changing it
  back is another paid visit, made only after the capture: a second sit in
  the same session spoils the counts. Beta characters are wiped anyway.
- Change one colour only. Do not change the body type: forever-addon-kit's
  API list for 69893 has `C_BarberShop.SetSelectedSex`, so the shop may offer
  it, and it would switch the model and its option ids.

Steps:

1. Client closed: install the current addon, `uv run wowlab addon install
   lab --root "$WOW"` (a `guard` transaction: snapshot first, and `wowlab
   undo` reverses the install). The copy installed before M11-29 writes
   schema 1 and neither new key.
2. Log in on one character and wait for the "recording in 15 s" line to
   pass. If the "recording in 15 s" line does not appear, WowLab is not
   running: check it is listed, ticked and not out of date at character
   select (`docs/handoffs/M11-03.md` §2); stop and report. If the client
   crashes at login, untick WowLab (§2 step 4): the six new event names are
   registered at `ADDON_LOADED`, before `/wowlab skip` can be typed in that
   session. Then stop and report (the `Error:` line and the WowLab file and
   line, M11-03 §2 step 4), and do not go on to step 3.
3. Sit in a barber chair once and change one colour (hair colour is
   enough). Before changing it, note the position of the current swatch and
   of the one you pick, counting from 1, left to right, then top to bottom
   (**[verify]** that this is the order the shop lists them), for example
   "hair colour: 7th, then 3rd". If the barber shop does not open, log out,
   capture anyway and report it; do not redo.
4. Click Accept once. Do not cancel and do not sit down again. Note whether
   the barber shop closed by itself after Accept or stayed open, and if it
   stayed open, how you left it. After you stand up, check that the new
   colour shows on the character. If it did not change, say so: the capture
   then says nothing about the applied event.
5. Log out in the same session. No `/reload` between the visit and the
   logout: `events_received` counts only the session that saves the file, and
   a `/reload` starts a new one (the record would then be written as carried,
   with the new session's counts, all 0). Do not log that character in again
   before capturing: a second login re-saves the file with a carried record
   and zero counts, the same as a `/reload`. Exiting from character select is
   fine. `/wowlab save` first is harmless; the file is written at logout.
   Exit the client.
6. Capture that character's `WowLab.lua` with `scripts/lab_capture.py`
   (`--sv WowLab.lua`, as in `docs/handoffs/M11-03.md` §4), and report the
   notes from steps 3 and 4 with it.

How the capture reads. The rows are in order; the first one that fits
applies. `OPEN`, `APPLIED` and the other short names are the
`BARBER_SHOP_*` entries of `customization.events_received`; "no entry" means
the client refused that name (a refused change event is also in
`events_unregistered`).

- **`schema = 1`**: the file was last written by the addon from before
  M11-29: the old copy ran, or the new one did not load, or the session
  crashed and wrote nothing, or this is another character's file. Report it
  with your notes; redo from step 1 only if step 1 was skipped.
- **`customization = { absent = "switched off by the owner" }`**: nothing to
  read, unless you switched it off because the client crashed: then report
  it. Otherwise type `/wowlab unskip customization`, `/reload`, and redo from
  step 2.
- **The shop did not open (step 3), or the colour did not change (step
  4)**: the Accept did not apply, or there was none (not enough money is one
  way). Send the capture anyway: it still answers the control, and, if the
  shop opened, the `BARBER_SHOP_CLOSE` baseline. `APPLIED` at 1 or more
  without an apply is itself a finding. If the shop did not open, do not
  redo. If it opened and the Accept failed, you may redo from step 2 in a new
  session.
- **`carried = true`**: no `BARBER_SHOP_OPEN` or
  `BARBER_SHOP_APPEARANCE_APPLIED` reached the section in the session that
  saved the file, and the counts belong to that session. If a `/reload` or
  second login came after the visit, that is why; it can also be another
  character's file. `carry` keeps
  `recorded_at` and the choices, so a carried `recorded_at = "applied"` with
  `recorded_load` one below `probe.loads` and the picked position still
  confirms item 12; only the counts are lost. Otherwise report it with
  `events_unregistered`; do not redo.
- **`customization = { absent = "no barber-shop visit recorded with the
  addon enabled" }` with `OPEN` at 0 or no entry**: no open reached the
  section in the session that saved the file: the visit was in another
  session, or this is another character's file; if neither, `OPEN` did not
  reach the section: report it.
- **You sat or clicked Accept more than once (notes from steps 3 and 4)**:
  the "applied" row and the "`APPLIED` at 0" row below still read as
  written. An "open" record with `APPLIED` at 1 or more cannot tell your
  later sit from an open the client fires itself: report it, and redo from
  step 2 in a new session if item 12 is still open. If the record is absent,
  read it with the absent-with-counts row.
- **`customization` absent with a gather reason and counts** (for example
  `"C_BarberShop.GetAvailableCustomizations returned nothing"` or
  `"error: ..."`, with `events_received` on the absent record): the last
  gather found nothing to read or failed, and it replaced the earlier record.
  With `APPLIED` at 1 or more and `OPEN` at 1, most likely the applied event
  fired when the barber API had nothing to give, and it replaced the open
  record (an absent record has no `recorded_at`, so the order is not shown).
- **`recorded_at = "applied"` with `APPLIED` at 1 or more**: the applied
  event reaches the section on this build, and no open reached it after.
  Then the changed option's `choice_index`:
  - the position picked in step 3: the record holds the new look. Item 12 is
    confirmed for that build, and item 11 (a 1-based index) with it.
  - the old position: either the applied event fired before the choices
    updated, or it was not the Accept's. Item 12 stays open.
  - neither noted position: report both; item 11 stays open.
- **`recorded_at = "open"`, `APPLIED` at 1 or more, `OPEN` at 2 or more, and
  you sat once**: the client fired the second open itself, for example to
  refresh after the Accept (**[verify]**). An open reached the section after
  the applied event. Compare the changed option's `choice_index` with step
  3: at the new position, the choices are the new look; if it is the old
  position, both opens came before the Accept.
- **`recorded_at = "open"`, `APPLIED` at 1 or more, `OPEN` at 1**: the
  applied event reached the section before the only open, so on this build
  it does not mark the Accept. The choices should be the old look: compare
  with step 3; at the new position the only open came after the Accept:
  report it.
- **`recorded_at = "open"` with `APPLIED` at 0 or no entry, and the new
  colour visible on the character (step 4)**: the applied event did not
  reach the section after this Accept. With `OPEN` at 1 the choices are the
  look at the open; compare the old position from step 3 for item 11. With
  `OPEN` at 2 or more, compare `choice_index` with step 3: at the new
  position the client opened again after the Accept, and that open, not the
  applied event, followed the apply (a candidate for a later ticket); at the
  old position, read as with `OPEN` at 1.
  `BARBER_SHOP_CLOSE` is expected at 1 on any visit, applied or cancelled (on
  Retail, **[verify]**); it is not an apply signal and nothing may be
  recorded on it (§13.1). `BARBER_SHOP_RESULT` at 1 or more says the client
  answered the Accept, not that it succeeded (on Retail, **[verify]**).
  `BARBER_SHOP_COST_UPDATE` at 1
  or more says the addon saw shop activity during the visit (on Retail it
  fires as previewed choices change the price, **[verify]**); it is not an
  apply signal. `BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE` or
  `BARBER_SHOP_SUCCESS` at 1 or more is the candidate a later ticket may test
  with its own capture. If only `BARBER_SHOP_CLOSE` moved (besides `OPEN` at
  1 and `BARBER_SHOP_COST_UPDATE`), none of the watched events marked the
  apply; whether the client offers another signal stays open.
- **None of these**: report the file and your notes as they are.

Read with every row that has `events_received`:

- **`WOWLAB_CONTROL_NOT_A_REAL_EVENT`**, the made-up control. If the control
  has no entry, this client refuses a name it does not know, so every other
  entry, 0 included, is a name this client knows; 0 means it did not reach
  the section in that session, and a missing entry means a name the client
  does not know. If the control has an entry at 0, the client accepts any
  name, so an entry (or a name missing from `events_unregistered`) proves
  nothing about a name being known; only a count of 1 or more shows an event
  exists and fires. The reading holds whether or not
  `C_EventUtils.IsEventValid` exists, because the control tests the addon's
  whole gate (that check, then `RegisterEvent`).
- An event with no entry in `events_received` was refused by the client (a
  refused change event is also in `events_unregistered`).
- `chr_model_id` is a number, or `chr_model_id_absent` gives one of the four
  outcomes listed under "`customization` in schema 2" above: the outcome of
  the call when the section gathered, not why. The Lab keeps taking the body
  type from `sex`.

Registering a name the client does not know (the control, or any of the new
names Forever lacks) has never been exercised on Forever: every
`events_unregistered` on 70058 is `{}`. It happens at `ADDON_LOADED`, before
`/wowlab skip` can be typed, so if it ever crashed the client, untick WowLab
at character select (`docs/handoffs/M11-03.md` §2 step 4), or with the
client closed `wowlab undo` the step 1 install.
