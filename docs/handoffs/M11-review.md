# Wave 2 (M11) review

Written by the conductor per `docs/LAB_PLAN.md` §11 and ADR-0024, on
2026-09-29. Wave 2 built the lab-addon and the tools that stand on it, the
data-only customization sandbox, profiles and sv-merge. Dispatch stops here
until the owner picks Wave 3; the recommendation is at the end.

## What shipped

| Ticket | PR | What |
|---|---|---|
| M11-01 | #97 | lab-addon sources, TOC template, selene Lua lint |
| M11-02 | #105 | `wowlab addon install/remove lab` through guard |
| M11-03 | #118 | the first real lab-addon capture (two characters) and the §13.1 **[verify]** table |
| M11-04 | #125 | `wowlab_core.labaddon` reader and `wowlab char show [--json]` |
| M11-05 | #95 | customization tables and the looks model |
| M11-06 | #101 | `wowlab looks races/options/save/show/compare` |
| M11-07 | #104 | `wowlab looks page` (static HTML, ADR-0027) |
| M11-08 | #96 | profiles |
| M11-09T / M11-09 | #122 / #TBD | sv-merge graders, then `wowlab sv merge` |
| M11-11T / M11-11 | #99 / #107 | guard and store reads never block |
| M11-12 | #100 | profiles hardening |
| M11-13 | #106 | lab-addon static checks (every stored value type-checked) |
| M11-14 | #103 | small CLI follow-ups |
| M11-15 | #112 | snapshot store hardening (gc never deletes through a link) |
| M11-16T | #110 | M10-04 timing probes measure CPU time |
| M11-17 | #111 | addon install and looks page follow-ups |
| M11-18 | #123 | snapshot `create` never writes through a link |
| M11-19 | #120 | load-tolerant timing tests outside luadata |
| M11-20 | #117 | the lab-addon no longer crashes the client (appearances) |
| M11-21 | #121 | `/wowlab skip`/`unskip` with an announced 15 s first-pass window |
| M11-22 | #124 | the lab-addon says why a value is missing |

Also: `guard_bash.py` blocks `pkill`/`killall` (#109); backlog updates #108,
#119; Dependabot #113 (Actions, security-reviewed), #114, #115.

Deferred, not blocking the wave: **M11-23** `looks import-char` (owner
decision, 2026-09-28: no real capture holds a customization record).

## What was learned about the client (Forever beta, 1.60.1.70009)

Details and the full table are in `docs/LAB_FORMATS.md`'s M11-03 amendment.

- **A client assertion is not a Lua error.** `pcall` does not catch a C++
  assert: one `C_TransmogCollection.GetCategoryAppearances(9)` call crashed
  the client about 4 s after entering the world (M11-20). Every unverified C
  API the addon calls can take the game down; the addon now has an off switch
  per section and a 15 s announced window before its first recording pass.
- **The client writes its crash reports into the flavor folder**, as
  `Errors/<date>_<time>_Error_<pid>.txt` (seen 2026-09-28 on macOS). This
  answers the location **[verify]** on the crash-forensics idea. The reports
  hold the character name, GUID, BattleTag and guild roster: never capture
  one into the repository.
- **The barber shop UI did not open in-world** (entering the shop and
  right-clicking a chair did nothing; one capital, two characters). The
  customization record stays absent, so `looks import-char` has nothing real
  to read.
- **Forever's character sheet shows no item level.** `GetAverageItemLevel`
  still returns values, but nothing in the UI backs them.
- **Legacy talents are present below level 25**, every node at rank 0, not
  empty as §13.1 assumed. Talents, spec and professions (Classic-style) record
  correctly; class talents run on `C_Traits`.
- **SavedVariables keys are written in the client's table-iteration order**,
  not insertion order.
- **On 70009 each `<digits>/<First>-<Second>/` folder also holds an
  `AddOns.txt`**, beside the one in the `<Realm>/<First>/` twin **[verify]**.
- **The `probe.loads` counter increments** (4 and 2 on the two characters),
  which proves the SavedVariables round-trip on Forever.

## What went wrong, and what changed because of it

- **The addon crashed the client on its first login** (M11-20). It was fixed
  the same day by removing the call and adding a source scan; then M11-21 gave
  the owner a way out of any future crash loop and M11-22 made captures say
  why a value is missing. Lesson: an addon section is only as safe as its
  least-verified C API, and the first capture is the real test.
- **A conductor-requested speed-up introduced a regression** (M11-18 round 1:
  one open descriptor per shard ran out under macOS's default limit of 256 and
  reported it as a link). Both reviewers caught it independently before merge;
  round 2 holds at most four descriptors. Lesson: a performance ask gets the
  same adversarial review as a security fix, and "cannot open" is never
  reported as "is a link".
- **The first M11-21 design gave the owner no real window** to type the
  switch (all sections gather in one pass ~2 s after a loading-screen event).
  The domain review caught it; the window is now 15 s, announced in chat.
- **A privacy near-miss**: the client crash report pasted into the session
  held the owner's identity. Nothing from it reached a file; every brief and PR
  was checked. The rule stands: crash reports and captures are local until the
  scrub tool and a security review have been through them.
- **The sv-merge loader check had a gap** (a failed load after the newest
  snapshot passed). Closed in M11-09 by also comparing the file on disk; a
  reset from a snapshot at 1 still cannot be seen, and §13.4 says so.

## Effort against the plan

The plan was 10 tickets, four M-sized (M11-01, M11-05, M11-08, M11-09). The
wave took 25: the 10, eleven follow-ups from reviews (M11-11T/11, 12–19,
M11-22), two from the live capture (M11-20, M11-21), a deferred ticket
(M11-23) and the backlog PRs. Nearly every ticket needed one or two fix
rounds; none needed a third. Most of the growth was hardening the store,
guard and the addon against hostile or unusual input, which the reviews
found rather than the plan.

## Ideas unblocked, and estimates corrected

- **Now buildable:** alt-dashboard (the M11-04 reader plus ADR-0027 static
  pages), char-planner (talents recorded; needs db2lake for tree data),
  sv-health (the probe and the loader check exist; it is mostly done),
  crash-forensics (the report location is now known), time-capsule,
  tabletop-sheet, roguelike-me.
- **Cheaper than thought:** sv-health (M11-09's loader check is most of it);
  crash-forensics.
- **More expensive than thought:** anything that needs the barber shop
  (customization from a capture), anything that needs appearance collections
  (the one API crashed the client), and any new lab-addon section (each needs
  a live capture before it can be trusted).
- **Still gated:** everything 3D waits on casc-source (a spike first);
  crew-book and comp-builder wait on a privacy ADR; look-from-image loses its
  collections half until a safe appearance source is found.

## Next wave: recommendation

The owner's stated interest is the offline character tools, and the original
goal was a character planner. Wave 2 made captures real, so:

1. **Recommended: db2lake + char-planner (talents first), M.** db2lake (S)
   gives typed tables from the wago recordings; char-planner uses the class
   talent data the addon already records (`C_Traits` config, trees, nodes,
   export string) to view a character's build offline and plan a new one.
   Legacy talents join when a level-25+ capture exists. This is the direct
   payoff of Waves 1 and 2.
2. **Small add-on or alternative: alt-dashboard, S–M.** A static local page
   over every character's capture, built on the M11-04 reader; the quickest
   visible result.
3. **Alternative small wave: crash-forensics + api-types, S + S.** Parse the
   client's own crash reports (location now known) and correlate them with the
   addon set; generate API stubs from the flavor's `Blizzard_APIDocumentation`
   so the lab-addon's calls can be checked against what the client documents.
   Directly motivated by M11-20.
4. **Not recommended yet:** anything 3D (needs the casc-source spike),
   wow-as-code (M, needs the WTF writers; worth its own wave).

A pick of 1 + 2 is a full wave (about the size Wave 2 turned out to be).

## Follow-ups filed as tickets (carried forward, not holding the wave)

- **M11-24** sv-merge loader check. (a) Merging `WowLab.lua` itself copies
  `probe` from theirs, because the M11-09T graders require it. (b) After a
  reset, two identical snapshots in a row hide the older snapshot that shows
  the drop; the security reviewer found this in the M11-09 verification pass.
  Both need a §13.4 ruling from the owner, and new graders from a test-writer.
- **M11-25** one path grammar shared by `sv dump --path` and `sv merge --key`.
- **M11-26** the last two wall-clock parser timing tests.
- **M11-27** `char show` follow-ups:
  - the Legacy headline sums pools across configs;
  - the events line for a section with no events;
  - a long or non-ASCII reason refuses the whole file.

## Changed before Wave 3 because of what was learned

- Test-writer/implementer separation now covers `svmerge.py` and
  `profiles.py` as well as `luadata.py` and `guard.py`: every file that
  rewrites the owner's data (owner decision, 2026-09-29; `AGENTS.md`, the
  conduct and start-ticket skills, and the implementer and test-writer
  definitions). It paid off in M11-09, where the implementer stopped rather
  than edit a grader.

- `docs/AGENT_WORKFLOW.md` adds three rules:
  - a GO/NO-GO verification pass after the last fix round, for code that
    writes or parses the owner's files;
  - performance asks get adversarial review under default OS limits;
  - a lab-addon API call stays **[verify]** until a capture shows it ran.
  The addon README's API section says the same.
- The pr-shepherd removes its own worktree and branches after a confirmed
  merge, and ignores the harmless "'main' is already used by worktree"
  message.
- The file map's `Errors/` entry now says the crash reports carry identity
  and are never captured. The capture tool already skipped the folder.
- `docs/LAB_IDEAS.md` records the answered crash-report location, sv-health
  mostly built, and the blocked halves of look-from-image and
  customization-sandbox.
- The owner's install has the current lab-addon: the conductor reinstalled it
  on 2026-09-29, with the owner's permission, through
  `wowlab addon install lab`. It includes the M11-21 off switch and 15 s
  window and the M11-22 keys.

## Owner actions outstanding

- **Pick Wave 3** (above).
- **Rule on M11-24** when convenient. It changes the §13.4 merge rules and
  can go in with Wave 3's plan PR.
- Optional, when convenient: a login on a level-25+ character (Legacy talents,
  the points cap) with a staged talent change and `/wowlab save`; a barber
  visit if the UI ever opens; a solo dungeon boss log for corpus part 4.
