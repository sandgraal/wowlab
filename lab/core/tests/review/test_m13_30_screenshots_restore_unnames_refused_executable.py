# Probe from review of m13/30-restore-screenshots; reproduces a whole restore of a --screenshots snapshot that commits where a whole restore refuses an unchanged executable the gate also refuses for its name.
"""`docs/LAB_PLAN.md` §6.11 (amended 2026-10-17, M13-30): with screenshots
in the snapshot, the rest is restored by name "so the gate refuses what it
would refuse there (a reserved name, a case collision, a changed link or
executable)"; only unchanged links and executables are left unnamed.

The gate's whole restore leaves an executable alone only when the path
passes every other rule (`guard._unchanged_executable` checks it with
`_check_rel(rel, allow_executable=True)` and walks it with `_look`, which
refuses a name that differs from the disk's in case). `cli._unchanged`
checks content and mode only. So an executable that is also a reserved
Windows device name (`aux.sh`), or whose name on disk differs from the
snapshot's in case only, is left unnamed with screenshots in the snapshot
and the restore commits, while the same restore without screenshots is
refused (exit 3). Nothing is written to that path either way.

Constructed (L8): an addon's `aux.sh`, or its `build.sh` renamed to
`Build.sh` after the snapshot, a screenshot file under `Screenshots/`, and
an edited copy of a captured SavedVariables file standing for a later
session, in the captured tree copied into `tmp_path` with the user data
directory redirected and the process table a fake. Nothing reads or writes
a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    _flip_syndicator,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import guard
from wowlab_core.snapshot import Manifest

ADDON = "Interface/AddOns/DBM-Brawlers"
windows_skip = pytest.mark.skipif(
    sys.platform == "win32", reason="Windows cannot create a file named aux.sh"
)
SHOT = "Screenshots/WoWScrnShot_101626_120000.jpg"


def _restore(flavor: Path, *, shots: bool, case_rename: bool) -> tuple[int, str]:
    name = "build.sh" if case_rename else "aux.sh"
    (flavor / ADDON / name).write_bytes(b"#!/bin/sh\necho constructed\n")
    if shots:
        (flavor / SHOT).parent.mkdir(parents=True, exist_ok=True)
        (flavor / SHOT).write_bytes(b"\xff\xd8constructed jpeg\xff\xd9")
    args = ["snap", "create", "--json"] + (["--screenshots"] if shots else [])
    manifest = Manifest.model_validate_json(ok(*args).stdout)
    if case_rename:
        (flavor / ADDON / name).rename(flavor / ADDON / "Build.sh")
    _flip_syndicator(flavor)
    result = run("snap", "restore", manifest.id, "--yes")
    return result.exit_code, _plain(result.stderr)


def _case_insensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "Case-Probe-constructed"
    probe.write_bytes(b"")
    try:
        return (tmp_path / "case-probe-constructed").exists()
    finally:
        probe.unlink()


@windows_skip
def test_positive_control_without_screenshots_a_reserved_executable_refuses_constructed(
    root: Path, flavor: Path
) -> None:
    code, err = _restore(flavor, shots=False, case_rename=False)
    assert code == 3, err
    assert "reserved device name" in err
    assert guard.history() == ()


@windows_skip
def test_with_screenshots_a_reserved_executable_refuses_as_a_whole_restore_does_constructed(
    root: Path, flavor: Path
) -> None:
    code, err = _restore(flavor, shots=True, case_rename=False)
    assert code == 3, "committed; a whole restore refuses an unchanged aux.sh"
    assert "reserved device name" in err
    assert guard.history() == ()


def test_positive_control_without_screenshots_a_case_renamed_executable_refuses_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive volume")
    code, err = _restore(flavor, shots=False, case_rename=True)
    assert code == 3, err
    assert guard.history() == ()


def test_with_screenshots_a_case_renamed_executable_refuses_as_a_whole_restore_does_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive volume")
    code, _ = _restore(flavor, shots=True, case_rename=True)
    assert code == 3, "committed; a whole restore refuses a case-renamed build.sh"
    assert guard.history() == ()
