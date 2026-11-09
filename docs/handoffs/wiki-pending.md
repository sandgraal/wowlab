# Wiki pages waiting for an update

The wiki (`sandgraal/wowlab.wiki`) last changed on 2026-10-22, in the daily
wiki job's final pass. That job was deleted on 2026-11-04 (#361). On
2026-11-09 the owner ruled that a wiki item in a ticket's acceptance is met
when the merged PR names the pages it affects
(`docs/AGENT_WORKFLOW.md`, "User-facing changes"). The conductor keeps this
list, and the owner updates the wiki from it and deletes each row once the
page is done.

| Merged | Ticket | What changed for the player | Wiki pages |
|---|---|---|---|
| #363 | M16-35 | `char list` lists every character folder | Characters; Command Reference |
| #366 | M16-09 | Snapshot kinds, restore hints, short references (`latest-user`) | Snapshots-and-Undo; Command Reference |
| #368 | M16-12 | `sv merge` rulings: every removal disclosed, lists replaced whole, `--key` / `--replace` / `--from` help, `--json` `removed[].entries` | SavedVariables-and-Merging; Command Reference |
| #369 | M16-36 | `binds coach` inputs and defaults: log by caster name, minimum sample, unseen bindings | Key bindings; Command Reference |
| #370 | M13-69 | `filemap gaps` reports a non-account folder under `WTF/Account/` as a gap | File map; Command Reference |
| #371 | M16-43 | Releases a player can verify: `#sha256=` install line, `gh attestation verify` | Install |
| #377 | M16-03 | `snap restore --into-flavor/--into-root`, `profile apply --into-flavor`; restoring after the launch rename; the clash refusal and `--paths` | Snapshots-and-Undo; Profiles; Install; Command Reference |
| #380 | M16-16 | Answers first, notes behind `--notes` on `doctor`, `char gear`, `char binds`, `char macros`, `macros check`, `binds coach` | Command Reference; Characters; Key bindings; Macros; Doctor / First run |
