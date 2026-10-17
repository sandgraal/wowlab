# Probe from review of m13/30-restore-screenshots; reproduces a whole restore of a --screenshots snapshot that commits where a whole restore refuses an executable whose permission bits changed.
"""`docs/LAB_PLAN.md` §6.11 (amended 2026-10-17, M13-30): a whole restore of
a `--screenshots` snapshot names every entry a whole restore would check, "so
the gate refuses what it would refuse there (a reserved name, a case
collision, a changed link or executable)"; only unchanged links and
executables are left unnamed. The gate's whole restore leaves an executable
alone only when a walk finds a regular file with the entry's bytes *and*
mode (`guard._unchanged_executable`); a mode change alone makes it refuse
the restore (exit 3). The CLI judges "unchanged" with
`profiles.disk_state`, which compares content only, so with screenshots in
the snapshot the same executable is left unnamed and the restore commits.
The positive control is the same sequence on a snapshot taken without
`--screenshots`.

Constructed (L8): an addon's `build.sh` with mode 0644 at the snapshot and
0755 after it, a screenshot file under `Screenshots/`, and an edited copy of
a captured SavedVariables file standing for a later session, in the captured
tree copied into `tmp_path` with the user data directory redirected and the
process table a fake. Nothing reads or writes a real install.
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

SCRIPT = "Interface/AddOns/DBM-Brawlers/build.sh"
SHOT = "Screenshots/WoWScrnShot_101626_120000.jpg"

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="permission bits are not kept on Windows"
)


def _restore_after_chmod(flavor: Path, *, shots: bool) -> tuple[int, str]:
    script = flavor / SCRIPT
    script.write_bytes(b"#!/bin/sh\necho constructed\n")
    script.chmod(0o644)
    if shots:
        (flavor / SHOT).parent.mkdir(parents=True, exist_ok=True)
        (flavor / SHOT).write_bytes(b"\xff\xd8constructed jpeg\xff\xd9")
    args = ["snap", "create", "--json"] + (["--screenshots"] if shots else [])
    manifest = Manifest.model_validate_json(ok(*args).stdout)
    script.chmod(0o755)  # the bytes are the snapshot's; the mode is not
    _flip_syndicator(flavor)
    result = run("snap", "restore", manifest.id, "--yes")
    return result.exit_code, _plain(result.stderr)


@posix_only
def test_positive_control_without_screenshots_a_mode_change_refuses_the_restore_constructed(
    root: Path, flavor: Path
) -> None:
    code, err = _restore_after_chmod(flavor, shots=False)
    assert code == 3, err
    assert "is an executable; guard never writes one" in err
    assert guard.history() == ()


@posix_only
def test_with_screenshots_a_mode_change_refuses_the_restore_as_a_whole_restore_does_constructed(
    root: Path, flavor: Path
) -> None:
    code, err = _restore_after_chmod(flavor, shots=True)
    assert code == 3, (
        "the restore was not refused, although a whole restore refuses an executable "
        "whose mode changed"
    )
    assert "is an executable; guard never writes one" in err
    assert guard.history() == ()
