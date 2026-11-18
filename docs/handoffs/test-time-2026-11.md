# Where the test time goes, and how to cut it (M15-21)

For the owner. Written 2026-11-18 by `code-reviewer` at the conductor's
request, after the owner's "the tests take seriously way too long ... lets
figure out how to schedule it during off hours and/or reduce their length or
how often they're called". **Nothing in this document is a decision.** The
options are ranked; the owner picks; the cuts become tickets under M15-21.

Short answer: two groups of tests are 73% of the suite's CPU time. The
17 review probes that mutation-test the lab-addon grader (66 tests, 33%)
re-run the whole addon grader, its own slow self-tests included, for every
case; stopping at the first failing grader test keeps every assertion and
removes about 84% of their cost. The 901 `test_ui_*` tests (40%) drive the
page under Node against a server that starts a fresh `python -m wowlab_core`
child for every command, and each child imports the whole CLI. Everything
the hard invariants rest on (guard, process, the luadata and parser
round-trips, the scrub, the L1 sweep, the write-site and repo scans) is
6,509 tests and 8% of the CPU time; it stays on every pull request in every
option below, at about two minutes.

## 1. Measurements

### How they were taken

- **CI, Linux.** The log of the `Lint, typecheck, tests` job of run
  44374160065 (push to `main`, 2026-11-18): the parallel pass printed
  `14400 passed, 285 skipped in 1516.63s (0:25:16)`, the serial pass
  `79 passed, 14705 deselected ... in 186.44s (0:03:06)`; the job ran
  19:03:37 to 19:32:22 (28.8 min). Run 44394649844 (20:47 UTC): parallel
  28.0 min, serial 3.2 min. The job on the last eight green pull-request
  runs took 20.5, 24.4, 26.1, 28.2, 29.4 and 29.5 min (and 0.2 min twice,
  for docs-only PRs). CI prints no per-test durations.
- **Per-test durations, local.** One run of the parallel pass at df6bae7,
  macOS, 8 cores, `WOWLAB_WOW_ROOT` unset:
  `uv run --frozen pytest -q -n auto --dist worksteal -m "not live and not serial" -p no:cacheprovider --durations=0 --durations-min=0`.
  Result: `14485 passed, 257 skipped in 902.42s (0:15:02)`; `/usr/bin/time`
  905 s wall, 2,959 s user, 433 s sys. Other agents' suites were running
  (load average 25 to 35), so absolute seconds are inflated; the analysis
  uses the **shares**, which agree with the CI timeline in 1.4. The sum of
  every test's setup, call and teardown is 6,667 s: call 96.3%, setup 2.3%,
  teardown 1.4%. Fixture setup is not where the time goes.
- **Child processes.** A second local run with an audit hook on
  `subprocess.Popen` (a scratch `sitecustomize.py` on `PYTHONPATH`) logged
  every process a test started, with the test's node id (1.5). That run was
  at load average 100 to 150 and is used only for counts.
- **Windows and macOS.** Step times of `windows.yml` runs 44394649745 and
  44381681237 and `macos.yml` run 44394649569 (pushes to `main`, 2026-11-18).
- **Triggers.** `gh run list`, 2026-11-11 to 2026-11-18.

### 1.1 By area (local; share of 6,667 test-seconds)

| Group | Tests | Seconds | Share |
|---|---:|---:|---:|
| `lab/core/tests/test_ui_*.py` (page under Node, real server, real CLI children) | 901 | 2,645 | 39.7% |
| `lab/core/tests/review/`: the 17 lab-addon grader mutation probes | 66 | 2,209 | 33.1% |
| `lab/core/tests/test_cli*.py` | 1,722 | 446 | 6.7% |
| `lab/core/tests/`: the other top-level files | 3,855 | 360 | 5.4% |
| `lab/core/tests/review/`: the other 266 probe files | 849 | 349 | 5.2% |
| `lab/core/tests/test_guard*.py` | 1,645 | 212 | 3.2% |
| `lab/core/tests/parser/` | 3,538 | 207 | 3.1% |
| `tests/scripts/` | 1,419 | 124 | 1.9% |
| `tests/addon/` | 210 | 37 | 0.6% |
| `tests/packaging/` | 131 | 31 | 0.5% |
| `tests/repo/` | 157 | 29 | 0.4% |
| `tests/harness/` | 220 | 17 | 0.2% |
| `tests/review/` (hook probes) | 29 | 2 | 0.0% |
| **Total** | **14,742** | **6,667** | |

By directory: `lab/core/tests` top level 8,123 tests and 54.9%;
`lab/core/tests/review` 915 tests in 283 files and 38.4%;
`lab/core/tests/parser` 3,538 tests and 3.1%; all of `tests/` 2,166 tests
and 3.6%.

The time is concentrated: the 100 slowest tests are 47.5% of it, the 562
tests of a second or more are 84.6%, and the other 14,180 tests take 1,029 s
together. 173 tests take 10 s or more (4,156 s, 62%).

### 1.2 The slowest 30 files

`review/` is `lab/core/tests/review/`.

| Seconds | Share | Tests | File |
|---:|---:|---:|---|
| 462.9 | 6.9% | 31 | `lab/core/tests/test_ui_home.py` |
| 315.1 | 4.7% | 24 | `lab/core/tests/test_ui_account_patch_day.py` |
| 264.2 | 4.0% | 6 | `review/test_m13_05_secret_scan_misses_a_rebound_is_secret.py` |
| 252.9 | 3.8% | 6 | `review/test_m11_37_section_scan_misses_a_second_namespace_binding.py` |
| 251.0 | 3.8% | 6 | `review/test_m13_05_secret_scan_misses_a_raw_value_not_from_a_named_handle.py` |
| 223.6 | 3.4% | 5 | `review/test_m11_13_stored_value_check_misses_call_stores.py` |
| 207.6 | 3.1% | 31 | `lab/core/tests/test_ui_explorer_stage2.py` |
| 199.3 | 3.0% | 11 | `lab/core/tests/test_ui_character_page.py` |
| 196.4 | 2.9% | 5 | `review/test_m11_37_section_scan_misses_self_and_client_given_parameters.py` |
| 185.0 | 2.8% | 26 | `lab/core/tests/test_ui_dressing.py` |
| 159.2 | 2.4% | 4 | `review/test_m13_05_secret_scan_misses_a_cleaned_value_read_as_a_table_through_an_alias.py` |
| 157.5 | 2.4% | 4 | `review/test_m11_37_section_scan_misses_client_function_handed_on.py` |
| 151.5 | 2.3% | 20 | `lab/core/tests/test_ui_character_panels.py` |
| 151.3 | 2.3% | 8 | `lab/core/tests/test_ui_kept.py` |
| 136.8 | 2.1% | 4 | `review/test_m11_01_line_based_privacy_tests_fail_open.py` |
| 131.7 | 2.0% | 3 | `review/test_m13_05_combat_scan_accepts_a_combat_check_that_never_blocks.py` |
| 120.4 | 1.8% | 3 | `review/test_m11_37_section_scan_misses_ns_method_call_and_alias.py` |
| 101.0 | 1.5% | 41 | `lab/core/tests/test_ui_explorer.py` |
| 100.2 | 1.5% | 59 | `lab/core/tests/test_ui_confirm_writes.py` |
| 96.5 | 1.4% | 43 | `lab/core/tests/test_ui_looks.py` |
| 93.6 | 1.4% | 3 | `review/test_m11_01_carry_check_misses_saved_subtables.py` |
| 91.7 | 1.4% | 11 | `lab/core/tests/test_ui_character_history.py` |
| 87.7 | 1.3% | 2 | `review/test_m11_37_section_scan_accepts_a_client_call_in_carry.py` |
| 86.7 | 1.3% | 2 | `review/test_m13_05_macro_pin_misses_the_name_stored_through_underscore.py` |
| 80.7 | 1.2% | 6 | `lab/core/tests/test_ui_backup_hint.py` |
| 77.1 | 1.2% | 10 | `lab/core/tests/test_ui_flavor_picker.py` |
| 74.1 | 1.1% | 29 | `lab/core/tests/test_ui_character.py` |
| 69.0 | 1.0% | 27 | `lab/core/tests/test_l1_sweep_live.py` |
| 67.4 | 1.0% | 14 | `lab/core/tests/test_ui_account_backup.py` |
| 66.7 | 1.0% | 167 | `lab/core/tests/test_cli_player_copy.py` |

### 1.3 The slowest 40 tests

Thirty-five of the forty are cases of the lab-addon grader mutation probes,
39 to 50 s each (the slowest:
`review/test_m13_05_secret_scan_misses_a_raw_value_not_from_a_named_handle.py::test_a_raw_client_value_is_rejected[constructed-field-stored-from-a...]`,
49.9 s). The other five:

| Seconds | Test |
|---:|---|
| 68.5 | `lab/core/tests/test_l1_sweep_live.py::test_sweep_on_a_captured_install_changes_nothing_constructed` (the L1 sweep on a captured install; about 70 child processes) |
| 40.6 | `lab/core/tests/parser/test_casc_source_constructed.py::test_too_many_blocks_with_rows_are_refused_constructed` (all but about 1 s is the test's own byte builder; S2) |
| 27.1 | `review/test_cr573_keybinds_while_kept_page_opens_shows_it.py::test_positive_control_keybinds_after_the_open_leaves_the_kept_page` |
| 26.3 | `review/test_cr573_keybinds_while_kept_page_opens_shows_it.py::test_keybinds_clicked_while_a_kept_card_opens_leaves_the_kept_page` |
| 24.2 | `tests/addon/test_lab_addon.py::test_m11_37_a_call_added_outside_a_section_fails` (the grader's own mutation self-test) |

Next: `parser/test_db2lake_constructed.py::test_constructed_load_memory_follows_the_batch_not_the_file`
22.6 s; the three `test_cli_player_copy.py::test_read_output_is_player_copy[macos*]`
cases, 17 to 20 s each; then UI tests at 7 to 25 s each
(`test_ui_home.py::test_a_card_opens_the_character_page_constructed`, 24.7 s).

### 1.4 Why the parallel pass takes 25 minutes on CI

- It is CPU-bound, not tail-bound. On `ubuntu-latest`, `-n auto` gives 4
  workers; 4 x 1,517 s = 6,066 worker-seconds, close to the 6,667
  test-seconds measured locally under load. Locally the 8 workers were busy
  92% of the wall clock (6,667 / (8 x 905)). Work stealing does its job;
  there is no long tail for another `--dist` to cut.
- The CI log's progress lines show where it goes: 0 to 50% of the tests
  finished in 4 min 38 s (19:03:53 to 19:08:31), 50% to 54% took
  13 min 4 s (to 19:21:35), and 54% to 100% took 7 min 36 s. The
  13-minute plateau is the four workers on the mutation probes (66 tests,
  about 35 s each, over 4 workers: about 10 min), with UI tests around them.
- No sleep, timeout or wait loop is a cost. The longest real sleep in a test
  is 0.5 s; the two `time.sleep(120)` calls are in planted grandchildren that
  `test_ui_server.py` kills. No hypothesis or fuzz loop with a large count
  exists (`parser/test_parser_mutation_constructed.py` is 273 cases and 27 s).
- Parametrize is not a cost: the largest parametrized functions
  (`test_filemap_addon_executables.py`, 253 ids twice; `test_verdict.py`,
  249) take under a second each in all.
- Wider fixture scopes would not help much: all fixture setup and teardown
  together is 3.7% of the time. The UI tests' `install` fixture copies a
  captured tree for each test; that is small next to the children they start,
  and the tests write into it, so it cannot be shared.

### 1.5 Child processes

The audit-hook run (same command, `14485 passed, 257 skipped`) logged 4,677
processes started by tests:

| Started | Count | Mostly from |
|---|---:|---|
| `python -P -m wowlab_core ...` (the real CLI, a child of the `ui` server or of the L1 sweep) | 1,906 | `test_ui_*` 1,704; `review/` 126; `test_l1_sweep_live.py` 70; `tests/scripts/test_perf_budget.py` 6 |
| `git` | 1,347 | `tests/scripts/test_check_grader_diff.py` 412, `test_check_wiki.py` 335, `test_check_wiki_hardening.py` 209, `review/test_m12_14_new_host_through_www_or_protocol_relative_link.py` 156 |
| other Python (helpers, hooks, `-c` children) | 1,030 | `tests/harness/test_hooks.py`, the measurement children, the planted UI recorders |
| `node` | 263 | the page harness of the UI tests and UI probes |
| other | 131 | `bash`, `uname`, `file`, `make` |

The UI tests that start a CLI child or Node are 226 of the 901; they take
2,236 of the UI group's 2,645 s (85%, a third of the whole suite), about
10 s and 7 to 8 CLI children each. Each child starts by importing all of
`wowlab_core.cli`: `python -P -m wowlab_core --version` costs 0.66 s of CPU
on this machine against 0.02 s for a bare interpreter (`/usr/bin/time`,
three runs). So about 1,906 x 0.66 = 1,260 CPU-seconds of the suite are CLI
start-up before any command runs, and each UI scenario repeats the same
reads (`char list` four times in `test_ui_account._scenario`, for example) on
an unchanged copy of the same captured install. The 263 Node runs are not
the cost: the tests that start Node and no CLI child are few and quick.
The `git` children are cheap: the four files that start most of them take
94 s together (1.4%).

### 1.6 The lab-addon grader mutation probes

`lab/core/tests/review/` holds 283 files and 915 tests. 266 of the files
(849 tests) cost 349 s together, 0.4 s a test: cheap. Seventeen files
(66 tests) cost 2,209 s; `grep -l test_lab_addon.py lab/core/tests/review/*.py`
lists them. Each case copies `lab/addon/` into `tmp_path`, edits
`Actions.lua` (or another source), loads `tests/addon/test_lab_addon.py` as a
module pointed at the copy, and calls **every** `test_*` function in it,
each `path` test once per Lua source, collecting the names that raise
`AssertionError`; the probe asserts only that the list is not empty
(`_failing_tests`, for example at line 93 of
`review/test_m13_05_secret_scan_misses_a_rebound_is_secret.py`). So every
case also runs the grader's own self-tests, which are mutation loops
themselves: `test_m11_37_a_call_added_outside_a_section_fails` (24.2 s alone
in `tests/addon`), `test_m11_37_a_call_added_inside_a_section_passes`
(3.7 s) and `test_m13_05_constructed_secret_edits_fail` (2.0 s).

Measured on two cases of that file, same machine and load: the current loop
took 70.5 s and 70.9 s; the same loop with those three self-tests moved last
and a stop at the first `AssertionError` took 11.5 s and 11.2 s, and still
found `test_m13_05_every_client_value_is_asked_about_before_use` failing, so
both cases still pass. (In the first case the full loop also counted the
self-test `test_m13_05_constructed_secret_edits_fail` as failing on the
mutant: noise that the early stop drops.)

These probes read `lab/addon/**`, `tests/addon/test_lab_addon.py`,
`wowlab_core.toc` (the grader's one import from the package) and three docs
the grader reads (`docs/LAB_PLAN.md`, `docs/LAB_FORMATS.md`,
`docs/handoffs/M11-03.md`); a doc change can only make more grader tests
fail, which cannot turn a probe red. Of the 320 commits on `main` since
2026-10-18, 2 touched `lab/addon/`, `tests/addon/` or `toc.py`.

Near-duplicates: 2 of the 634 test function names under `review/` also
exist in `lab/core/tests/` or `parser/`. A semantic duplicate check was not
done: the cheap probes are 5% of the time, so pruning them buys little, and
each was a real defect once.

### 1.7 Windows and macOS

| Run | Parallel pass | Result |
|---|---:|---|
| `lab (windows)` 44381681237 | 43.9 min | red; queued 28 min behind the previous `main` run before it started |
| `lab (windows)` 44394649745 | 33.7 min | red: 10 failed, 11,453 passed, 1,113 skipped |
| `lab (macos)` 44394649569 | 17.3 min (+ 3.1 serial) | green: 12,320 passed, 256 skipped |

Both desktop jobs run `lab/core/tests` only, which holds both expensive
groups. Every UI test and the L1 sweep start Python children; process
creation and the Python import are several times dearer on Windows than on
Linux, and Defender scans what each test writes. Not measured per test
there (no durations in the log), so the Windows estimates below assume the
Linux shares, which is conservative for the child-heavy UI tests.

`lab (windows)` has been red on `main` in every completed push and nightly
run since 2026-11-17 20:34 UTC (eight runs, the nightly of 2026-11-18 among
them; for example 44374159825, 44381681237, 44394649745): ten tests in
`lab/core/tests/test_l1_sweep_live.py`, among them
`test_sweep_on_a_captured_install_changes_nothing_constructed`,
`test_fingerprint_is_stable_and_hashes_only_what_it_should_constructed` and
`test_a_command_that_exits_badly_fails_the_sweep_constructed[3]` and
`[-9]`. That is a defect to file, outside this review; it also shows that a
per-merge desktop run is not being read within the day.

### 1.8 Triggers, 2026-11-11 to 2026-11-18

| Workflow | Event | Runs | Cancelled | Failed | Green |
|---|---|---:|---:|---:|---:|
| `ci.yml` | pull_request (150 PRs) | 356 | 126 | 23 | 199 |
| `ci.yml` | push to `main` | 145 | 26 | 3 | 116 |
| `windows.yml` | pull_request (until M18-15, 2026-11-17) | 210 | 92 | 28 | 83 |
| `windows.yml` | push to `main` | 145 | 50 | 22 | 73 |
| `windows.yml` | schedule | 8 | 2 | 4 | 2 |
| `windows.yml` | workflow_dispatch | 4 | 1 | 0 | 2 |
| `macos.yml` | push to `main` (since 2026-11-17) | 13 | 4 | 1 | 8 |
| `macos.yml` | schedule | 1 | 0 | 0 | 1 |

(Runs still in progress at the time of the query are counted in "Runs"
only.) That is 2.4 `ci.yml` runs per pull request, and 145 pushes to `main`
in the week; busy days have 20 to 44, each a full 29-minute Linux run and,
unless superseded while pending, a 34 to 44 minute Windows run.

What runs where today:

| Trigger | Linux suite (`quality`) | Parser job | Hooks job | Windows | macOS | App builds |
|---|---|---|---|---|---|---|
| Pull request | full, 28.8 min | `make test-parser`, 2.3 min: a subset of `quality` | `tests/harness` on 3.9 and 3.12; the 3.12 leg is a subset of `quality` | no | no | when `packaging/**`, `pyproject.toml`, `uv.lock` etc. change |
| Push to `main` | full again | again | again | full, 34 to 44 min | full, 21 min | no |
| Nightly | no | no | no | 07:17 UTC | 10:17 UTC | no |
| Tag `v*` | full again (`release.yml`, job `Lab suite`: 22.5 min for v0.3.0, run 44125134788, alongside the app builds; the whole run took 55 min) | inside the full run | no | no | no | build and smoke |

Redundancies: the parser job and the 3.12 hooks leg repeat tests the
`quality` job runs (runner minutes, not wall clock; they run alongside).
Every merged change runs the Linux suite at least twice, on the PR and on
`main`, and `ci.yml` never cancels a superseded `main` run
(`cancel-in-progress` is true only for pull requests), so in a burst of
merges every run that started finishes. `release.yml` runs the suite a third
time on a commit `main` already graded. The desktop workflows let a newer
push replace only the *pending* run. Because a concurrency group holds one
running and one pending run, `main` runs also wait for each other: run
44374160065 was created at 18:51:04 and its jobs started at 19:03:26, 12
minutes in the queue (41 minutes from push to result), and Windows run
44381681237 waited 28 minutes before it started.

### 1.9 Cron times

The owner is in America/Costa_Rica (UTC-6, no daylight time). The 307
`ci.yml` push runs on `main` that `gh run list` returned since 2026-10-20, by
local hour of the push:

| 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 17 | 14 | 5 | 5 | 4 | 2 | 2 | 4 | 13 | 12 | 9 | 12 | 23 | 21 | 12 | 23 | 11 | 9 | 25 | 17 | 11 | 8 | 4 | 13 |

The quiet window is 02:00 to 07:00 local (08:00 to 13:00 UTC).
`windows.yml` at 07:17 UTC is 01:17 local, the tail of late-evening work;
`macos.yml` at 10:17 UTC is 04:17 local, inside the window;
`perf-nightly.yml` at 04:17 UTC is 22:17 local, in the evening. The
workflows' comments reason in Pacific time ("clear of 6-9 AM Pacific").

### 1.10 Local runs per ticket

What the instructions ask today (`.claude/agents/implementer.md`, "Verify
before reporting"; `docs/AGENT_WORKFLOW.md`, "Local verification"):

| Role | Runs |
|---|---|
| implementer | `make lint`, the touched tests, `make test-fast`; the full `make ci` instead when the diff touches `guard.py`, `luadata.py`, any parser, `redact.py`, `lab_capture.py`, `conftest.py`, `pyproject.toml`, `Makefile`, `uv.lock`, the harness or a workflow |
| test-writer | `make test` and `make test-parser` |
| code-reviewer | `make ci` once, on the first round |
| pr-shepherd | `make ci` after a rebase with a textual conflict |

An ordinary ticket costs one `make test-fast` and one full `make ci` (the
reviewer's); a `guard`, parser or workflow ticket two full runs; a `[TEST]`
and `[IMPL]` pair three. `make ci` runs `make test` (both passes), then
`make test-parser` and `make hooks-test`, whose parser tests and 3.12 hook
tests `make test` has just run. `make test-fast` leaves out
`lab/core/tests/review/` (and with it the mutation probes) and the serial
pass, about 38% of the CPU time; with the UI tests in it, it is not the
"about 10 min" `AGENTS.md` gives it on a loaded 8-core machine.

## 2. What stays on every pull request in any option

Cheap: 6,509 tests and 540 of the 6,667 test-seconds (8.1%), about two
minutes on four workers.

- `lab/core/tests/test_guard*.py` (1,645 tests, 212 s), and every review
  probe that imports `wowlab_core.guard`: L2, ADR-0021.
- `lab/core/tests/test_process.py` (118 tests, 1 s): L7.
- `lab/core/tests/parser/` (3,538 tests, 207 s), the luadata and WTF
  round-trips included: L3, L4, L8. It is also the required "Parser fixtures
  and round-trip" check.
- `tests/scripts/test_lab_capture*.py` (811 tests, 19 s): the scrub.
- The constructed tests of `lab/core/tests/test_l1_sweep_live.py` (27 tests,
  69 s): L1. The `@live` sweep stays a hand run before a release.
- `lab/core/tests/test_write_sites.py` (108 tests, 3 s) and `tests/repo/`
  (157 tests, 29 s): the one write gate, no FFI, workflow rules, bidi.
- `lab/core/tests/test_gamedata*.py` (105 tests, 2 s): L5 and replayed
  fixtures instead of the network.
- The graders of the load-bearing files and the tests of
  `scripts/check_grader_diff.py` (ADR-0013).

## 3. Safe now: pure speedups, no owner decision

Each keeps every assertion it touches. Savings are for the Linux parallel
pass (25.3 min), from the shares above.

- **S1. Stop the mutation probes at the first failing grader test, and run
  the grader's three self-tests last.** In the 17 files of 1.6,
  `_failing_tests` returns as soon as one grader function raises
  `AssertionError`, with `test_m11_37_a_call_added_outside_a_section_fails`,
  `test_m11_37_a_call_added_inside_a_section_passes` and
  `test_m13_05_constructed_secret_edits_fail` sorted to the end. Each probe
  asserts only that the list is non-empty, so no outcome changes; the
  measured case went from 70.5 s to 11.5 s. **Saving: about 84% of 33%, some
  7 minutes of the 25 on Linux and 9 to 12 of the 34 to 44 on Windows.** These are
  `code-reviewer`'s files; the edit is test-only (the conductor's XS lane or
  a ticket), and the grader itself, `tests/addon/test_lab_addon.py`, is not
  touched.
- **S2. Build constructed CASC root files in a `bytearray`.** `root_bytes`
  (`lab/core/tests/parser/_casc_build.py:306`) appends to a `bytes` object
  with `out += ...`, copying the whole buffer each time: quadratic.
  `test_too_many_blocks_with_rows_are_refused_constructed`
  (`parser/test_casc_source_constructed.py:964`) builds 65,536 and 65,537
  blocks with it, about 40 s, of which `parse_root` is about 1.3 s.
  Measured: 65,537 appends of 41 bytes take 19.4 s with `bytes +=` and
  0.013 s with `bytearray +=`; `return bytes(out)` keeps the type. **Saving:
  about 39 CPU-seconds and the slowest parser test.** The other builders
  (`_blp_build.py`, `_m2_build.py`) are worth the same grep.
- **S3. `make ci` stops running tests twice.** Its `test-parser` stage
  repeats parser tests that `test` has just run, and `hooks-test` repeats
  `tests/harness` under 3.12. Keep `make test-parser` as a target (CI's
  required parser job calls it, and so does a parser change's local check)
  and the 3.9 leg of `hooks-test`; drop the repeats from `CI_STAGES`.
  Measured here: the repeat of `make test-parser` is 3,467 tests and 135 s
  of user CPU in one process (CI's `make test-parser` step, the same
  command with its `uv sync`, takes 2.1 min; 15 min on this loaded machine);
  the 3.12 repeat of `tests/harness` is 220 tests and 10 s. **Saving: about
  two minutes per local `make ci`, more on a loaded machine.**
  A Makefile change, so a ticket, but nothing runs fewer than once.
- **S4. Cancel superseded `ci.yml` runs on `main`.** `cancel-in-progress`
  true for pushes to `main` as well. The newest commit on `main` contains
  every older one, so grading it is enough; a break in an older commit is
  still red on the newer run, and each change's PR run already graded it.
  **Saving: one 29-minute Linux run per merge that lands while another is
  running; on a busy day, most of them.** It touches `.github/`, so
  `security-reviewer` reads it.
- **S5. Correct the text.** The comments in `macos.yml` and `windows.yml`
  reason in Pacific time. `AGENTS.md` (Verification) says `make test-fast`
  takes "about 10 min"; that file is the owner's to edit.

Looks safe, is not: `--dist loadfile` or `loadgroup` instead of
`worksteal` (the pass is CPU-bound; grouping by file lengthens the tail);
more workers than cores on CI (the children use CPU; one
`workflow_dispatch` run with `-n 6` would settle it, nothing more); a
module-scoped UI `install` fixture (the tests write into it).

## 4. Options that need the owner's choice

Ranked by minutes saved per risk taken. "Gap" is what stops being caught on
the pull request; "noticed" is when it would be caught instead.

| # | Option | Saving | Gap | Noticed |
|---|---|---|---|---|
| O1 | **The 17 mutation probes run only when `lab/addon/**`, `tests/addon/**`, `lab/core/src/wowlab_core/toc.py`, the probes themselves, `conftest.py`, `pyproject.toml` or `uv.lock` change**, and in the nightly full run. A marker set by node id in `conftest.py`, as `serial` is, and a second filter in the `changes` job. | PR: what is left of their cost after S1, about 1.3 min (8 min without S1) | A change outside those paths that weakens the addon grader. The grader and the addon are in the filter, so there is none known. | Next night |
| O2 | **Desktop suites nightly and on demand only**, not after every push to `main`. | Runner time only: 34 to 44 min of Windows and 21 of macOS per merge; no PR wait changes | A Windows- or macOS-only break merged by day shows the next night, not within the hour. Today it is not read within the day anyway (1.7). | Next night (GitHub emails the owner); before a release |
| O3 | **Serial pass nightly.** `test_guard_pins_m10_16.py::test_constructed_pin_a_forked_child_leaving_the_with_neither_unlocks_nor_commits` stays on the PR in a step of its own: it is serial because of `os.fork`, not a budget, and it guards L2. | PR: 3.1 min | A CPU-time or RSS budget regression (a quadratic parse, a memory blow-up on a hostile file) merges; every correctness test still runs. | Next night; `perf-nightly.yml` also times five commands |
| O4 | **The CLI imports a command group's module when that command runs**, not all of them at `import wowlab_core.cli`. A product change, so a ticket; `test_cli_package.py` (M16-111) requires every name of every `cli` module on the package, so the package would re-export lazily (a module `__getattr__`) and that test's monkeypatch rule needs a second look. | Each child process: `python -P -m wowlab_core --version` costs 0.66 s of CPU against 0.02 s for a bare interpreter; of that, importing pydantic, typer and httpx is about 0.1 s and wowlab's own modules about 0.37 s (`addoncheck` and what it pulls in are the largest), which a command that needs two of them would mostly skip. Estimate, not measured: up to 0.37 s x 1,906 children, about 700 CPU-seconds or 10% of the suite, some 2.5 min on Linux CI and more on Windows. | None: a speedup players feel in `wowlab ui` too | n/a |
| O5 | **A fast PR tier and a full tier.** PR: lint, mypy, Lua lint, the parallel pass without `lab/core/tests/review/` except the probes that import `guard`, `luadata`, `redact` or `proposal`, and §2. Nightly and in `release.yml`: everything. | PR: after S1, about 1.3 min more | A regression only a review probe catches. Review probes exist because the regular suite missed something once. | Next night |
| O6 | **UI tests by changed path**: `test_ui_*` only when `lab/core/src/**`, the UI tests, `pyproject.toml` or `uv.lock` change. | Large, but only on PRs that leave `lab/core/src` alone (docs with tests, scripts, workflows, the addon): 142 of the last 320 commits touched `lab/core/src` | None for those PRs: the UI tests read nothing else | n/a |
| O7 | **The Linux suite nightly only, not on PRs.** | The whole 25 min | Every regression merges, and is found the next morning after other merges built on it; `code-reviewer` becomes the only gate. | Next night |

How test directories map to sources, for O1 and O6 and for agents choosing
local tests:

| Source changed | Tests that read it |
|---|---|
| `lab/addon/**` | `tests/addon/`, the 17 mutation probes, `lab/core/tests/test_labaddon*`, `parser/test_labaddon*` |
| `lab/core/src/wowlab_core/ui/**` | `lab/core/tests/test_ui_*`, the UI review probes (`review/*ui*`, `review/test_cr5*`), `test_l1_sweep_live.py` |
| `lab/core/src/wowlab_core/cli/**` | everything under `lab/core/tests/` that runs a command (`test_cli*`, `test_ui_*`, `test_doc_examples.py`, `test_l1_sweep_live.py`), `tests/scripts/test_gen_command_reference.py`, `tests/scripts/test_release.py` |
| a parser module | `parser/test_<module>*`, `test_<module>*` |
| `guard.py`, `snapshot.py` | `test_guard*`, `test_snap*`, the review probes that import `guard`; in practice the full suite (AGENTS.md) |
| `scripts/lab_capture.py`, `redact.py` | `tests/scripts/test_lab_capture*`, `test_cli_doctor_redact.py` |
| `.claude/hooks/**` | `tests/harness/`, `tests/review/` |
| workflows, `Makefile`, `conftest.py` | `tests/repo/` |
| `packaging/**` | `tests/packaging/` and the app workflows |

A selector finer than this (per module, from imports) misses tests that reach
a module through a helper or through the CLI; the implementer's import grep
has that hole today and says so.

### Local verification

Recommended (it changes `.claude/agents/implementer.md`, `/ship`,
`.claude/agents/code-reviewer.md` and `docs/AGENT_WORKFLOW.md`):

- **Default:** `make lint`, the touched tests as today, and the always-run
  set (`tests/repo`, `test_write_sites.py`).
- **`make test-fast`** only when the diff touches `lab/core/src/**` or
  `lab/core/tests/**`.
- **The full `make ci`** only for `guard.py`, `luadata.py` and `proposal.py`
  (AGENTS.md), and for `conftest.py`, `pyproject.toml`, `uv.lock` and the
  `Makefile`. A workflow change gains nothing from a local suite run.
- **code-reviewer:** `make lint`, the branch's touched tests, the tests of
  §2 and its own probes on the first round; the full `make ci` only for the
  files above. CI runs the whole suite on the same commit. This is the
  largest local saving: one full run per review today.

## 5. Recommended package

S1 to S5 now; then O1, O2, O3, O4 and the local-verification change. O5 and
O7 not taken. O6 only if, after O4, the UI tests are still most of the time.

| | Today | With the package |
|---|---|---|
| Pull request, `Lint, typecheck, tests` | 28.8 min (25.3 parallel + 3.1 serial) | about 17 min: parallel 25.3 x (1 - 0.331 - 0.006) = 16.8 min, the serial step down to one fork test; O4, once built, about 14 to 15 (estimate) |
| Push to `main` | Linux 29 min, Windows 34 to 44, macOS 21; nothing cancelled | Linux about 17 min, superseded runs cancelled; no desktop runs |
| Nightly | Windows and macOS full; perf 1.5 min | at 03:17 local: the Linux full tier (a `schedule` trigger on `ci.yml` or a new workflow; with the mutation probes and the serial pass, about 21 min after S1), Windows (about 25 to 32 min after S1) and macOS (about 15); perf before them |
| Release tag | Linux full again | unchanged (a tag is rare; cheap insurance) |
| Local, ordinary ticket | `make test-fast` + the reviewer's `make ci` | touched tests, `make test-fast` when `lab/core/src` changed; no full run by the reviewer |

Cron: `windows.yml`, `macos.yml` and the new nightly Linux full tier at
09:17 UTC (03:17 in Costa Rica; GitHub can delay a scheduled run 30 to 60
minutes at busy times, which still lands before 05:00 local), and
`perf-nightly.yml` at 08:47 UTC so it gets an idle runner first. The
comments say Costa Rica, not Pacific.

## 6. Questions for the owner

1. **Mutation probes off the PR when no addon file changed (O1)?**
   Recommended: yes. They still run nightly and on every PR that touches the
   addon or its grader.
2. **Windows and macOS suites nightly and on demand only, not after every
   merge (O2)?** Recommended: yes, with an on-demand branch run for tickets
   that touch path handling, `guard`, `snapshot` or `process`, and the
   nightly at 03:17 local.
3. **Budget (serial) tests nightly instead of on every PR (O3)?**
   Recommended: yes, keeping the `os.fork` guard test on the PR.
4. **Reviewers stop running the full `make ci` locally, and implementers run
   `make test-fast` only when `lab/core/src` or its tests changed?**
   Recommended: yes, with the full local run kept for `guard.py`,
   `luadata.py`, `proposal.py`, `conftest.py`, `pyproject.toml`, `uv.lock`
   and the `Makefile`.
