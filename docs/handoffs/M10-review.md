# Wave 1 (M10) review

Written by the conductor per `docs/LAB_PLAN.md` §11 and ADR-0024. Wave 1
built the core library and CLI; the owner picked Wave 2 on 2026-09-28
(lab-addon, customization-sandbox data-only, profiles, sv-merge), before
this review, so the recommendation section records that pick instead of
proposing one.

## What shipped

| Module / tool | Ticket | PR |
|---|---|---|
| Scaffold, gates, harness | M10-01 | #14 |
| Capture and scrub tool (`scripts/lab_capture.py`), six follow-ups | M10-02 | #21, #33, #38, #52, #63, #64, #85 |
| Fixture corpus, parts 1–3 (Forever beta, macOS) | M10-03 | #35, #51, #80 |
| `luadata` parser (graders first) | M10-04T, M10-04 | #54, #58, #67 |
| Install discovery | M10-05 | #40 |
| Layout walker, file map, TOC parser | M10-06 | #45 |
| `wtfconfig` (Config.wtf, bindings, macros), lossless | M10-07 | #39 |
| Game data client (wago.tools, build-keyed) | M10-08 | #19, #20 |
| Client process detection | M10-09 | #17, #22 |
| Snapshot store | M10-10 | #18, #24, #44 |
| Write gate and restore (graders first) | M10-11T, M10-11 | #29, #41, #48, #43 |
| `luadata` serializer (graders first) | M10-12T, M10-12 | #66, #74 |
| Combat log tokenizer, `follow()`, `wowlab log tail` | M10-13 | #87 |
| `wowlab` CLI | M10-14 | #68 |
| Guard lock, temp cleanup, changed-file refusal | M10-16T, M10-16 | #56, #62, #59 |
| Guard store creation, undo by id | M10-17T, M10-17 | #73, #77 |
| Serializer sibling-listing follow-ups | M10-18T, M10-18 | #86, (M10-18 PR) |
| Guard grader polish; `snap create` overlap order | M10-19T, M10-19 | #82, #83 |

Every change to `luadata.py` and `guard.py` went through a separate
test-writer first (ADR-0013). Every PR had a code review; everything touching
the write gate, the parser, the capture tool or fixtures also had a security
review; format and wording work had a domain review.

## What was learned about the client (Forever beta)

- **Layout.** Flavor folder `_classic_beta_`, product `wow_classic_beta`,
  interface 16001 (confirmed in game), Lua 5.1, builds 69913 → 69977 → 70009
  during the wave. Characters sit in `<digits>/<First>-<Second>/` with a
  retail-style `<Realm>/<First>/` twin holding only `AddOns.txt`; a second
  name is not a realm (`docs/GLOSSARY.md`).
- **SavedVariables.** CRLF, a leading blank line, no indentation, no `-- [n]`
  comments in every captured file; floats with at most 16 significant digits
  that need not read back as the same double. The remembered retail layout
  (tabs, `-- [n]`) is never the serializer's default (owner decision).
- **Combat log.** Written by the client with `PROJECT_ID,18` and
  `BUILD_VERSION` as the patch only (a log does not name its build);
  unit names `"<Name>-<Realm>-"` with an empty region and no second name;
  raid flags `0x80000000` everywhere; `SWING_DAMAGE_LANDED` is not a copy of
  `SWING_DAMAGE`. No `COMBATANT_INFO` or groups yet (no boss-pull log).
- **Privacy.** Combat logs carry other players, creature spawn and server
  numbers and exact times that can link a log to someone else's upload. The
  capture tool now pseudonymises other players, invents unit-GUID location
  parts and shifts each log's timestamps and file name by a secret offset.
- Details and every open **[verify]**: `docs/LAB_FORMATS.md` amendments and
  `docs/DATA_SOURCES.md` (known state and breakage log).

## What went wrong, and what changed because of it

- **Private data in public places.** A real second name reached test files
  once; real session times reached a pushed branch, a doc draft and a PR
  description. Each was removed; a deleted branch and a description revision
  may stay reachable on GitHub by id. The conductor now scans every brief,
  commit message and PR body for real names, dates and times before sending.
- **Performance targets.** The `luadata` memory and time target took four fix
  rounds (owner-approved) and ended as a cost budget: every document within
  `MAX_COST` parses in at most about 6.4 s and 1.1 GB on the owner's M1.
- **Harness.** Agents share one scratchpad (a PR body was swapped once); agent
  worktrees under `/private/tmp` break the hook tests; usage limits stop agents
  mid-task. All three are now in the conductor's briefs.

## Ideas unblocked, and estimates corrected

- **Now buildable on Wave 1 alone:** explorer, profiles, sv-merge,
  wow-as-code (needs WTF writers), skin-packs, db2lake, build-diff,
  customization-sandbox (data-only), addon-kit, api-types, crash-forensics,
  addon-provenance, addon-manager.
- **lab-addon is the hub:** sv-health, quest-recorder, keybind-coach,
  macro-doctor, drill-cards, bag-triage, char-planner, alt-dashboard and more
  stand on it.
- **Cheaper than thought:** nothing in Wave 1.
- **More expensive than thought:** anything that writes SavedVariables
  (the serializer grew a sibling-style model, a data-only rule and a
  re-parse); anything that shares combat logs (the privacy work above);
  sv-merge (most addons keep every character in one account file, so a
  character-to-character merge is usually a key copy within one file).
- **Still gated:** everything 3D waits on casc-source (a spike first);
  crew-book and comp-builder wait on a privacy ADR.

## Next wave

Picked by the owner on 2026-09-28: lab-addon, customization-sandbox
(data-only), profiles, sv-merge, as Wave 2 (M11), with ADR-0026 and ADR-0027
accepted. The plan, tickets and the owner's review are in the M11 plan PR
(#84). The wave is 10 tickets, four of them M-sized; the M11-10 review
compares actual effort against that.

## Owner actions outstanding

- Delete the original description revision of PR #80 (it named the real log
  date).
- Optional: a dungeon boss-pull combat log, to replace the constructed
  `COMBATANT_INFO` graders with a real line.
