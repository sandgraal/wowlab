# QA and test engineer

## Verdict

For a tool of this size, the safety core held up better than anything I usually test. Over 14 sandboxes and roughly 400 command runs, the install hash (contents, sizes, modes, mtimes, directory mtimes) never changed after a read command, from the CLI or through the UI's `/api/run`. A `kill -9` in the middle of a `snap restore` left an install that `undo` returned to its exact bytes. A full store, a read-only store, a corrupt object and a second writer were all refused, and none of them touched the install. 80,000 seeded mutations of the real fixtures did not crash `luadata` or `wtfconfig` once, and L4 held every time a mutation parsed. I would recommend it to a careful single-install player on macOS today. The biggest problem is a gap in the data model. Snapshots and profiles are tied to the flavor folder's name, and the project's own docs say the Forever folder will change at launch. On that day every snapshot the player took during the beta stops being restorable, and the tool has no way to restore one into the renamed folder. After that, the gaps are failure-path messages and recovery (an interrupted change, a full disk, a closed pipe) and a test corpus that is real but narrow (one Mac, one owner, largest SavedVariables file 44 KB).

## What works

- **L1 held everywhere I looked.** `readsweep.sh` ran about 40 read commands per sandbox (baseline, hostile SavedVariables, five WTF corruption variants, the filesystem attacks, two flavors) and hashed the install before and after. Every run printed `L1-OK`, and so did the UI API probe (`uiprobe.py`).
- **The write-ahead journal really works.** I killed a restore with SIGKILL once its third temp file appeared, at 8.7 s. Two 125 MB/31 MB files had already been replaced and a 2 MB partial temp sat in `SavedVariables/`. `wowlab undo --yes` put back both files, deleted the half-created one and removed the temp. The content hash matched the state before the restore (`dd6d39fc9de2e86a` both times).
- **Refusals fail closed with exit 3 and nothing written.** This held for a store that filled during the pre-write snapshot, a read-only store (`[Errno 30]` on the lock), a corrupt object ("does not decompress"), a second concurrent writer ("store lock … held by another wowlab call"), and a fake client whose executable lives in the flavor folder or that is named `WowClassic.exe`.
- **Parser hardening is real.** Nesting depth over 200 is refused, not a `RecursionError`. Number literals over 4300 characters are refused. Functions, calls, bare identifiers and NUL bytes are rejected with a line and column. Duplicate keys are kept and flagged in `--json`. Latin-1 bytes survive as base64. Mutation fuzz found 0 crashes and 0 round-trip mismatches (`fuzz.py`, 2 seeds, 80k SavedVariables plus 240k WTF mutations).
- **Links and odd names are handled with care.** Snapshot and tree record links "not followed", including a link to `/` and a loop. A permission-denied folder is named by `doctor` ("a snapshot stops there until it can"). Non-UTF-8 file and folder names snapshot and restore byte for byte.
- **The combat log is fast.** On a 400 MB log with a 3 MiB line: `log tail -n 5` takes 0.9 s, `binds coach` 12.6 s at 64 MB RSS, and the long line is flagged as cut, as L4's exception says it should be.

## Findings

### F1: Snapshots and profiles cannot be restored after the flavor folder is renamed (beta to launch)
- Severity: major
- Area: `snap restore`, `profile apply`, `guard.restore` (`_refuse_foreign_snapshot`)
- Evidence (sandbox `qa-two`): `W snap create --flavor _classic_beta_`, then rename the folder to `_classic_` and change the product to `wow_classic` in `.build.info`/`.flavor.info`, then `W snap restore <id> --dry-run` gives
  `wowlab: snapshot 20261021T180938.990064Z-832e0860 is of flavor folder '_classic_beta_', which …/World of Warcraft does not have (flavors: _classic_, _retail_)`.
  `snap restore` and `profile apply` take only `--paths/--dry-run/--yes/--root/--json`, with no target flavor. AGENTS.md ("The Forever beta's folder will change at launch") and LAB_PLAN.md:51 both predict this rename.
- Impact: every beta-era snapshot and profile becomes read-only history on launch day, which is exactly when a player wants their UI back. This hits the tool's core audience, the Forever players.
- Proposal: add `snap restore --into-flavor <folder>` and `profile apply --into-flavor`. They go through `guard` as usual (pre-write snapshot of the target flavor, same allowlist), and the plan names both folders. Ship it before launch, and grade it with a renamed-folder test. This does not conflict with L6: the folder names come from discovery and from the manifest.
- Status: new

### F2: A whole `snap restore` leaves in place the files added since the snapshot, and the plan does not say so
- Severity: major
- Area: `snap restore` (whole), README "Why you might want it"
- Evidence (sandbox `qa-pipe`): after the snapshot, add `Interface/AddOns/BrokenUI/BrokenUI.toc` and `WTF/Account/90000001#6/SavedVariables/BrokenUI.lua`, then run `W snap restore <id> --dry-run`. The plan lists 400 changes (the edited files) and neither BrokenUI path. In `qa-hostile`, `NEWFILE.lua` survived three whole restores. `profile apply` does delete files added since the save (LAB_PLAN §2556), so the two restore paths behave differently. The guard docstring says a deletion happens only for a named `--paths` entry.
- Impact: the README pitch is "I broke my UI … take a snapshot … puts every file back exactly as it was". When the breakage is a newly installed addon or its SavedVariables, a whole restore leaves the cause in place and reports success.
- Proposal: (a) list the files added since the snapshot under the plan's "left alone" heading, and (b) add `snap restore --mirror` to delete them, through `guard`, journaled and undoable. Until then, say in the README and the wiki that a whole restore never deletes anything.
- Status: new

### F3: An interrupted change is invisible to `doctor`, and its leftover temp file is treated as a user file
- Severity: minor
- Area: `doctor`, `explain`, `tree`, `filemap gaps`, `snap create`
- Evidence (`killontmp.py`, `KILL_AT_TMP=3`, sandbox `qa-hostile`): after the kill, `doctor --offline` prints `most recent: 00000003-5d0071ec (open, 3 file(s))`, `problems: []` and exits 0, with no "run wowlab undo". `explain …/.wowlab-807f2f…tmp` says `no file-map row` and hints `wowlab snap create … so a later change to this file can be put back`. `filemap gaps` lists it. `snap create` succeeds and stores the 2 MB temp (56 files instead of 55). Undo also works as a one-level toggle: after an interrupted restore, a rerun and two `undo`s, the install was back at the rerun's result, not at the state before the interrupt.
- Impact: a player whose run was killed (a closed terminal, the UI's 120 s limit, a crash) gets no instruction, and wowlab advises them to snapshot its own debris.
- Proposal: make an `open` journal record a `doctor` problem, with the exact `wowlab undo` line and a non-zero exit. Classify `.wowlab-<32hex>.tmp` as guard's own in the file map ("leftover from an interrupted change; the next write or undo removes it"). Leave it out of snapshots. Document that undo is a toggle and that the "guard pre-write" snapshots are the way further back.
- Status: new

### F4: A full disk under the store is reported as "cannot read" the player's install file
- Severity: minor
- Area: `snapshot.py:1144-1149` (capture loop)
- Evidence: put the store on a 48 KB tmpfs (`XDG_DATA_HOME=…/mnt/tiny`) and run `W snap create`. It prints `wowlab: cannot read …/_classic_beta_/WTF/Account/90000001#6/SavedVariables/DBM-StatusBarTimers.lua: [Errno 28] No space left on device`. The pre-write path does the same: `refused by the write gate: the pre-write snapshot failed; nothing was written: cannot read …/DBM-Party-Vanilla.lua: [Err…`. The `except OSError` around `_store_stream` also catches the store's write errors. The 12 objects left behind are swept only by a later `snap gc`.
- Impact: the player is pointed at a healthy install file, not at their full disk.
- Proposal: separate read errors from store write errors (catch the write side in `_store_stream`) and say "the snapshot store at <path> is full (N bytes free)". `doctor` could also show the free space where the store lives.
- Status: new

### F5: A closed stdout makes a committed write exit 1, and a long plan aborts the write
- Severity: minor
- Area: every command (EPIPE handling)
- Evidence (`qa-nobuild2`): `wowlab snap restore <id> --yes | head -1` prints `wowlab: [Errno 32] Broken pipe` and exits 1, yet the journal shows `00000002-cbd0c267 (committed, 1 file(s))`. In `qa-pipe`, with a 400-line plan, the same pipe aborts before the write (rc 1, nothing written). `W install show | head -4` and `W snap create | head -0` print the same errno line.
- Impact: a script or wrapper cannot tell a failed restore from a successful one with truncated output. The same command either writes or does not depending on the plan's length.
- Proposal: handle `BrokenPipeError` once, at the CLI boundary. Exit 0 or 141 silently when the work completed, and finish or refuse the write regardless of the pipe. Add a test that pipes into `head -1`.
- Status: new

### F6: `sv dump` hangs forever on a FIFO and follows links out of the install
- Severity: minor
- Area: `luadata.read` (`longpath.open_read`), `sv dump`, UI Preview
- Evidence (`mkfs.sh`, sandbox `qa-fs`): `timeout 20 wowlab sv dump "WTF/Account/90000001#6/SavedVariables/Fifo.lua"` reaches `Terminated` (rc 124). `sv dump …/LinkOut.lua`, a link to a file outside the install, prints its content, while `tree` says the same entry is "not followed". `luadata.read` uses a plain blocking open. The M11-11 helper `snapshot.open_regular_file` (non-blocking, `O_NOFOLLOW`, fstat regular) is already used by guard and the store, but not here.
- Impact: in the UI, Preview on such a file burns the full 120 s timeout. Reads behave one way through `tree` and another through `sv dump`.
- Proposal: move `luadata.read` and the `wtfconfig` `read_*` functions onto `open_regular_file`. This changes `luadata.py`, so a `[TEST]` ticket comes first.
- Status: new

### F7: The WTF parsers have no budget: 11 MB of key bindings costs 1.7 to 2.9 GB of memory
- Severity: minor
- Area: `wtfconfig` (`binds list`, `char binds`, `macros list/check`)
- Evidence (`gen_wtf.py huge`, sandbox `qa-wtf-huge`, machine shared with other seats):

  | command | time | peak RSS |
  |---|---|---|
  | `binds list` (11.3 MB, 1M lines) | 41.1 s | 1,762 MB |
  | `char binds --character 1/Labcharb-Labrealmd` | 59.6 s | 2,861 MB |
  | `macros check --character …` (26.8 MB, 200k macros) | 27.6 s | 1,461 MB |

  `luadata` has `MAX_COST`, `MAX_DEPTH` and `MAX_FILE_BYTES`, and the combat log has its 1 MiB line cap. `wtfconfig` has nothing comparable.
- Impact: these are constructed sizes, but a corrupt or hostile cache file in a shared install can exhaust memory. It is the one parser family the plan left unbounded.
- Proposal: a byte bound and a line-count bound per WTF file type, refused with a position, as `luadata` does. Add a serial RSS probe.
- Status: new

### F8: A UTF-8 BOM silently hides the first CVar, bind or macro
- Severity: minor
- Area: `wtfconfig`, `cvar get`, `luadata`
- Evidence (`gen_wtf.py bom_crlf`): `W cvar get plunderstormRealm` prints `plunderstormRealm is not set in WTF/Config.wtf (the client default applies…)` with `1 line(s) … are not SET lines`, but the value is on line 1, behind the BOM. `binds list` drops `BUTTON3`, and the character's only macro becomes "3 line(s) outside any macro". A SavedVariables file with a BOM is rejected as `expected name = value, found this (at b'\xef')`, with no mention of the BOM. LAB_FORMATS.md:33 says "Line endings and a possible UTF-8 BOM: record from the fixture."
- Impact: one save from a Windows editor and wowlab gives a confident wrong answer ("not set").
- Proposal: recognise a leading BOM as its own lossless prefix token in both parsers (L4 is kept), or at least name it in the error and in the "not SET lines" note.
- Status: new

### F9: Large real-world SavedVariables files are slow, and the bigger ones are refused only after a full parse
- Severity: minor
- Area: `sv dump`, UI Preview, §6.4 budget
- Evidence (constructed Auctionator-shaped file, `qa-hostile`):
  - 30 MB: `sv dump --path 'AUCTIONATOR_PRICE_DATABASE["Realm"]["i:100000"]'` takes 11.3 s at 569 MB.
  - 30 MB: `--json` takes 117.9 s and writes 354 MB, 11x the file.
  - 120 MB: refused (`over the parse budget of 1150000000 (MAX_COST)`) after 23.2 s at 857 MB.
- Impact: the plan's target is parse time only. A `--path` lookup still parses the whole file. Whole JSON exceeds the UI's 120 s limit and its output cap. TSM and Auctionator users on retail routinely have 50 to 150 MB files.
- Proposal: refuse on file size before parsing when the size alone predicts a refusal. Measure `--json` output time in the perf table. Consider a streaming `--path` mode, as an idea for a later wave.
- Status: new (the refusal is by design: owner decision 2026-09-24)

### F10: A whole restore is all-or-nothing, and the refusal suggests no next step
- Severity: minor
- Area: `snap restore`
- Evidence (`qa-fs`): delete one symlink that the snapshot holds, then run `W snap restore <id> --yes`. It refuses with `'…/LinkOut.lua' is a link in snapshot …; restore never creates one`, and the two changed regular files are not restored either. A corrupt object likewise refuses the whole restore (`qa-nobuild2`). The only way forward is to list every other path by hand with `--paths`. A path shown with escapes (`Na\xefve\xff.lua`) cannot be pasted back (`holds characters that are not a plain name: ['\\']`), and the raw bytes are needed (`$'…\xef…'`).
- Impact: a single stray link or bad object blocks recovery of everything else.
- Proposal: add `--skip-refused`, which restores the rest and lists what was skipped in the journal. Have the refusal print the exact command for that, and accept the escaped spelling in `--paths`.
- Status: new

### F11: One unreadable file or folder blocks every write
- Severity: minor
- Area: `snap create`, the pre-write snapshot
- Evidence (`qa-fs`, run without DAC override): `snap create` fails with `cannot list …/Interface/AddOns/NoPermDir: [Errno 13]`. With the folder fixed, a mode-000 `NoPerm.lua` gives `cannot read …/NoPerm.lua: [Errno 13]`. Since every write needs that snapshot, `restore`, `profile apply` and `sv merge` all become impossible. `doctor` does warn about the folder.
- Impact: this is common with addons unpacked under sudo on Linux or Wine, or copied in with odd ACLs on macOS.
- Proposal: name the remedy in the error ("fix permissions: chmod -R u+rX …" or "wowlab cannot snapshot it"). Consider recording unreadable entries as "unreadable, not captured" with an explicit `--allow-unreadable` for the pre-write snapshot, after an owner decision, because it weakens the guarantee that the snapshot holds every file.
- Status: new

### F12: Without `.build.info`, even `undo` refuses, and `doctor` gives its first-run speech first
- Severity: minor
- Area: install discovery, `guard`
- Evidence (`qa-nobuild`, `qa-nobuild2`): with `.build.info` moved away, `tree`, `sv list`, `cvar list` and `snap create` all fail with `… is not a WoW install: it has no .build.info`. `W undo --yes` shows its plan, then fails with `refused by the write gate: not a flavor of an install: .build.info … is missing`. `doctor --offline` prints the five-bullet first-run text before the "not a WoW install" line.
- Impact: when a launcher repair or update interrupts and removes `.build.info`, the player loses wowlab's recovery path at the worst moment.
- Proposal: let `undo` and `snap restore` accept the install recorded in the journal or manifest when `.build.info` is missing, behind an explicit `--root`, with a warning. Or at least say "restore .build.info (Battle.net: Scan and Repair) and retry". Put the not-an-install verdict before the first-run text.
- Status: new

### F13: `make ci` on this box: one failure depends on running as root, and one serial probe depends on load
- Severity: minor
- Area: `make ci`, `tests/repo/test_wiki_daily_outside.py`, the serial pass
- Evidence (logs in `$EVAL/work/qa/make_ci*.log`, `make_rest.log`):
  - The first `make ci` stops in 1 s: `selene not found at …/.tools/selene-0.31.0/selene`. I fetched the pinned zip into the work dir (sha256 `0580eed9…` matches) and reran with `SELENE=`. lint and lint-lua are green.
  - Parallel pass: `1 failed, 9161 passed, 275 skipped, 1 xfailed in 1323.55s` (22 min wall, user 41 min). Another seat's `make test` was running on the same 4 cores. The failure is `test_constructed_the_last_step_removes_git_and_the_venv_and_runs_nothing[read-only-as-user-fails]` (`assert 0 != 0`), and it fails again on a rerun alone, as root.
  - make then stops, so I ran the rest myself. Serial: `1 failed, 67 passed` in 262 s (`test_m10_04_semicolon_list_at_budget…::test_constructed_control_zeros_at_the_budget_are_inside_the_target`, which passes alone at load 1.4). test-parser: `2047 passed, 24 skipped` in 82 s. hooks-test: `185 passed`, 64 s wall.
  - About 30 skips read "POSIX permissions, and not as root" or "root reads past permission bits". `/tmp/pytest-of-root` grew to 1.9 GB.
- Impact: in a root dev container (common), a contributor sees a red suite that is not about the change, and the permission-denied tests never run there. Because `make ci` stops at the first red target, test-parser and hooks-test never run either.
- Proposal: add `skipif(os.geteuid()==0)` to that parametrised case, as the other permission tests have. Make `ci` run every stage and report all of them (`make -k`, or a script that collects the results). Make the selene requirement explicit in `make ci`'s message ("run `make selene`, or pass SELENE=").
- Status: new (temp-dir growth, the hang guard and load flakes: already-ticketed: M13-81)

### F14: The real-fixture corpus is narrow, and nothing exercises L1 against a real install
- Severity: major (test strategy)
- Area: `lab/core/tests/fixtures`, `@live` tests
- Evidence: 43 install files from one owner, all macOS, all Forever beta, plus 39 wago responses. The largest SavedVariables file is 44,218 bytes (`macos-70450/…/Sangraal-Sett/SavedVariables/WowLab.lua`) and the combat log is 23 KB. There is no Windows, Wine or retail capture, and no heavy addon (WeakAuras, Details, Auctionator, ElvUI). The §6.4 performance table is built only from constructed shapes. The `@live` tests are `test_looks_live`, `test_gamedata_live`, `test_casc_live` and `test_casc_blte_live`, and none of them hashes an install around the read commands.
- Impact: L4 ("on every real fixture") and the performance target are proven on files 1,000x smaller than what a raider has. The main Windows population (the README marks Windows "Untested") has no real bytes at all.
- Proposal: (1) an `@live` "L1 sweep" the owner runs before each release. It would hash the real install (contents, mtimes, directory mtimes), run every read command and the UI read endpoints, and compare, as `readsweep.sh` does here. (2) Recruit `explicit`-consent captures through `lab_capture.py` from one Windows retail player with a heavy addon set. (3) Add one real SavedVariables file of 10 MB or more to the §6.4 table.
- Status: new (Windows CI: already-ticketed: M13-02)

### F15: No fuzz or property test, though the parsers would pass one today
- Severity: idea
- Area: test strategy (`luadata`, `wtfconfig`)
- Evidence: `uv.lock` has no `hypothesis`, and no test mutates the fixtures. `fuzz.py` (seeded mutations of the real fixtures: byte flips, token inserts, truncation, splices, repeats of `{`, `\\`, a BOM, NUL) ran `60000 iterations in 187s: {'sv_ok': 7065, 'sv_rejected': 52935, 'cfg': 180000}; problems: 0`, and seed 1 also found 0.
- Impact: today's robustness rests on hand-written hostile cases and 191 review probes. A cheap, general regression net is missing.
- Proposal: check in a seeded mutation test (stdlib `random`, no new dependency) with a fixed number of iterations in `make test-parser`. Its invariants are: only `LuaDataError` escapes, `serialize(parse(x)) == x` when the parse succeeds, and `to_bytes()` round-trips for WTF. For `luadata.py` it goes through a `[TEST]` ticket.
- Status: new

### F16: "Graders activated by marker deletion only" is a checkbox, not a check
- Severity: minor
- Area: process (ADR-0013), `.github/PULL_REQUEST_TEMPLATE.md:11`, CI
- Evidence: the rule appears only as a PR-template checkbox and in `.claude/rules/lab-load-bearing.md`. `tests/repo/` holds no diff check. `protect_paths.py` confines `code-reviewer` to `tests/review/` but does not keep `implementer` out of `test_luadata*`/`test_guard*`. One strict xfail is active (`test_m14_03_sv_merge_confirms_a_source…`, M13-74).
- Impact: the most important separation in the project relies on reviewers reading the diff. A grader weakened alongside an implementation change would pass CI.
- Proposal: a CI job for PRs that touch `luadata.py` or `guard.py` that fails if any already-tracked grader file has a hunk other than the removal of an `@pytest.mark.xfail(strict=True, …)` block. Optionally, a `protect_paths` rule for the implementer agent.
- Status: new

### F17: Error messages expose raw errno text and give no hint about relative paths
- Severity: minor
- Area: `sv dump`, CLI errors
- Evidence: `W sv dump Syndicator.lua` gives `wowlab: [Errno 2] No such file or directory: '…/_classic_beta_/Syndicator.lua'` (`sv list` prints the full relative path, but the dump does not suggest it). A dangling link and a mode-000 file show `[Errno 2]` and `[Errno 13]` in the same form.
- Impact: a first-time user's first `sv dump` fails, and the message reads like a crash.
- Proposal: say that the path is relative to the flavor folder and suggest the `sv list` match for a bare file name. Turn the common errnos into sentences.
- Status: new

### F18: `wowlab ui --help` says the write commands are not offered, but `/api/commands` offers them
- Severity: minor
- Area: `ui`
- Evidence: the `ui --help` text says "Commands that change your game files … are not offered in this version". `uiprobe.py` against `/api/commands` finds `snap restore`, `undo`, `profile apply` and `sv merge` offered.
- Impact: the help contradicts the README and the behaviour.
- Proposal: rewrite the docstring.
- Status: already-ticketed: M13-76

## Top 3 changes

1. **F1: restore into a renamed flavor folder before the Forever launch.** It is the one failure with a known date, and it hits every user of the target audience at once.
2. **F2 + F3: make restore and recovery say what they do.** List (and optionally delete) the files added since the snapshot. Make an `open` journal record a `doctor` problem that gives the `undo` line. Classify guard's own temp files.
3. **F14 + F15 + F16: harden the test strategy where it is weakest.** An `@live` L1 sweep against the owner's install, one heavy real SavedVariables file plus a Windows capture, a seeded mutation test, and a mechanical check for the marker-deletion rule.

## Not tested

- **Any real WoW install, Windows, Wine or macOS.** I simulated client detection with renamed `sleep` binaries only (a Wine-like argv0 without an `.exe` in the flavor folder was not detected; `WowClassic.exe` was).
- **Network paths.** No `db2 fetch`, and no `doctor` without `--offline`.
- **The UI in a browser.** I used only the token API (`/api/commands`, `/api/run` read commands) and did not run the confirm flow or the 120 s kill in the UI.
- **Case-insensitive and normalising volumes, NTFS junctions and long paths.** CI covers these on macOS and Windows, and this Linux tmpfs cannot.
- **Unprivileged-user runs.** Permission tests were done as root without `CAP_DAC_OVERRIDE` (`setpriv`), not as a real non-root user, so the root-skipped tests stay unrun here.
- **Timings.** All figures were taken while other seats ran `make test` on the same 4 cores (load 3 to 6). Treat them as upper bounds.

Probe scripts and outputs: `$EVAL/work/qa/` (`readsweep.sh`, `svattack.sh`, `gen_sv.py`, `gen_wtf.py`, `mkfs.sh`, `killontmp.py`, `ptime.py`, `uiprobe.py`, `fuzz.py`, `sweep-*/`, `svattack-*/`, `make_ci2.log`, `make_rest.log`). The repository working tree is unchanged (`git status --short` is empty).
