# Probe from review of m10/19-snap-create-overlap-first; reproduces a store
# inside an install (that also holds this install) now exiting 1, not 3.
"""`docs/LAB_PLAN.md` §6.11: exit 3 means refused by guard. §6.10 (amended
2026-09-23 and 2026-09-28): a store inside any install is refused by
`guard.store_lock(create=True)` before anything is created. The M10-19
amendment to §6.9 scopes `SnapshotStore.refuse_holding` to "the one overlap a
store outside every install can have", and says a store inside an install is
still refused by the gate with exit 3.

`wowlab snap create` now calls `refuse_holding` before `store_lock`, and
`refuse_holding` does not ask whether the store is outside every install. So
a store that is inside install A and is also an ancestor of install B (the
install being captured) is refused as an overlap with exit 1, where
origin/main refused it at the gate with exit 3. Nothing is created in either
case; the defect is the exit code and the reason given (the L1 refusal is
masked by the overlap message).

Constructed (boundary case, L8): two installs made from the captured tree,
one nested under a store directory inside the other. Positive controls: a
store inside another install that does not hold this one still exits 3; a
store outside every install that holds this one exits 1 with nothing created
(M10-19's own case).
"""

# ruff: noqa: F811  (the `root` fixture is imported from test_cli and requested by name)
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import platformdirs
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_cli import (
    CAPTURE,
    _state,
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import install


def _user_data_at(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: path)


def _other_install(where: Path) -> Path:
    where.mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", where / ".build.info")
    return where


def _snap_create(tmp_path: Path) -> tuple[int, str, list[str]]:
    before = _state(tmp_path)
    result = run("snap", "create")
    created = sorted(set(_state(tmp_path)) - set(before))
    return result.exit_code, result.stderr, created


def test_a_store_inside_an_install_that_holds_this_one_is_refused_by_the_gate_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _other_install(tmp_path / "A")
    store = other / "wowlab" / "store"
    shutil.copytree(root, store / "World of Warcraft")
    monkeypatch.setenv(install.ENV_ROOT, str(store / "World of Warcraft"))
    _user_data_at(monkeypatch, other / "wowlab")
    code, err, created = _snap_create(tmp_path)
    assert created == []
    assert code == 3, f"a store inside an install is refused by guard (exit 3); got {code}: {err}"
    assert "inside an install" in err


def test_control_a_store_inside_another_install_exits_3_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _other_install(tmp_path / "Other WoW")
    _user_data_at(monkeypatch, other / "wowlab")
    code, err, created = _snap_create(tmp_path)
    assert (code, created) == (3, []), err


def test_control_a_store_outside_every_install_that_holds_this_one_exits_1_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "ud" / "store"
    shutil.copytree(root, store / "World of Warcraft")
    monkeypatch.setenv(install.ENV_ROOT, str(store / "World of Warcraft"))
    _user_data_at(monkeypatch, tmp_path / "ud")
    code, err, created = _snap_create(tmp_path)
    assert (code, created) == (1, []), err
    assert "must not contain each other" in err
