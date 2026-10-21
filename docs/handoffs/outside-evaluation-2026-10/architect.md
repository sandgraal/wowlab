# Python architect

## Verdict

I would recommend wowlab's core to a technical WoW player who wants an
auditable, local tool, and I would trust its write path on a small install.
I would not yet recommend the browser write path on a large one. The library
layer is disciplined: one write gate, typed errors, 0/1/2/3 exit codes that
hold up under probing, `--json` on every data command, five direct runtime
dependencies, and lint and `mypy --strict` clean. Scale is the weak point,
and the code shows it in two places. First, the write gate checks each path
by listing every directory on the way, with no memo, so the cost is files ×
siblings. A whole `snap restore` that finds **nothing to change** takes 42 s
on a constructed 402-addon / 8,430-file install, and a restore of 400 changed
files takes 91 s. The UI kills a confirmed run at 120 s. Second, the
presentation layer is a 9,977-line `cli.py`. It holds policy as well as
output, Wave 6 already has to queue tickets behind it, and the UI talks to it
by spawning subprocesses and reading stderr text. **The single biggest
problem is the quadratic gate walk (F1).** It is cheap to fix, and it decides
whether a real install, which is bigger than any fixture, can use restore,
undo and profile apply from the UI at all.

## What works

- **Exit-code contract holds.** Probed: guard refusals (`--paths Data/foo`, `../../etc/passwd`) exit 3, unknown flavor and missing argument exit 2, other errors exit 1 (`cli.py:168-171`, `_handled` at `cli.py:782`).
- **`--json` is everywhere it should be.** I checked 26 data commands and all of them have it. `_safe`/`_note` (`cli.py:376-455`) escape terminal controls and bidi characters by construction, not ad hoc.
- **Small dependency footprint.** The direct dependencies are `pydantic`, `typer`, `httpx`, `platformdirs` and `psutil`, matching LAB_PLAN §6. The build backend is pinned with hashes (`lab/core/build-constraints.txt`).
- **`make lint` passes**, in 12.8 s: ruff check passes, 500 files are formatted, mypy `--strict` reports no issues in 47 source files, and the hooks pass mypy under 3.10.
- **Hot data stays out of Pydantic.** `luadata` keeps tuples and builds models only at the CLI boundary (LAB_PLAN §6.4 end). `casc` keeps the index as raw bytes with `bisect` (`casc.py:509-530`). Both are the right pattern for Wave 6.
- **Library-level inventory meets its target.** `layout.layouts(inst)[0].addons()` covers 402 addons with 20 files each in **0.78 s**, against the "400 addons under 2 s" target in LAB_PLAN §6.1. `snap create` of the same 8.4k files takes 2.1 s.
- **luadata on this machine (Xeon, 2.8 GHz).** The constructed 50 MiB "auction" file parses in 9.37 s with 490 MiB peak, inside the §6.4 target of 10 s and 1.5 GB.

## Findings

### F1 — The write gate's path walk is O(files × siblings); a no-op whole restore takes 42 s at 400 addons
- Severity: major
- Area: `guard` (load-bearing), `snap restore`, `undo`, `profile apply`, the UI confirm flow
- Evidence: `guard._entry` (`guard.py:1188-1218`) runs `{p.name for p in longpath.iterdir(directory)}` for **every path component of every file**, and `_look` (`guard.py:1221`) calls it once per component. So `Interface/AddOns/` (402 entries) is listed again for each of the 8,430 files. All inputs below are constructed sandboxes: real fixtures, plus N copies of the two DBM addons with 20 filler `.lua` files each. The script is `$EVAL/work/architect/timeit.py`.
  ```
  bash $EVAL/make_sandbox.sh architect-perf   # then add 400 addon copies x 21 files
  wowlab snap create -m one
  wowlab snap restore <id> --dry-run
  ```
  | addons / files | `restore --dry-run`, nothing to change |
  |---|---|
  | 100 / 2,130 | 5.7 s |
  | 402 / 8,430 | 42.2 s (unloaded), the same for `--yes` with nothing to restore: 43.5 s |

  cProfile of the 8,430-file dry-run: 33,751 `_entry` calls, 3.6 M `Path` objects built by `longpath.iterdir`, and 117 of the 121 profiled seconds spent under `guard._look`. With one file changed in each of the 400 addons, re-measured unloaded: `snap restore <id> --dry-run` took 39.0 s, `snap restore <id> --yes` took **91.3 s**, and `undo --yes` took 12.5 s. Under `make test` load the same restore took 111.7 s.
- Impact: Every player with a normal addon folder is affected. 300 addons × about 50 files each is common, and that input is larger than my constructed one. The UI kills a confirmed run at `DEFAULT_TIMEOUT_S = 120.0` (`ui/__init__.py:153`) and leaves a half-applied change (`_KILLED_NOTE`, `ui/__init__.py:933`). The recovery path, `undo` through the same gate, has the same cost profile. LAB_PLAN §13.3 already caps `profile apply` at 2,000 deletes for this reason ("the gate re-walks the install per delete; a gate-side listing cache is a follow-up"). `snap restore` has no cap.
- Proposal: Add a per-transaction listing memo inside `guard`, keyed by directory path plus the identity the chain already records (`_identity(st)`: dev, ino, mtime_ns), and invalidated on any write the transaction makes in that directory. `_verify` keeps re-checking identities at commit, so the TOCTOU defence is unchanged. Add a §6.10 performance target, for example "a whole restore check of 20,000 files in ≤ 5 s", and a `serial` benchmark test. Because guard is load-bearing this is a `[TEST]` S ticket (the timing grader and identity-change cases) followed by an `[IMPL]` S ticket. **Before the next release**, since this release puts restore and undo in the browser.
- Status: new (LAB_PLAN §13.3 names it as an unticketed follow-up for `profile apply` only)

### F2 — The UI↔CLI plan protocol for `undo` and `sv merge` is stderr text plus a sentinel line
- Severity: major
- Area: `cli._confirm`, `CONFIRM_DECLINED`, `EXPECT_FIELDS` (`cli.py:745-779`); `ui/__init__.py:399-405`
- Evidence: `undo` and `sv merge` have no `--dry-run` (`wowlab undo --help`, `wowlab sv merge --help`). The UI gets their plan by running them with stdin closed and reading stderr up to the literal line "Nothing was changed." (`cli.py:745-750`). The other five writing commands have `--dry-run` but no `--expect`, so the README says that they "apply what is there when you confirm". This text protocol has already cost: M13-75 (25 Windows failures, because the declined line came back CRLF) and a review grader that exists only to stop SavedVariables content from forging the marker (`lab/core/tests/review/test_m14_03_declined_marker_not_forged_by_sv_content.py`).
- Impact: The contract is fragile and has two shapes. Every new writing command (Wave 6 adds none, but Wave 7 and macros fix will) has to pick a shape, and the UI has to special-case it. Security reviewers keep re-deriving the forgery argument.
- Proposal: Give every writing command one plan/apply contract. `--dry-run --json` returns a `Plan` model that carries a `plan_digest` (SHA-256 over the ordered operations and the pre-image hashes guard already computes). `--yes --expect DIGEST` exits 3 unless the plan recomputes to the same digest. The UI reads only JSON, `CONFIRM_DECLINED` parsing goes away, and the five commands that now "apply what is there" gain the same pin. Cost M: cli and ui, with guard unchanged if the digest is computed from the plan the CLI already builds. **Before the next release.**
- Status: new (M13-74 widens one pin, `sv merge`'s; this generalises it)

### F3 — `sv dump --json` is 14× slower than the parse, and a one-line change halves it
- Severity: major
- Area: `sv dump`, `cli._stream_dump` (`cli.py:3415-3429`), `_map_strings` (`cli.py:720`)
- Evidence: Input is a constructed, Forever-layout, 10 MiB file of 68,751 auction-like records (`$EVAL/work/architect`; the file is in the `architect-perf` sandbox).
  ```
  library luadata.parse:            1.61 s
  wowlab sv dump FILE:              8.85 s, 15 MB out
  wowlab sv dump FILE --json:      23.23 s, 77,868,483 bytes out (7.4x the input)
  ```
  The profile shows `json/encoder.py:_iterencode_dict` (the pure-Python encoder) and `_map_strings` at 5.6 M calls. The cause: `encoder.iterencode(...)` never uses CPython's C encoder, which only runs for one-shot `encode()`. Replacing that loop with `out.write(encoder.encode(_map_strings(row, _json_text)))` gave **byte-identical output in 8.8 s instead of 20.0 s**, in-process and unloaded (`$EVAL/work/architect/dumpfix.py`).
- Impact: The §6.4 target (50 MB in under 10 s) is met by the parser, but no user sees the parser on its own. Extrapolated, `sv dump --json` of a 50 MB auction file is about 2 minutes and 390 MB of output. The UI caps output at 16 MiB (`ui/__init__.py:156`), so a Preview of any SavedVariables file over about 2 MB is cut off.
- Proposal: Make the one-line encoder change (XS). Skip `_map_strings` for strings without a surrogate. Move the §6.4 target to the command (`sv dump --json` end to end). Add a leaner JSON view, for example values without the per-leaf typed node, for the UI. **Before the next release** (the XS part).
- Status: new

### F4 — `cli.py` is a 10k-line serialization point that also holds policy
- Severity: major
- Area: `lab/core/src/wowlab_core/cli.py`; Wave 6 scheduling
- Evidence: The file is 9,977 lines and 401 KB (about 111k tokens). It has 47 commands, 87 `_Out` report models and 310 functions. The largest are `sv_merge` (280 lines), `_loader_check` (194), `snap_restore` (193) and `explain` (172). Whole-restore policy (screenshots, executables, Edit-`no` skips, restoring by name) lives in `cli.py:6060-6540`, not in a library module. 24 test files monkeypatch `cli` internals, and 136 distinct `cli.*` names are used from tests. LAB_PLAN §17.9 says: "M15-01, M15-06 and M15-11 all edit `cli.py` and are dispatched so that no two are in flight at once". The section seams already exist (`# ─── doctor ───`, `# ─── snap ───`, and so on at `cli.py:1049…9954`).
- Impact: Parallel dispatch, the constitution's default, breaks down at every user-facing ticket. No agent on the standard window can read the file whole. A non-CLI caller (the UI, a future library user) cannot get restore's skip policy without shelling out.
- Proposal: (a) Turn `cli.py` into a `wowlab_core/cli/` package, one module per existing section, plus `_app.py` (app, options, `_handled`, `_safe`, `_emit`) and `_target.py` (install, flavor, account and character resolution). `cli/__init__.py` re-exports `app`, `CONFIRM_DECLINED` and `EXPECT_FIELDS`, and the tests are re-pointed. This is one mechanical M ticket with no behaviour change, and each report model moves with its command. (b) Move the restore planning policy into `wowlab_core/restoreplan.py` (M), which the CLI only renders. Do (a) **before M15-06**, so that M15-06 and M15-11 stop serialising, and (b) later.
- Status: new

### F5 — No common error base; `--json` errors are text only
- Severity: minor
- Area: `cli._handled` (`cli.py:782-814`), every module's errors
- Evidence: `_handled` lists 10 exception families by name. `casc.CascError` (`casc.py:223`), `db2lake.LakeError` (`db2lake.py:206`) and the `ValueError` subclasses (`ItemLinkError`, `SitesError`, `CostTableError`, `LabAddonError`) are not among them. Each new module has to remember to add its error, and M15-06 is the next one that must. With `--json`, an error is one stderr line and exit 1: `wowlab snap show zzz --json` prints `wowlab: not a snapshot id or prefix: 'zzz'` and nothing on stdout.
- Impact: A missed registration shows the user a traceback. Escaped and exit 1, but a traceback. A machine consumer, the UI included, can only tell errors apart by exit code and by parsing English.
- Proposal: Add `wowlab_core.errors.WowlabError(Exception)` with a class attribute `exit_code`, have module errors subclass it, and let `_handled` catch one class. Under `--json`, print an `ErrorReport {kind, message, exit_code, notes}` on stdout. Cost S. Before M15-06.
- Status: new

### F6 — The `--json` contract is unversioned, has no published schema, and leaks parser internals
- Severity: minor
- Area: every `--json` report; `addons list --json`
- Evidence: No report carries a schema or version field (`grep schema_version` finds nothing in `cli.py`), and the command reference names models but publishes no JSON Schema (`scripts/gen_command_reference.py:57-115`). `addons list --json` embeds the whole lossless TOC document, `tocs[].document.lines[].raw` as base64: 40,301 bytes for 2 addons and **8,093,701 bytes for 402 addons**, against 29,943 bytes of text output.
- Impact: Any refactor of `TocDocument` silently breaks consumers. At about 800 addons, `addons list` exceeds the UI's 16 MiB cap.
- Proposal: Add `"schema": "wowlab.<Report>/<n>"` to each top-level report. Publish `model_json_schema()` per command beside the command reference. State a stability rule: additive within a minor release. Keep parser documents out of summary reports, behind `--json --full` or a separate `addons show NAME`. Cost S.
- Status: new (M13-72 is the same class of problem for `char list`)

### F7 — Wave 6: the render plan has no table-access layer, and `db2lake` sits unused
- Severity: major
- Area: M15-09, M15-13 (`renderplan`); `gamedata.rows` (`gamedata.py:857`); `db2lake.py`
- Evidence: §17.4 joins about twenty tables (`ChrRaceXChrModel` → `ChrModel` → `CreatureDisplayInfo` → …, `ItemBonus` → `ItemModifiedAppearance` → … with fallbacks). The only table API is `GameData.rows(name, build) -> Iterator[dict[str, str]]`, which does "No type coercion" and has no index. `db2lake.py` (1,132 lines, typed SQLite per build, ADR-0028, merged as #158 per `docs/handoffs/M12-backlog.md:78`) is imported by no module (import map: `db2lake <-` nothing). §17 never mentions it.
- Impact: Without a decision, M15-09 and M15-13 will each write ad-hoc `dict` indexes over string columns, the parse-int-everywhere code will be duplicated, and a third table-access style will appear beside db2lake's.
- Proposal: Decide now, as a §17.4 amendment. Either renderplan reads through `db2lake` (typed, indexed, L5-keyed; its CSV source is still `gamedata`), or a small `gamedata.Table` helper (typed columns from WoWDBDefs, `by(column)` indexes) is the one API, and db2lake is removed from the wheel until a wave uses it. Also state in §17 that geometry and pixels cross module boundaries as `bytes`/`array`/`memoryview` inside a Pydantic metadata model. A 226,301-vertex M2 as per-vertex models would blow the time and memory targets, and AGENTS.md's "Pydantic for anything that crosses a module boundary" reads as requiring it. XS (docs) plus S.
- Status: new

### F8 — Wave 6: the compositor's 3 s target is at risk in pure Python
- Severity: minor
- Area: M15-10, LAB_PLAN §17.5
- Evidence: A constructed micro-benchmark, `$EVAL/work/architect/blendbench.py`, on a full 2048×1024 RGBA canvas on this machine:
  ```
  opaque slice copy:            0.004 s
  multiply (per byte, listcomp): 0.953 s
  alpha-over, per-pixel alpha:   2.120 s
  ```
  Only opaque alpha-over is a byte-slice copy, which is §17.5's "rows as byte slices". The body texture stacks the skin, face, underwear and up to nine item pieces with real alpha, plus multiply, overlay and screen layers.
- Impact: About 1.5 canvas-equivalents of per-pixel blending already reach 3 s. A miss is a stop-and-ask (ADR-0020), which can stall the wave at M15-10, the second-to-last ticket.
- Proposal: Put a per-mode cost model in §17.5 now, and plan the pure-Python techniques up front: per-section blending only, `bytes.translate` lookup tables for constant-alpha layers, and 256×256 precomputed multiply and screen tables applied per row. Or relax the target to "≤ 3 s per canvas-equivalent of blended area". XS docs.
- Status: new

### F9 — Performance targets are library-level, owner-laptop-only, and not regression-tested
- Severity: minor
- Area: LAB_PLAN §6.1, §6.4, §17.1-§17.5; CI
- Evidence: §6.4 says "on the owner's laptop". On this Xeon, the 50 MiB "collection" shape parses in **11.65 s** and misses the 10 s target, while the auction shape takes 9.37 s (`python -I lab/core/tests/luadata_bench_constructed.py --shape collection`). User-visible costs no target covers: `sv dump --json` (F3), the restore check (F1), and the 0.66 s import floor (F10). The benchmark is "run by hand". CI has `serial` RSS and CPU tests but no throughput guard on these paths.
- Impact: Windows players on older laptops (the untested platform) will miss targets set on an M1. Regressions like F1 and F3 were not caught.
- Proposal: Add a §6.12 "performance budget" table with **command-level** targets on a reference CI runner, expressed as ratios to a calibration loop so they survive runner noise. Add a nightly `bench` job (not on the PR path) that fails at 1.5× the baseline. Cost S.
- Status: new

### F10 — Every invocation imports the whole package (0.66 s), and the UI spawns one per action
- Severity: minor
- Area: `cli.py` imports (`cli.py:127-158`), `ui/__init__.py:1515-1517`
- Evidence: `python -c 'import wowlab_core.cli'` takes 0.66 s. `-X importtime` attributes 104 ms self to `labaddon`, 83 ms to `cli` and 43 ms to `svmerge`, and typer, pydantic and httpx together take 0.18 s. `wowlab --version` takes 0.73 s, and `addons list` on 402 addons takes 1.51 s end to end against 0.78 s in the library. The UI runs `[sys.executable, "-P", "-m", "wowlab_core", ...]` per Preview and Commands action.
- Impact: Every UI click has a floor of about 0.7 s, and the plus-confirm flow pays it twice. Wave 7's viewer will run `char model` (CASC open: 1.9 s in the spike) the same way.
- Proposal: After F4, import a command group's modules inside its functions, so `httpx` loads only for `db2`, `doctor` and `looks` and `labaddon` only for `char`. Target `wowlab --version` under 0.25 s. For Wave 7, consider a build-keyed derived index cache under the data directory (allowed by L1 and L5, never in the install) so `casc` does not re-parse `.idx`, encoding and root files on every process. Cost S.
- Status: new

### F11 — Released wheel dependencies float, and only 3.12 against the lockfile is tested
- Severity: minor
- Area: `lab/core/pyproject.toml:7-13`, `.github/workflows/ci.yml:155-186`, `release.yml:150-183`
- Evidence: Runtime requirements are `pydantic>=2.12`, `httpx>=0.28`, `psutil>=7.0` and `platformdirs>=4.3`, with no upper bound, and `requires-python = ">=3.12"`. CI runs `uv sync --frozen` on 3.12 only. The release `smoke` job resolves fresh dependencies once, at release time. `typer` is bounded `<0.28` because `cli.py:123-124` imports the private `typer._click`, and it also brings `rich`, `markdown-it-py` and `pygments`, which `rich_markup_mode=None` never uses.
- Impact: A player who installs v0.1.0 next month gets whatever httpx 1.x or pydantic 3 exists by then, or Python 3.13/3.14 if they drop `--python 3.12`, and nobody has tested that combination.
- Proposal: Add upper bounds on majors (`pydantic<3`, `httpx<1`, `psutil<8`). Add a weekly scheduled job that installs the latest released wheel fresh (`uv tool install`) on 3.12 and 3.13 and runs `--version`, `doctor --offline` and `tree` on a fixture install. Either add 3.13 to CI or declare `<3.13`. Evaluate `typer-slim` to drop rich (**[verify]** that it is still published for 0.27). XS–S. **Before the next release** (the bounds).
- Status: new

### F12 — The gate's rules are copied rather than shared, and safe-read primitives live in `snapshot`
- Severity: minor
- Area: `verdict.py:92-176`, `filemap.py:73-80`, `install.py:69-70`, `firstrun.py:662,723,787`, `pathfacts.py:244`, `doctorredact.py:412`
- Evidence: `verdict.py` and `filemap.py` carry hand copies of `guard._check_rel`, `guard._is_executable` and `guard._is_link`. Their comment says the reason: "`guard.py` is load-bearing; its private rule is copied here rather than imported". Drift tests guard them. `install.py:70` imports `snapshot._is_link_stat`, `firstrun` uses `snapshot._is_link_like_dir`, `doctorredact` uses `guard._JournalRecord`, and `install`, `casc`, `guard` and `pathfacts` read through `snapshot.open_regular_file`/`read_regular_file`. So `install`, a leaf in LAB_PLAN §5, depends on the snapshot store.
- Impact: The process rule protecting load-bearing files now produces duplicate logic. Every rule change is three edits plus a drift test. Wave 6's `casc` adds one more consumer of `snapshot` internals.
- Proposal: Add a leaf module `wowlab_core/fsrules.py` holding the link, executable and relative-path rules and the bounded read-only opens. `guard`, `snapshot`, `verdict`, `filemap` and `casc` import it. It is a `[TEST]`/`[IMPL]` pair, because `guard`'s behaviour must be graded unchanged. Cost S+S. Later (after the next release).
- Status: new

### F13 — The spec reads as a changelog, and the context-window rule rests on a stale number
- Severity: minor
- Area: `docs/LAB_PLAN.md`, `CLAUDE.md` ("Models")
- Evidence: `wc -w docs/*.md` gives **119,260 words** (about 160k tokens), and `LAB_PLAN.md` alone has 45,276 words, 68 `*Amended` blocks and 112 `[verify]` marks. CLAUDE.md says "All of `docs/` is under 40K tokens, … so the standard window fits". §6.11's CLI spec is one paragraph of contract followed by pages of dated amendments, and the exit-code and `--json` rules are a single sentence inside it.
- Impact: "Spec-first" review has to replay amendments to learn the current rule. The model-selection argument for agents is based on a number that is off by about 4×.
- Proposal: At each wave review, fold amendments into the body and move the history to `docs/handoffs/`. Split LAB_PLAN into `docs/spec/<module>.md`. Give the CLI contract (exit codes, `--json`, plan and expect, errors) its own short section that F2, F5 and F6 amend. Correct the CLAUDE.md figure (XS, conductor or owner). Folding is M, docs only.
- Status: new

### F14 — Later waves: read the DB2 tables from the install, not from wago.tools
- Severity: idea
- Area: `gamedata`, `casc`, ADR-0022/0033
- Evidence: §17.4 notes that `ChrCustomizationCondModel` "is not published for this build (HTTP 400) and is skipped", and the render plan needs about 23 MB of CSV per build from wago.tools, so `char model` cannot run offline on a fresh build. Once M15-05 lands, the install's own DB2 files are readable through `CascSource.read`.
- Impact: The character render is offline-incapable and depends on a community site's coverage for the player's exact beta build.
- Proposal: A `db2` (WDC) reader over `casc` as a second `gamedata.Source`, with build keying unchanged (L5). This needs an ADR-0033 amendment (a new asset format with its three-layer grading). Hotfixes in `DBCache.bin` stay out (ADR-0033 decision 1), so local tables can lag hotfixed values. Encryption of some tables is **[verify]**. Wave 7 or later.
- Status: new (in-ideas: `hotfix-reader` is adjacent)

## Top 3 changes

1. **F1:** a listing memo in `guard` plus a restore-scale target. It is two S tickets, it is the only finding that can leave a player's install half-restored through the UI's 120 s kill, and it removes the reason behind the 2,000-file cap on `profile apply`.
2. **F2 + F5:** one machine contract for writes and errors: a `--dry-run --json` plan with a digest and `--expect` on every writing command, a common error base, and a JSON error report. This retires the stderr sentinel, its forgery grader and its CRLF class of bugs before the UI's write path ships wider.
3. **F4(a), with F7 decided at the same time:** split `cli.py` into a package before M15-06, so Wave 6's three CLI tickets can run in parallel, and settle the render plan's table layer (db2lake or one typed helper) before M15-09 starts.

## Not tested

- The UI in a browser. Other seats cover it. I read the UI's subprocess, timeout and cap code and did not drive it.
- A real large install. All scale inputs are constructed copies of the two fixture addons with filler `.lua` files (labelled above). Real addon trees are deeper, which makes F1's per-component listing cost higher, not lower.
- Windows and macOS. All timings come from this Linux Xeon box (4 cores, unloaded unless a finding says otherwise).
- The network paths (`doctor` without `--offline`, `db2`, `looks`), per the brief.
- Wave 6 code beyond M15-03 and M15-04 (`casc.py` index and BLTE), which is not written yet. F7 and F8 are design risks backed by micro-benchmarks, not defects.
- `make test` result: 9,161 passed, 275 skipped, 1 xfailed and **1 failed**, in 21 min 2 s on 4 workers. The failure is `tests/repo/test_wiki_daily_outside.py::test_constructed_the_last_step_removes_git_and_the_venv_and_runs_nothing[read-only-as-user-fails]`. It expects a non-root user, and this box runs as uid 0, so a read-only `.git` is still removed. That is an environment artifact, not a product bug. The test could skip when `os.geteuid() == 0`. The 21-minute local run is what M13-81 (`make test-fast`) already targets.
