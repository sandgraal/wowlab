# Wave 4 (M13) review

Written by the conductor on 2026-10-18, following `docs/LAB_PLAN.md` §11 and
ADR-0024. Wave 4 was picked on 2026-10-13 (ADR-0031): the first public
release, v0.1, with the read-only first stage of `wowlab ui` pulled forward
on 2026-10-14 (ADR-0029). The wave's last non-owner code ticket, M13-44,
merged on 2026-10-18. Three tickets stay with the owner: M13-02 (a human
runs the first run on a real Windows install), M13-07 (the macros-cache
persistence test, `docs/handoffs/M13-07.md`) and M13-13 (the 30-day gate,
`docs/handoffs/M13-gate.md`, gate day 2026-11-17).

The v0.1.0 tag is on `fd5aab4` (2026-10-18 05:09 UTC). At the time of
writing the release run waits for the owner to approve the `release`
environment, so the release page is not yet public and the strangers'
reports §11 asks about do not exist yet.

## What shipped

| Ticket | PR | What |
|---|---|---|
| M13-02 (CI half) | #173 | Windows and macOS run the Lab suite; Windows later moves off the PR path, then back on for `lab/` PRs (ADR-0014 amendments) |
| M13-03 | #175, #216 | The release workflow for tagged builds; the v0.1.0rc1 dry run |
| M13-04 | #179 | The first run inside `wowlab doctor`; `docs/INSTALL.md` |
| M13-05, M11-29 | #176, #221 | Lab-addon schema 3: spellbook, action bars, bindings; the schema-3 capture |
| M13-06 | #227 | `wowlab macros check` |
| M13-08 | #241 | `wowlab binds coach` from the owner's own combat log |
| M13-09 | #178, #185 | `wowlab doctor --redact`; identifier patterns in `wowlab_core.redact` |
| M13-10 | #220 | The player-facing README |
| M13-11 | #246 | Release v0.1.0: version, lock, notes, README |
| M13-14 | #182 | The lab-addon ships in the wheel |
| M13-15, M13-15T | #247, #234 | Extended-length paths on Windows where the graders need them; a partial snapshot is never written |
| M13-16, M13-19, M13-39, M13-40 | #191, #236, #244, #245 | The wiki-daily job: outside check, gates on a fresh runner, no cache in the model's job, crash exit codes |
| M13-17T, M13-18, M13-20T, M13-31 | #189, #193, #238, #255 | Parallel suite with serial budget tests; the CPU and memory probes made robust on loaded machines |
| M13-21, M13-22, M13-23, M13-29, M13-32, M13-33, M13-35 | #195, #203, #204, #242, #215, #228, #235 | The safe-to-edit verdict in core; the whole install with file-map gaps; per-path `explain` facts, views and hints; links and reparse points judged one way everywhere |
| M13-24, M13-24T, M13-25, M13-26, M13-27, M13-27T | #201, #197, #206, #205, #237, #226 | `wowlab ui`: the local server over the CLI, the explorer in two stages, generic forms, the parity test |
| M13-28, M13-30, M13-38 | #231, #239, #249 | Addon executables are Edit "no"; a whole restore restores around screenshots and around changed or missing executables |
| M13-34 | #224 | The scrub covers the countries in `.build.info` |
| M13-36, M13-43 | #233, #253 | Windows CI: tests stop assuming a POSIX host; link text loses its `\\?\` prefix |
| M13-37, M13-45, M13-46 | #248, #258, #256 | Discovery uses the one link test, says why a `_*_` folder was not taken, and opens `.flavor.info` without following a link |
| M13-41 | #252 | `combatlog` filters lines by event before tokenizing (binds coach 7.4 s → 3.1 s on a 110 MiB log) |
| M13-44, M13-44T | #260, #254 | The readers M13-15 left on plain paths, with graders first for `luadata` |
| M13-49 | #257 | The scrub and the wiki gate keep character and realm names (ADR-0019 amendment, 2026-10-17) |

Harness and process, same wave: #232 and #250 (ADR-0013 amendments of
2026-10-17: the load-bearing set back to two files, the conductor's XS lane,
review-thread resolution no longer a merge gate, Windows on `lab/` PRs, bot
findings seen by both reviewers not re-checked, narrow re-checks, range-diff
rebase comparison, "Off the branch" findings) and #261 (the frontier respects
the owner marker; a failed non-required job is named in every shepherd
report).

## What was learned about the client and the platforms

- **Links are everywhere and nothing agreed on them.** Symlinks, junctions,
  WCI links and WSL symlinks each reached a different code path until M13-35,
  M13-37, M13-45 and M13-46 made one `is_link` the rule: a reparse point with
  the name-surrogate bit is a link; a cloud placeholder (OneDrive, Dropbox)
  is not. Battle.net runs the client from a junctioned flavor folder, so the
  words must say wowlab declined, never that the folder "is not a flavor".
- **Windows paths past 240 characters** (M13-15, M13-44): `os.readlink`
  answers an absolute link with the `\\?\` prefix; `ntpath.abspath` trims a
  trailing dot, so a `foo.` made through `\\?\` looks "gone"; the plain
  spelling's limit is in UTF-16 units, not code points; a root with no plain
  spelling (`\\?\Volume{GUID}`) is refused at discovery.
- **Inode reuse** differs by filesystem (M13-46): ext4 hands a freed inode to
  the next file, APFS does not, so a race test that unlinks and recreates
  passes on macOS and fails on Linux. Tests that need a stable identity rename
  the original aside.
- **The combat log's unit flags** (M13-08): the player's own casts need the
  controller field (`& 0x300` = `0x100`) as well as affiliation and type;
  `SPELL_CAST_SUCCESS` carries 30 fields in V22. The charm case is still
  **[verify]** (`docs/LAB_FORMATS.md` §8, 2026-10-17 amendment).
- **An addon folder can ship executables** (M13-28): `.sh`, `.bat`, `.ps1`,
  `.js`, `.dll`, and an `.app` bundle; the gate never writes one, so a whole
  restore now restores around them.
- **Performance probes under load** (M13-31): on a loaded Mac `ru_maxrss`
  undercounts (the compressor holds pages), so the memory probe reads the
  lifetime peak footprint from `/usr/bin/time -l` and takes the higher of two
  projections; a parser between the real one's time and the 10 s target can
  pass the CPU probe (disclosed limit).
- **Privacy.** Character and realm names are visible to anyone in the game,
  so scrubbing them cost capture rounds and reviewer time for nothing; the
  owner narrowed the public-repository identifier set on 2026-10-17 to the
  account folder, email and BattleTag, the real name, a home path with the
  login, and GUIDs. `doctor --redact` is unchanged.
- **The explorer** (M13-27, checked end to end by the owner and the conductor
  on the owner's install): tree, verdicts, detail, hints copied byte for
  byte, previews through the read commands, gaps, and the commands tab with
  the `--yes` commands greyed out. `wowlab ui` takes no `--root`.

## What strangers' reports taught

Nothing yet: the release is tagged but not published. `docs/handoffs/M13-gate.md`
holds the counts to record on 2026-11-17 and the commands that produce them.

## Follow-ups filed, not yet picked

Fourteen M13 follow-up tickets came out of this wave's reviews and are on the
backlog, none built: M13-42 (CI consumers stop restoring the Actions cache),
M13-47 (a new `--json` model fails CI until the release notes list it),
M13-48 (mode-only executable change; owner picks (a) or (b)), M13-50
(`char show` and a stray `SavedVariables` folder; `ui --help` wording),
M13-51 and M13-52 (link-target prefixes in the first run and in manifests;
M13-52 is an owner format decision with M13-52T first), M13-53 (`check_wiki`
quoting an account number and missing `#…#1`), M13-54 (a flavor folder
swapped for a link after the scan), M13-55 (the capture tool hunts blanked
identity values), M13-56 (`doctor` prints its problems in text; a linked
flavor beside a real one), M13-57 (a root link judged in-flavor), M13-58
(`labaddon` plain path; `tree PATH` prefix echo). Together about three days
of agent work; none blocks v0.1.

## `docs/LAB_IDEAS.md`: what moved

- **Unblocked by this wave:** **keybind-coach**'s read-only half shipped as
  `binds coach`; **macro-doctor**'s read-only half as `macros check`. Both
  "apply" halves wait on M13-07 (whether the client re-reads
  `macros-cache.txt`) and on the WTF text writers that **wow-as-code** needs
  too.
- **Cheaper than estimated:** `wowlab ui` (ADR-0029). The server-over-CLI
  design with the parity test meant the explorer's two stages took six
  tickets, and every later page is a form over an existing `--json` model.
- **More expensive than estimated:** Windows. Links, long paths and
  POSIX-only test assumptions cost nine tickets across the wave and are not
  finished (M13-51, M13-52, M13-58); `docs/INSTALL.md` still calls Windows
  untested until M13-02's human run.
- **Still gated:** **crew-book** and **comp-builder** on a privacy ADR for
  other players' names (the ADR-0019 amendment covers the owner's own names
  only); everything 3D on **casc-source**.

## Recommended next wave (at most three; the owner picks)

1. **Wave 4.1: the M13 follow-ups** (the fourteen tickets above, about
   three days). Scope: close the Windows path and link gaps (M13-51, 52, 54,
   57, 58), the two `check_wiki`/capture privacy gaps (M13-53, 55), the CI
   cache and release-notes guards (M13-42, 47), and the wording tickets
   (M13-50, 56). Nothing new to design; two owner decisions (M13-48, M13-52).
   Doing this before M14 means v0.2's notes list the `--json` changes
   (M13-45's `not_taken`, M13-41's narrowed `unparsed`) with a guard behind
   them.
2. **Wave 5 (M14): `wowlab ui`, the confirm flow** (ADR-0029, already
   named). Scope: the commands that declare `--yes` (`snap restore`, `undo`,
   `profile apply`, `sv merge`, `addon install`, `addon remove`, `doctor`'s
   first snapshot) each behind the same confirm step as the CLI, the plan
   shown before the change, and the embedded pages (`looks page`). One
   security review per ticket, since every one reaches `guard`.
3. **The apply halves of keybind-coach and macro-doctor**, only after
   M13-07's answer and only if the gate (M13-13) says public work continues:
   `binds coach --apply` and `macros fix`, each a `guard` write of a WTF text
   file with undo, sharing the writers **wow-as-code** needs.

Not recommended yet: anything from section D (bridges and live data) or 3D,
and the **crew-book**/**comp-builder** pair until a privacy ADR for other
players' names exists.
