# Probe from review of m10/14-wowlab-cli (afda260); reproduces: `snap diff` exits 1 and prints no diff at all when one side of a changed SavedVariables file cannot be read from the store, instead of falling back to the plain changed-path entry.
"""M10-14 amendment (2026-09-21): `snap diff` adds the luadata-aware diff for
`.lua` SavedVariables, "falling back to the plain changed-path entry when
either side does not parse". `_lua_diff` catches only `luadata.LuaDataError`;
a side whose object is missing (or corrupt) raises `SnapshotError` out of
`read_object`, and the whole command fails with `object <sha> is missing`,
so the added/removed/changed listing that `SnapshotStore.diff()` computes
from the manifests alone is lost. A side that cannot be read has not parsed
either; the plain entry, with a note, is what the amendment asks for.

The damage is constructed (L8, hostile case): one stored object deleted from
a store built from the captured tree. The positive control runs the same
diff before the deletion.
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

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
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


def test_snap_diff_falls_back_when_a_side_cannot_be_read_constructed(root: Path) -> None:
    assert _run("snap", "create", "-m", "a").exit_code == 0
    sv = root / FLAVOR / SYNDICATOR
    sv.write_bytes(sv.read_bytes().replace(b"= false", b"= true", 1))
    assert _run("snap", "create", "-m", "b").exit_code == 0
    store = snapshot.SnapshotStore()
    first, second = (m.id for m in store.list())

    control = _run("snap", "diff", first, second)
    assert control.exit_code == 0, control.stderr
    assert f"changed  {FLAVOR}/{SYNDICATOR}" in control.stdout

    change = next(c for c in store.diff(first, second).changed if c.path.endswith(SYNDICATOR))
    assert change.before.sha256 is not None
    store.object_path(change.before.sha256).unlink()  # constructed damage

    result = _run("snap", "diff", first, second)
    assert result.exit_code == 0, result.stderr
    assert f"changed  {FLAVOR}/{SYNDICATOR}" in result.stdout
