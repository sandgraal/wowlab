"""`wowlab snap export` and `wowlab snap import` (M16-04).

Two sandboxes stand for two computers. Each is the real capture
(`fixtures/macos/`, copied into `tmp_path` as `test_cli.py` builds it) with
its own user data directory, so its own snapshot store and journal: the
export runs in the first, the import and an `--into-root` restore (M16-03)
in the second. Constructed, and labelled so in the test names (L8): the
second install's location, the later session that changes its Config.wtf,
the renamed flavor folder, and the hostile destinations of an export.
Hostile archives are graded at the library level in `test_snaparchive.py`.

The process table is a fake with no client in it and game data is replayed
(the autouse fixtures of `test_cli.py`). Nothing reads or writes a real
install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import platformdirs
import pytest
from test_cli import (
    CAPTURE,
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

from wowlab_core import cli, guard, snaparchive
from wowlab_core.snapshot import Manifest, SnapshotStore

CONFIG = "WTF/Config.wtf"
LAUNCH = "_classic_"  # constructed: the name the launch rename gives the folder


def _snapshot() -> Manifest:
    return Manifest.model_validate_json(ok("snap", "create", "-m", "carried", "--json").stdout)


def _second_computer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str) -> Path:
    """Constructed: another computer, with the captured install at another
    path (its flavor folder named `folder`) and its own user data directory,
    after a later session changed its Config.wtf."""
    other = tmp_path / "computer-b" / "Games" / "World of Warcraft"
    other.mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", other / ".build.info")
    shutil.copytree(CAPTURE / "forever", other / folder)
    config = other / folder / CONFIG
    config.write_bytes(config.read_bytes() + b'SET constructed "later session"\n')
    data_b = tmp_path / "computer-b" / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data_b)
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    return other


@pytest.mark.parametrize(
    ("folder", "extra"),
    [
        pytest.param(FLAVOR, [], id="constructed-same-folder"),
        pytest.param(LAUNCH, ["--into-flavor", LAUNCH], id="constructed-renamed-folder"),
    ],
)
def test_export_import_then_restore_on_another_computer_constructed(
    root: Path,
    flavor: Path,
    user_data: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    folder: str,
    extra: list[str],
) -> None:
    saved = (flavor / CONFIG).read_bytes()
    a = _snapshot()
    stick = tmp_path / "usb-stick"
    stick.mkdir()
    before = _state(root)

    exported = ok("snap", "export", a.id, str(stick / "carried.zip"), "--json")
    report = snaparchive.ExportReport.model_validate_json(exported.stdout)
    assert report.id == a.id and Path(report.file) == stick / "carried.zip"
    assert _state(root) == before, "an export opens nothing in the install (L1)"

    other = _second_computer(tmp_path, monkeypatch, folder)
    assert not SnapshotStore().path.exists(), "the second computer's store starts empty"

    said = _plain(ok("snap", "import", str(stick / "carried.zip")).stdout)
    assert f"Imported snapshot {a.id}" in said
    assert "--into-root" in said
    # It says where and when the snapshot was taken, on the other computer.
    assert a.created_at in said and str(root) in said
    shown = _plain(ok("snap", "show", a.id).stdout)
    assert "kind:     imported" in shown, shown
    # The JSON reports the effective kind too (review round 2).
    shown_json = json.loads(ok("snap", "show", a.id, "--json").stdout)
    assert shown_json["kind"] == "imported" and a.kind == "user"
    listed = json.loads(ok("snap", "list", "--json").stdout)
    assert [s["id"] for s in listed["snapshots"]] == [a.id]
    assert json.loads(ok("snap", "verify", "--json").stdout)["corrupt_objects"] == []

    ok("snap", "restore", a.id, "--into-root", str(other), *extra, "--yes")
    assert (other / folder / CONFIG).read_bytes() == saved
    # The restore went through the write gate of the second computer.
    assert len(guard.history()) == 1

    again = _plain(ok("snap", "import", str(stick / "carried.zip")).stdout)
    assert "already in the store; nothing was added" in again


def test_export_text_names_the_file_and_the_size_constructed(root: Path, tmp_path: Path) -> None:
    a = _snapshot()
    said = _plain(ok("snap", "export", "latest", str(tmp_path / "out.zip")).stdout)
    assert f"Exported snapshot {a.id} to {tmp_path / 'out.zip'}" in said
    assert "file(s)" in said


@pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX matter")
def test_export_archive_is_0600_under_umask_0022_constructed(root: Path, tmp_path: Path) -> None:
    """M16-67: `snap export` leaves its archive readable by its owner only."""
    _snapshot()
    out = tmp_path / "out.zip"
    old = os.umask(0o022)
    try:
        ok("snap", "export", "latest", str(out))
    finally:
        os.umask(old)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def _other_install(tmp_path: Path) -> Path:
    other = tmp_path / "Second Install"
    (other / "_other_" / "WTF").mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", other / ".build.info")
    return other


@pytest.mark.parametrize(
    "where",
    [
        pytest.param(lambda root, tmp: root / "carried.zip", id="constructed-install-root"),
        pytest.param(
            lambda root, tmp: root / FLAVOR / "WTF" / "carried.zip", id="constructed-flavor-wtf"
        ),
        pytest.param(
            lambda root, tmp: root / FLAVOR / "Interface" / "AddOns" / "carried.zip",
            id="constructed-addons",
        ),
        pytest.param(
            lambda root, tmp: _other_install(tmp) / "_other_" / "WTF" / "carried.zip",
            id="constructed-another-install",
        ),
        pytest.param(
            lambda root, tmp: root / FLAVOR / "WTF" / ".." / ".." / "carried.zip",
            id="constructed-dotdot",
        ),
    ],
)
def test_export_into_any_install_is_refused_and_the_install_is_untouched_constructed(
    root: Path, tmp_path: Path, where: object
) -> None:
    a = _snapshot()
    dest = where(root, tmp_path)  # type: ignore[operator]
    before = _state(root)
    result = run("snap", "export", a.id, str(dest))
    said = _plain(result.stdout + result.stderr)
    assert result.exit_code == cli.EXIT_REFUSED, said
    assert not Path(dest).exists()
    assert _state(root) == before, "the install is byte- and mtime-identical (L1)"


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_export_through_a_link_into_an_install_is_refused_constructed(
    root: Path, tmp_path: Path
) -> None:
    a = _snapshot()
    door = tmp_path / "door"
    try:
        door.symlink_to(root / FLAVOR / "WTF", target_is_directory=True)
    except OSError:
        pytest.skip("this system cannot make a symlink")
    before = _state(root)
    result = run("snap", "export", a.id, str(door / "carried.zip"))
    assert result.exit_code == cli.EXIT_REFUSED, _plain(result.stdout + result.stderr)
    assert _state(root) == before


def test_export_refuses_an_existing_file_constructed(root: Path, tmp_path: Path) -> None:
    a = _snapshot()
    there = tmp_path / "keep.zip"
    there.write_bytes(b"constructed: the player's own file\n")
    result = run("snap", "export", a.id, str(there))
    assert result.exit_code == cli.EXIT_ERROR
    assert "already exists" in _plain(result.stdout + result.stderr)
    assert there.read_bytes() == b"constructed: the player's own file\n"


def test_import_of_a_damaged_archive_adds_nothing_constructed(
    root: Path, tmp_path: Path, user_data: Path
) -> None:
    a = _snapshot()
    archive = tmp_path / "carried.zip"
    ok("snap", "export", a.id, str(archive))
    store = SnapshotStore()
    manifest_file = store.manifests_dir / f"{a.id}.json"
    manifest_file.unlink()  # so the import would add it back
    before = sorted(p.relative_to(store.path).as_posix() for p in store.path.rglob("*"))

    damaged = tmp_path / "damaged.zip"
    raw = bytearray(archive.read_bytes())
    raw[len(raw) // 3] ^= 0xFF  # constructed: one flipped byte inside an object
    damaged.write_bytes(bytes(raw))
    result = run("snap", "import", str(damaged))
    assert result.exit_code == cli.EXIT_ERROR, _plain(result.stdout + result.stderr)
    after = sorted(p.relative_to(store.path).as_posix() for p in store.path.rglob("*"))
    assert after == before
    assert not manifest_file.exists()

    ok("snap", "import", str(archive))  # positive control: the sound archive adds it
    assert manifest_file.exists()
