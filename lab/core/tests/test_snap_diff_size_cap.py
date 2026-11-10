"""`wowlab snap diff`: the SavedVariables value diff keeps the parser's size
bound (M16-72, from the M16-04 to M16-26 reviews).

`_lua_diff` used to inflate both sides of a changed SavedVariables file from
the store whatever their recorded size, and only then hand them to the
parser, which refuses anything over its bound. A side over the bound is now
never read: the change stays a plain changed-path entry, with a note.

The tree is the captured macOS fixture (L8); the oversized side is
`constructed`: the bound is lowered for the test rather than a 256 MiB file
written, and the store's reader is watched to prove it is never called.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SYNDICATOR = "WTF/Account/90000001#6/SavedVariables/Syndicator.lua"


class _Proc:
    pid = 1

    def name(self) -> str:
        return "python3"

    def exe(self) -> str:
        return "/nowhere/python3"

    def cmdline(self) -> list[str]:
        return ["/nowhere/python3"]

    def status(self) -> str:
        return "running"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([_Proc()]))
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _run(*args: str) -> Any:
    return CliRunner().invoke(cli.app, list(args))


def _two_snapshots(root: Path) -> tuple[str, str, snapshot.Change]:
    assert _run("snap", "create", "-m", "a").exit_code == 0
    sv = root / FLAVOR / SYNDICATOR
    sv.write_bytes(sv.read_bytes().replace(b"= false", b"= true", 1))
    assert _run("snap", "create", "-m", "b").exit_code == 0
    store = snapshot.SnapshotStore()
    first, second = (m.id for m in store.list())
    change = next(c for c in store.diff(first, second).changed if c.path.endswith(SYNDICATOR))
    return first, second, change


def test_snap_diff_does_not_read_a_side_over_the_parser_bound_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, change = _two_snapshots(root)
    bound = min(change.before.size, change.after.size) - 1
    assert bound > 0
    monkeypatch.setattr(cli.luadata, "MAX_FILE_BYTES", bound)
    reads: list[str] = []
    real = snapshot.SnapshotStore.read_object

    def watched(self: snapshot.SnapshotStore, sha256: str, *, size: int | None = None) -> bytes:
        reads.append(sha256)
        return real(self, sha256, size=size)

    monkeypatch.setattr(snapshot.SnapshotStore, "read_object", watched)

    result = _run("snap", "diff", first, second, "--json")
    assert result.exit_code == 0, result.stderr
    report = cli.DiffReport.model_validate_json(result.stdout)
    (changed,) = (c for c in report.changed if c.path.endswith(SYNDICATOR))
    assert changed.lua is None
    assert changed.lua_note is not None
    assert changed.lua_note.startswith("not compared as data")
    assert str(bound) in changed.lua_note
    assert change.before.sha256 not in reads
    assert change.after.sha256 not in reads

    text = _run("snap", "diff", first, second)
    assert text.exit_code == 0, text.stderr
    assert f"changed  {FLAVOR}/{SYNDICATOR}" in text.stdout


def test_snap_diff_still_compares_values_at_the_bound(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The positive control: a side exactly at the bound is read and compared.
    first, second, change = _two_snapshots(root)
    monkeypatch.setattr(cli.luadata, "MAX_FILE_BYTES", max(change.before.size, change.after.size))
    result = _run("snap", "diff", first, second, "--json")
    assert result.exit_code == 0, result.stderr
    report = cli.DiffReport.model_validate_json(result.stdout)
    (changed,) = (c for c in report.changed if c.path.endswith(SYNDICATOR))
    assert changed.lua_note is None
    assert changed.lua
