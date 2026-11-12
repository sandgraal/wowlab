# M16 status and plan: stopping point of 2026-11-12

Written by the conductor on 2026-11-12, for the next session that runs
`/conduct next`. It replaces the 2026-10-21 version of this file. Read it
first, then `AGENTS.md`, `docs/AGENT_WORKFLOW.md`, and the M16 section of
`docs/BACKLOG.md`.

## Where things stand

`main` is at 737399d or later. The repository has no open PR except #379
(M16-45), which waits on the owner. No agent is running, and no scheduled
check-in is pending.

The owner tested on macOS on 2026-11-12, against `main` run from the clone
with `uv run wowlab`, and reported that things look good. The conductor
smoke-tested the CLI on a constructed install. It checked macOS discovery
under `~/Games`, `char list` across flavors, `doctor` on a leftover flavor,
`snap create` / `snap list`, and `undo` on an empty journal. The run found
the three usability issues filed as M16-122 to M16-124.

### Merged on 2026-11-11 and 2026-11-12

- **Write gate (`guard.py`):**
  - leftover temp files are cleaned up before the pre-write snapshot (M16-69);
  - a closed record that never took a snapshot is passed over by `undo` (§6.10 (a1), (a2));
  - damaged journal records and flavor paths with control or format characters are refused (M16-90, (e), (e1));
  - its docstrings now state the current rule (M16-120).
- **Spec:**
  - LAB_PLAN §6.10 is rewritten as one current section. Its dated amendments moved unchanged to Appendix A, and a key resolves the old references (M16-110).
  - ADR-0037 (accepted) freezes automatic crash recovery. Compound failures get `doctor`'s one generic answer (M16-109).
- **What the owner hit on macOS, all fixed:**
  - discovery searches about six folders, including `~/Games` and `/Volumes/*` last, and lists them when nothing is found (M16-112);
  - the `wowlab ui` link, a reload and a second tab work until Ctrl-C. This replaced the one-time key, and the ADR-0029 amendment is accepted (M16-113);
  - characters are found across flavors, `WOWLAB_FLAVOR` was added, and the UI gained a "Runs use flavor" picker (M16-114, M16-116).
- **`doctor`:**
  - a flavor with an unknown version is explained in words, in all three cases (no product, no row, blank version), in text and in `--json`, everywhere the old "no version in .build.info" appeared (M16-118, M16-121);
  - a failure it cannot work out gets one answer that names a whole snapshot of this install to restore (M16-109).
- **Code layout:** `cli.py` (14,844 lines) is now the `wowlab_core/cli/` package, one module per command group, with help output byte-identical (M16-111).
  - Tests patch names on `cli` as before. `_Package` in `cli/__init__.py` passes a patch on to every module that holds the name.
  - A new top-level name that a test reads or patches must be re-exported in `cli/__init__.py`. `test_cli_package.py` fails until it is.
- **CI:** Windows CI was red on `main` after #496 and was fixed in #499. A PR that adds tests reaching the write gate now waits for `lab (windows)`.

### Owner decisions recorded

- ADR-0037 is accepted (2026-11-11).
- The ADR-0029 amendment of 2026-11-12 is accepted.
- The M16-90 legacy risk is accepted (§6.10, under the `flavor_path` rule): a pre-M16-90 record whose `flavor_path` holds a refused character makes the journal unreadable, and that blocks changes on every install sharing the store. Review it with ADR-0037's frozen cases before outside users are invited.
- Keep the grader gate (`scripts/check_grader_diff.py`).
- The process rules are in `docs/AGENT_WORKFLOW.md`:
  - the review-round cap;
  - owner questions carry their consequences;
  - conductor housekeeping: worktrees, Dependabot, status messages, per-agent `TMPDIR`, fresh conductor worktrees, keeping an implementer's worktree until its reviews end, and waiting for Windows on write-gate tests.

## Plan for the next session

1. **Usability fixes from the 2026-11-12 test.** All three are ready and independent:
   - M16-122: `doctor` names the version that is running;
   - M16-123: `doctor` stops offering a lab-addon install into a flavor whose version is unknown;
   - M16-124: `snap create` with several flavors.

   Dispatch them in parallel. M16-122 and M16-123 both touch `cli/_cmd_doctor.py`, so expect a small merge.
2. **M16-88** (XS): re-record the performance baselines from `ubuntu-latest`. It is ready.
3. **Owner-blocked, in this order:**
   - The **schema-5 capture** unblocks M16-45 (#379), then M16-46, M16-48, M16-55, M16-58 and M16-64. When #379 resumes, merge `main` into it: its `cli.py` hunks now belong in `cli/_cmd_binds.py` and `cli/_cmd_char.py`, and any new name a test reads goes into `cli/__init__.py` (#508 has the map).
   - The owner tickets: M13-02, M13-07, M13-13, M13-62, M13-64, M13-66, M13-79, M16-32, M16-33, M16-38, M16-87T/87. M16-87T/87 are deferred under ADR-0037.
4. **M16-47, the wave review**, once the M16-45 family is done. Write `docs/handoffs/M16-review.md` and stop dispatch until the owner picks the next wave: resume M15 (the CASC/model chain) or tag v0.2.

### Things to know before dispatching

- **The owner's installed `wowlab` can be stale.** `~/.local/bin/wowlab` was the 0.1.0rc1 release, which hid every fix until the owner ran `uv run wowlab` from the clone. M16-122 addresses this. Until then, ask the owner to run from the clone, or to reinstall with `uv tool install --force --editable ./lab/core`. Their clone is on an external drive, so an editable install works only while that drive is mounted.
- **Linux has no default install locations.** A smoke test in a cloud container must set `WOWLAB_WOW_ROOT`, or call `install.default_roots("darwin", home=…, volumes=…)` directly.
- **Wiki:** user-facing rows for today's PRs are added to `docs/handoffs/wiki-pending.md`. The owner updates the wiki from that file.

## Cleanup done at this stopping point

- Agent worktrees were removed; `git worktree list` shows only the locked worktrees of agents from earlier sessions.
- Merged remote branches were left in place. Deleting them is an owner action (`git push origin --delete <branch>`).
