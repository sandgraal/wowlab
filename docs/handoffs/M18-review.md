# Wave M18 (make the companion worth opening) review

Written by the conductor on 2026-11-17, following ADR-0024 and ticket
M18-10, at the owner's request ("do 1 and 2, then prep v0.3"). The owner
started the wave on 2026-11-14, after saying that `wowlab ui` looked bad and
was not yet useful. The conductor agreed: the page read like a debug console, showed ids where
names belonged, and raised a false alarm about the game's own files.

## Users first

Measured 2026-11-17 with `gh api`:

| | |
|---|---|
| Stars / forks / watchers | 0 / 0 / 0 |
| v0.2.1 downloads | Mac app 12, Windows app 9, wheel 5, sdist 1, `SHA256SUMS` 8 |
| v0.1.0 downloads (wheel / sdist / `SHA256SUMS`) | 10 / 1 / 3 |
| PyPI (`wowlab`, first upload 2026-11-14) | not measured: pypistats refused the request (rate limit) |
| Issue authors other than the owner | none (two issues by `github-actions[bot]`) |
| Repository page views, last 14 days | 65 (12 unique) |

The apps have more downloads than the wheel did: 21 app zips in three days,
against 10 wheels for v0.1.0 in a month. Some of them are the owner's and
CI's, so read it as a direction, not a count. There is still no outside
issue, star or discussion. Nothing has been announced yet.

## What shipped

| Ticket | PR | What a player gets |
|---|---|---|
| M18-01T, M18-01 | #560, #564 | The false alarm is gone: a saved-data file with an empty slot in a list (`nil,`, which the game itself writes) is read, kept byte for byte, and merged with every slot in place. Graders first, as ADR-0013 asks for `luadata.py` |
| M18-02, M18-07 | #562, #574 | Names by default: `wowlab names fetch` downloads the four name tables in one go; `doctor`, `char list --summary` and the home screen offer it. A folder a beta left behind no longer fails the run |
| M18-03 | #565, #563 | The companion shell: five sections, the dark tokens, Inter and Sora (ADR-0035 amendment, accepted by the owner 2026-11-14), the game named for people, caveats under "About this data" |
| M18-04 | #566 | The Characters home: a backup card, an addons card, and a card per character with class colour, level, spec, item level, professions and last played |
| M18-05 | #571 | The character page: gear in quality colours, progress over saves, professions, history; Macros under Keybinds |
| M18-06 | #573 | Characters kept only in a backup: dimmed cards and a read-only page; `wowlab char kept` |
| M18-08 | #575 | The README's pictures show the new screens |
| (spec) | #559, #561, #572 | `docs/design/COMPANION.md`, the spec amendments for nil holes, the tickets |
| (fix) | #570 | A review probe read files in the Windows code page; it now reads UTF-8 |

The design was agreed before any UI code: a mockup built from the owner's
own characters, approved in session ("mockup approved"), then written down
as `docs/design/COMPANION.md`. Every UI ticket was built from that book.

None of this is released. v0.2.1 still carries the old UI, so a player who
downloads today gets the screen the owner disliked. M18-09 (v0.3) fixes that.

## What went wrong

- **The mockup's scratch files vanished** between sessions, so M18-05 was
  built from the written spec alone. The conductor compared the result with
  the published mockup and sent back two differences (the picker labels and
  the dressing link's note). Lesson: the spec, not a scratch file, is the
  durable copy. It held.
- **A review caught a real data bug in #564.** The first `sv merge` across a
  hole renumbered the entries after it, when the other side's list was taken
  and the target had positional entries past it. The fix pads with `nil` so every key
  keeps its slot. This is the kind of bug the two-session rule for
  `luadata.py` exists for, and it is in `svmerge.py`, outside that rule:
  two reviewers found it independently, not a grader.
- **Two platform bugs reached CI only:** a probe read test files in the
  Windows code page (#570), and Linux printed a backup's date as `1-01-01`
  (fixed inside #573 with `isoformat`). Neither shows on the owner's Mac.
- **The weekly usage limit** stopped a reviewer mid-run on #566. It resumed
  where it left off once the owner said "Try again".

## Left open (not filed)

- The page's tab icon is refused by the server (a 403 in the console).
- A folder with no lab record says nothing about a backup that holds one.
- `wowlab ui --help` does not say that macros are under Keybinds.
- The `NamesFlavor` docstring says a `no_version` folder never counts in the
  exit code; it should add "while another folder has a version".
- `sv merge` could keep a list with holes as a list when every slot is
  filled after the merge (a domain reviewer's suggestion; today's result is
  correct, only less tidy).

Each is small. None should block v0.3.

## Recommendation for the next step

1. **Ship v0.3 now** (M18-09). The new screens are the release; until they
   are out, the README's pictures describe something nobody can download.
2. **Announce it**, with the two README pictures. M17's review said the
   same about v0.2, and the post has not gone out. The apps' download count
   suggests the double-click install was worth building. The announcement is
   what tells anyone it exists.
3. **No new build wave until there is outside evidence**: an issue, a setup
   thread, a Windows tester. The small items above can go in a cleanup
   ticket if the owner wants one. The 2026-11-12 review's later ideas (bag
   triage across alts, a weekly checklist) stay on the menu.

**Ideas unblocked** (ADR-0024): none.

Dispatch stops here until the owner picks.
