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

## Merged between the last wiki pass (2026-10-22 09:07) and 2026-11-04

These PRs merged after the job's final pass and name wiki pages in their
bodies; read each PR's "User-facing surface" box for the pages: #325, #326
(`sv merge` and the command reference), #327 (CLI-Reference regenerated),
#328, #332, #335, #345, #346, #347, #349, #351, #354 (the `wowlab ui` page
needs new screenshots, as M16-52 says), #365.
