"""`snap restore --into-flavor/--into-root` and `profile apply --into-flavor` (M16-03).

docs/LAB_PLAN.md §18.1 item 1 and §6.10 as amended 2026-10-22: a snapshot
or profile of one flavor folder goes into another folder of the same
install, or into another install root, only when the player names the
target; the plan names both folders and every mapped account folder.

The install is the captured tree as `test_cli.py` builds it. The rename,
the second root and the account folder renames are constructed, and the
test names say so (L8). Folder names here are test data (L6). Nothing reads
or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_cli import (
    FLAVOR,
    _json,
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

from wowlab_core import cli, guard
from wowlab_core.snapshot import Manifest

LAUNCH = "_classic_"  # constructed: the name the launch rename gives the folder
ACCOUNT = "90000001#6"  # the captured account folder
NEW_ACCOUNT = "90000002#1"  # constructed: a target account folder of another name
CONFIG = "WTF/Config.wtf"
BINDINGS = f"WTF/Account/{ACCOUNT}/bindings-cache.wtf"


def _snapshot() -> Manifest:
    return Manifest.model_validate_json(ok("snap", "create", "-m", "constructed", "--json").stdout)


def _later_session(folder: Path) -> bytes:
    """A later client session changes Config.wtf; returns the saved bytes."""
    config = folder / CONFIG
    saved = config.read_bytes()
    config.write_bytes(saved + b'SET constructed "later session"\n')
    return saved


def test_snap_restore_into_a_renamed_folder_names_both_and_undoes_constructed(
    root: Path, flavor: Path
) -> None:
    a = _snapshot()
    launch = root / LAUNCH
    flavor.rename(launch)
    saved = _later_session(launch)
    before = _state(root)

    planned = ok("snap", "restore", a.id, "--into-flavor", LAUNCH, "--dry-run")
    said = _plain(planned.stdout)
    assert f"From {FLAVOR} in " in said and f"into {LAUNCH} in " in said, said
    assert f"Account folder {ACCOUNT} goes into account folder {ACCOUNT}." in said, said
    assert CONFIG in said
    assert _state(root) == before

    ok("snap", "restore", a.id, "--into-flavor", LAUNCH, "--yes")
    assert (launch / CONFIG).read_bytes() == saved
    assert not (root / FLAVOR).exists(), "nothing is created under the old name"
    record = guard.history()[-1]
    assert Path(record.flavor_path).resolve() == launch.resolve()

    ok("undo", "--yes")
    assert (launch / CONFIG).read_bytes() != saved


def test_snap_restore_into_json_names_the_source_and_the_mapping_constructed(
    root: Path, flavor: Path
) -> None:
    a = _snapshot()
    launch = root / LAUNCH
    flavor.rename(launch)
    _later_session(launch)
    # The launch client made its own account folder under another name.
    (launch / "WTF" / "Account" / ACCOUNT).rename(launch / "WTF" / "Account" / NEW_ACCOUNT)

    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--into-flavor", LAUNCH, "--dry-run")
    assert report.into is not None
    assert report.into.from_flavor_folder == FLAVOR
    assert Path(report.into.from_install_root).resolve() == root.resolve()
    assert [(m.snapshot, m.target) for m in report.into.accounts] == [(ACCOUNT, NEW_ACCOUNT)]
    paths = {i.path for i in report.plan}
    assert CONFIG in paths
    assert all(f"/{ACCOUNT}/" not in p for p in paths), paths


def test_snap_restore_without_into_leaves_the_report_unchanged_constructed(
    root: Path, flavor: Path
) -> None:
    a = _snapshot()
    _later_session(flavor)
    result = ok("snap", "restore", a.id, "--dry-run", "--json")
    assert '"into"' not in result.stdout


def test_snap_restore_map_account_to_a_new_folder_constructed(root: Path, flavor: Path) -> None:
    a = _snapshot()
    launch = root / LAUNCH
    flavor.rename(launch)
    saved = (launch / BINDINGS).read_bytes()
    (launch / BINDINGS).write_bytes(b"constructed: a later session's bindings\n")

    ok(
        "snap", "restore", a.id, "--into-flavor", LAUNCH,
        "--map-account", f"{ACCOUNT}={NEW_ACCOUNT}", "--paths", BINDINGS, "--yes",
    )  # fmt: skip
    created = launch / "WTF" / "Account" / NEW_ACCOUNT / "bindings-cache.wtf"
    assert created.read_bytes() == saved
    assert (launch / BINDINGS).read_bytes() == b"constructed: a later session's bindings\n"


def test_snap_restore_paths_in_the_targets_spelling_is_refused_constructed(
    root: Path, flavor: Path
) -> None:
    a = _snapshot()
    launch = root / LAUNCH
    flavor.rename(launch)
    (launch / "WTF" / "Account" / ACCOUNT).rename(launch / "WTF" / "Account" / NEW_ACCOUNT)
    before = _state(root)
    target_spelling = BINDINGS.replace(ACCOUNT, NEW_ACCOUNT)

    result = run("snap", "restore", a.id, "--into-flavor", LAUNCH, "--paths", target_spelling,
                 "--yes")  # fmt: skip
    assert result.exit_code != 0
    assert _state(root) == before
    assert guard.history() == ()


def test_snap_restore_into_root_restores_into_another_install_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    a = _snapshot()
    other = tmp_path / "Other WoW"
    other.mkdir()
    shutil.copy2(root / ".build.info", other / ".build.info")
    shutil.copytree(root / FLAVOR, other / FLAVOR)
    saved = _later_session(other / FLAVOR)
    source_before = _state(root)

    ok("snap", "restore", a.id, "--into-root", str(other), "--yes")
    assert (other / FLAVOR / CONFIG).read_bytes() == saved
    assert _state(root) == source_before, "the source install is only read"


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(["--map-account", f"{ACCOUNT}={ACCOUNT}"], id="constructed-map-without-into"),
        pytest.param(["--into-root", "elsewhere", "--root", "x"], id="constructed-two-roots"),
        pytest.param(["--into-flavor", "_nowhere_"], id="constructed-no-such-folder"),
        pytest.param(
            ["--into-flavor", LAUNCH, "--map-account", "A=B", "--map-account", "A=C"],
            id="constructed-source-twice",
        ),
    ],
)
def test_snap_restore_refuses_contradicting_options_constructed(
    root: Path, flavor: Path, options: list[str]
) -> None:
    a = _snapshot()
    flavor.rename(root / LAUNCH)
    before = _state(root)
    result = run("snap", "restore", a.id, *options, "--yes")
    assert result.exit_code != 0, _plain(result.stdout + result.stderr)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert _state(root) == before
    assert guard.history() == ()


def test_profile_apply_into_a_renamed_folder_constructed(root: Path, flavor: Path) -> None:
    ok("profile", "save", "keys", "--preset", "bindings")
    saved = (flavor / BINDINGS).read_bytes()
    launch = root / LAUNCH
    flavor.rename(launch)
    (launch / BINDINGS).write_bytes(b"constructed: a later session's bindings\n")
    before = _state(root)

    planned = ok("profile", "apply", "keys", "--into-flavor", LAUNCH, "--dry-run")
    said = _plain(planned.stdout)
    assert f"into {LAUNCH} in " in said and f"Account folder {ACCOUNT} goes into" in said, said
    assert BINDINGS in said
    assert _state(root) == before

    report = _json(cli.ProfileApplyReport, "profile", "apply", "keys", "--into-flavor", LAUNCH,
                   "--yes")  # fmt: skip
    assert report.applied
    assert report.into is not None and report.into.from_flavor_folder == FLAVOR
    assert (launch / BINDINGS).read_bytes() == saved
    assert not (root / FLAVOR).exists()


def test_profile_apply_into_maps_a_renamed_account_folder_constructed(
    root: Path, flavor: Path
) -> None:
    ok("profile", "save", "keys", "--preset", "bindings")
    saved = (flavor / BINDINGS).read_bytes()
    launch = root / LAUNCH
    flavor.rename(launch)
    account = launch / "WTF" / "Account"
    (account / ACCOUNT).rename(account / NEW_ACCOUNT)
    mapped = account / NEW_ACCOUNT / "bindings-cache.wtf"
    mapped.write_bytes(b"constructed: a later session's bindings\n")

    ok("profile", "apply", "keys", "--into-flavor", LAUNCH, "--yes")
    assert mapped.read_bytes() == saved
    assert not (account / ACCOUNT).exists(), "the old account name is never created"
