# Veteran raider and alt-holic

Reproduce: every command below ran after
`source $EVAL/work/raider/env.sh` (sandbox `raider`, base fixtures) or `env2.sh`
(sandbox `raider2`, overlay `macos-70450`, which has schema-4 lab-addon records
for `Sangraal-Sett` and `Dangling-Participle`), then `unset UV_NATIVE_TLS;
W() { uv run -q --offline --project /home/user/wowlab wowlab "$@"; }`.
For `looks`, I seeded the game-data cache with the recorded wago CSVs for
1.60.1.70009 (copied into `xdg/data/wowlab/gamedata/tables/1.60.1.70009/`) and
pointed `HTTPS_PROXY` at `127.0.0.1:9`, so no request could reach wago.tools.
To simulate the client, I edited files in my sandbox install with `python3`.

## Verdict

I would install it today for one job: **`snap create` before patch day or an
addon update, then `snap diff` and `snap restore --paths` when something goes
wrong.** That job works, it is safe, and it shows SavedVariables changes value
by value, which my manual WTF zip cannot do. For everything else in my week,
it is not ready. It cannot move my setup to a new PC or a reinstalled drive,
because a snapshot only restores into the exact install path and flavor folder
it was taken from. It has no way to copy a UI setup to a new alt. Its
keybind, macro and coach views only follow Blizzard's default action bars,
and every raider I know uses Bartender, ElvUI or Dominos. And it wraps each
fact in so many caveats that reading the output often takes longer than
opening the file. The biggest single problem is that **snapshots cannot move
between installs**: "moving to a new PC" is the main reason players back up
WTF by hand, and the planned Forever launch will rename `_classic_beta_`,
after which every snapshot taken of the beta folder will be refused.

## What works

- **Safety model.** Every write showed a plan, asked first, and took a
  pre-write snapshot. `undo` reversed `profile apply`, `sv merge` and
  `snap restore` exactly, and an undo can itself be undone. Nothing I ran
  wrote outside the sandbox.
- **SavedVariables diff.** `snap diff` showed
  `~ DBT_AllPersistentOptions["Default"]["DBM"]["TimerY"]: -260 -> -100`.
  That is the exact answer to "what did this DBM update change?".
- **Single-file recovery.** `snap restore <id> --paths 'WTF/Account/…/SavedVariables/Syndicator.lua' --yes`
  brought back a wiped 14 kB Syndicator file in one command, with undo behind it.
- **`char binds` with a schema-4 record** (Sangraal-Sett) is the best
  keybind view I have seen outside the game. Each key is shown with the
  action slot it presses and the spell in that slot
  (`1  ACTIONBUTTON1  1  casts Immolate (Rank 2, spell 707)`). It lists
  filled slots that no key reaches, and the slot math is right (bottom left
  bar = 61–72, bottom right bar = 49–60, BONUSACTIONBUTTON = pet bar).
- **The coach's rank matching.** "cast at Rank 2 (695), the bar holds Rank 3
  (705)" is a real Classic insight.
- **`macros check`** caught the length, duplicate-name and unbalanced-bracket
  problems in macros I wrote to test it.
- **`doctor --offline`** gives an honest, readable first-run statement.

## Findings

### F1 — Snapshots cannot be restored into another install path or flavor folder (new PC, new drive, Forever launch)
- Severity: blocker (for the "move to a new PC" job; the README promises "puts everything back")
- Area: `snap restore`, snapshot store, guard
- Evidence: `snap restore 20261021T170633 --root ".../sandboxes/raider2/World of Warcraft" --dry-run`
  → `wowlab: refused by the write gate: snapshot 20261021T170633.082808Z-832e0860 was taken of '_classic_beta_' in .../raider/World of Warcraft, not of '_classic_beta_' in .../raider2/World of Warcraft`.
  The refusal names both the install path and the flavor folder. AGENTS.md
  itself warns that "The Forever beta's folder will change at launch". There
  is no `snap export` or `snap import` either (`snap --help`: create, list,
  show, diff, verify, gc, restore).
- Impact: These cases are exactly when players need a backup: a new PC, a
  reinstall onto another drive (`C:\Program Files (x86)` to `D:\Games`), or a
  flavor folder rename at launch. In all of them, wowlab's snapshots cannot
  be used, and the only workaround is `sv merge --from SNAPSHOT`, one file at
  a time (that call does work across installs). I would keep zipping WTF by hand.
- Proposal: Add `snap restore --into-install/--into-flavor` (a "transplant").
  It maps the snapshot's account and character folders onto the target's,
  shows the mapping in the plan, and still goes through guard with a
  pre-write snapshot of the target. Also add `snap export <id> FILE.zip` and
  `snap import`, so a snapshot can travel on a USB stick. Plan level: a
  "portability" section next to `docs/LAB_PLAN.md` §6.9 (store) and the
  snapshot spec. Re-test it at the Forever launch rename.
- Status: new

### F2 — `explain` suggests restoring from the latest snapshot, which after a recovery is the broken state you just left
- Severity: major
- Area: `explain` hints (also the explorer's per-file panel, which shows the same "matches your latest snapshot")
- Evidence: I wiped `Syndicator.lua` (simulating a bad addon update), took a
  `snap create`, then recovered the file with `snap restore 20261021T170633 --paths …Syndicator.lua --yes`.
  Then I ran `explain "WTF/Account/90000001#6/SavedVariables/Syndicator.lua"`:
  `In the latest snapshot (20261021T170733.944499Z-74072287, …, "guard pre-write: restore …"): the file on disk differs`
  `hint: wowlab snap restore 20261021T170733.944499Z-74072287 --paths '…/Syndicator.lua'  # put back the copy in snapshot …`
  That snapshot is guard's pre-write copy, which holds the wiped 50-byte file.
- Impact: A player who follows the tool's own hint undoes their recovery
  and loses the addon data again. Every guard write creates a new "latest"
  snapshot, so this happens after every restore, profile apply or merge.
- Proposal: Compare against, and suggest, the latest snapshot that the user
  created (`snap create`, `doctor --yes`, a profile), not a `guard pre-write:`
  one. When the newest snapshot is a pre-write copy, say so in words: "this
  is the state before your last wowlab change; restoring it re-applies what
  you undid". Add `latest`, `latest-user` and `~1` aliases to `snap diff` and
  `snap restore` (`snap diff latest …` is refused today).
- Status: new

### F3 — No way to copy a UI setup to a new alt (the alt-holic's number-one job)
- Severity: major
- Area: `profile`, `sv merge`, missing command
- Evidence: `profile save --help` says "A preset covers every account and
  every character folder that exists now … Character folders created since
  are not touched." When I created `1/Newalt-Realmz/` after saving, then ran
  `profile apply raid`, the plan had 2 changes and none of them touched the
  new alt. `sv merge` can copy keys
  (`--key 'DBT_AllPersistentOptions["Default"]=DBT_AllPersistentOptions["Raid"]'`),
  but only if I already know each addon's internal key layout, and it still
  requires `--into` a character that has a `WowLab.lua`, even for an
  account-wide file.
- Impact: Today I copy `config-cache.wtf`, `edit-mode-cache-character.txt`,
  `chat-cache.txt`, `layout-local.txt`, `AddOns.txt` and the
  `SavedVariablesPerCharacter` files by hand, then fix the AceDB
  `profileKeys` in game for each addon. wowlab saves none of this time.
- Proposal: Add `wowlab char clone-ui --from <char> --to <char>
  [--include cvars,editmode,chat,addons-enabled,per-char-sv,bindings,macros]`.
  It is one guard transaction with a plan listing every file. For AceDB
  addons, an optional `--acedb-profiles` maps `profileKeys["New - Realm"]`
  to the source's profile. This builds on `sv merge` and the profile
  presets. Plan level: a Wave item next to profiles and sv-merge in
  `docs/LAB_PLAN.md` §14.
- Status: new (in-ideas: **profiles** and **sv-merge** are built, but neither covers per-character cloning)

### F4 — Keybind, macro and coach views ignore addon action bars (Bartender, ElvUI, Dominos)
- Severity: major
- Area: `char binds`, `char macros`, `binds coach`, the lab-addon `bindings` and `actionbars` record
- Evidence: `char binds --character Sangraal-Sett` prints
  "Left out of the bindings record, counted with no list: 1 command not named
  in plain upper case (a key bound straight to a spell, macro, item or click
  may be one)". `binds coach` says "an addon's buttons are not followed".
  `lab/addon/WowLab/Actions.lua:390` keeps only commands matching `^[%u%d_]+$`.
  Bartender's `CLICK BT4Button13:Keybind` and ElvUI's
  `CLICK ElvUI_Bar1Button1:LeftButton` both fail that pattern, so they are
  dropped.
- Impact: For most raiders every combat key is a CLICK binding to an addon
  button. For them `char binds` lists only movement and chat keys, and the
  coach reports "spells cast with no bind" for their whole rotation. The
  advice it gives is wrong.
- Proposal: Keep `CLICK <frame>:<button>` bindings in the record as data.
  For the main three bar addons, map the frame name to an action slot (each
  exposes its slot as a button attribute that the lab-addon can read with
  `GetAttribute("action")` out of combat). When that is not possible, list
  the key as "addon button, not followed" instead of silently counting it.
  Until then, say in `binds coach` before any judgement that a bar addon is
  installed and its keys are invisible (it can see BT4, ElvUI or Dominos in
  `addons list`).
- Status: new (in-ideas: **keybind-coach** is built, but without this)

### F5 — The tool will not say whether a restored binding or macro survives login, and that is the whole question
- Severity: major
- Area: `doctor`, `profile apply`, `snap restore`, `docs/LAB_FILE_MAP.md:133-140`
- Evidence: `doctor --offline` reports
  `synchronizeBindings not set (client default; wowlab does not know whether that default is on)`
  for all three CVars, in both files. The `profile apply` plan says
  "the server may replace this at your next login". The file map calls the
  default **[verify]**.
- Impact: As far as I know from Retail, all three `synchronize*` CVars
  default to on, and the server's copy of bindings and macros wins at login.
  If that holds on Forever, `profile apply --preset bindings/macros` and a
  bindings restore are reverted the moment I log in. The tool reports
  "Applied", and I find out in the raid. The honest hedge is right, but it
  hedges on the one fact that decides whether the feature works.
- Proposal: Run the owner's live test that M13-07 already describes, but
  for bindings, the edit-mode cache and Config sync as well as macros. Then
  state the result as a fact in `doctor`, and refuse or strongly warn in the
  `profile apply` plan when sync is on for a file in the change. Also explain
  the in-game workaround (turn sync off, log in once, apply, turn sync back on).
- Status: already-ticketed: M13-07 (macros only; bindings, edit-mode and config are new)

### F6 — No alt overview: `char list` hides characters and shows a spec id where class and level should be
- Severity: major (for an alt-holic)
- Area: `char list`, `char show`
- Evidence: `char list` (raider) shows 2 rows,
  `1/Labcharb-Labrealmf … schema 1, saved by client 1.60.1.70009, spec id 1482`.
  `1/Labcharb-Labrealmd`, which has config, macros and chat files, is left
  out because it has no `WowLab.lua`. Even with the schema-4 identity record,
  `char show --character Sangraal-Sett` puts class on line 24
  (`Identity: class WARLOCK …`), and the level appears only inside
  "Legacy candidates: … (level 15)". Professions are `skill line 182`,
  `393`, `129`, `185` with no names, and the spec is `id 1490`.
- Impact: The first thing I want from ten or more alts is one row each:
  name, realm, class, level, item level, professions, last played. Today I
  would need to read 47 lines per character and look up ids myself.
- Proposal: Have `char list` list every character folder. Columns: name,
  realm folder, class, level, average item level, and last seen (the newest
  mtime of any file in the folder), with a "no lab record" marker for
  characters without one. Name spec and skill line ids offline when the
  build's `ChrSpecialization` and `SkillLine` tables are cached (the
  fixtures already hold subsets). This is the alt-dashboard, cut to one
  table.
- Status: in-ideas: **alt-dashboard** (deferred in the Wave-3 re-scope)

### F7 — The lab-addon throws away the macro name, so which macro sits on which button stays [verify] forever
- Severity: minor
- Area: lab-addon `Actions.lua:358-376`, `char macros`, `macros check`
- Evidence: `macros check --character Sangraal-Sett` reports "not-on-action-bar:
  the record has 2 macro slot(s) … how a slot's macro index maps to the ids
  in macros-cache.txt is [verify]; so which macros are on one cannot be
  told". In the addon, `local okMacro, _, icon = pcall(getMacro, id)` binds
  the name to `_` and drops the body. `char binds` also shows slot 65 as
  "runs macro 140". By the tool's own offset of 120 that would be character
  macro 20, but Retail allows only 18, so the numbering is already
  suspicious, and the name would settle it.
- Impact: "Which of my macros is on a button" and "which macro is broken"
  are what I want the checker to answer. Today it says it cannot.
- Proposal: Record `GetMacroInfo(index)`'s name (and optionally a hash of
  the body) per macro slot. The data is local, and the existing scrub tool
  can pseudonymise it in fixtures. Then match slots to `macros-cache.txt` by
  name and body. While doing that, flag a macro index above 120+18 as
  suspicious.
- Status: new

### F8 — `addons list` misses what a 60-addon player needs: missing dependencies, out-of-date TOCs, orphaned SavedVariables
- Severity: major
- Area: `addons list`, `sv list`
- Evidence: `addons list` prints only
  `DBM-Challenges  "<DBM Mod> Challenges"  TOC declares Interface 50504, 120100`.
  The TOC has `## RequiredDeps: DBM-Core` and `## LoadOnDemand: 1`,
  DBM-Core is not installed, and the client is interface 16001. None of
  that is said. `sv list` shows `DBM-Party-Vanilla`, `DBM-StatusBarTimers`,
  `RareScanner` and `Syndicator` SavedVariables with no matching addon
  folder, and they are not flagged. The TOC `## Version: r264` is not shown
  either.
- Impact: These are the questions after every patch: "why doesn't X load"
  (a missing dependency, or out of date for this interface) and "what junk
  can I delete". The data is already parsed, but the tool does not draw the
  conclusion.
- Proposal: Add columns to `addons list`: version; "loads on this client?"
  (interface match, `AllowLoadGameType`); missing `RequiredDeps`; which
  characters enable it (`AddOns.txt`). Add `sv list --orphans` (SV files
  whose addon folder is gone, with their size). Both are read-only.
- Status: in-ideas: **addon-audit** (interface check); missing dependencies and orphans are new

### F9 — Diffs and plans are byte-level for bindings, macros and CVars, so a profile apply silently drops a macro made since
- Severity: major
- Area: `snap diff`, `profile apply` and `snap restore` plans
- Evidence: After I changed `bindings-cache.wtf` (`Q -> STRAFELEFT`) and
  added macro "New", `snap diff` printed only
  `changed  …/macros-cache.txt` and `changed  …/bindings-cache.wtf`.
  The `profile apply raid` plan said `replace …/macros-cache.txt (91 bytes)`,
  and applying it deleted macro "New" without naming it. For a SavedVariables
  wipe, by contrast, the diff is 554 lines with no summary line.
- Impact: The plan step is the safety promise. A player cannot see from
  "replace (91 bytes)" that a macro or keybind they made last week will be
  lost.
- Proposal: The parsers already exist (`wtfconfig`, `macros`, `binds`).
  Add semantic diffs for `bindings-cache.wtf` (key: old → new),
  `macros-cache.txt` (macro added, removed or changed, by name) and
  `config-cache.wtf`/`Config.wtf` (CVar old → new), and show the same lines
  in every write plan. Add a per-file summary to SV diffs
  ("412 values removed; the file went from 14,484 to 50 bytes: looks like
  a reset").
- Status: new

### F10 — `binds coach` pairs "newest log" with "newest record" blindly and judges from tiny samples
- Severity: minor
- Area: `binds coach`
- Evidence: Its own note says "a log written by another character than the
  record's makes these reports wrong: check the log's time and choose it
  with --log". Yet each log line names the caster:
  `SPELL_DAMAGE,Player-9999-00000001,"Labchard-LabrealmbPartbPartcPartd-"`.
  That matches the folder `Labrealmb Partb Partc Partd/Labchard`. On
  Sangraal-Sett it judged 6 casts over 23 seconds and listed 8
  "binds to spells not cast", including Fear and Will of the Forsaken.
- Impact: With ten alts, the newest log is often from another character, so
  the coach's report is wrong. Unbinding situational or defensive spells
  because they were not cast in 23 seconds is bad advice.
- Proposal: Pick the log, or the log segment, by the caster name that
  matches the chosen character, and warn on a mismatch. Aggregate over
  several logs (`--since 14d`). Require a minimum sample (for example 200
  casts or 3 encounters) before showing "bound-never-cast". Let players
  mark spells as situational in the costs TOML.
- Status: new

### F11 — `looks` hangs for 31 s without network, has no `--offline`, and shows ids where the barber shop shows ordinals
- Severity: minor
- Area: `looks races/options/save/show/compare/page`
- Evidence: With no cache for 69913 and the proxy blocked, `time W looks races` →
  `https://wago.tools/db2/ChrRaces/csv?build=1.60.1.69913: gave up after 5 attempts` after `real 0m31.1s`.
  This happened even though 1.60.1.70009 was cached. `looks options Undead --sex female --class Warlock`
  lists choices as `984`, `985`, …, but the record holds the barber-shop
  index (`char show`: `option 58: choice 918 (index 6)`), and `looks show`
  never prints it. `looks import-char` says "The record holds no class"
  while `identity.class_id = 9` is in the same file.
- Impact: Without a working internet connection, the command is slow and
  then fails. Choice ids mean nothing in game ("Skin Color 7" does).
- Proposal: Add `--offline` (use the nearest cached build, say so, and fail
  fast). Print the 1-based ordinal next to every choice id. The class fix is
  already ticketed.
- Status: already-ticketed: M13-80 (import-char class); offline and ordinals are new

### F12 — The caveats bury the answers; reading the output often takes longer than opening the file
- Severity: major (it decides whether players keep using it)
- Area: every `char …`, `macros …`, `binds …` text output; the looks page
- Evidence: `char macros --character Labcharb-Labrealmd` prints 50 lines for
  4 macros. About 9 of those lines are standing caveats ("the offset of 120
  is Retail's, from memory [verify]", "whether it counts characters or bytes
  is [verify]"), and every character command repeats them. `char gear`
  prints 6 caveat lines after a 19-line table. The first screen of the looks
  page is a block of caveat paragraphs (`work/raider/looks.png`).
- Impact: I want "Q: Will of the Forsaken" and to be done. The [verify]
  notes are honest, but they are written for the spec reviewers, not for the
  player.
- Proposal: Make terse the default: data plus at most one line of caveats
  that apply to this result. Move the standing caveats behind `--explain` or
  `--notes` and to the wiki. Keep everything in `--json`. One rule for all
  text views, in `docs/LAB_PLAN.md` §15 (CLI conventions).
- Status: new

### F13 — `log tail` is raw CSV with a timestamp; no encounter view
- Severity: idea
- Area: `log tail`
- Evidence: `log tail` prints lines like
  `4/1/2026 02:17:01.969-4  SWING_DAMAGE_LANDED,Player-9999-00000001,"Labchard-…",0x511,0x80000000,…,nil,1,nil`.
  That is the file with a space inserted, even though the help says "tokenized".
- Impact: No time saved over `tail -f`. A raider wants to know which pulls
  are in this log, how long each lasted, and whether it was a kill or a wipe.
- Proposal: Add a readable one-line form ("Labchard Shadow Bolt → Vampiric
  Duskbat 9") and `log encounters` (ENCOUNTER_START/END: boss, difficulty,
  duration, kill or wipe, size). Also add `log list` with sizes, since raid
  logs reach GBs and players delete them by hand.
- Status: new

### F14 — Every guarded write snapshots all of `Interface/`, which will be slow with 60+ addons
- Severity: minor
- Area: guard pre-write snapshot, snapshot store growth
- Evidence: This is a constructed load test: 60 fake addons, 15,091 files,
  60 MB, in `raider2`. The first `snap create` took 23.9 s. A one-key
  `sv merge --yes` took 5.2 s and `undo --yes` took 5.0 s, and each wrote a
  15,091-file pre-write snapshot (`snap list`). A real 60-addon `AddOns`
  folder is often 1–2 GB.
- Impact: Every small change costs a full hash walk of the addon tree, and
  `snap list` fills with "guard pre-write" entries that have no retention
  policy.
- Proposal: Scope the pre-write snapshot to the subtrees the transaction
  touches (that still meets L2's "snapshot first"; check ADR-0021's wording),
  or cache hashes by mtime and size. Add a retention policy for
  `guard pre-write` snapshots, and hide them from `snap list` by default.
- Status: new (check against ADR-0021 before treating as easy)

### F15 — `wowlab ui --help` says write commands are not offered; the Commands tab offers them
- Severity: minor
- Area: `ui --help` (`lab/core/src/wowlab_core/cli.py:9901`)
- Evidence: The help says "Commands that change your game files … are not
  offered in this version". The live Commands tab lists `snap restore`,
  `profile apply`, `undo`, `sv merge`, `addon install` and `addon remove`
  (headless capture), and the README describes the confirm step for them.
- Impact: It tells a cautious user the wrong thing about what the browser
  can change.
- Proposal: Update the help text.
- Status: already-ticketed: M13-76

### F16 — Piping to `head` prints an error and exits 1
- Severity: minor
- Area: CLI output
- Evidence: `W cvar list --scope account | head -30` →
  last line `wowlab: [Errno 32] Broken pipe`, and `PIPESTATUS[0]` = 1. The
  same happens with `char show | head`.
- Impact: Noise in every pipe that players and scripts use.
- Proposal: Handle SIGPIPE/BrokenPipeError quietly with exit 0, as standard
  CLIs do.
- Status: new

### F17 — `cvar get` reads one scope at a time and never says what a CVar does
- Severity: minor
- Area: `cvar get`, `cvar list`
- Evidence: `cvar get nameplateMaxDistance` →
  "not set in WTF/Config.wtf (the client default applies, unless another
  scope sets it)". Finding where it is set means running it three times
  with `--scope global|account|character`. `cvar list` is 132 raw lines with
  no descriptions.
- Impact: "Where is my nameplate range set, and which file wins?" takes
  three commands and some guesswork.
- Proposal: Add `cvar get NAME --all-scopes` (or `cvar where`), printing
  every file that sets it in precedence order, with the effective value
  stated as an assumption. Add `cvar diff --character A --character B` for
  "why does my alt look different". Optional descriptions could come from
  the exported `Blizzard_APIDocumentation` and CVar tables.
- Status: in-ideas: **wow-as-code** (drift and state); the command shape is new

## Top 3 changes

1. **Make snapshots portable (F1), and fix the restore hint that re-breaks
   recoveries (F2).** Backup and restore is the job that works today. Make
   it survive a new PC, a reinstall and the Forever launch rename, and stop
   the tool from pointing at its own pre-write copies.
2. **Make the keybind and macro views honest for real UIs: follow addon
   action bars and record macro names (F4, F7), and settle the server-sync
   question with one live test (F5).** Until then, those features give
   confident wrong answers to raiders.
3. **Ship the alt-holic jobs: `char clone-ui` (F3), a one-row-per-character
   `char list` (F6), and `addons list` dependency, out-of-date and orphan
   checks (F8)**, with terse output by default (F12).

## Not tested

- Real client behaviour at login (whether the server replaces restored
  bindings or macros, what the client does with a merged SV file). There is
  no client here.
- Windows paths, OneDrive placeholders, and a running-client refusal (the
  client check always reports "not running" on this Linux box).
- `db2 fetch`, `doctor` without `--offline`, and live `looks` against
  69913: no network, by rule. I used the recorded 70009 tables instead.
- `addon install lab` and a real schema-3/4 capture across several sessions.
  The `sv merge` loader check (probe.loads across snapshots) was only seen in
  its "fewer than two snapshots" message.
- A real 1–2 GB addon tree: F14 is extrapolated from a 60 MB constructed one.
- `log tail --follow` across a log rotation.
- The UI beyond one Character-tab capture (`work/raider/ui-character.png`).
