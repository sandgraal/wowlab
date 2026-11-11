# Probe from review of m16/69-temp-cleanup-snapshot; reproduces a failed pre-write snapshot or a relinked install making the last real change impossible to undo, and doctor claiming a removal that never happened.
"""Three behaviours of the record-first order (LAB_PLAN §6.10, 2026-11-11
(a) and (a1)) that the branch introduces; each passes on `main`'s gate or
holds in its positive control.

1. A pre-write snapshot that fails (a `SnapshotError`: a full disk, an
   unreadable file) after a committed change T1 now leaves a `rolled_back`
   record with no snapshot. It is not passed over (it has no `undoes`), so it
   becomes the undo target, `wowlab undo` says it "changed no files", and T1
   can no longer be undone. On `main` a failed snapshot journaled nothing and
   T1 stayed the target. The record-first path is taken whenever an earlier
   record names a temp, which is every change after one that wrote a file
   (its temps were renamed into place, but are still candidates).
   Positive control: T1 wrote nothing, so there is no candidate, the
   snapshot is taken first, and T1 stays undoable.

2. After the install is moved and a link left at the old name, `undo()` of
   the last record is refused ("now resolves to ..."), where `main` undid
   it. `doctor` keeps offering the same undo line. Positive control: the
   same undo without the move.

3. `doctor`'s problem for an `open` record that never took a snapshot says
   "it only removed temporary files an earlier change left behind" even
   when the record removed none (`temps_removed` empty). Positive control:
   a real leftover at the earlier record's temp path, which the killed
   change did remove.

Constructed (L8, boundary): the synthetic install of `test_guard.py` under
`tmp_path`, the user data directory redirected, the process table injected;
a snapshot failure and a kill (KeyboardInterrupt) are injected at
`SnapshotStore.create`. Nothing reads or writes a real install.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import snapshot as snapmod


def _load(name: str, filename: str) -> types.ModuleType:
    path = Path(__file__).resolve().parent.parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = _load("_cr_m1669_guard_graders", "test_guard.py")
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
idle = G.idle
CONFIG = G.CONFIG
FONT = G.FONT
FLAVOR_FOLDER = G.FLAVOR_FOLDER


@pytest.mark.parametrize(
    "t1_writes",
    [
        pytest.param(True, id="constructed-t1-wrote-a-file"),
        pytest.param(False, id="constructed-positive-control-t1-wrote-nothing"),
    ],
)
def test_a_failed_snapshot_leaves_the_earlier_change_undoable(
    guard: Any,
    flavor: Any,
    tmp_path: Path,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    t1_writes: bool,
) -> None:
    store = tmp_path / "store"
    with guard.transaction(flavor, label="t1", store=store) as tx:
        if t1_writes:
            tx.write(CONFIG, b"constructed: t1\n")
    t1 = guard.history(store=store)[-1]

    def full(self: Any, *a: Any, **k: Any) -> Any:
        raise snapmod.SnapshotError("constructed: the store's disk is full")

    with monkeypatch.context() as m:
        m.setattr(snapmod.SnapshotStore, "create", full)
        with (
            pytest.raises(guard.GuardError),
            guard.transaction(flavor, label="t2", store=store) as tx,
        ):
            tx.write(FONT, b"constructed: never written\n")

    # T1 is still the change `undo` offers, as on main.
    guard.undo(store=store, expected_id=t1.id)


@pytest.mark.parametrize(
    "relinked",
    [
        pytest.param(True, id="constructed-install-moved-and-linked"),
        pytest.param(False, id="constructed-positive-control-not-moved"),
    ],
)
def test_undo_after_the_install_is_moved_behind_a_link(
    guard: Any, flavor: Any, install_root: Path, tmp_path: Path, idle: None, relinked: bool
) -> None:
    store = tmp_path / "store"
    with guard.transaction(flavor, label="t1", store=store) as tx:
        tx.write(CONFIG, b"constructed: t1\n")
    root = install_root
    if relinked:
        root = install_root.parent / "WoW moved"
        install_root.rename(root)
        try:
            install_root.symlink_to(root, target_is_directory=True)
        except OSError as exc:  # pragma: no cover - Windows without the privilege
            pytest.skip(f"cannot make a directory link here: {exc}")

    guard.undo(store=store)  # main: undone; the branch: refused

    assert (root / FLAVOR_FOLDER / CONFIG).read_bytes() != b"constructed: t1\n"
    guard.history(store=store)  # and the journal still reads


@pytest.mark.parametrize(
    "leftover",
    [
        pytest.param(False, id="constructed-nothing-to-remove"),
        pytest.param(True, id="constructed-positive-control-a-leftover-removed"),
    ],
)
def test_doctor_says_temps_were_removed_only_when_they_were(
    guard: Any,
    flavor: Any,
    tmp_path: Path,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    leftover: bool,
) -> None:
    from wowlab_core import cli

    store = tmp_path / "store"
    with guard.transaction(flavor, label="t1", store=store) as tx:
        tx.write(CONFIG, b"constructed: t1\n")
    t1 = guard.history(store=store)[-1]
    temps = json.loads((store / "journal" / f"{t1.id}.json").read_text())["temps"]
    assert temps, "t1 journaled the temp it renamed into place"
    if leftover:
        (flavor.path / temps[0]).write_bytes(b"constructed: a leftover\n")

    def killed(self: Any, *a: Any, **k: Any) -> Any:
        raise KeyboardInterrupt  # constructed: the process dies here

    with monkeypatch.context() as m:
        m.setattr(snapmod.SnapshotStore, "create", killed)
        with (
            pytest.raises(KeyboardInterrupt),
            guard.transaction(flavor, label="t2", store=store),
        ):
            pass
    k = guard.history(store=store)[-1]
    assert k.state == "open" and not k.snapshot_id, "killed between cleanup and snapshot"
    assert bool(k.temps_removed) == leftover, "positive control: what the cleanup removed"

    words = cli._stopped_before_any_file(k.id)
    claims = "removed temporary files" in words
    assert claims == bool(k.temps_removed), words
