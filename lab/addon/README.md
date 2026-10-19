# lab/addon/

The Lab's own addons: Lua that runs inside the game client, never in the Lab
(ADR-0026, `docs/LAB_PLAN.md` §13.1). One folder per addon; the first is
`WowLab/`. Nothing in the Lab loads, runs or evaluates these files (L3): they
are checked by a static linter, by Python tests that read their tokens, by
review, and later by the Python reader graded on the SavedVariables the addon
wrote (M11-04).

## WowLab

Records this character's equipped gear, spec, class talents, Legacy-tree candidates,
barber-shop choices, collections, currencies, professions, spellbook, action
bars, key bindings and the character's race, body type and class into
`WowLabCharDB` (`## SavedVariablesPerCharacter`) as a versioned table
(`schema = 4` since M15-01; 3 since M13-05; 2 since M11-29), written by the client at logout, `/reload` or a
clean exit. `/wowlab save` refreshes the tables in memory only; a crash
writes nothing.
`/wowlab skip <section>` and `/wowlab unskip <section>` switch one section
off and on ("Switching a section off" below).
`WowLabDB` (`## SavedVariables`) holds only `{ schema = 4 }` until a capture
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

### Table layout (schema 4)

```
WowLabCharDB = {
  schema = 4,
  probe = { loads = <n>, lost = true? },      -- carried across sessions, with customization; lost: this load only
  skip = { "<section key>", ... },            -- sections the owner switched off; left out when none (M11-21)
  client = { version, build, interface },     -- GetBuildInfo(); its date is not kept
  spec = { index, id?, api } | { absent },
  gear = { first_slot, last_slot, slots = { { slot, link, crafter_removed, item_level, item_level_api } },
          average = { overall (best items owned, bags included), equipped (GetAverageItemLevel's second value; Forever's character sheet shows none), pvp } },
  talents = {
    class = { config = <config>, export | export_absent, last_selected_config | last_selected_config_absent },
    legacy = { legacy_ui, player_level, skipped_types, left_out_config | left_out_config_absent, configs = { <config + found_by> } },  -- left_out_*: schema 3
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
  -- schema 3 (M13-05), each read only out of combat:
  spellbook = { item_types = { <name> = <number>, ... }, skill_lines = { { index, offset, count, guild, hidden, spec_id, off_spec_id } },
                list = { { slot, skill_line, type, action_id, spell_id, spell_name, subtext, passive, off_spec } }, probes },
  actionbars = { first_slot, last_slot, slots = { { slot, type, id, sub_type } }, probes },
  bindings = { binding_set, account_bindings, character_bindings, commands, commands_left_out, keys_left_out, list = { { command, keys = { "<key>", ... } } }, probes },
  -- schema 4 (M15-01); each value is exactly one of <field> and <field>_absent:
  identity = { race_id | race_id_absent, sex_raw | sex_raw_absent, class_id | class_id_absent, class_file | class_file_absent },
}
probes = { { api, present, calls, errors, answered, secret } }  -- one per API the section asks, in a fixed order, then issecretvalue, C_Secrets, canaccessvalue
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

Schema 3 (M13-05) is schema 2 with one pair of keys on `talents.legacy`
(`left_out_config` or `left_out_config_absent`, from M11-35) and three new
sections, `spellbook`, `actionbars` and `bindings`; nothing else changed. The
Lab reads schema 1 and 2 files exactly as before (a new section's key in one
of them is an unknown key, and refused when it holds text), and refuses a
schema it does not know. The first logout with the M13-05 addon rewrites the
file as schema 3, and `probe.loads` keeps rising across that write.

Schema 4 (M15-01) is schema 3 with one new section, `identity`; nothing
else changed. The Lab reads schema 1, 2 and 3 files exactly as before and
reports every `identity` value as absent with the reason "the record is
schema n; identity is recorded from schema 4 (the lab-addon from M15-01:
install it with wowlab addon install lab, then log in and out on this
character)", and refuses a schema it does not know. The first
logout with the M15-01 addon rewrites the file as schema 4, and
`probe.loads` keeps rising across that write.

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
- `identity` (schema 4, M15-01): the character's race, body type and
  class, gathered on entering the world (after the 15 s window, like every
  on-world section) and again on `BARBER_SHOP_APPEARANCE_APPLIED`, since
  a body type can change in the barber shop. That the event fires is
  confirmed (70425, M11-29 capture: `events_received` 1); that `UnitSex`
  already returns the new body type when the debounced gather runs 2 s
  after it is **[verify]**, and not tested by the M15-01 capture. If that
  gather is too early, the logout write does not gather again (the section
  is no longer dirty), so the file keeps the old value until the next
  login. Each value is exactly one of the value and `<field>_absent`:
  - `race_id`: the third return of `UnitRace("player")`, as
    `Customization.lua` reads it; the name and file token are not kept.
  - `sex_raw`: the first return of `UnitSex("player")` as the client gives
    it: 1 unknown, 2 male, 3 female (from memory, **[verify]**). The addon
    does not map it. `customization.sex` comes from
    `C_BarberShop.GetCurrentCharacterData`, which `customization` calls
    only in the barber shop (how it answers outside is untested), so it
    does not serve here.
  - `class_id` and `class_file`: the third and second returns of
    `UnitClass("player")` (the class id and the client's file token, such
    as `WARLOCK`); the localized class name is not kept.

  The reasons, as the addon writes them:
  - `"the client has no UnitRace function"`
  - `"the third return of UnitRace was not a number"`
  - `"the client has no UnitSex function"`
  - `"the first return of UnitSex was not a number"`
  - `"the client has no UnitClass function"` (on both class fields)
  - `"the third return of UnitClass was not a number"`
  - `"the second return of UnitClass was not a non-empty string"`

  The Lab's reader (`wowlab_core.labaddon.identity_of`) derives `sex`, the
  barber shop's `Enum.UnitSex` convention (0 male, 1 female, as
  `customization.sex` holds it), from `sex_raw` by one mapping: 2 to 0, 3
  to 1, and anything else absent with the reason `"UnitSex returned <n>"`.
  A value the reader cannot take (a race or class id that is not a whole
  number from 1 to 2147483647, a `sex_raw` that is not a whole number, a
  class file that is not 1 to 32 capital letters or underscores) is set
  aside with a reason instead of refusing the file.
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
      `RegisterEvent`). Registering a name the client does not know is not
      known to be safe on Forever: the empty `events_unregistered` lists
      (every one on 70058 is `{}`) show only that no refusal was seen. It
      happens at `ADDON_LOADED`, before `/wowlab skip` can be typed, so if
      it ever crashed the client, untick WowLab at character select
      ("Switching a section off" above), or with the client closed `wowlab
      undo` the install.
- `customization`, settled by the M11-29 capture (1.60.1.70425, 2026-10-16;
  `docs/LAB_FORMATS.md`, amendment of that date). One sit and one applied
  hair-colour change gave `OPEN` 2, `APPLIED` 1, `CLOSE` 2, `RESULT` 1,
  `COST_UPDATE` 0 and `FORCE_CUSTOMIZATIONS_UPDATE` 0, with
  `recorded_at = "open"` holding the new look. Confirmed:
  `BARBER_SHOP_APPEARANCE_APPLIED` fires and reaches the section. The
  client then fired `BARBER_SHOP_OPEN` again, after the Accept, and that
  open's record replaced the applied one. Whether the shop actually
  reopens, and why, is **[verify]**: the owner saw it close by itself. So in
  this capture the applied change ended as `"open"`; one capture does not
  show that it always will. `currentChoiceIndex` is consistent with
  1-based. The new swatch 1 is index 1, and the 70058 record reports index 7
  for option 61, which has 7 choices in the 70009 table, so a 0-based client
  could not report it. The addon takes `choice` from `choices[index].id`, so
  the choice ids are no evidence of the base. That the client's list has the
  table's 7 choices is **[verify]**. Counting left to right, then top to bottom, matches
  the table's order for hair colour. `OrderIndex` and `UiOrderIndex` are
  identical for these choices, so this does not tell which column the shop
  uses.
  The control and `BARBER_SHOP_SUCCESS` have no entry: this client refuses
  a name it does not know, and `BARBER_SHOP_SUCCESS` is not one.
  `BARBER_SHOP_CLOSE` came twice for one sit. `GetViewingChrModel`
  returned nil. M11-29's other question, why, is still **[verify]**.
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
- `talents.legacy` in schema 3 (M11-35) holds exactly one of
  `left_out_config`, the active class config id the addon left out of
  `configs` at this gather (a config with that id is never listed), and
  `left_out_config_absent`, why no id was left out:
  - `"C_ClassTalents.GetActiveConfigID missing"`
  - `"C_ClassTalents.GetActiveConfigID raised an error"`
  - `"C_ClassTalents.GetActiveConfigID returned nil"`
  - `"C_ClassTalents.GetActiveConfigID returned no number"`

  The call goes through `pcall` directly, so an error is told apart from nil.
  `talents.class` asks the same function at its own gather, which may be
  another moment.
- `spellbook`, `actionbars` and `bindings` (schema 3, M13-05) are read only
  out of combat. Each gather asks `InCombatLockdown` first; when it says the
  player is in combat, or is missing, or raises an error, nothing else is
  called. The gather is then blocked, not failed: a record gathered earlier
  in the session is kept as it was, the section stays dirty, and the reason
  is written as the section's absent record only when nothing was gathered
  for it in that session (a `/reload` or logout in combat before the first
  pass ran out of combat). Each of the three has `PLAYER_REGEN_ENABLED`
  (combat ended) as a retry event: it gathers the section again only while
  the section is still dirty, so a fight that blocked nothing re-reads
  nothing. A logout also gathers whatever is still dirty, and keeps the
  earlier record when combat blocks that too. Values are read only when
  `InCombatLockdown` says not in combat; whether these values can be secret
  outside combat in restricted content (`docs/GLOSSARY.md`, "Secret value")
  is **[verify]**. Where the client has `issecretvalue` (forever-addon-kit's
  69893 list does not have it. On 70425 a global function by that name was
  present and answered a boolean on every call, M13-05 capture of
  2026-10-16. The probe cannot tell the client's function from an addon's,
  so where it comes from is **[verify]**), every
  value a call gives, and every field of a table it gives, is tested before
  it is compared, used or stored. One it calls secret, or raises an error or
  gives no boolean for, is dropped and counted on its API's probe as
  `secret`. Without `issecretvalue` every value is kept, and its probe says
  `present = false`. No `issecretvalue` means values are not tested, not
  that none are secret; a secret value then makes the gather raise and the
  section is written as an error, unless it was gathered earlier in the
  session: a section with a retry event keeps that record through an error
  as through a block, and stays dirty. `C_Secrets` and `canaccessvalue`
  (both present on 70425) are looked up, never called; their probes say only
  whether the client has them. In `Actions.lua` no client function is called but
  through `pcall`, and every value a `pcall` gives is cleaned, or cleared
  when the call raised, before anything reads it (`tests/addon` pins this
  structurally). The reasons:
  - `"in combat: this section reads only out of combat, and gathers again when combat ends"`
  - `"InCombatLockdown missing, so whether the player is in combat is not known; nothing was read"`
  - `"InCombatLockdown raised an error, so whether the player is in combat is not known; nothing was read"`
- `probes` (schema 3) is written on each of the three, gathered or absent
  for a missing function (not on the combat reasons above, which call
  nothing). One entry per API the section asks, in a fixed order with
  `issecretvalue`, `C_Secrets` and `canaccessvalue` last: `api` (its
  name), `present` (the function was
  there), `calls`, `errors` (calls that raised an error), `answered` and
  `secret` (values it gave that were dropped as secret). Presence proves
  nothing, so a capture reads what each function did from these counts;
  `calls − errors − answered` is the calls that answered nothing (a call
  whose value was dropped as secret answered nothing). What "answered"
  means:
  - `C_SpellBook.GetNumSpellBookSkillLines`, `GetCurrentBindingSet`,
    `GetNumBindings`: returned a number;
  - `C_SpellBook.GetSpellBookSkillLineInfo`, `C_SpellBook.GetSpellBookItemInfo`:
    returned a table;
  - `C_SpellBook.GetSpellBookItemName`, `GetBinding`: returned a string
    first;
  - `GetActionInfo`: returned something for the slot (an empty slot answers
    nothing);
  - `GetMacroInfo`: returned an icon for the macro index, so the index names
    a macro;
  - `GetBindingKey`: returned at least one key;
  - `issecretvalue`: returned a boolean;
  - `C_Secrets`, `canaccessvalue`: never called, so `calls`, `errors`,
    `answered` and `secret` are 0; only `present` says anything.
- `spellbook`: the player's spellbook only (the bank is the client's
  `Enum.SpellBookSpellBank.Player`, read, never assumed), walked by skill
  line from `itemIndexOffset + 1` for `numSpellBookItems` slots. Future
  spells (shown in the spellbook, not learned yet) are items too. `type` is
  the client's raw `Enum.SpellBookItemType` number, and `item_types` is that
  enum as the client has it, member name to number (at most 64 members, each
  name a letter then letters, digits and `_`), so a reader names each type
  from the file, never from numbers it assumes; the record has no
  `item_types` when the client has no such table. `action_id` and `spell_id`
  are kept raw (a flyout's action id is not a spell id). `spell_name` and
  `subtext` come from `GetSpellBookItemName` (the subtext is where a rank
  shows, when the client gives one) and are kept only when they are 1 to 255
  bytes with no control character and no `|`. Skill-line names are not read.
  `passive` and `off_spec` are the client's flags. A gather walks at most 64
  skill lines and 4096 items. Privacy **[verify]**: whether a hunter's named
  pet shows in a spell's name or subtext (a pet's name is text the owner
  typed); checklist item 13. A warlock is no test of this: the addon never
  reads the pet spellbook, and a warlock demon's name is generated, not
  typed (from memory, **[verify]**). On 70425 a
  spell with several ranks is one item per rank, each with its own spell id
  and a `Rank n` subtext, and the spellbook has no "show all ranks" toggle.
  A flyout has an `action_id` and no `spell_id`.
- `actionbars`: every slot from `first_slot` to `last_slot` (1 to 180, the
  addon's own bound: fifteen runs of twelve slots, the main bar's pages,
  the stance and bonus bars and the multi-action bars **[verify]**; no
  client constant gives it) that answered something. In a vehicle or while
  skyriding the client fills some slots with temporary actions
  **[verify]**, so a capture is taken outside both; whether skyriding
  exists on Forever is not known. `type` and `sub_type` are the client's words (letters
  only, at most 32); `id` is kept only when it is a number. A macro slot
  keeps an id only, never the macro's name or body: with no subtype (nil or
  empty) the id is the macro index **[verify]**; with one, it is the spell
  or item id the macro shows now **[verify]**. On 70425 neither macro slot
  had a subtype, the `#showtooltip <spell>` macro included, and
  `GetMacroInfo` answered for both ids. So a `#showtooltip` macro slot
  gives no spell id there, and how the index is numbered stays
  **[verify]** (`docs/LAB_FORMATS.md`, 2026-10-16, item 7). `GetMacroInfo` is asked only for a macro
  index (never for a subtype's id, nor when the subtype was dropped as
  secret: an out-of-range index into a C API is the crash class in
  `docs/DATA_SOURCES.md`, 2026-09-28), only whether the index names a macro,
  and only its count is kept in `probes`. An equipment-set slot, whose id is
  the set's name, keeps no id. Every unfilled slot up to 180 answers
  nothing and raises no error (70425: 180 calls, 0 errors, 26 answered;
  highest filled slot 72).
- `bindings`: the keys `GetBindingKey` returns now, per binding command, for
  every command `GetBinding` lists that is a plain upper-case name
  (`ACTIONBUTTON1`, `MULTIACTIONBAR1BUTTON3`, `MOVEFORWARD`) and has a key.
  A command that is not a plain upper-case name (text after the name, as in
  `MACRO x`, or other characters) is counted in `commands_left_out` and
  never asked about or stored: a macro's name is text the owner typed. `GetBinding` lists the
  client's named commands; whether a key bound straight to a spell, macro,
  item or click (a spell dragged onto a key, an action-bar addon's button)
  is listed at all is **[verify]**, so `commands_left_out` counts only those
  `GetBinding` listed, and such keys may be in neither count. A key that is
  not printable ASCII without a space is counted in `keys_left_out`.
  `commands` is every command `GetBinding` gave. `binding_set` is
  `GetCurrentBindingSet`'s raw value, and `account_bindings` and
  `character_bindings` are the client's `ACCOUNT_BINDINGS` and
  `CHARACTER_BINDINGS`, so a reader names the set, and so which
  `bindings-cache.wtf` applies, from the file. **Contradicted on 70425**:
  neither constant was a number at the last gather (nil or another type;
  the file cannot say which), so neither key is written, and `binding_set`
  2 (with "Character Specific Key Bindings" ticked) cannot be named from
  the file. `Enum.BindingSet` is a **[verify]** candidate for a later
  schema. On 70425 the eight lines of both (identical) `bindings-cache.wtf`
  files matched the live keys. The other 118 bound commands are
  unexplained. A spell dragged onto a key and the owner's `/say` macro were
  in neither cache, so the caches may predate the session **[verify]**. One
  command was counted in `commands_left_out`; whether it is the dragged
  spell stays **[verify]**. A gather walks at most
  4096 commands.
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
M11-29's is schema 2, M13-05's is schema 3, M15-01's is schema 4, and the
next one is schema 5.

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
- `Actions.lua`: `spellbook`, `actionbars`, `bindings`
- `Identity.lua`: `identity`
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
No tracked file may hold a raw bidirectional control character (the
embeddings, overrides, isolates and marks that can make text read in a
different order than it runs). `tests/repo/test_bidi_controls.py` searches the
raw bytes of every tracked file, this folder's Lua and YAML included, so a NUL
or a stray byte elsewhere in a file does not hide one, and it checks every
tracked file name. It leaves out only the real captures under
`lab/core/tests/fixtures/`; their index, the README there, is scanned. ruff's
`PLE2502` checks Python as well (M11-37).

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

Where a call may sit (M11-37). A call is *switchable* when a `/wowlab skip`
typed in the session stops it: it sits in a section's `gather`, or in a
function that only gathers call, and call at once (directly, through `pcall`,
`xpcall` or `ns.Call`, or as a `table.sort` comparator). A gather runs on the
first on-world pass (15 s after entering the world), on its section's change
events, on `/wowlab save` and at logout, and never for a switched-off
section. A `carry` is not switchable: it runs at `ADDON_LOADED`, before the
world is visible, so only a skip saved in an earlier session stops it. Nor is
anything run at load or from an event handler outside a section.
`tests/addon/test_lab_addon.py` holds the sources to three rules:

- Every reference to a client global, and every use of a file-level local
  that holds one, sits in a gather, or is listed in `SECTIONLESS_API` by
  file, function and name, with how many times it is called, tested, bound,
  passed as its own method's self or assigned there, and why. That function
  must be the only one of its label in its file (a gather or carry written
  in a section spec is labelled with its key, `gather("gear")`), and a
  function holding such a reference is only ever called directly, from the
  callers pinned in `SECTIONLESS_CALLERS`, each with its number of calls. In
  `Core.lua` the list holds the event frame (`CreateFrame`, `RegisterEvent`,
  `UnregisterEvent`, `SetScript`), the timer (`C_Timer.After`),
  `C_EventUtils.IsEventValid`, `GetBuildInfo` for the `client` block and the
  `/wowlab` registration (`SlashCmdList`).
- A gather hands no function on. Nothing that may be a function goes to
  `ns.On`, a timer, `SetScript`, a hook or any other call that could keep it,
  into a table, a field, a global or a local declared outside the gather's
  own code, or out as a return value; a function statement such as
  `function ns.later()` written in a gather counts too. Such a function would
  keep running after `/wowlab skip`. "May be a function" covers a function
  literal, an addon function, a client global, `ns.Fn(...)`, what `next`,
  `pairs`, `ipairs`, `select` or `unpack` return from one, a loop variable
  over one, and any local or helper parameter ever given one. The exceptions
  are listed in `HANDED_ON`, each with its reason. Today there are four:
  `ItemLocation` as the self of its own `CreateFromEquipmentSlot`, and three
  values `talents.legacy` reads out of the client's `Enum.TraitConfigType`
  and `Constants` tables and hands at once to `C_Traits` or `string.find`.
- Each file binds its `...` once, as `local <name>, ns = ...`, and uses it
  nowhere else. `ns` is written only as `ns.Name` (never `ns:Name`, which
  hands the namespace on as `self`), no function is defined with `:`, and
  `ns.state` is only indexed or assigned where it stands. So neither the
  namespace nor the state the race check reads can be reached under another
  name.

The one listed call outside `Core.lua` is `UnitRace` in the
`PLAYER_ENTERING_WORLD` race check in `Customization.lua`. It runs only while
a carried barber-shop record is held, so switching `customization` off stops
it from the next check. But it runs on every loading screen (each
`PLAYER_ENTERING_WORLD`), not after the 15 s window. On a login with a carried
record and no skip saved in an earlier session, nothing typed in that session
can stop the first one: if it crashed the client, the crash would repeat until
the addon is unticked at character select.

Left to review, because the test does not follow them:

- a function returned by a call other than `ns.Fn`, `next`, `pairs`,
  `ipairs`, `select` and `unpack` (a client call's result, or a field of it);
- a client object that a gather stores in a table (`ns.state`, a record) and
  that code outside a section later reaches through that table;
- a way of reaching a client global that the dynamic-lookup check does not
  name (it rejects `_G`, `getfenv`, `debug`, `setmetatable` and the others
  it lists);
- which addon functions `Core.lua`'s own handlers call: for example,
  `ns.Refresh()` added to its `PLAYER_ENTERING_WORLD` handler would gather
  every on-world section as the world is entered, before the 15 s window,
  and the test would not see it.

Within those limits, a new client call outside a gather, or a function handed
on from one, fails the test until someone lists it on purpose.

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
| `C_BarberShop.GetAvailableCustomizations` | customization: options, choices, current index | ran on 70058 (M11-23 capture: seven options with choices) and 70425 (M11-29 capture: `currentChoiceIndex` 1-based, confirmed against the swatch picked); other builds: [verify] | yes |
| `C_BarberShop.GetCurrentCharacterData` | customization: `sex` only (name fields never read) | ran on 70058 (M11-23 capture: `sex` 0, no name stored); `sex` 1 not yet seen: [verify] | yes |
| `C_BarberShop.GetViewingChrModel` | customization: chr model id, or `chr_model_id_absent`: how the call went (schema 2, M11-29) | [verify]: no number on 70058 (M11-23 capture); returned nil on 70425 (M11-29 capture); why stays open | yes |
| `UnitRace` | customization and identity: race id (third return only) | ran on 70058 (M11-23 capture: `race_id` 5); the drop on a race change is untested; in `identity` (schema 4): [verify] | yes |
| `UnitSex` | identity: `sex_raw`, the first return as the client gives it (1 unknown, 2 male, 3 female, from memory) | [verify] | yes |
| `UnitClass` | identity: class file and class id (second and third returns; the localized name is never kept) | [verify] | yes |
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
| `InCombatLockdown` | spellbook, actionbars, bindings: nothing is read in combat, or when this cannot say (schema 3) | ran on 70425 (M13-05 capture: all three sections gathered out of combat); its answer in combat: [verify] | yes |
| `issecretvalue` | spellbook, actionbars, bindings: each value is tested before it is used or stored; a secret one is dropped and counted | ran on 70425 (M13-05 capture: a global function present, a boolean on every call, 0 secret); whether it is the client's or an addon's, and restricted content: [verify] | no |
| `C_Secrets` | spellbook, actionbars, bindings: looked up, never called; its probe says whether the client has the table | present on 70425 (M13-05 capture); what it does: [verify] | no |
| `canaccessvalue` | spellbook, actionbars, bindings: looked up, never called; its probe says whether the client has it | present on 70425 (M13-05 capture); what it does: [verify] | no |
| `Enum.SpellBookItemType` | spellbook: `item_types`, member name to number, read from the client | ran on 70425 (M13-05 capture: `None` 0, `Spell` 1, `FutureSpell` 2, `PetAction` 3, `Flyout` 4); a future-spell item not yet seen: [verify] | n/a (table) |
| `ACCOUNT_BINDINGS` | bindings: the client's value for the account-wide set, recorded beside `binding_set` | [verify]: not a number at the last gather on 70425 (nil or another type; M13-05 capture), so not recorded; `Enum.BindingSet` is a candidate | n/a (constant) |
| `CHARACTER_BINDINGS` | bindings: the client's value for the character's set, recorded beside `binding_set` | [verify]: not a number at the last gather on 70425 (nil or another type; M13-05 capture), so not recorded; `Enum.BindingSet` is a candidate | n/a (constant) |
| `C_SpellBook.GetNumSpellBookSkillLines` | spellbook: how many skill lines | ran on 70425 (M13-05 capture: 4); other builds: [verify] | yes |
| `C_SpellBook.GetSpellBookSkillLineInfo` | spellbook: each line's item offset and count, guild, hidden and spec flags (the line's `name` is never read) | ran on 70425 (M13-05 capture: 4 of 4 answered); other builds: [verify] | yes |
| `C_SpellBook.GetSpellBookItemInfo` | spellbook: item type, action and spell id, passive and off-spec flags (its `name` field is never read) | ran on 70425 (M13-05 capture: 28 of 28 answered); other builds: [verify] | yes |
| `C_SpellBook.GetSpellBookItemName` | spellbook: the spell's name and subtext (a rank where the client gives one) | ran on 70425 (M13-05 capture: 28 of 28 answered, ranks as `Rank n`); other builds: [verify] | yes |
| `Enum.SpellBookSpellBank` | spellbook: the `Player` bank value, read, never assumed | ran on 70425 (M13-05 capture: the section was not absent for it); other builds: [verify] | n/a (table) |
| `GetActionInfo` | actionbars: type, id and subtype per slot, 1 to 180 | ran on 70425 (M13-05 capture: 180 calls, 0 errors, 26 answered); a `#showtooltip` macro slot had no subtype; other builds: [verify] | yes |
| `GetMacroInfo` | actionbars: probe only, whether a macro slot's id names a macro, asked only when the slot has no subtype (name and body never stored) | ran on 70425 (M13-05 capture: 2 of 2 answered); index numbering: [verify] | yes |
| `GetCurrentBindingSet` | bindings: which binding set is active, raw | ran on 70425 (M13-05 capture: 2, with "Character Specific Key Bindings" ticked); other builds: [verify] | yes |
| `GetNumBindings` | bindings: how many binding commands | ran on 70425 (M13-05 capture: 299); other builds: [verify] | yes |
| `GetBinding` | bindings: each command (first return only) | ran on 70425 (M13-05 capture: 299 of 299 answered); whether it lists a spell dragged onto a key: [verify] | yes |
| `GetBindingKey` | bindings: the keys bound to a command now | ran on 70425 (M13-05 capture: 124 of 298 with a key; the 8 `bindings-cache.wtf` lines match, the other bound commands are unexplained); other builds: [verify] | yes |

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
`SKILL_LINES_CHANGED`, and since schema 3 `SPELLS_CHANGED`,
`ACTIONBAR_SLOT_CHANGED`, `UPDATE_BINDINGS` and `PLAYER_REGEN_ENABLED` (a
retry event: it re-gathers only a section a blocked gather left dirty). An
event the client does not know is skipped and
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

## M13-05 [verify] checklist

What the owner's schema-3 capture must settle (the conductor's runbook is
`docs/handoffs/M13-05.md`). Every API `Actions.lua` asks is **[verify]**
until a capture shows it ran. Each item ends up confirmed, contradicted (with
what Forever returned) or still open in the dated `docs/LAB_FORMATS.md`
amendment that follows the capture.

Read on 2026-10-16 (capture on 1.60.1.70425, fixtures under
`lab/core/tests/fixtures/macos-70425/`; `docs/LAB_FORMATS.md`, amendment
of that date). Items 2, 3, 4, 6, 9 and 10 are confirmed. Item 7 is
contradicted in part: a `#showtooltip` macro slot has no subtype, and the
macro numbering is still open. Item 8 is confirmed for the eight cache lines
only, and contradicted in part: `ACCOUNT_BINDINGS` and `CHARACTER_BINDINGS`
were not numbers at the last gather. The other 118 bound commands and a
spell dragged onto a key are still open. Item 1 is confirmed for `schema`;
`probe.loads` rising is still open, since the file was a first load. Items
5, 11, 12 and 14 are still open. Item 13's macro check is confirmed; the
hunter-pet check is still open.

Capture steps beyond §13.1's: log in out of combat, not in a vehicle and not
skyriding (both swap the main bar's page), and wait for the "recording in
15 s" pass; have on an action bar at least one spell, one item, one macro
whose first line is `#showtooltip <spell>` and one plain-text macro with no
`#showtooltip`; drag one spell straight onto a key in the key bindings panel
if the client allows it, and, if an action-bar addon is installed, bind one
of its buttons to a key; open the key bindings panel once and note whether
"Character Specific Key Bindings" is ticked; then log out.

Combat steps (optional; each `/reload` writes the file, so copy it aside
after each one you want to keep):

- `/reload` while fighting a training dummy and keep fighting past the 15 s
  pass, then `/reload` again still in combat: that file shows the combat
  reason on all three sections (nothing was gathered in that session).
- Then end combat, wait at least 3 s (the debounce is 2 s), pull the dummy
  again and `/reload` in combat. Records present means the retry ran; the
  combat reason means it did not. Don't use `/wowlab save` in between.
- Separately: after a clean first pass out of combat, pull the dummy and
  `/reload` in combat: that file still holds the pre-fight records, not the
  reason.

Restricted content (optional): in an instance, between pulls (out of
combat), `/wowlab save` and `/reload`: whether any probe's `secret` count is
above 0 there.

Items:

1. `schema = 3` in both variables, and `probe.loads` rising across the
   first schema-3 write.
2. `probes` on `spellbook`, `actionbars` and `bindings`: for each API,
   `present`, `calls`, `errors`, `answered` and `secret`. Any `errors` above
   0, or a function `present = false`, is a finding. `issecretvalue`:
   whether Forever has it at all (forever-addon-kit's 69893 list does not),
   and, if it does, `answered` equal to `calls` (it gave a boolean each
   time). `C_Secrets` and `canaccessvalue`: whether each is `present`
   (looked up only, never called).
3. `spellbook` is not absent with `"Enum.SpellBookSpellBank.Player missing"`;
   its `skill_lines` offsets and counts match the spellbook on screen.
4. `subtext` and ranks: whether ranks show as text (`"Rank 3"`) and which
   other subtexts appear (settles part of M13-06's rank question); how a
   spell with several ranks appears (one item, or one per rank); whether
   the spellbook has a "show all ranks" toggle and what the file holds with
   it on and off. `item_types` as the client has it, the `type` numbers
   seen, and whether a flyout's `action_id` differs from its `spell_id`.
5. A future spell (shown in the spellbook, not learned yet): its `type`
   (`FutureSpell` in `item_types`?) and whether it has a `spell_id`.
6. `actionbars`: whether slots past the client's own range answer nothing
   or raise an error (`GetActionInfo` `errors`), and the highest filled
   slot, taken out of a vehicle and not skyriding.
7. Macro slots: `type = "macro"`; for the `#showtooltip <spell>` macro,
   whether `sub_type` is set and `id` is the spell's id; for the plain-text
   macro, no `sub_type` and an `id` that is the macro index. Compare the
   index with the `VER 3 <id>` of that macro in the account folder's or the
   character folder's `macros-cache.txt`, and record how account and
   character indices are numbered **[verify]**. `GetMacroInfo` `calls` equal to the
   macro slots with no `sub_type`, and `answered` equal to `calls`. No
   macro name or body anywhere in the file.
8. `bindings`: `binding_set` against the ticked "Character Specific Key
   Bindings", with `account_bindings` and `character_bindings` present and
   naming it, and the keys against the matching `bindings-cache.wtf`
   (account folder or character folder). Whether the spell dragged onto a
   key and the action-bar addon's key appear at all: as a counted
   `commands_left_out`, or nowhere (then `GetBinding` does not list them).
   `commands_left_out` and `keys_left_out` as counts only; no `MACRO`,
   `SPELL`, `ITEM` or `CLICK` command stored.
9. `talents.legacy.left_out_config` equals `talents.class.config.id` when
   both gathers ran at the same moment; a `left_out_config_absent` reason is
   a finding.
10. `events_unregistered` on the three new sections: whether
    `SPELLS_CHANGED`, `ACTIONBAR_SLOT_CHANGED`, `UPDATE_BINDINGS` and
    `PLAYER_REGEN_ENABLED` are known.
11. Combat, if those steps were done: the reason only in the file written
    while nothing had been gathered; the retry after combat: end combat,
    wait at least 3 s (the debounce is 2 s), pull the dummy again and
    `/reload` in combat. Records present means the retry ran; the combat
    reason means it did not. Don't use `/wowlab save` in between. And the
    pre-fight records kept through a `/reload` in combat.
12. Restricted content, if that step was done: any `secret` count above 0
    in an instance out of combat is a finding (which API, how many).
13. Privacy, on a hunter with a named pet: grep the capture for the pet's
    name (and `macros-cache.txt`'s macro names); any match is a finding
    against the scrub and against this section.
14. Hitches: whether the first pass stutters, and whether the end of a
    fight does after a gather was blocked (only then does
    `PLAYER_REGEN_ENABLED` re-read anything).

If the client crashes at login or within the first pass, untick WowLab or
`/wowlab skip spellbook` (or `actionbars`, `bindings`) as in
"Switching a section off", and report the file and line.

## M11-29 capture step

One applied barber-shop change, for checklist items 11, 12 and 14: why, in
the M11-23 capture, the record's last write came from an open after an
applied change, and why it had no `chr_model_id`. The count-only events are
new client events for the addon, so they are **[verify]** until this capture
shows the addon ran with them; `/wowlab skip customization` switches them off
with the rest of the section.

Read on 2026-10-16 (capture on 1.60.1.70425; `docs/LAB_FORMATS.md`,
amendment of that date). The row that fits is "`recorded_at = "open"`,
`APPLIED` at 1 or more, `OPEN` at 2 or more, and you sat once", with the
changed option at the new position. The applied event fires and reaches the
section. The client then fired `BARBER_SHOP_OPEN` again after the Accept,
and that open's record, holding the new look, is the one saved. This is why
the record ended as `"open"` in this capture. Whether the shop actually
reopens, and why, is **[verify]**: the owner saw it close. Item 11 is
consistent with 1-based: the 70058 record's index 7 for an option with 7
choices could not come from a 0-based client. That the client's list has
the table's 7 choices is **[verify]**. The swatch order is
consistent with the table. Item 12 is settled as that reason. In item 14,
`GetViewingChrModel` returned nil; M11-29's other question, why, stays
**[verify]**. The four Retail count-only names are names
the client knows. `BARBER_SHOP_SUCCESS` and the control have no entry.

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
   pass. If the "recording in 15 s" line does not appear, WowLab is probably
   not running (the line can be missed): type `/wowlab`; if the addon loaded,
   it answers with two lines starting `WowLab:` (`/wowlab save ...` and
   `/wowlab skip <section> ...`); then go on. If nothing answers, check it
   is listed, ticked and not out of date at character select
   (`docs/handoffs/M11-03.md` §2); stop and report. If the client crashes at
   login, untick WowLab (§2 step 4): the six new event names are registered
   at `ADDON_LOADED`, before `/wowlab skip` can be typed in that session.
   Then stop and report (the `Error:` line and the WowLab file and line,
   M11-03 §2 step 4), and do not go on to step 3.
3. Sit in a barber chair once and change one colour (hair colour is
   enough). Before changing it, note the position of the current swatch and
   of the one you pick, counting from 1, left to right, then top to bottom
   (consistent with the table's order for hair colour on 70425), for example
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
  refresh after the Accept (a second `BARBER_SHOP_OPEN` was seen on 70425,
  2026-10-16; whether the shop reopens, and why, stays **[verify]**). An open reached the section after
  the applied event. Compare the changed option's `choice_index` with step
  3: at the new position, the choices are the new look; at the old position
  the last open saw the old look: both opens came before the Accept, or the
  second came before the choices updated; item 12 stays open.
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
names Forever lacks) is not known to be safe on Forever: the empty
`events_unregistered` lists (every one on 70058 is `{}`) show only that no
refusal was seen. It happens at `ADDON_LOADED`, before
`/wowlab skip` can be typed, so if it ever crashed the client, untick WowLab
at character select (`docs/handoffs/M11-03.md` §2 step 4), or with the
client closed `wowlab undo` the step 1 install.
