# Probe from re-review of m10/14-wowlab-cli (63a7790); reproduces: the first `snap create` calls `SnapshotStore.ensure_exists()` before any location check, so a user data directory inside an install gets `ud/wowlab/store/` created in the install before guard refuses (L1).
"""L1: no module but `guard` creates anything inside an install, and
§6.10 amendment item 4 has `guard.store_lock` refuse a store inside any
install "before anything is created". Fix round 1 made `snap create` call
`store.ensure_exists()` (a bare `mkdir(parents=True)`) and only then
`guard.store_lock(store.path)`. When the user data directory lies inside an
install, the mkdir runs first: guard then refuses (exit 3) and names the
problem, but three directories are already inside the flavor folder.

Before the fix round the same command reached `SnapshotStore.create`,
whose overlap check refuses before creating anything; the positive control
shows that library path still leaves the install untouched.

The misplaced user data directory is constructed (L8, hostile case).
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
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    inside = root / FLAVOR / "WTF" / "ud" / "wowlab"  # constructed: user data inside the install
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: inside)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([_Proc()]))
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def test_library_create_refuses_before_creating_anything_control(root: Path) -> None:
    before = _tree(root)
    with pytest.raises(snapshot.StoreLocationError):
        snapshot.SnapshotStore().create(root, [f"{FLAVOR}/WTF"])
    assert _tree(root) == before


def test_first_snap_create_creates_nothing_inside_the_install_constructed(root: Path) -> None:
    before = _tree(root)
    result: Any = CliRunner().invoke(cli.app, ["snap", "create"])
    assert result.exit_code != 0, "a store inside the install is refused"
    assert set(_tree(root)) - set(before) == set()
