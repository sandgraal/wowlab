# Wave M19 (the crew book) review

Written by the conductor on 2026-11-22, following ADR-0024 and ticket
M19-08, at the owner's request ("yes, start on those"). The owner picked the
wave on 2026-11-18. It was built between 2026-11-19 and 2026-11-20, and the
PvP book wave (M20) was started on the owner's word before this review was
written, so this review also says what M20 changed in M19's work.

One ticket of the wave is still open: M19-07, the owner's check of the crew
book in the game. Nothing in this review is a substitute for it.

## Users

Measured 2026-11-22 with `gh api`: 0 stars, 0 forks, 0 watchers. v0.3.0 has
20 asset downloads and v0.2.1 has 35; some are the owner's and CI's. The
project is not announced (owner, 2026-11-17). Nothing of M19 is in a
release.

## What shipped

| Ticket | PR | What a player gets |
|---|---|---|
| M19-01 | #626, #631 | Static checks for `Crew.lua`, written before the addon was |
| M19-10 | #634 | The crew book in game: `/wowlab crew on`, `off`, `who`, `good`, `bad`, `clear`, `note`; off by default |
| M19-02 | #625 | `wowlab crew list` and `crew show` |
| M19-03 | #630 | `crew forget`, `crew rate`, `crew note`, through the write gate, with `undo` |
| M19-04 | #624 | The capture tool refuses a crew book that is not empty; the repository is scanned for records |
| M19-05 | #627 | `doctor` says whether the book is on and how many players it keeps, never a record |
| M19-11 | #629 | `snap export` and the plans of `snap restore`, `undo` and `sv merge` say when they carry the crew book |
| M19-06 | #635 | The Crew tab in `wowlab ui` |
| M19-09 | #639 | `wowlab snap rm`, on the command line only (owner ruling, 2026-11-19) |
| M19-12 | #642 | `snap rm` removes a damaged snapshot named by its full id |
| M19-13, M19-14, M19-15 | #646, #649, #654 | No command quotes a damaged snapshot any more |

M19-09 was built: the owner said yes on 2026-11-19.

## The terms of ADR-0036, each with its proof

| Term | Proof on `main` |
|---|---|
| 1. Local only | No network client was added. The "what leaves your machine" block is unchanged in substance. |
| 2. Off until turned on | `Crew.lua`'s gate and the static checks' failing edits for a read with the book off; the turn-on notice and `crew --help` carry one identical sentence. |
| 3. Records stay out of the repository | `tests/repo/test_crew_book_out_of_repo.py`; the capture tool's refusal (M19-04). Every crew test uses the four constructed keys. |
| 4. Shared output | `doctor` and `doctor --redact` print counts only (M19-05); plans and exports say when they carry the book (M19-11). |
| 5. Visible and deletable | `crew list`, `crew show`, `crew forget` and `crew forget --all`; forget says what it does not reach (older snapshots until `snap rm` and `snap gc`). |

**What the owner changed afterwards.** On 2026-11-20 the owner lifted every
limit on *what* the crew book records ("those restrictions are not anything
I imposed intentionally"; ADR-0036's second note of that date). The wave was
built to "names only, never a GUID, never opponents". Since M20-11 the book
keeps what the game shows of each player, GUID and scoreboard line included.
Terms 1, 3, 4 and 5 stand, and term 2 for the released addon. The limits
that went were the conductor's and the reviewers', written into the plan
and the ADR amendment unasked; they cost several review rounds per ticket.

## What stayed unconfirmed

Nothing of the crew book has been checked in the game by the owner
(M19-07). The runbook is `docs/handoffs/M19-07-runbook.md`; it was written
for schema 1 and the addon is now at schema 2 with matches, so step numbers
may not match what the addon prints. What the owner's other runs on the
client (build 71037, 2026-11-20) showed that bears on the crew book:

- `GetNormalizedRealmName` gave nothing about 15 s after login. The crew
  book keyed same-realm players from it. M20-17 added a fallback (the
  character's full name) and a line in `/wowlab crew` when no home realm is
  found. Whether the fallback's form equals the normalized realm is not yet
  confirmed.
- Whether a unit's name is a secret value in instances, and so whether the
  book records anyone inside a dungeon, is not yet confirmed.
- The 20,000 limit's cost at login (M20-05) is unmeasured.
- §20.6 says "a snapshot is the way back". Nothing takes one automatically;
  the runbook now has the owner take one. The plan's sentence is still to
  be corrected.

## What went wrong

- **The conductor invented limits.** See above. The same happened in
  ADR-0026's note of 2026-11-20, corrected the same day when a review found
  the owner's real macros already in a committed fixture.
- **The conductor told the owner something false about `snap rm`**: that
  removing a snapshot "ends undo for that change and every earlier one".
  `undo` reverses only the most recent change. Corrected in the command's
  words, §20.8 decision 9 and to the owner.
- **Asking implementers to close what a reviewer rated residue added bugs
  twice** (a quadratic scan in M19-04; a forget blocked by another player's
  key in M19-03). The conductor stopped doing it.
- **Forget-by-GUID** (M20-12, in M19's `crew forget`): four separate
  defects in one review before the conductor withdrew the design. Forget
  now takes the name it is given and nothing else.
- **Static checks lost their teeth when one session changed the addon and
  its checks** (M20-11, M20-13, M20-14, after the owner asked for lighter
  process). Each time the independent review restored them. The lighter
  arrangement is kept; the review's "break each kept rule on purpose" step
  is what makes it safe.
- **Only the real client finds some things.** Three runs by the owner found
  four defects no review could have: a constant that exists only after a
  window is opened, two realm calls returning nothing at login, and a
  logout read that emptied the action bars.
- **The conductor's agents slowed the owner's game.** Test runs with four
  workers on the owner's laptop compete with the client. Reviewers are now
  asked for two.

## Cheaper or dearer than estimated

- **Dearer:** the wave grew from 9 tickets to 14. Three were the
  damaged-snapshot wording (M19-13 to M19-15), found by a security review
  of the first; one was `snap rm` for a damaged snapshot.
- **Cheaper:** the desk. `crewbook`, `crewedit` and the Crew tab each
  merged after one or two rounds.

## Wiki

Up to date at wiki commit 8928d26 (2026-11-21), with everything from this
wave marked as not in a release. Rows merged since are in
`docs/handoffs/wiki-pending.md`.

## What the owner might pick next

The owner has already picked beyond this wave: the PvP book (M20,
2026-11-19), lifting the recording limits (2026-11-20), and world PvP from
the combat log (2026-11-21). Open choices, none started:

- **A release.** Everything since v0.3.0 is on `main` only: the 3D
  character model, the crew book, the PvP book, world PvP, and the addon
  keeping macros and bindings. It needs the owner's in-game checks first,
  because each real run so far has found a defect.
- **Stuns, fears and misses as fights** in world PvP, after the first real
  scan.
- **Chat beside a player** in the crew book.
- **Wave 7**, the 3D viewer inside `wowlab ui`.
