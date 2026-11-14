# Wiki pages waiting for an update

The wiki (`sandgraal/wowlab.wiki`) last changed on 2026-10-22 at 09:07, in
the daily wiki job's final pass. That job was deleted on 2026-11-04 (#361).
On 2026-11-09 the owner ruled that a wiki item in a ticket's acceptance is
met when the merged PR names the pages it affects
(`docs/AGENT_WORKFLOW.md`, "User-facing changes"). The conductor keeps this
list. The owner updates the wiki from it and deletes each row once the page
is done.

Page names are the wiki's own: CLI-Reference, Command-Reference,
Getting-Started, Lab-Addon-and-Characters, Snapshots-and-Undo, Profiles,
SavedVariables-and-Merging, Combat-Log, Safety-and-Guarantees, FAQ, Home.
Where a PR's wiki line was conditional ("only if the page says …"), the
condition is kept.

## Merged since 2026-11-04

| Merged | Ticket | What changed for the player | Wiki pages (from the PR) |
|---|---|---|---|
| #362 | M16-26 | `luadata.read` and the `wtfconfig` readers open through one helper | SavedVariables-and-Merging (the `sv dump` part); the command reference pages |
| #363 | M16-35 | `char list` lists every character folder | Command-Reference (`char list`); Lab-Addon-and-Characters (the Character tab) |
| #366 | M16-09 | Snapshot kinds, restore hints, short references (`latest-user`) | Snapshots-and-Undo (`snap show`, `snap diff`, `snap restore`); the page covering `explain` and the Explorer tab |
| #368 | M16-12 | `sv merge` rulings: every removal disclosed, lists replaced whole, the `--key` / `--replace` / `--from` help, `--json` `removed[].entries` | SavedVariables-and-Merging; Command-Reference |
| #369 | M16-36 | `binds coach` inputs and defaults: log by caster name, minimum sample, unseen bindings | Command-Reference; CLI-Reference; Combat-Log |
| #370 | M13-69 | `filemap gaps` reports a non-account folder under `WTF/Account/` as a gap | Command-Reference and CLI-Reference (`filemap gaps`), only if they say otherwise |
| #371 | M16-43 | Releases a player can verify: the `#sha256=` install line, `gh attestation verify` | Getting-Started, if it copies the install line or the download-checking steps |
| #372 | M13-81 | Developer only: `make test-fast`, `make test-clean` | None |
| #377 | M16-03 | `snap restore --into-flavor/--into-root`, `profile apply --into-flavor`; restoring after the launch rename; the clash refusal and `--paths` | Snapshots-and-Undo; Profiles |
| #380 | M16-16 | Answers first, notes behind `--notes` on `doctor`, `char gear`, `char binds`, `char macros`, `macros check`, `binds coach` | CLI-Reference, and any page quoting their output (Lab-Addon-and-Characters, Getting-Started, Combat-Log) |
| #384 | M13-80 | `looks import-char` takes the class from the lab-addon's identity record; `--json` gains five fields | Customization-Looks; Command-Reference |
| #386 | M16-17 | `explain` in player words: `--verbose` is new and holds the tier, read-by and pattern lines; `--json` gains `notes`; the "wowlab never changes it" badge | the `wowlab ui` Explorer page; any page quoting `explain` output; Command-Reference (`explain --verbose`) |
| #388 | M16-34 | New `addons check`; `sv list --orphans` and `--notes`; `explain` of AddOns.txt offers `addons check` | CLI-Reference (regenerate); Command-Reference |
| #389 | M16-14 | A command piped into `head` exits with its own status and no broken-pipe line | CLI-Reference or a troubleshooting page, only if it describes exit codes under a pipe |
| #390 | M16-11 | A whole `snap restore` lists the files added since the snapshot under "Left alone" | Snapshots-and-Undo (`snap restore`); CLI-Reference |
| #393 | M16-44 | `doctor --redact` no longer rewrites a login outside a path; the capture tool's free-text deny-list | the page covering `doctor --redact`; a capture page, if the wiki has one |
| #394 | M16-10 | Plans and `snap diff` list changed bindings, macros, CVars and SavedVariables lines by name; `--json` `plan_lines` | Snapshots-and-Undo; Profiles |
| #395 | M16-25 | An over-budget WTF file is refused in one line and listed by `doctor` | a troubleshooting page, if one mentions the refusal |
| #396 | M16-07 | `doctor` is offline by default; `--check-builds` asks wago.tools; `--offline` is hidden; the UI doctor form and Looks tab | Home; Getting-Started; Command-Reference; CLI-Reference (regenerate); Game-Data; FAQ; Safety-and-Guarantees; the `wowlab ui` page (Looks tab). Every `--offline` mention |
| #400 | M16-04 | New `snap export` and `snap import`; `wowlab ui` refuses `snap export` | Snapshots-and-Undo; Command-Reference; the `wowlab ui` page (refused commands) |
| #402 | M16-08 | `undo --help` and `snap restore --paths` help reworded | Command-Reference (`undo` row); Snapshots-and-Undo (`--paths`; a second undo redoes); Home (the wave 6 sentence) |
| #408 | M16-19 | Errors in words: missing file, no install, a full store, errno text | Installing and Troubleshooting, where they quote the old no-install line or `[Errno 2]` |
| #409 | M16-18 | `explain` lists candidates and exits 1 when nothing matches; `sv dump` takes a bare name with `--account` and `--character` | Explain-a-file; Command-Reference (`explain`, `sv dump`); any page listing exit codes |
| #410 | M16-49 | Readers refuse a file reached through a linked folder and name the folder | Safety-and-Guarantees; the `sv dump` page. The sentence was reworded by #429 |
| #411 | M16-41 | `doctor` and `snap verify` say when other accounts can read the store; `--json` `exposure`; `snap verify --notes` | the doctor page; the `snap verify` page; any page on where the store lives or on privacy |
| #413 | M16-62 | The `snap restore --into` account-clash refusal suggests a snapshot before a hand removal | Snapshots-and-Undo |
| #416 | M16-13 | `doctor` reports an interrupted change with its `undo --expect` line, puts the no-install verdict first, and notes a missing `.build.info` (`--json` `build_info_missing`); the `.wowlab-*.tmp` row; a whole `snap restore` or `profile apply` skips wowlab's leftover temps | the doctor page; the file map / `explain` page; Snapshots-and-Undo; Profiles; Safety-and-Guarantees; undo / troubleshooting |
| #417 | M16-31 | The Character tab header and `char gear` name Level, Class, Race and Spec, or say "not recorded" | Lab-Addon-and-Characters (Character tab); Command-Reference (`char gear`, `char list`, `char show`, `looks import-char`); the lab-addon page wherever it quotes the old reason |
| #422 | M16-67 | `snap export` writes its archive 0600 on POSIX | the `snap export` / snapshots page; the store-privacy note |
| #426 | M16-22 | `--json` errors: a one-line error also prints `{"kind", "message", "exit_code"}` on stdout | CLI-Reference (regenerate; the `ErrorReport` sentence); any JSON or Troubleshooting page that says `--json` prints nothing on error |
| #429 | M16-65 | `sv merge`, the `char` commands, the bindings coach, `char list` and the cvar, binds and macros reads refuse a file behind a linked folder; final link wording; `sv dump` through a shortcut is refused | Safety-and-Guarantees; `sv dump` and `sv merge`; the `char` pages; `macros check`/`list`; `binds list`; `cvar`; the undo and plan detail |
| #433 | M16-72 | `snap diff` notes a SavedVariables side over 256 MiB; `--redact` handles `file://` and drive letters | the `snap diff` section; the doctor `--redact` page, if it shows a `file://` example |
| #437 | M16-76 | `snap export` archive members unpack 0600 | the snapshot/export page, if it describes file modes |
| #438 | M16-77 | A manifest with non-ASCII digits, or a date that is not its id's, counts as damaged | Snapshots-and-Undo, if it lists why a manifest counts as damaged |
| #494 | M16-112 | Discovery searches more folders (macOS `~/Games`, `/Volumes/*` last; Windows `<drive>\\Games`) and lists them when no install is found; `--json` errors gain `notes` | Getting-Started; Installing and Troubleshooting; CLI-Reference (regenerate) |
| #495 | M16-113 | The `wowlab ui` link, a reload and a second tab work until Ctrl-C; the "already used" page is gone | the `wowlab ui` page |
| #496 | M16-114 | Characters across flavors: `char list` lists every flavor; `WOWLAB_FLAVOR`; the UI's "Runs use flavor" picker ; every command's `--flavor` help names `$WOWLAB_FLAVOR` | Lab-Addon-and-Characters; Command-Reference (`char list`); CLI-Reference (regenerate); the `wowlab ui` page |
| #498 | M16-90 | A damaged journal record, and an install path with a control or format character, are refused | Snapshots-and-Undo; Installing and Troubleshooting |
| #502 | M16-109 | `doctor`'s one answer when it cannot work out what a change did, naming a whole snapshot to restore | Snapshots-and-Undo; the doctor page |
| #504, #510 | M16-118, M16-121 | "version unknown" explained in words (doctor, first run, `db2 --build`, `addon install`); `doctor --json` `game_data.detail` names the case, and `lab_addon.detail` carries the new text | the doctor page; Getting-Started (first run); Command-Reference (`db2`) |
| #505 | M16-116 | `char list --account X --json`: every flavor listed, with `account_found` | Command-Reference (`char list`) |
| #521 | M17-02 | New `char history`: one character over time, one row per snapshot that holds its lab-addon file (and one for the file in the game folder now), with level, equipped item level and spec, and the gear and currencies that changed since the row before. `--character`, `--account`, `--flavor`, `--json`, `--notes` | Command-Reference: add the row below; Lab-Addon-and-Characters (a "Your character over time" section); Snapshots-and-Undo (snapshots build the history); CLI-Reference (regenerate) |
| #523 | M17-01 | The Account tab is the browser view's home tab: every character folder in every flavor, sortable, filterable by flavor and class, a click opens the Character tab. New `char list --summary` prints the same table, with `--notes` | Home and Getting-Started (`wowlab ui` opens on the Account tab); Lab-Addon-and-Characters; the `wowlab ui` page (a new Account tab section, with a screenshot); Command-Reference: add the row below; CLI-Reference (regenerate) |
| #525 | M17-08 | The Mac app (Apple silicon), a zip on the release page: double-click it, no terminal and no uv needed. Unsigned: the first open needs a right-click and Open. No release has carried it yet | Getting-Started; Installing (a "The Mac app" section, from the install guide's section of that name); Home (the install line). Only once a release carries it |
| #529 | M16-124 | `snap create` with no `--flavor` takes one snapshot per flavor | Snapshots-and-Undo (`snap create`); Command-Reference (`snap create`) |
| #530 | M16-123 | `doctor` no longer offers a lab-addon install into a game version it cannot name; it says the install has to wait until the version is known | the doctor page; Lab-Addon-and-Characters, if it quotes doctor's next step |
| #531 | M16-122 | `doctor` names the wowlab that is running: its version, how it was installed and where it runs from, and a different checkout in the current folder | the doctor page; Installing and Troubleshooting ("which wowlab am I running?"); CLI-Reference (regenerate) |
| #532 | M17-04 | Take a backup now on the Account tab: one line per flavor saying how old the last backup is, "The game updated since your last backup" after a patch, a Take a backup now button (only into wowlab's own store) and Restore this backup…, which opens the Commands tab on the plan. `doctor` says the game updated too | Home; Getting-Started (the first backup); Snapshots-and-Undo (a "From the browser" section); the `wowlab ui` page (a screenshot of the backup line); the doctor page |
| #533 | M17-09 | The Windows app (64-bit), a zip on the release page: extract it, double-click `wowlab.exe`. Unsigned: Windows may show "Windows protected your PC" (More info, then Run anyway). Untested by a person on a real Windows install; CI builds it and starts it. No release has carried it yet | Getting-Started; Installing (a "The Windows app" section); Home. Say "untested" wherever Windows is named. Only once a release carries it |
| #534 | M16-130 | `char list --help`'s short line says it lists every character folder | CLI-Reference (regenerate) |
| #535 | M16-32 | The release workflow publishes the wheel to PyPI by trusted publishing. User-facing at the first upload: no PyPI install line goes on any page until that upload exists | Getting-Started and Installing, only after the first upload |
| #537, #544 | M17-05, M17-04 fix | The Account tab's Patch day card: addons made for another game version, addons that need one that will not load, and saved data no installed addon declares; a line opens its file in the Explorer. A part of the Account tab that fails says "This part of the page could not be drawn" with the reason, in that part only, and the rest still draws (#544 also fixed the character list not appearing) | the `wowlab ui` page (Account tab: the Patch day card; the "could not be drawn" line, under troubleshooting); Getting-Started (patch day) |
| #538 | M17-03 | The Character tab's History panel: the same rows as `char history`, newest first, for the character picked | Lab-Addon-and-Characters (Character tab); the `wowlab ui` page (Character tab) |
| #540 | M16-131 | New `sv list --unreadable`: only the saved-data files wowlab cannot read (a file cut off during a save, say), with where and why; the Patch day card lists them too | SavedVariables-and-Merging; Command-Reference: add the row below; CLI-Reference (regenerate); the `wowlab ui` page (Patch day card) |
| #548 | M16-32 | The distribution is renamed from `wowlab-core` to `wowlab` (the command is still `wowlab`); the next release's files are `wowlab-<version>-…`. Upgrade note: "Installed v0.1.0 (wowlab-core)? Run `uv tool uninstall wowlab-core` first." User-facing at the next release | Getting-Started and Installing (the install line and the upgrade note, at the next release); Home, if it names `wowlab-core`; FAQ, if it names the wheel |
| #549 | M17-11 | This list's Command-Reference rows, and the README's first screen (the Account tab and Take a backup now, with a screenshot of each) | Home: lead with the Account tab and Take a backup now, as the README does; the two images are in the repository's `docs/assets/` folder and may be linked from the wiki |
| #557 | M16-32 | v0.2.1 is the current release (v0.2.0 was tagged but never released; "there is no PyPI package" is superseded by the PyPI row below). Install line: `uv tool install --python 3.12 https://github.com/sandgraal/wowlab/releases/download/v0.2.1/wowlab-0.2.1-py3-none-any.whl#sha256=b55b4ce6101e1018e6ecd05fe57ac59eb2005f2e019c5f8385e9507b89693ed5`. No-terminal downloads: [`wowlab-0.2.1-macos-arm64.zip`](https://github.com/sandgraal/wowlab/releases/download/v0.2.1/wowlab-0.2.1-macos-arm64.zip) (Apple silicon) and [`wowlab-0.2.1-windows-x64.zip`](https://github.com/sandgraal/wowlab/releases/download/v0.2.1/wowlab-0.2.1-windows-x64.zip) (untested). Upgrade note: "Installed v0.1.0 (wowlab-core)? Run `uv tool uninstall wowlab-core` first." This completes #548's row | Installing and Getting-Started: show v0.2.1 (the line above, the two app zips, the upgrade note); Releases: v0.2.1 as current, v0.1.0 as history; Home, if it names v0.1.0 |
| (this PR) | M16-32 | wowlab is on PyPI since 2026-11-14 (v0.2.1, the first upload). To try it without installing: `uvx --python 3.12 wowlab ui` (or `uvx --python 3.12 wowlab doctor` for a quick check in the terminal). To install: `uv tool install --python 3.12 wowlab`; `uv tool upgrade wowlab` updates it later. The GitHub wheel line with its `#sha256=` stays as the alternative. PyPI's wheel and sdist are the release's own files: their SHA-256 at `https://pypi.org/pypi/wowlab/<version>/json` equals the lines in `SHA256SUMS`. Upgrade note, for either way: "Installed v0.1.0 (wowlab-core)? Run `uv tool uninstall wowlab-core` first." This completes #535's row | Installing: a "From PyPI" part (both lines, `uv tool upgrade wowlab`, how to compare the hashes); Getting-Started: lead with the two PyPI lines, keep the wheel line as the checkable alternative; Home, if it shows an install line |

### Command-Reference rows, ready to paste

Under the characters heading, replacing the `char list` row (it still says
"Every character folder with a `WowLab.lua`", which has been wrong since
#363: `char list` lists every character folder, with or without one):

| Command | What it does | Writes? |
|---|---|---|
| `wowlab char list [--account A] [--flavor F]` | Every character folder, in every account and every flavor. For a folder the lab-addon has written to: when it last saved, and the spec, or why the file could not be read. For one it has not: "no lab record", and how many settings, macros and saved-data files the folder holds. | No |
| `wowlab char list --summary` | One line per character: level, class, race, spec, item level, professions and when it last logged out or reloaded, or what to do when the lab-addon has not recorded it yet. The same table as the Account tab of `wowlab ui`. `--notes` adds what the figures mean. | No |
| `wowlab char history [--character C]` | One character over time: one row per snapshot you took that holds its lab-addon file, and one for now, with level, equipped item level and spec, and the gear and currencies that changed since the row before. Without `--character`, the character that saved last. Take snapshots with `wowlab snap create` to build history. | No |

Under the SavedVariables heading:

| Command | What it does | Writes? |
|---|---|---|
| `wowlab sv list --unreadable` | Only the saved-data files wowlab cannot read (a file cut off during a save, say), each with where it is and why. | No |

## Merged between the last wiki pass (2026-10-22 09:07) and 2026-11-04

These PRs merged after the job's final pass and name wiki pages in their
bodies; read each PR's "User-facing surface" box for the pages: #325, #326
(`sv merge` and the command reference), #327 (CLI-Reference regenerated),
#328, #332, #335, #345, #346, #347, #349, #351, #354 (the `wowlab ui` page
needs new screenshots, as M16-52 says), #365.
