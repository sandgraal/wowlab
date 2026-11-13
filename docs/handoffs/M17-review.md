# Wave M17 (the companion wave) review

Written by the conductor on 2026-11-13, following ADR-0024 and ticket
M17-12. The owner picked the wave on 2026-11-12, from the product review
(`docs/handoffs/review-2026-11-12.md`). Its aim was to re-aim `wowlab ui` from an explorer of
the install's files to a companion for the player's account. The owner asked
for this review on 2026-11-13, before M17-11 and v0.2 were finished ("Now").
Some of what is below is therefore still in flight, and it is marked so.

## Users first

Measured 2026-11-13 with `gh api`:

| | |
|---|---|
| Stars / forks / watchers | 0 / 0 / 0 |
| v0.1.0 downloads (wheel / sdist / `SHA256SUMS`) | 8 / 0 / 2 |
| v0.1.0rc1 downloads (wheel / sdist / `SHA256SUMS`) | 5 / 3 / 1 |
| PyPI | not published yet (no upload; the first goes out with v0.2) |
| Issue authors other than the owner | none (two issues by `github-actions[bot]`) |
| Discussion authors other than the owner | none (Discussions turned on 2026-11-13; GitHub's welcome post only) |
| Repository page views, last 14 days | 7 (5 unique) |
| Clones, last 14 days | 11,750 (1,090 unique), almost all CI runners and agent worktrees, not people |

**There are still no outside users.** That is expected: nothing has been
announced, and the only release predates the companion work. The wave did
what the review said comes first, the product shape and a double-click
install. It did not yet do the part that brings people: a release that
carries it, PyPI and an announcement.

## What shipped

| Ticket | PR | What a player gets |
|---|---|---|
| M17-01 | #523 | The Account tab: every recorded character in one place, the UI's home tab; `char list --summary` |
| M17-02 | #521 | `wowlab char history`: a character over time, from snapshots |
| M17-03 | #538 | The History panel on the Character tab |
| M17-04 | #532, #544 | "Take a backup now" and a "the game updated" prompt on the Account tab; `doctor` says it too. #544 fixed a crash #532 brought in that blanked the Account and Character tabs |
| M17-05 | #537 | The patch-day card: addons out of date or broken for this client |
| M16-131 | #540 | The patch-day card also lists saved data wowlab could not read |
| M17-07, M17-13 | #517, #519 | ADR-0031 amendment and ADR-0038 (frozen apps), accepted by the owner |
| M17-08 | #525 | The macOS app (Apple silicon), built by the release |
| M17-09 | #533 | The Windows app, built by the release (no human has run it) |
| M16-32 (part) | #526, #535 | The PyPI job in the release workflow; the Discussions checklist |
| M16-33 | #527 | The README's first screen for players (merged by the owner) |

## Dropped by the owner (2026-11-13)

- **M17-06**, gold in the lab-addon. The hub and the timeline show gold as
  "not recorded".
- **M17-10**, a retail capture. The README keeps saying retail is untested.

## In flight when this was written

- **M17-11**: the README and the wiki lead with the hub and the backup
  button. A separate PR on top of #527.
- **M16-32**: the distribution is renamed from `wowlab-core` to `wowlab`
  (owner decision, 2026-11-13), so that `uvx wowlab ui` works. After it
  merges, the owner adds the PyPI pending publisher for `wowlab`.
- **M16-88**: the performance baselines re-recorded from `ubuntu-latest`
  (#546).

## Before v0.2 is tagged

1. The owner opens the macOS app once (M17-08's acceptance). A fresh build
   from `main` is in the "macOS app" workflow run 43507228636.
2. The rename and M17-11 merge.
3. The owner adds the PyPI pending publisher (`docs/handoffs/M16-32-pypi.md`,
   with the project name `wowlab`). This is needed only if PyPI should
   ship with v0.2.
4. Tag. Approve `release`, then `pypi`.

Not needed for v0.2: the M16-45 schema-5 capture (the lab-addon works on
schema 4), and the human Windows run (M13-02). The README says Windows is
untested.

## What went wrong

- **#532 shipped a page crash** (`picker.options.some` on a DOM collection).
  The Node stand-in for the DOM returned real arrays, so the tests passed.
  #544 fixed it, and a static check now refuses an Array method on a DOM
  collection. The owner found it in a minute of use, where several reviewer
  rounds had not. For UI work, a run of the real page in a browser is worth
  more than another reviewer.
- **A shell-guard loosening (#541) took three review rounds and was closed
  unmerged.** Only the holes it found were fixed (#543). The remaining gaps
  are documented in CLAUDE.md, not chased.

## Recommendation for the next step

No new build wave yet. Ship v0.2 with the apps and PyPI, post the
announcement, and pin the setup thread in Discussions (the owner will post
it). Then wait for the M13 gate's kind of evidence: outside downloads,
issues and setups. Pick the next wave from what those people ask for. If
the owner wants a wave anyway, the review of 2026-11-12 still argues
against resuming M15 (the 3D chain) before there are users.

Dispatch stops here until the owner picks.
