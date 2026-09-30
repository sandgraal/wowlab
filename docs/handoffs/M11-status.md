# M11 — conductor status for the next session

> **Superseded on 2026-09-29.** Wave 2 closed; read `docs/handoffs/M11-review.md`
> instead. This file is kept as the record of the state on 2026-09-28.

Written 2026-09-28 at the end of a long conductor session, mid-wave. Read this,
then `AGENTS.md`, `CLAUDE.md` and `docs/BACKLOG.md`, then run `/conduct next`.

## Where Wave 2 stands

Every Wave 2 ticket that does not need the game has merged:

| Ticket | PR | What |
|---|---|---|
| M11-01 | #97 | lab-addon sources, TOC template, selene Lua lint (CI step in `quality`) |
| M11-02 | #105 | `wowlab addon install/remove lab` through guard |
| M11-05 | #95 | customization tables and the looks model |
| M11-06 | #101 | `wowlab looks races/options/save/show/compare` |
| M11-07 | #104 | `wowlab looks page` (static HTML, ADR-0027) |
| M11-08 | #96 | profiles |
| M11-11T / M11-11 | #99 / #107 | guard and snapshot reads never block (FIFO, links, bounded reads) |
| M11-12 | #100 | profiles hardening (dir-fd walk, linked-root note) |
| M11-13 | #106 | lab-addon static checks: every stored value type-checked, taint analysis |
| M11-14 | #103 | small CLI follow-ups |
| M11-15 | #112 | snapshot store hardening (gc never deletes through a link, inflation cap) |
| M11-16T | #110 | M10-04 timing probes measure CPU time |
| M11-17 | #111 | addon install and looks page follow-ups |

Harness: #109 made `guard_bash.py` block `pkill`/`killall` (one implementer's
`pkill -f pytest` killed other worktrees' test runs).

## What waits on the owner

**M11-03**, the owner's capture. The runbook is `docs/handoffs/M11-03.md`: install
the addon, play a short session, run `scripts/lab_capture.py` on branch
`m11/03-lab-addon-capture`, and tell the next session "capture done". Then:

1. Dispatch an implementer for M11-03's remaining work on that branch: move the
   staged files from `lab/core/tests/fixtures/incoming/` into place, add
   provenance rows, and write the `docs/LAB_FORMATS.md` table that marks every
   §13.1 **[verify]** and every item in `lab/addon/README.md`'s checklist as
   confirmed, contradicted or open (use the owner's notes). Security and domain
   review, as for every capture. Grep everything for real names, dates and times
   before it is pushed.
2. Then M11-04 (reader, `char show`, `looks import-char`; the reader accepts
   strings only in the fields the addon writes as strings) and M11-09T → M11-09
   (sv-merge) in parallel.
3. Then M11-10, the wave review: `docs/handoffs/M11-review.md` per §11, and
   stop dispatch until the owner picks Wave 3.

Optional, independent: a dungeon combat log for corpus part 4 (runbook §5),
branch `m10/03-lab-corpus-4`. It settles the open M10-13 `COMBATANT_INFO`
criterion.

## Open follow-up tickets in the backlog

- **M11-18** — snapshot `create` still writes objects and manifests through a
  linked `objects/`, shard, `manifests/` or `tmp/` (pre-existing; #112 review).
- **M11-19** — wall-clock timing tests that fail under load: the
  `tests/scripts/test_lab_capture_*` sites and `test_layout.py`'s 400-folder test
  (#110 review).

Both are eligible now.

Also open at handoff: Dependabot PRs #113 (GitHub Actions group, which touches
`.github/`, so security-reviewer first), #114 (ruff 0.16.8 → 0.16.9, dev
tooling) and #115 (platformdirs 4.11.10 → 4.11.14, runtime). Shepherd each
once CI is green; a ruff bump can reformat, so check `make lint` on it.

## Owner decisions this wave (all recorded in the plan)

- Wave 2 pick: lab-addon, customization sandbox (data-only), profiles, sv-merge.
  The addon is installed by wowlab and records everything in the idea; the
  sandbox is a CLI plus a static local web page. ADR-0026 and ADR-0027 accepted.
- Profiles cover every character; apply removes files added since the save; the
  lab-addon (`Interface/AddOns/WowLab/`, `WowLab.lua`) is always left alone.
- L4 exception: combat-log lines over 1 MiB keep their first 1 MiB (AGENTS.md).
- Third fix rounds approved for #95 and #97; on #97 a fourth gap was deferred to
  M11-13 rather than a fourth round.
- Conductor rulings the owner may reverse: §13.1 keeps the last barber-shop
  record across sessions; `probe.lost` describes one load; crafter GUIDs are
  blanked in item links; `looks import-char` moved into M11-04; `addon remove`
  deletes all of the addon's files or none (exit 3 on any refusal); the looks
  page keeps native path separators and refuses paths inside a linked `pages/`
  target.

## Working rules this session learned

- The session scratchpad is shared by every agent: give each agent a scratch
  prefix.
- Worktrees go under `.claude/worktrees/`, never `/private/tmp`.
- Two fix rounds, then the owner decides; polish after round 2 goes to a
  follow-up without asking.
- Ask reviewers for exact replacement text; send it verbatim to the implementer.
- Implementers may open draft PRs when told to; shepherds adopt them.
- A shepherd changes only PR text and threads; CI failures in the author's own
  tests go back to the implementer.
- Privacy: never put real character, realm, account or other players' names,
  the owner's GUID (`~/.config/wowlab/own-guid`), or real dates, times or log
  file names into any public file, PR body, commit message or brief.
- `lab (windows)` catches POSIX assumptions in new tests (`os.O_ACCMODE`,
  renaming an open file, `/` in paths); expect it.
