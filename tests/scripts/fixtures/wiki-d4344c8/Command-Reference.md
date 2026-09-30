# Command Reference

Run every command as `uv run wowlab …` from the checkout. `wowlab --help` and `wowlab <group> <command> --help` are always the authoritative source; this page is the map.

**Options most commands share**

| Option | Meaning |
|---|---|
| `--root PATH` | The install folder (the one holding `.build.info`). Default: `$WOWLAB_WOW_ROOT`, then the usual install locations. |
| `--flavor NAME` | The flavor folder to use, as `wowlab install show` lists it. Implied when the install has only one. |
| `--account NAME` | Account folder under `WTF/Account/`. Implied when there is only one. |
| `--character NAME` | A character folder: `<Character>`, or `<Realm folder>/<Character>` when the name alone is ambiguous. On Forever the folder is `<digits>/<First>-<Second>`. |
| `--json` | Print JSON instead of text. |
| `--dry-run` | Print the plan and change nothing (commands that write). |
| `-y`, `--yes` | Do not ask before changing files. |

**Exit codes:** `0` ok · `1` error · `2` usage · `3` refused by the write gate.

**The "Changes your install?" column.** *No* means the command only reads. *Own data* means it writes only into wowlab's own user-data folder. *Via gate* means it changes your install through the [write gate](Safety-and-Guarantees): game closed, snapshot first, journaled, undoable.

---

## Inspect

| Command | What it does | Changes your install? |
|---|---|---|
| `wowlab doctor [--offline]` | Install, flavors, versions, whether the client is running, server-sync settings, snapshot-store health. `--offline` skips asking wago.tools whether each version is listed. | No |
| `wowlab install show` | The install root, its flavors and its `.build.info` rows. | No |
| `wowlab tree [PATH] [--explain]` | Inventory of a flavor folder: WTF files, SavedVariables, addons, overrides, and area folders (counted, not listed). `--explain` adds each path's file-map entry. | No |
| `wowlab explain PATH` | What a path in the install is, who writes it, when, and whether wowlab may edit it. | No |

## Read your settings

| Command | What it does | Changes your install? |
|---|---|---|
| `wowlab sv list` | Every SavedVariables file, account-wide and per character. | No |
| `wowlab sv dump FILE [--path P]` | A SavedVariables file as data: one `path = value` line per value, in file order. Parsed, never run. `--path` selects one value. | No |
| `wowlab cvar list [--scope global\|account\|character]` | Every `SET` line of one config file. | No |
| `wowlab cvar get NAME [--scope …]` | One CVar's value (name compared without case). | No |
| `wowlab binds list [--character C]` | Key bindings of the account, or of one character. | No |
| `wowlab macros list [--character C]` | Account macros, or one character's. | No |
| `wowlab addons list` | Every folder under `Interface/AddOns/` with its TOC files. | No |

See [SavedVariables and Merging](SavedVariables-and-Merging).

## Snapshots and undo

| Command | What it does | Changes your install? |
|---|---|---|
| `wowlab snap create [-m LABEL] [--screenshots]` | Snapshot a flavor's `WTF/`, `Interface/` and `Fonts/` into the store. `--screenshots` also captures `Screenshots/`. | Own data |
| `wowlab snap list` | Every snapshot, oldest first. | No |
| `wowlab snap show ID` | One snapshot and every path in it. `ID` may be a unique prefix. | No |
| `wowlab snap diff A B` | Files added, removed and changed from A to B; for SavedVariables that parse, *which values* changed. | No |
| `wowlab snap verify` | Re-hash every stored object and check every manifest. Exits 1 on any problem. | No |
| `wowlab snap gc [--dry-run]` | Remove stored objects no snapshot refers to and older than an hour. | Own data |
| `wowlab snap restore ID [--paths P …] [--dry-run]` | Put back files from a snapshot. | **Via gate** |
| `wowlab undo` | Undo the most recent change made through the gate (a restore, or an earlier undo). | **Via gate** |

See [Snapshots and Undo](Snapshots-and-Undo).

## Profiles

| Command | What it does | Changes your install? |
|---|---|---|
| `wowlab profile save NAME [--preset P …] [--subtree S …]` | Save a named set of UI files as a labelled snapshot. Presets: `ui`, `bindings`, `macros`, `addons`. | Own data |
| `wowlab profile list` | Every profile, oldest first. | No |
| `wowlab profile show NAME` | One profile and every file in it. | No |
| `wowlab profile delete NAME` | Delete a profile. The snapshot stays in the store, relabelled. | Own data |
| `wowlab profile apply NAME [--dry-run]` | Return the profile's files to their saved bytes. **Also deletes files created in those places since the save.** | **Via gate** |

See [Profiles](Profiles).

## SavedVariables merge

| Command | What it does | Changes your install? |
|---|---|---|
| `wowlab sv merge FILE --into CHAR [--from SRC] [--base SNAP] [--key PATH …] [--take ours\|theirs]` | Merge one SavedVariables file into a character's copy, by key path. Two-way or three-way. | **Via gate** |

See [SavedVariables and Merging](SavedVariables-and-Merging).

## The lab-addon and your characters

| Command | What it does | Changes your install? |
|---|---|---|
| `wowlab addon install lab [--dry-run]` | Copy wowlab's in-game addon into `Interface/AddOns/WowLab/`, with the right `## Interface:` number filled in. | **Via gate** |
| `wowlab addon remove lab [--dry-run]` | Delete the addon's files. Its saved data (`WowLab.lua`) is never touched. | **Via gate** |
| `wowlab char show [--character C]` | What the addon recorded for one character, from its `WowLab.lua`. Defaults to the character whose file was written last. | No |

See [Lab-Addon and Characters](Lab-Addon-and-Characters).

## Customization looks

Data only: nothing here writes into an install or reaches the game.

| Command | What it does | Changes your install? |
|---|---|---|
| `wowlab looks races` | Races the build's tables flag as playable, with their body types. | No |
| `wowlab looks options RACE [--sex S] [--class C]` | A race's options and choices per body type, with what the tables say about each (refusals, "needs *unlock*"). | No |
| `wowlab looks save NAME --race R --sex S [--choice OPT=CHOICE …]` | Check a look against the build's tables and save it. | Own data |
| `wowlab looks show [NAME]` | One saved look, or every saved look, with its verdict. | No |
| `wowlab looks compare A B` | Two saved looks side by side. | No |
| `wowlab looks import-char NAME [--character C]` | Save a character's recorded barber-shop choices as a look. | Own data |
| `wowlab looks page [--out FILE]` | Write one self-contained HTML page to browse all options and view your saved looks. | Own data |

See [Customization Looks](Customization-Looks).

## Game data and the combat log

| Command | What it does | Changes your install? |
|---|---|---|
| `wowlab db2 builds [--product P]` | Products and their versions as wago.tools lists them. | No |
| `wowlab db2 fetch TABLE [--build V]` | Download one table for one version into the cache (never replaced once there). | Own data |
| `wowlab db2 head TABLE [-n ROWS] [--build V]` | The first rows of a table (fetched first if needed), as CSV. | Own data |
| `wowlab log tail [-n LINES] [--follow]` | The last lines of the newest combat log, tokenized; `--follow` keeps printing as the client writes. | No |

See [Game Data](Game-Data) and [Combat Log](Combat-Log).
