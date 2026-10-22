"""The CLI refusal for a foreign snapshot names the new option (M16-03T).

docs/LAB_PLAN.md §18.1 item 1: "Without `into`, a foreign snapshot is
refused as now, and the refusal names the option." The options are the
ones M16-03 adds: `snap restore --into-flavor/--into-root` and
`profile apply --into-flavor`. The guard-level graders are in
`test_guard_restore_into_target.py`; these hold the player-facing words.

The install is the captured tree as `test_cli.py` builds it (copied into
`tmp_path`, the user data directory redirected, the process table a fake).
Constructed, and labelled so in the test names (L8): the flavor folder
renamed as the launch is expected to rename it, and a second install root
copied from the first. The folder names are test data (L6). Nothing reads
or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_cli import (
    FLAVOR,
    _plain,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import guard, install
from wowlab_core.snapshot import Manifest

LAUNCH_FOLDER = "_classic_"  # constructed: the name the rename gives the folder


def _snapshot() -> Manifest:
    made = ok("snap", "create", "-m", "constructed", "--json")
    return Manifest.model_validate_json(made.stdout)


def _refused_naming(option: str, *args: str) -> None:
    result = run(*args)
    said = _plain(result.stdout + result.stderr)
    assert result.exit_code != 0, said
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(
        result.exception
    )
    assert option in said, f"the refusal must name {option}: {said}"


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize("extra", [["--dry-run"], ["--yes"]], ids=["dry-run", "yes"])
def test_snap_restore_after_a_rename_names_into_flavor_constructed(
    root: Path, flavor: Path, extra: list[str]
) -> None:
    a = _snapshot()
    flavor.rename(root / LAUNCH_FOLDER)
    before = _state(root)

    _refused_naming("--into-flavor", "snap", "restore", a.id, *extra)
    assert _state(root) == before
    assert guard.history() == ()


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
def test_profile_apply_after_a_rename_names_into_flavor_constructed(
    root: Path, flavor: Path
) -> None:
    ok("profile", "save", "keys", "--preset", "bindings")
    flavor.rename(root / LAUNCH_FOLDER)
    before = _state(root)

    _refused_naming("--into-flavor", "profile", "apply", "keys", "--dry-run")
    assert _state(root) == before
    assert guard.history() == ()


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
def test_snap_restore_in_another_root_names_into_root_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _snapshot()
    other = tmp_path / "Other WoW"
    other.mkdir()
    shutil.copy2(root / ".build.info", other / ".build.info")
    shutil.copytree(root / FLAVOR, other / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(other))
    before = _state(other)

    _refused_naming("--into-root", "snap", "restore", a.id, "--dry-run")
    assert _state(other) == before
    assert guard.history() == ()
