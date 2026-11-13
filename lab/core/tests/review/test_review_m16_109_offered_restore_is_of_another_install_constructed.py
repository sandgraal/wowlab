# Probe from review of m16/109-doctor-generic-answer; reproduces doctor offering a snapshot of another install, or a command without the --root it was given, so the one command it ends with fails.
"""`doctor`'s answer for a failure wowlab cannot work out (M16-109, ADR-0037)
ends with `wowlab snap restore <id> --dry-run` "for the player to inspect
before restoring". That command must be one the player can run as printed.

`_newest_whole_snapshot` picks the newest whole snapshot of the whole store,
whatever install it was taken in, and the command never carries the `--root`
the player gave `doctor` (every other command `doctor` prints does, M16-37).
`snap restore` discovers the install from `--root` (or the default) and the
write gate refuses a snapshot of another install. So:

1. With two installs, a snapshot taken of install B after the change in
   install A broke: `doctor` (for A) names B's snapshot, and the printed
   command is refused.
2. With `doctor --root A` (A not the default install), the printed command
   has no `--root` and plans against the default install instead.

Constructed (L8, boundary): two copies of the captured tree in `tmp_path`,
the journal record cut short. Nothing reads or writes a real install.
Positive control: the same command with `--root A` and A's own snapshot runs.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    CAPTURE,
    FLAVOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import guard, install, snapshot


def _other_install(tmp_path: Path) -> Path:
    other = tmp_path / "Other WoW"
    other.mkdir()
    shutil.copy2(CAPTURE / ".build.info", other / ".build.info")
    shutil.copytree(CAPTURE / "forever", other / FLAVOR)
    return other


def _damage_last_record() -> None:
    last = guard.history()[-1]
    path = snapshot.default_store_path() / "journal" / f"{last.id}.json"
    path.write_bytes(path.read_bytes()[:40])


def _command(*doctor_args: str) -> list[str]:
    result = run("doctor", "--json", *doctor_args)
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    command = report["cannot_work_out"]["command"]
    assert command is not None, report["problems"]
    assert report["problems"][-1].endswith(command)
    return shlex.split(command)


def test_offered_snapshot_is_of_the_install_doctor_checks(
    flavor: Path, root: Path, tmp_path: Path
) -> None:
    ok("snap", "create")
    ok("addon", "install", "lab", "--yes")
    own = guard.history()[-1].snapshot_id
    other = _other_install(tmp_path)
    ok("snap", "create", "--root", str(other))  # newest: another install's
    _damage_last_record()

    argv = _command()
    # Positive control: this install's own newest snapshot plans fine.
    assert ok("snap", "restore", own, "--dry-run").exit_code == 0
    planned = run(*argv[1:])
    assert planned.exit_code == 0, (
        f"doctor offered {argv!r}; it is refused: {planned.output}{planned.stderr}"
    )


def test_offered_command_keeps_the_root_doctor_was_given(
    flavor: Path, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok("snap", "create")
    ok("addon", "install", "lab", "--yes")
    _damage_last_record()
    # The install is now named only by --root; the default finds another one.
    other = _other_install(tmp_path)
    monkeypatch.setenv(install.ENV_ROOT, str(other))
    argv = _command("--root", str(root))
    # Positive control: with --root, the snapshot doctor named plans fine.
    assert ok("snap", "restore", argv[3], "--dry-run", "--root", str(root)).exit_code == 0
    planned = run(*argv[1:])
    assert planned.exit_code == 0, (
        f"doctor --root {root} offered {argv!r}; it is refused: {planned.output}{planned.stderr}"
    )
