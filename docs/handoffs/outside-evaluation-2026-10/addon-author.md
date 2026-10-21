# Addon author

## Verdict

As an addon author I would use `luadata` and `sv dump` today. They are the most careful SavedVariables reader I have seen outside the game: lossless, never evaluating anything, and every refusal gives a line, a column and the token. I would **not yet recommend `sv merge` or the lab-addon to ordinary players**, and I would not recommend either to Retail players at all.

The single biggest problem is that `sv merge` loses data in its two most natural uses, while its help text promises it never deletes:

- `--take theirs` merges a list index by index, so the result is neither side's list.
- `--key X` replaces the subtree outright and drops keys that only the target had, without listing them.

Both are undoable, but a player who does not diff the result will not notice. The lab-addon itself is well built: no taint surface, combat-gated, `pcall` everywhere, privacy-first. It still ships as a research instrument:

- a chat line on every login and `/reload`;
- per-API probe telemetry in every player's file;
- a deliberately made-up event registered on every load;
- a heuristic sweep of the whole `Constants` table that feeds numbers into a C API.

It also goes silently out of date after a client patch, and nothing in wowlab notices. Recommend it to Forever-beta players who already use wowlab's Character views, and to nobody else yet.

## What works

- **Round trip.** `luadata` gives `serialize(parse(x)) == x` on all 16 real SavedVariables fixtures (my own run over `lab/core/tests/fixtures/**/SavedVariables/*`).
- **Hostile input is refused cleanly, with exact positions.** This covers `function`, calls, `setmetatable`, `..`, long brackets, long comments, NUL bytes, Lua 5.2 escapes, `.5`, an extra `}`, and nesting 250 and 100 000 deep (refused by a limit, not by a `RecursionError`). Example: `line 3, column 10: function definitions are rejected (L3) (at b'function')`.
- **Duplicate keys** are kept and flagged (`"duplicate": true` in `--json`). `1e309`, `-0`, a 64-bit hex value, denormals, invalid UTF-8 (`"\xff\xfe"`), `|H` links and `\ddd` escapes all parse.
- **The serializer matches the target file's style.** A subtree added to a Retail-layout file came out tab-indented, with `-- [1]`/`-- [2]` comments generated. A merge into the Forever CRLF, no-indent file kept CRLF, and every untouched byte stayed the same (`cat -A` of the merged `BarAddon.lua`).
- **The merge path itself is solid.** Snapshot first, journal, `undo`, `--expect`; an account-wide file cannot be merged "from a character". `snap diff` shows changes key by key (`+ BarCharDB["tracked"][3] = "Iron Ore"`), and says why when a side does not parse instead of failing.
- **Lab-addon hygiene.**
  - The only globals are `WowLabDB`, `WowLabCharDB`, `SLASH_WOWLAB1` and `SlashCmdList.WOWLAB`.
  - No hooks, no secure frames, one anonymous event frame.
  - Each API is tested for presence (never `WOW_PROJECT_ID`), and every client call goes through `pcall`.
  - Spellbook, action bars and bindings are read only out of combat, and an unknown combat state counts as "in combat".
  - Where `issecretvalue` exists, every value is tested before it is stored.
  - Crafter GUIDs are removed from item links; no names are kept.
  - The journal filters are never changed.
  - `/wowlab skip` is an honest escape hatch for client crashes that `pcall` cannot catch.
- **The `## Interface:` derivation is correct.** The patch-number rule gives 16001, which matches the client's own `GetBuildInfo` interface in the captures. `addon install` is idempotent ("Nothing to install: … already holds this lab-addon").

## Findings

### F1 — `sv merge --take theirs` merges lists index by index and drops elements

- Severity: major
- Area: `sv merge` / `svmerge.py`
- Evidence: in sandbox `addon-author` I planted `BarAddon.lua` in two character folders. Ours had `tracked = {"Copper Ore","Tin Ore"}`; theirs had `{"Silver Ore","Copper Ore","Tin Ore"}`.
  ```
  W sv merge BarAddon.lua --into 1/Labcharb-Labrealmf --from 1/Labchard-Labrealmg --take theirs --yes
    4 conflict(s), took theirs: … ["tracked"][1] … ["tracked"][2] …
    2 key(s) absent from one side (never deleted): BarCharDB["tracked"][3] (missing from ours) …
  → file now: ["tracked"] = { "Silver Ore", "Copper Ore", }
  ```
  - "Tin Ore" is gone. The result is neither ours nor theirs.
  - `profile.outline`, which only theirs had, was not added either.
  - A three-way merge where both sides appended to the list gives a conflict at `[3]`, with no way to keep both values.
- Impact: lists are the most common addon data (tracked items, ignore lists, aura lists, bar layouts). A player who asks for "theirs" gets a corrupted list, and the report says nothing was deleted.
- Proposal: treat a table's array part (keys 1..n) as one value. Two-way, a differing array is one conflict, and `--take theirs` replaces the whole array. With `--take theirs`, add the keys only theirs has. Add per-path resolution (`--take theirs=PATH`, repeatable). LAB_PLAN §13.4 rules.
- Status: new (the current behaviour follows the §13.4 rulings, so this is a plan change)

### F2 — two-way `--key X` replaces the subtree outright and deletes keys only the target had, contrary to `--help`

- Severity: major
- Area: `sv merge --key`
- Evidence:
  ```
  W sv merge BarAddon.lua --into 1/Labchard-Labrealmg --from 1/Labcharb-Labrealmf --key 'BarCharDB.profile' --yes
    1 value(s) taken from the source: BarCharDB["profile"] = table (2 entries)
  W sv dump …/1/Labchard-Labrealmg/SavedVariables/BarAddon.lua --path BarCharDB.profile
    font = "Morpheus", size = 12      ← outline = true (target only) is gone; undo restores it
  ```
  - `--help` (cli.py:4165) says: "Two-way … every key whose values differ is a conflict … A key on one side only is listed as absent and never deleted."
  - With `--key` there are no conflicts and no absent list, and the plan shown before the prompt does not say that anything will be removed.
  - The svmerge docstring documents "copied whole", but the help a user reads does not.
- Impact: this is the obvious way to say "only merge this addon's profile", and it silently deletes settings.
- Proposal: by default, `--key X` should run the normal merge inside X. Name the wholesale copy explicitly (`--replace SRC=DST`), and make its plan list every key it removes from the destination.
- Status: new

### F3 — luadata refuses `nil` inside tables and every non-finite number, so one value makes a whole addon's file unreadable

- Severity: major (the Retail occurrence is [verify]; no Retail capture exists)
- Area: `luadata.py`, LAB_FORMATS §4.1–4.2
- Evidence: crafted files under `$EVAL/work/addon-author/sv/`:
  - `02_nil_hole.lua`: `line 5, column 3: nil is legal only as a top-level value (§4.1) (at b'nil')`
  - `03_inf.lua`: `bare identifiers are not values (L3) (at b'inf')`
  - `04_nan_win.lua`: `(at b'-nan(ind)')`
  - `05_win_inf.lua`: `(at b'1.#INF')`
  - When such a file is in the install, `sv dump`/`sv merge` refuse it and `snap diff` reports "not compared as data".
  - In my experience as an addon maintainer, the Retail client writes sparse arrays positionally as `nil, -- [n]`, and math.huge has appeared in SavedVariables in exactly these platform spellings (LAB_FORMATS §4.2 lists them as "reported").
- Impact: big Retail addons (WeakAuras, Details, Plater, ElvUI) hold sparse arrays and occasionally math.huge. One such value makes the whole file opaque to every wowlab command.
- Proposal: accept a positional `nil` and the listed non-finite spellings as opaque tokens that keep their raw text. This stays lossless (L4) and evaluates nothing (L3). Mark them read-only for merge. Get one Retail capture to confirm.
- Status: new (LAB_FORMATS §4.2 already anticipates the non-finite spellings "with the fixture that shows it")

### F4 — the SavedVariables corpus is 16 small Forever/macOS files; the round-trip claim is untested on Retail, Windows and large files

- Severity: major (for any claim beyond Forever)
- Area: `lab/core/tests/fixtures`, L8
- Evidence: my round-trip run covered 16 files, the largest 44 KB (`Sangraal-Sett/WowLab.lua`).
  - None contains a `-- [n]` comment: the Forever client writes CRLF with no indentation and no index comments (`cat -A Syndicator.lua`).
  - There is no Retail file, no Windows file and no file over 50 KB, and nothing from WeakAuras, ElvUI, TSM or Auctionator.
  - The Retail layout of §4.2 is graded only on constructed input.
- Impact: the README's promise to "read any addon's saved settings" rests on a corpus that does not contain the files players care about most.
- Proposal: add owner or volunteer Retail and Windows captures of 3–5 large, popular addons through `lab_capture.py`. Until then, label Retail and Windows SavedVariables support "untested" in the README, the way platforms already are.
- Status: new (permitted by L8, not in conflict with it)

### F5 — after a client patch the lab-addon goes out of date silently, and nothing in wowlab notices

- Severity: major
- Area: `doctor`, `addons list`, `char list/show`, `addoninstall.py`
- Evidence:
  - I edited the sandbox `.build.info` from `1.60.1.69913` to `1.60.2.71000`, where the derived interface would be 16002.
  - `W doctor --offline` gave no addon line. `W addons list` printed `WowLab "WowLab" TOC declares Interface 16001` with no mismatch flag. `W char show` gave no stale-record warning.
  - The only mention is a PATCH_NOTE printed once at install time (addoninstall.py:170).
- Impact: modern clients do not load an out-of-date addon unless the player ticks "Load out of date AddOns". After any x.y.Z patch, the Character tab and `char show` keep showing older data, and nothing says recording has stopped.
- Proposal:
  - `doctor` compares the installed `WowLab.toc` interface with the derived one and says "run `wowlab addon install lab`".
  - `addons list` flags every addon whose `## Interface:` list does not contain the client's number.
  - `char list` flags records saved by an older client version than the one installed.
- Status: in-ideas: addon-audit (partly: "missing `16001` in the TOC"); the lab-addon part is new

### F6 — a chat line on every login and `/reload`

- Severity: minor (it decides whether players keep the addon)
- Area: lab-addon `Core.lua:309-313`
- Evidence: `startFirstPass` prints `WowLab: recording in 15 s. To switch a section off first: /wowlab skip <section> …` once per UI load, so on every login and every `/reload` (README "The 15 s window").
- Impact: chat spam is the first thing players uninstall an addon for. Here it pays for a crash-recovery feature that most sessions never need.
- Proposal: print only when the previous session looks unclean (the probe can tell that: `loads` rose but there was no logout write), or once per addon version, plus `/wowlab quiet`. Keep the 15 s delay itself.
- Status: new

### F7 — `talents.legacy` sweeps every `Constants.*SYSTEM_ID*` into `C_Traits.GetConfigIDBySystemID` on each trait event

- Severity: major (Retail, untested); minor on Forever
- Area: lab-addon `Talents.lua:341-352`, events at `Talents.lua:368-375`
- Evidence:
  - `for group, fields in pairs(Constants) … if string.find(key, "SYSTEM_ID") then add(ns.Call(bySystem, value), …)`.
  - It runs on entering the world and again 2 s after any `TRAIT_CONFIG_UPDATED`, `TRAIT_TREE_CURRENCY_INFO_UPDATED` or `TRAIT_SYSTEM_INTERACTION_STARTED` burst, so on every click in the talent UI.
  - The addon already crashed the Forever client once with the same pattern: a `pairs` walk feeding ids into a C API (`GetCategoryAppearances`, M11-03). `pcall` cannot catch a C++ assertion.
- Impact: on Retail, `Constants` is large and changes each patch. Any `*SYSTEM_ID` value that a C++ assertion rejects crashes the client, from an addon that only records data.
- Proposal: on the client that has the panel (`ToggleLegacySystemUI` present), discover the Legacy system id once from a capture and call only that. Elsewhere, skip the sweep. In any case, do the discovery once per session, not on every trait event.
- Status: new

### F8 — research scaffolding ships in the player build

- Severity: minor
- Area: lab-addon `Customization.lua:83-89`, `Actions.lua` probes, `Core.lua` `events_unregistered`
- Evidence:
  - Every load registers `WOWLAB_CONTROL_NOT_A_REAL_EVENT` and five Retail-only barber events. The README says registering an unknown name "is not known to be safe on Forever".
  - Every schema-4 file carries `probes` (api/present/calls/errors/answered/secret) for three sections; see the `sett.dump` grep of `Sangraal-Sett`.
  - It also carries `events_received`, `events_unregistered` and verbose absent reasons on every write.
- Impact: this adds risk and file weight with no benefit to the player. It is also what a reviewer of a third-party addon would flag first.
- Proposal: put the probes and control events behind `/wowlab debug` (saved in `WowLabDB`), off by default. The Lab reader already treats them as optional.
- Status: new

### F9 — the addon rebuilds whole sections on high-frequency events; the data is only needed at logout

- Severity: minor
- Area: lab-addon `Core.lua:284-299` (2 s debounce), event lists in `Actions.lua:222/320/408`, `Collections.lua:122`, `Currencies.lua:54`
- Evidence:
  - `SPELLS_CHANGED`, `ACTIONBAR_SLOT_CHANGED` (it fires for each slot on every stance or form change and when entering or leaving a vehicle), `CURRENCY_DISPLAY_UPDATE` (looting, in combat), `PET_JOURNAL_LIST_UPDATE` and the trait events all re-walk their section 2 s after a burst.
  - Each walk builds new tables, and in `Actions.lua` adds about 3–7 extra `pcall(issecretvalue)` calls per value: 180 action slots, up to 4096 spellbook items, about 290 bindings (probe counts in the 70450 capture: `issecretvalue` was called 419 times for one bindings gather).
  - `currencies` is not combat-gated, so it re-walks during fights.
- Impact: steady garbage and CPU while playing, worst for druids and other form-shifters and for anyone in combat. Each walk is small, but it repeats all session.
- Proposal: change events only mark sections dirty. Gather dirty sections on `PLAYER_REGEN_ENABLED` and at most once every 30–60 s when idle, plus at `PLAYER_LOGOUT`. Keep `/wowlab save` immediate.
- Status: new

### F10 — WowLabCharDB versioning is lockstep: any additive change is a new schema, and an unknown schema refuses the whole file

- Severity: minor
- Area: `labaddon.py`, LAB_PLAN §13.1 ("after M11-04 … any change is a new schema")
- Evidence:
  - A copy of the schema-4 capture with `schema = 5` gives `wowlab: WowLabCharDB is schema 5, and this reader knows schema 1, 2, 3, 4 only: it was written by another version of the lab-addon; nothing was read`. That character drops out of `char show` and `char list` entirely.
  - An added `mythic_plus = { note = "…" }` gives "the unknown key mythic_plus.note holds text".
  - An added numeric-only key is accepted.
  - There have been 4 schema bumps in about 4 weeks.
- Impact: an addon from a newer wowlab, or a CLI that was downgraded, blanks every character view. The message does not say which side to update.
- Proposal:
  - Use a major.minor schema: additive sections bump only the minor. The reader reads the sections it knows from a newer minor, and lists the rest as "written by a newer lab-addon".
  - Keep the privacy guard by dropping text in unknown keys rather than refusing the whole file.
  - The message should say "update wowlab" or "reinstall the addon" according to the direction of the mismatch.
- Status: new (the text rule is a deliberate privacy guard; the proposal keeps its intent)

### F11 — the TOC has no `## Version`, `## Author`, `## Category` or `## IconTexture`

- Severity: minor
- Area: `lab/addon/WowLab/WowLab.toc`
- Evidence: the installed TOC has Interface, Title, Notes and the two SavedVariables lines, plus a leftover "# Template (ADR-0026): … replaces the Interface placeholder" comment in the player's copy.
- Impact: in the AddOns list the addon has no version, no category grouping and no icon. Neither the player nor wowlab can tell which addon build wrote a record, beyond the coarse `schema` number, or whether the installed copy is current.
- Proposal: `addon install` fills `## Version:` with the wowlab version. The addon writes that version into `WowLabCharDB.addon_version`, and `char show` and `doctor` compare it with the installed CLI. Add `## Category:` and `## IconTexture:`, and drop the template comment from the installed copy.
- Status: new

### F12 — `sv merge` on a large file: 88 s and 2.6 GB RSS to change one value

- Severity: minor
- Area: `sv merge`, `luadata.serialize`, LAB_PLAN §6.4 target
- Evidence: a 43 MB file with 2 M positional entries, placed in both characters, with one value changed:
  - `sv merge Big.lua --into 1/Labcharb-Labrealmf --from 1/Labchard-Labrealmg --take theirs --yes` took 88.3 s, with a child max RSS of 2594 MB.
  - On the same file alone, `luadata.read` took 9.2 s at 1.4 GB, and `serialize` took 24.2 s.
  - `sv dump --path 'Wide[5]'` took 14 s.
- Impact: auction and collection addons reach this size. On an 8 GB laptop the merge competes with the client for memory. The §6.4 target is measured on the parse only, not on the commands that use it.
- Proposal: extend the §6.4 target to `sv merge` and `sv dump --path`. Because every node keeps its raw bytes, a merge can splice only the changed byte ranges instead of re-serializing the whole document, and the source document can be released after the merge.
- Status: new

### F13 — the interactive merge plan hides the loader-check notes until after the write

- Severity: minor
- Area: `sv merge` text output
- Evidence:
  - With a character that has no `WowLab.lua`, the prompt run shows the plan and then `Write the merge? [y/N]`, with no loader line.
  - The `--json` notes and the `--yes` run both print `No WowLab.lua for 1/Labchare-Labrealmh: the SavedVariables loader was not checked.`
  - The same holds for "no login between the two snapshots; the loader was not re-checked".
- Impact: the player confirms without knowing that the one safety check specific to this command did not run.
- Proposal: print `notes` before the prompt.
- Status: new

### F14 — parser errors use internal jargon and miss the common real causes

- Severity: minor
- Area: `luadata` messages via `sv dump`
- Evidence:
  - `(L3)`, `(§4.1)`, `(§6.4)` appear in messages users see.
  - A UTF-8 BOM shows as `expected name = value, found this (at b'\xef')`.
  - A truncated real file (Syndicator cut at 4828 bytes) shows as `expected = after the key (at b'')`.
- Impact: these are the two most likely real-world causes of an unreadable SavedVariables file (a hand edit in Notepad, a crash during the write), and neither is named.
- Proposal: plain words, with "the file ends early: it may have been cut off by a crash during a save" when the token is end of input, and "this file starts with a UTF-8 byte-order mark, which the client does not write". Keep the rule id in `--json` only.
- Status: new

### F15 — no SavedVariables health view: parse status, orphans, size

- Severity: idea
- Area: `sv list`
- Evidence:
  - `sv list` lists `HoleAddon.lua`, which does not parse, like any other file.
  - The fixture install has SavedVariables files for RareScanner, Syndicator, DBM-StatusBarTimers and DBM-Party-Vanilla, but `Interface/AddOns` holds only DBM-Brawlers and DBM-Challenges. Nothing says those files are orphans.
  - wowlab already parses every TOC's `## SavedVariables[PerCharacter]`.
- Impact: these are the questions a player or addon author actually has: "which SavedVariables is broken, which addon is gone, which addon bloats my load time".
- Proposal: `sv list --check` adds a parse status, whether a TOC declaring the variable is installed, and a sort by size. `doctor` summarises it.
- Status: in-ideas: sv-health (only the loader probe); the rest is new

### F16 — merging an account-wide file requires an unrelated `--into` character

- Severity: minor
- Area: `sv merge` UX
- Evidence: copying within an account file (`--key 'DBT_AllPersistentOptions["Default"]=DBT_AllPersistentOptions["Raid"]'`) still requires `--into <character>`. That character's `WowLab.lua` is read for the loader check, which has nothing to do with DBM's file.
- Impact: this is confusing, and the loader verdict for an account-wide file is borrowed from whichever character the user happens to name.
- Proposal: make `--into` optional for an account-wide file. Run the loader check against the most recently written `WowLab.lua` of that account, and say which one was used.
- Status: new

## Top 3 changes

1. **Fix `sv merge` semantics before players use it** (F1, F2). Treat array parts as one value, make `--take theirs` mean theirs, merge inside the subtree for `--key X` and give wholesale replacement its own name, and list every key a write would remove.
2. **Keep the lab-addon recording, and quiet** (F5, F6, F8). Detect a stale `## Interface:` in `doctor`, `addons list` and `char list`; add `## Version` and stamp it into the record; remove the per-login chat line and ship the probes behind `/wowlab debug`.
3. **Earn the "reads any addon" claim** (F3, F4, F7). Add Retail and Windows captures of large real addons, accept `nil` holes and non-finite spellings as opaque raw tokens, and drop the `Constants` sweep (or run it once) before anyone runs the addon on Retail.

What I would want from wowlab as an addon author:

- `luadata` as a stable, documented library API with a pinned version, so my CI can validate SavedVariables migrations against real user files.
- `sv list --check` (F15).
- An out-of-date and dependency report across `Interface/AddOns`: `addon-audit` in the ideas file.
- `addon-kit` (in the ideas file): a guarded link of a dev folder into `AddOns`.
- `sv dump --json` kept stable as an export format for test fixtures.

## Not tested

- **Anything in the game client** (there is no client here): whether the addon loads, its in-game cost per event, real secret-value behaviour, the 15 s window, `/wowlab skip`, and whether the client accepts the derived interface number after a patch. My performance and crash-risk points come from reading the code, not from measurement in the game.
- **Retail and Windows SavedVariables:** no captures exist. The claim that Retail writes `nil, -- [n]` is from my experience and is marked [verify].
- **The refusal while the client is running** (the process check always reports "not running" on Linux), and the `wowlab ui` Confirm flow for `sv merge` and `addon install`.
- **A three-way merge across machines,** and merges of real AceDB `profileKeys` and `profiles` files: none are in the corpus. I used constructed per-character files, labelled as such, under the sandbox.
