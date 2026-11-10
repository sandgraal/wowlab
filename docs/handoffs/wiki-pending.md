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
| #416 | M16-13 | `doctor` reports an interrupted change with its `undo --expect` line; the `.wowlab-*.tmp` row; a whole restore skips wowlab's leftover temps | the doctor page; the file map / `explain` page; Snapshots-and-Undo |
| #417 | M16-31 | The Character tab header and `char gear` name Level, Class, Race and Spec, or say "not recorded" | Lab-Addon-and-Characters (Character tab); Command-Reference (`char gear`, `char list`, `char show`, `looks import-char`); the lab-addon page wherever it quotes the old reason |
| #422 | M16-67 | `snap export` writes its archive 0600 on POSIX | the `snap export` / snapshots page; the store-privacy note |
| #426 | M16-22 | `--json` errors: a one-line error also prints `{"kind", "message", "exit_code"}` on stdout | CLI-Reference (regenerate; the `ErrorReport` sentence); any JSON or Troubleshooting page that says `--json` prints nothing on error |
| #429 | M16-65 | `sv merge`, the `char` commands, the bindings coach, `char list` and the cvar, binds and macros reads refuse a file behind a linked folder; final link wording; `sv dump` through a shortcut is refused | Safety-and-Guarantees; `sv dump` and `sv merge`; the `char` pages; `macros check`/`list`; `binds list`; `cvar`; the undo and plan detail |
| #433 | M16-72 | `snap diff` notes a SavedVariables side over 256 MiB; `--redact` handles `file://` and drive letters | the `snap diff` section; the doctor `--redact` page, if it shows a `file://` example |
| #437 | M16-76 | `snap export` archive members unpack 0600 | the snapshot/export page, if it describes file modes |
| #438 | M16-77 | A manifest with non-ASCII digits, or a date that is not its id's, counts as damaged | Snapshots-and-Undo, if it lists why a manifest counts as damaged |

## Merged between the last wiki pass (2026-10-22 09:07) and 2026-11-04

These PRs merged after the job's final pass and name wiki pages in their
bodies; read each PR's "User-facing surface" box for the pages: #325, #326
(`sv merge` and the command reference), #327 (CLI-Reference regenerated),
#328, #332, #335, #345, #346, #347, #349, #351, #354 (the `wowlab ui` page
needs new screenshots, as M16-52 says), #365.
