"""Command-level performance budget (M16-27, docs/LAB_PLAN.md §6.12).

    python scripts/perf_budget.py            # check every row; exit 1 on a miss
    python scripts/perf_budget.py --record   # print the ratios, check nothing

Run nightly by `.github/workflows/perf-nightly.yml` on GitHub's
`ubuntu-latest`, never on the PR path (§18.1 item 6). What it does:

1. Calibrates: the CPU time of a fixed pure-Python loop (`calibrate`), the
   best of several runs. Every command's cost is given as a ratio to it, so a
   faster or slower runner moves both sides together.
2. Builds a sandbox in a fresh temporary directory, outside the repository
   and outside any install: the captured `macos/` fixture tree copied under a
   flavor folder whose name means nothing (discovery reads `.flavor.info`,
   L6), grown with `RESTORE_FILES` constructed files shaped like the M16-23T
   grader's tree (one in eight a SavedVariables file in the account folder,
   the rest addon files in folders of 40). The home and user data
   directories are redirected into the sandbox, and every command is given
   `--root`, so no real install and no real snapshot store is read or
   written. Nothing here calls a network service (ADR-0012).
3. Runs each command of `COMMANDS` as its own `python -m wowlab_core`
   process, `RUNS` times, and keeps the least child CPU time (user plus
   system, from `getrusage(RUSAGE_CHILDREN)`): load can only add time, so the
   minimum is the best estimate of the work. The ratio of that to the
   calibration is checked against `BASELINE`: a ratio over `FAIL_FACTOR`
   times the baseline fails.
4. Checks §18.1's absolute target: the whole-restore check of the
   `RESTORE_FILES`-file tree with nothing to change (`snap restore ID
   --dry-run`, the snapshot taken of the same tree) takes at most
   `RESTORE_LIMIT_S` seconds of wall-clock time, best of its runs.

The whole restore is the command, not the library call: it includes
discovery, reading the manifest and printing the plan, which is what a
player waits for. `sv merge` merges one character's `WowLab.lua` into
another's; the two differ, so it lists conflicts and writes nothing (exit 1).

POSIX only (`resource`). Standard library only, plus the `wowlab_core` the
interpreter running this script can import. Exit 0 when every row holds, 1
when one does not, 2 on a usage error or a command that failed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "lab" / "core" / "tests" / "fixtures" / "macos"
# Any name: discovery reads the folder's `.flavor.info`, not its name (L6).
FLAVOR_FOLDER = "_perf_"
ACCOUNT_SV = "WTF/Account/90000001#6/SavedVariables"
DUMP_FILE = "WTF/Account/90000001#6/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua"

RESTORE_FILES = 20_000
RESTORE_LIMIT_S = 5.0
FAIL_FACTOR = 1.5
RUNS = 5
RESTORE_RUNS = 3
CALIBRATION_RUNS = 7

# Command id -> (arguments after `wowlab`, the exit codes that count as a
# run). `{root}` and `{snapshot}` are filled in per sandbox.
COMMANDS: dict[str, tuple[tuple[str, ...], frozenset[int]]] = {
    "whole-restore": (
        ("snap", "restore", "{snapshot}", "--dry-run", "--json", "--root", "{root}"),
        frozenset({0}),
    ),
    "sv-dump-json": (("sv", "dump", "--json", DUMP_FILE, "--root", "{root}"), frozenset({0})),
    "sv-merge": (
        (
            "sv",
            "merge",
            "WowLab.lua",
            "--into",
            "1/Labchard-Labrealmg",
            "--from",
            "1/Labcharb-Labrealmf",
            "--json",
            "--root",
            "{root}",
        ),
        frozenset({1}),
    ),
    "addons-list": (("addons", "list", "--json", "--root", "{root}"), frozenset({0})),
    "version": (("--version",), frozenset({0})),
}

# Command id -> its cost as a ratio to the calibration loop's CPU time. How
# these were set is in docs/LAB_PLAN.md §6.12; the table there and this one
# are kept equal by tests/scripts/test_perf_budget.py.
BASELINE: dict[str, float] = {
    "whole-restore": 19.5,
    "sv-dump-json": 3.1,
    "sv-merge": 9.6,
    "addons-list": 3.1,
    "version": 3.0,
}


def calibrate(clock: Callable[[], float] = time.process_time) -> float:
    """CPU seconds of one fixed pure-Python workload: dict, str and int
    operations of the kind the parsers and the gate spend their time on."""
    start = clock()
    table: dict[str, int] = {}
    total = 0
    for i in range(1_500_000):
        key = f"key{i % 4096}"
        table[key] = table.get(key, 0) + i
        total += len(key) * (i & 7)
    for value in table.values():
        total ^= value
    elapsed = clock() - start
    if total == -1:  # keeps the loop from being judged dead; never true
        raise AssertionError
    return elapsed


@dataclass(frozen=True)
class Row:
    command: str
    cpu_s: float
    wall_s: float
    ratio: float
    baseline: float

    @property
    def limit(self) -> float:
        return FAIL_FACTOR * self.baseline

    @property
    def ok(self) -> bool:
        return self.baseline > 0 and self.ratio <= self.limit


def verdict(ratios: dict[str, float], baseline: dict[str, float]) -> list[str]:
    """One line per command over `FAIL_FACTOR` times its baseline, or with no
    baseline at all; empty when every row holds."""
    misses: list[str] = []
    for command, ratio in ratios.items():
        base = baseline.get(command, 0.0)
        if base <= 0:
            misses.append(f"{command}: no baseline")
        elif ratio > FAIL_FACTOR * base:
            misses.append(
                f"{command}: ratio {ratio:.2f} is over {FAIL_FACTOR:g} x {base:.2f} "
                f"= {FAIL_FACTOR * base:.2f}"
            )
    return misses


def build_sandbox(base: Path, files: int) -> tuple[Path, dict[str, str]]:
    """The install root and the environment every command runs in."""
    root = base / "World of Warcraft"
    root.mkdir(parents=True)
    shutil.copy2(FIXTURE / ".build.info", root / ".build.info")
    flavor = root / FLAVOR_FOLDER
    shutil.copytree(FIXTURE / "forever", flavor)
    for i in range(files):
        if i % 8 == 0:
            rel = f"{ACCOUNT_SV}/Constructed{i:05d}.lua"
        else:
            rel = f"Interface/AddOns/Constructed{i // 40:04d}/Libs/f{i % 40:02d}.lua"
        path = flavor / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"-- constructed %d\n" % i)
    home = base / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_STATE_HOME": str(home / "state"),
            "WOWLAB_WOW_ROOT": str(root),
            "NO_COLOR": "1",
        }
    )
    return root, env


# -P: nothing is imported from the working folder.
_WOWLAB = (sys.executable, "-P", "-m", "wowlab_core")


def _argv(command: str, fill: dict[str, str]) -> list[str]:
    args, _ = COMMANDS[command]
    return [*_WOWLAB, *(a.format(**fill) for a in args)]


def run_once(argv: Sequence[str], env: dict[str, str], cwd: Path) -> tuple[int, float, float, str]:
    """(exit code, child CPU seconds, wall seconds, stderr) of one run."""
    import resource

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    done = subprocess.run(
        list(argv),
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    wall = time.perf_counter() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return done.returncode, cpu, wall, done.stderr.decode("utf-8", "replace")


class CommandFailedError(Exception):
    pass


def take_snapshot(root: Path, env: dict[str, str], cwd: Path) -> str:
    done = subprocess.run(
        [*_WOWLAB, "snap", "create", "-m", "perf", "--json", "--root", str(root)],
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        raise CommandFailedError(f"snap create exited {done.returncode}: {done.stderr.decode()}")
    report = json.loads(done.stdout)
    snapshot = report.get("snapshot_id") or report.get("id")
    if not isinstance(snapshot, str):
        raise CommandFailedError(f"snap create --json named no snapshot id: {sorted(report)}")
    return snapshot


def measure(
    command: str, fill: dict[str, str], env: dict[str, str], cwd: Path, runs: int
) -> tuple[float, float]:
    """(least CPU seconds, least wall seconds) over `runs` runs."""
    _, accepted = COMMANDS[command]
    cpus: list[float] = []
    walls: list[float] = []
    for _ in range(runs):
        code, cpu, wall, err = run_once(_argv(command, fill), env, cwd)
        if code not in accepted:
            raise CommandFailedError(f"{command} exited {code}: {err.strip()[-400:]}")
        cpus.append(cpu)
        walls.append(wall)
    return min(cpus), min(walls)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record", action="store_true", help="print ratios, check nothing")
    parser.add_argument("--files", type=int, default=RESTORE_FILES, help=argparse.SUPPRESS)
    parser.add_argument("--runs", type=int, default=RUNS, help=argparse.SUPPRESS)
    parser.add_argument("--restore-runs", type=int, default=RESTORE_RUNS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.files < 0 or args.runs < 1 or args.restore_runs < 1:
        parser.error("--files must be >= 0 and the run counts >= 1")
    if os.name != "posix":
        print("perf_budget: POSIX only (resource.getrusage)", file=sys.stderr)
        return 2

    calibration = min(calibrate() for _ in range(CALIBRATION_RUNS))
    if calibration <= 0:
        print("perf_budget: the process clock did not advance", file=sys.stderr)
        return 2

    rows: list[Row] = []
    with tempfile.TemporaryDirectory(prefix="wowlab-perf-") as tmp:
        base = Path(tmp)
        started = time.perf_counter()
        root, env = build_sandbox(base, args.files)
        try:
            fill = {"root": str(root), "snapshot": take_snapshot(root, env, base)}
            setup = time.perf_counter() - started
            for command in COMMANDS:
                runs = args.restore_runs if command == "whole-restore" else args.runs
                cpu, wall = measure(command, fill, env, base, runs)
                rows.append(Row(command, cpu, wall, cpu / calibration, BASELINE[command]))
        except CommandFailedError as exc:
            print(f"perf_budget: {exc}", file=sys.stderr)
            return 2

    print(f"calibration: {calibration:.4f} s CPU (best of {CALIBRATION_RUNS})")
    print(
        f"tree: {args.files} constructed files plus the macos fixture ({setup:.1f} s to build and snapshot)"
    )
    print(f"{'command':<15} {'cpu s':>8} {'wall s':>8} {'ratio':>8} {'baseline':>9} {'limit':>8}")
    for row in rows:
        mark = "" if args.record else ("  ok" if row.ok else "  OVER")
        print(
            f"{row.command:<15} {row.cpu_s:>8.3f} {row.wall_s:>8.3f} {row.ratio:>8.2f} "
            f"{row.baseline:>9.2f} {row.limit:>8.2f}{mark}"
        )
    restore = next(r for r in rows if r.command == "whole-restore")
    print(
        f"whole-restore check at {args.files} files: {restore.wall_s:.3f} s wall "
        f"(limit {RESTORE_LIMIT_S:g} s at {RESTORE_FILES})"
    )
    if args.record:
        return 0

    misses = verdict({r.command: r.ratio for r in rows}, BASELINE)
    if args.files == RESTORE_FILES and not restore.wall_s <= RESTORE_LIMIT_S:
        misses.append(
            f"whole-restore: {restore.wall_s:.3f} s at {RESTORE_FILES} files "
            f"is over {RESTORE_LIMIT_S:g} s"
        )
    for miss in misses:
        print(f"OVER BUDGET {miss}", file=sys.stderr)
    return 1 if misses or not math.isfinite(calibration) else 0


if __name__ == "__main__":
    sys.exit(main())
