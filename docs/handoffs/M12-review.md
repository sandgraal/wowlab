# Wave 3 (M12) review

Written by the conductor on 2026-10-15, following `docs/LAB_PLAN.md` §11, ADR-0024 and the
re-scope in §14.8. Wave 3 was picked on 2026-09-29: db2lake, a talent
planner (char-planner) and an alt-dashboard. The owner also asked, on
2026-09-30, for the wiki to stay true to `main`. On 2026-10-13 the owner
decided that wowlab becomes usable by other players (ADR-0031). The wave was
then cut to what serves that, and Wave 4 (M13, the first public release)
started alongside it.

Dispatch does not stop here. Wave 4 is already planned and running, so the
recommendation at the end is for Wave 5 and after.

## What shipped

| Ticket | PR | What |
|---|---|---|
| M12-01 | #146 | Trait, Spell, Currency and Skill tables recorded at 1.60.1.70058; `scripts/wago_subset.py` (byte-level id-filtered subsets) |
| M12-02 | #157 | `gamedata`: a `400` for a table that a listed build lacks is `TableNotPublished`, with a plain message |
| M12-09 | #145 | `labaddon.read_all` and `wowlab char list`: every character the lab-addon recorded |
| M12-13 | #155 | `scripts/gen_command_reference.py`: the command reference generated from the CLI |
| M12-14 | #159, then #170, #172, #174, #180, #181, #186 | `wiki-daily`: a scheduled job that checks, improves and publishes the wiki behind `check_wiki.py` and gitleaks |
| M12-15 | #177 | `check_wiki` tests grade frozen wiki copies against their era's command set, so a new command no longer breaks CI |
| M12-03 | #158 | `wowlab_core.db2lake`: typed tables in SQLite, one database per build. Deferred by the re-scope, then merged from the owner's account on 2026-10-15. Library only: no command uses it yet |

Carried from Wave 2 and merged during Wave 3: M11-28 (#140), M11-30 (#139),
M11-31 (#144), M11-32 (#138), M11-33 (#152), M11-34 (#150), M11-36 (#149),
M11-37 (#153). M11-29 (barber-shop record) merged its code as #143. Its ticket stays open
until the owner's in-game capture is read (`docs/handoffs/M11-29.md`).

**Deferred** by the re-scope, word for word in
[`M12-deferred.md`](M12-deferred.md): M12-04 (the `db2` SQL commands),
M12-05 to M12-08 (talents), M12-10 (alt-dashboard) and M12-11 (talent
capture). M12-03's text stays there too, with a note that it merged.

The wave's tickets are archived in [`M12-backlog.md`](M12-backlog.md).

## What was learned about the client and its data

- **wago.tools answers `400`, not `404`, for a table a build does not
  have.** At 1.60.1.70058 that included `TraitSubTree`,
  `TraitTreeLoadout` and `TraitTreeLoadoutEntry`, all at a build its listing
  includes. Forever's trait data is a subset of retail's, so a planner can't
  assume retail's table set (`docs/DATA_SOURCES.md`, breakage log).
- **The recorded trait tables match a real capture.** Every tree id in the
  `macos-70058` capture exists in the recorded `TraitTree`, and every node
  id of its class tree exists in `TraitNode` (M12-01's acceptance). If the
  talent tickets come back, they start from data that has already been
  shown to join, not from guesswork.
- **Character folders come in twins.** Next to the digits-shaped folder that
  holds a character's SavedVariables, the client keeps a
  `<Realm>/<First>/` folder that may hold only `AddOns.txt`. `char list`
  lists each character once and skips the twin (M12-09, `domain-reviewer`).
- **Column types can be inferred safely.** Inferring them from wago's CSVs
  holds up in `STRICT` SQLite (`docs/LAB_PLAN.md` §14.2, amended 2026-09-30),
  and WoWDBDefs is not needed until foreign keys are.

## What was learned about running the wave

- **The wiki job was the hard part, and none of it was about the wiki.** It
  went through seven PRs and five failed dry runs:
  - the runner image lacks bubblewrap, so the token scrub could not start;
  - an API-key account ran out of credit, which was the wrong billing path
    for an owner on a Max plan;
  - a subscription token was pasted wrong;
  - the sandbox also needs `socat`;
  - a first pass after weeks of merges needs more than 60 turns.

  Each failure was diagnosable only after #172 added a redacted diagnosis
  step, so add that kind of step to any future job that runs a model.
- **Review rounds still dominate.** M12-09 sat in fix rounds from 2026-09-30
  to 2026-10-14. M11-37 took four rounds. The pattern from Wave 2 holds:
  the spec is written first, the reviews harden it, and an S ticket costs
  an M.
- **A second conductor session in the same checkout** (2026-09-30) caused
  sequencing friction on BACKLOG and DECISIONS edits. One conductor per
  checkout, with git work in worktrees, is the rule now.
- **Merging around the process.** #158 and #174 were merged from the
  owner's account before their last review round. #174 merged without its
  security fix and needed #180 to close a token exposure. A merge from the
  owner's account skips the reviews, so a PR still in review should not be
  merged that way.

## Estimates

The plan said ten development tickets (M12-01 to M12-10).
- **Four of the ten shipped:** M12-01, 02 and 09, and M12-03 despite its
  deferral.
- **The other six were deferred unstarted**, along with the M12-11 capture.
- **Three tickets the plan did not have were added for the wiki** (M12-13
  to M12-15).
- **Three of the seven shipped tickets merged before the re-scope** (M12-01,
  02 and 13), and **four after it** (M12-09, 14, 15 and 03).

The wiki tickets were estimated at S, M and S. Together they took nine code
PRs (#155, #159, #170, #172, #174, #180, #181, #186 and #177), plus four docs
PRs for the rules and ticket fixes (#151, #154, #156, #169). The estimate
missed that a job running a model on a schedule pulls in authentication,
sandboxing, billing and privacy gates.

## Ideas this unblocks

- **`db2` SQL commands (M12-04)**: db2lake is on `main`, so the SQL surface
  is now a thin layer, and its hostile-statement acceptance is already
  written.
- **Talents (M12-05 to M12-08)**: the tables are recorded and join to a
  real capture. Incumbents (Wowhead, Forever++) still cover planning, which
  is why they were deferred.
- **Alt-dashboard (M12-10)**: `char list` gives it every character's
  record. It is a page over data that already exists.

## Recommendation for Wave 5 and after

At most three, per §11. Wave 4 (M13) runs first.

1. **`wowlab ui` (M14), as already decided.** ADR-0029 is accepted, and the
   surface it must cover grew this wave: `char list`, `doctor --redact`, the
   first run and (from Wave 4) `macros check`. Its parity test makes every
   command reachable. Aim it at the second public release.
2. **`db2` SQL commands (M12-04)**, only if a Wave 4 or 5 tool needs a game
   table that the lab-addon cannot give. The owner's 2026-10-14 rule for
   db2lake still applies, and `macros check` and `binds coach` need none.
3. **Alt-dashboard (M12-10)**, as a page inside `wowlab ui` rather than a
   separate HTML file, once the UI exists.

Talents stay deferred unless players ask for something the incumbents do
not do.
