# Probe from review of m13/04-first-run; reproduces the undo line promising that a restore removes files added since, which `snap restore` does not do.
"""The last line of a first run says: "`wowlab snap restore <id> --dry-run`
shows what restoring it would change; a restore also removes anything added
there since". `docs/INSTALL.md` step 7 says the same.

A whole-snapshot `snap restore` (no `--paths`) puts back the files the
snapshot holds and nothing else (`guard._Transaction.restore`: only a path
named with `--paths` that the snapshot covers but does not hold is
deleted). A file added to WTF, Interface or Fonts after the first snapshot
is neither in the dry-run plan nor removed by the restore, so the player is
told their install would go back to the snapshot when it would not.

The positive control shows the same dry run does list a file the snapshot
holds and that was changed since, so the plan is not simply empty.

Constructed: the captured tree copied into `tmp_path`, with one file added
and one file changed after the snapshot. Nothing reads or writes a real
install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401  (autouse: the store lives in tmp_path)
)

from wowlab_core.snapshot import SnapshotStore


def _first_run(flavor: Path) -> tuple[str, str]:
    result = run("doctor", "--offline", "--yes")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    (manifest,) = SnapshotStore().list()
    last = result.stdout.strip().splitlines()[-1]
    return manifest.id, last


def _planned(snapshot_id: str) -> set[str]:
    result = run("snap", "restore", snapshot_id, "--dry-run", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    return {item["path"] for item in json.loads(result.stdout)["plan"]}


def test_positive_control_a_changed_file_is_in_the_restore_plan_constructed(
    root: Path, flavor: Path
) -> None:
    snapshot_id, _ = _first_run(flavor)
    config = flavor / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + b'SET probeChanged "1"\n')
    assert "WTF/Config.wtf" in _planned(snapshot_id)


def test_the_undo_lines_promise_that_a_restore_removes_added_files_holds_constructed(
    root: Path, flavor: Path
) -> None:
    snapshot_id, last = _first_run(flavor)
    (flavor / "WTF" / "added-since.txt").write_bytes(b"added after the first run\n")
    (flavor / "Interface" / "added-since.txt").write_bytes(b"added after the first run\n")
    planned = _planned(snapshot_id)
    missing = {"WTF/added-since.txt", "Interface/added-since.txt"} - planned
    promises = "removes anything added" in last or "removes" in last.split("snap restore")[-1]
    assert not (promises and missing), (
        f"the undo line says a restore removes anything added since, but `snap restore "
        f"{snapshot_id} --dry-run` does not plan to remove {sorted(missing)}: {last}"
    )
