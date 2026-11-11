# Probe from review of m16/69-temp-cleanup-snapshot (round 2); reproduces an undo journaled under a link's spelling that cannot itself be undone once the link is gone.
"""Round 2 of the M16-69 review. To keep `history()`'s `undoes` check by
spelling, `undo()` now journals its record under the spelling the undone
record used (`journal_as`), not the path it resolved (§6.10 item 1: the
install root "as guard validated it"; M13-15: the journal names the plain
path guard took). After an install is moved and a link left at the old
name, the undo U of the last change journals the link's spelling. Once the
player removes the link, U cannot be undone ("not a flavor of an install"),
though the install is where U wrote. On `main` U journaled the resolved
path and its undo works.

Positive control: the link is kept, and the undo of U works.

Constructed (L8, boundary): the synthetic install of `test_guard.py` under
`tmp_path`, the user data directory redirected, the process table injected.
Nothing reads or writes a real install.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest


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
    "link_removed",
    [
        pytest.param(True, id="constructed-link-removed-after-the-undo"),
        pytest.param(False, id="constructed-positive-control-link-kept"),
    ],
)
def test_the_undo_of_an_undo_after_a_move_does_not_need_the_old_link(
    guard: Any, flavor: Any, install_root: Path, tmp_path: Path, idle: None, link_removed: bool
) -> None:
    store = tmp_path / "store"
    original = (flavor.path / CONFIG).read_bytes()
    with guard.transaction(flavor, label="t1", store=store) as tx:
        tx.write(CONFIG, b"constructed: t1\n")
    moved = install_root.parent / "WoW moved"
    install_root.rename(moved)
    try:
        install_root.symlink_to(moved, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows without the privilege
        pytest.skip(f"cannot make a directory link here: {exc}")
    guard.undo(store=store)  # U: puts back what was there before t1
    assert (moved / FLAVOR_FOLDER / CONFIG).read_bytes() == original
    if link_removed:
        install_root.unlink()

    guard.undo(store=store)  # the undo of U; main: done, the branch: refused

    assert (moved / FLAVOR_FOLDER / CONFIG).read_bytes() == b"constructed: t1\n"
