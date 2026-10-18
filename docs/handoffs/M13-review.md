# Wave 4 (M13) review

Written by the conductor on 2026-10-18, following `docs/LAB_PLAN.md` §11,
ADR-0024 and ticket M13-12. Wave 4 was picked on 2026-10-13 (ADR-0031): the
first public release, v0.1, with the read-only first stage of `wowlab ui`
pulled forward on 2026-10-14 (ADR-0029). The plan PR (#165) carried thirteen
tickets, M13-01 to M13-13; the wave grew as it ran: M13-14 to M13-20T from
follow-up and probe PRs (#183, #192), M13-21 to M13-27 from the owner's
2026-10-14 pull-forward of `wowlab ui`, and M13-28 to M13-58 from reviews.
The backlog's M13 section holds 62 headings: 46 merged, this review, twelve
review follow-ups still open, and three with the owner.

**The release is out.** v0.1.0 is tagged on `fd5aab4` (2026-10-18 05:09 UTC)
and was published at 13:49 UTC the same day, after the owner approved the
`release` environment: the wheel, the sdist and `SHA256SUMS` are on the
release page. No stranger's report has arrived yet, and downloads and stars
stood at zero when this was written; `docs/handoffs/M13-gate.md` holds the
commands for gate day, 2026-11-17.

**Dispatch stops here.** Earlier review follow-ups (M13-28 to M13-35) were
dispatched inside the wave because the wave's own work was still running;
with the release out and the review written, the twelve that remain wait for
the owner's word, as §11 says the next step is the owner's pick. Finishing
them first or starting Wave 5 is the first recommendation below.

## What shipped

| Ticket | PR | What |
|---|---|---|
| M13-01 | #166 | README and docs follow the public direction (ADR-0031) |
| M13-02 (CI half) | #173 | Windows and macOS run the Lab suite; Windows later moves off the PR path, then back on for `lab/` PRs (ADR-0014 amendments of 2026-10-15 and 2026-10-17) |
| M13-03 | #175, #214, #216 | The release workflow for tagged builds; the 0.1.0rc1 version bump and dry run |
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
| M13-41 | #252 | `combatlog` filters lines by event before tokenizing (binds coach 7.4 s → 3.1 s on a constructed 110 MiB log) |
| M13-44, M13-44T | #260, #254 | The readers M13-15 left on plain paths, with graders first for `luadata` |
| M13-49 | #257 | The scrub and the wiki gate keep character and realm names (ADR-0019 amendment, 2026-10-17) |

Harness and process in the same wave: #232 (ADR-0013 amendment of
2026-10-17: the load-bearing set back to two files, the conductor's XS lane,
review-thread resolution no longer a merge gate, lighter reviewer routing),
#250 (ADR-0014 amendment: Windows on `lab/` PRs; ADR-0013, later: bot
findings seen by both reviewers are not re-checked, narrow re-checks,
range-diff rebase comparison, "Off the branch" findings), and #261 (open at
the time of writing: the frontier respects the owner marker and prints every
eligible ticket; a failed non-required job is named in the shepherd's report;
the fixtures rule follows ADR-0019).

## What slipped to v0.2

- M13-02's human half: a person running the first run on a real Windows
  install. `docs/INSTALL.md` calls Windows untested until then.
- M13-07, `wowlab macros fix`, behind the owner's macros-cache persistence
  test (`docs/handoffs/M13-07.md`).
- The apply halves of the binds coach and the macro checker (see the ideas
  section).
- Two `--json` changes already on `main` that v0.2's notes must list:
  `not_taken` on `Install`, `DoctorReport` and `ExplainReport` (M13-45), and
  the narrowed meaning of `log.counts.unparsed` in `binds coach` (M13-41).
  M13-47 adds the guard that makes a missing line fail CI.
- The twelve open follow-ups below.

## What was learned about the client and the platforms

- **Links are everywhere and nothing agreed on them.** Symlinks, junctions,
  WCI links and WSL symlinks each reached a different code path until M13-35,
  M13-37, M13-45 and M13-46 made one `is_link` the rule: a reparse point with
  the name-surrogate bit is a link; a cloud placeholder (OneDrive, Dropbox)
  is not. The client reads through a linked flavor folder like a folder
  (`docs/LAB_PLAN.md` §6.1), so the words must say wowlab declined, never
  that the folder "is not a flavor".
- **Windows paths past 240 characters** (M13-15, M13-44): `os.readlink`
  answers an absolute link with the `\\?\` prefix; `ntpath.abspath` trims a
  trailing dot, so a `foo.` made through `\\?\` looks "gone"; the plain
  spelling's limit is in UTF-16 units, not code points; a root with no plain
  spelling (`\\?\Volume{GUID}`) is refused at discovery.
- **Inode reuse** differs by filesystem (M13-46): ext4 hands a freed inode to
  the next file, APFS does not, so a race test that unlinks and recreates
  passes on macOS and fails on Linux. Tests that need a stable identity rename
  the original aside.
- **The combat log's unit flags** (M13-08; `docs/LAB_FORMATS.md` §8,
  2026-10-17 entry): on the one real log, the player's own casts were picked
  by affiliation, type and the controller field (`& 0x300` = `0x100`) and
  `SPELL_CAST_SUCCESS` carried 30 fields with advanced logging on. The bit
  values come from community documentation and no fixture shows them, the
  control-field reading rests on an assumption about charmed players, and
  advanced logging off was not captured: all three stay **[verify]**.
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
  owner narrowed the public-repository identifier set on 2026-10-17
  (ADR-0019 amendment) to the account folder, email and BattleTag, the real
  name, a home path with the login, the owner's own GUIDs, other players'
  GUIDs with the unit name beside each, and the identity CVars.
  `doctor --redact` is unchanged.
- **The explorer** (M13-27), checked end to end by the owner and the
  conductor on the owner's install: tree, verdicts, detail, hints copied byte
  for byte, previews through the read commands, gaps, and the commands tab
  with the `--yes` commands greyed out. `wowlab ui` takes no `--root`.

## What was learned about running the wave

- **Six guardrails were loosened on the owner's word** (ADR-0013 amendment,
  2026-10-17) after a cost audit, and four review-round rules followed from
  the same day's reviews (#250). The conductor's observation, not a
  measured claim: no merge of the wave went wrong for lack of them, and the
  merges that waited longest were waiting on docs items that lived outside
  the branch, which is what the "Off the branch" rule now names.
- **The Windows job went red on `main` twice while off the PR path**
  (M13-36, M13-43), each time from a test that assumed a POSIX host. It now
  runs on `lab/` PRs (ADR-0014 amendment, 2026-10-17).
- **The performance probes flaked in almost every review run** on the loaded
  machine until M13-31; reviewers now run the full suite once and re-checks
  run only what a fix touched.
- **Agents stop at the usage limit every few hours** and resume from their
  worktrees without loss (the conductor's observation across this wave's
  sessions); the one loss came from removing a shepherd's worktree while
  its PR was open, which is now a note in the conductor's memory.
- **The automated review overlapped the independent reviewers** and found
  small items beside them: on #245 one both reviewers had seen and not
  counted, on #255 one neither had raised. The first kind now gets a reply
  and no re-check (ADR-0013 amendment, 2026-10-17, later); the second is
  fixed like any finding.

## Effort against the plan

Thirteen tickets at the pick (#165), 62 M13 headings by the end: 46
merged, this review, twelve open follow-ups, and three with the owner
(M13-02's human half, M13-07, M13-13). Every merged ticket went through the
independent reviewers; most needed one or two fix rounds, a few (for
example M13-04, M13-05, M13-09, M13-16) ran to a third or fourth, and
M13-30 had a narrow third re-check. Round numbering differs between PR
bodies, so no exact count is given.

## Follow-ups filed, not yet picked

Twelve M13 tickets came out of this wave's reviews and are on the backlog,
none built: M13-42 (CI consumers stop restoring the Actions cache), M13-47
(a new `--json` model fails CI until the release notes list it), M13-48
(mode-only executable change; owner picks (a) or (b)), M13-50 (`char show`
and a stray `SavedVariables` folder; `ui --help` wording), M13-51 and M13-52
(link-target prefixes in the first run and in manifests; M13-52 is an owner
format decision with M13-52T first), M13-53 (`check_wiki` quoting an account
number and missing `#…#1`), M13-54 (a flavor folder swapped for a link after
the scan), M13-55 (the capture tool hunts blanked identity values), M13-56
(`doctor` prints its problems in text; a linked flavor beside a real one),
M13-57 (a root link judged in-flavor), M13-58 (`labaddon` plain path; `tree
PATH` prefix echo). About three days of agent work; none blocks v0.1.

## `docs/LAB_IDEAS.md`: what moved

- **Unblocked by this wave:** **keybind-coach**'s read-only half shipped as
  `binds coach`; **macro-doctor**'s read-only half as `macros check`. Both
  "apply" halves wait on M13-07 (whether the client re-reads
  `macros-cache.txt`) and on the WTF text writers that **wow-as-code** needs
  too.
- **Cheaper than its M row:** `wowlab ui` (ADR-0029). The server-over-CLI
  design with the parity test meant the explorer's two stages took six
  tickets, and every later page is a form over an existing `--json` model.
- **More expensive than planned:** Windows. Links, long paths and POSIX-only
  test assumptions took at least eleven tickets (M13-02, 15T, 15, 33, 35, 36,
  37, 43, 44T, 44, 46) and are not finished (M13-51, M13-52, M13-58).
- **Still gated:** **crew-book** and **comp-builder**, which the ideas file
  gates on ADR-0026's rule for the lab-addon and on a privacy ADR of their
  own for other players' names (the ADR-0019 amendment settled what the
  public repository may hold, not what the Lab stores locally about other
  players); everything 3D on **casc-source**.

## Owner actions outstanding

- Pick the next wave (below).
- M13-48: option (a) or (b); (a) needs M13-48T first.
- M13-52: how a manifest spells a link target; then M13-52T.
- M13-07: the macros-cache persistence test, when there is a play session.
- M13-02: a first run on a real Windows install, by a person.
- M13-13: the gate on 2026-11-17 (`docs/handoffs/M13-gate.md`).
- The M13 backlog section is archived with the pick PR, as #188 did for M12.

## Recommended next wave (at most three; the owner picks)

1. **Finish M13 first: the twelve follow-ups** (about three days). Not a new
   milestone, so not a new wave under ADR-0024: a continuation the owner
   authorises before Wave 5. Scope: close the Windows path and link gaps
   (M13-51, 52, 54, 57, 58), the two `check_wiki`/capture privacy gaps
   (M13-53, 55), the CI cache and release-notes guards (M13-42, 47), and the
   wording tickets (M13-50, 56). Nothing new to design; two owner decisions
   (M13-48, M13-52). Doing this before M14 means v0.2's notes carry the
   `--json` changes with a guard behind them.
2. **Wave 5 (M14): `wowlab ui`, the confirm flow** (ADR-0029, already
   named). Scope: the commands that declare `--yes` (`snap restore`,
   `snap gc`, `undo`, `profile apply`, `sv merge`, `addon install`,
   `addon remove`, and `doctor`'s first snapshot) each behind the same
   confirm step as the CLI, the plan shown before the change, and the
   embedded pages (`looks page`). A security review per ticket that reaches
   `guard`; `snap gc` and the first snapshot write only wowlab's own store.
3. **The apply halves of keybind-coach and macro-doctor**, only after
   M13-07's answer and only if the gate (M13-13) says public work continues:
   `binds coach --apply` and `macros fix`, each a `guard` write of a WTF text
   file with undo, sharing the writers **wow-as-code** needs.

*Owner, 2026-10-18 (later the same day):* recommendation 1 picked as
written, then 2 with an addition: the player's character in the UI (gear,
keys, macros), read-only in M14 and built toward editing and
model-assisted optimizing in the wave after. Specified in
`docs/LAB_PLAN.md` §16; ADR-0032 (Proposed) covers the model's access to
the data; recommendation 3 is §16.5. The plan's proposed default (§16.6
F-F, not an owner ruling) is that §16.5 is built whether or not the gate
passes and the gate decides only whether it is released; the owner has not
spoken to that.

Not recommended yet: anything from section D (bridges and live data) or 3D,
and the **crew-book**/**comp-builder** pair until their privacy ADR exists.
