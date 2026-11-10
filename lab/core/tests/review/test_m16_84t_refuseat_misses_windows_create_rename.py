# Probe from review of m16/84-left-alone-paths-tests; reproduces RefuseAt not intercepting the create rename guard uses on Windows, so the recreate-deleted grader's positive control cannot fire there.
"""guard.py `_replace_through_temp` creates a missing target with
`Path.rename` (`os.rename`) when `os.name == "nt"`, and `os.replace`
elsewhere. The rollback of a delete recreates the file, so on Windows it
goes through `os.rename`. `RefuseAt` in test_guard_left_alone_m16_84.py
patches only `os.replace` and `os.unlink`, so on Windows the refusal never
fires and `test_constructed_a_rollback_the_system_refused_names_the_path
[constructed-recreate-deleted]` fails its `refusal.fired` control for any
implementation once the marker is deleted.

Constructed: Linux cannot run the `nt` branch, so the second case emulates
it by routing `Path.replace` to `Path.rename` when the target is absent,
which is what guard does there. The first case is the positive control:
the same flow without the emulation fires the refusal.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "_review_m16_84t_graders",
    Path(__file__).resolve().parents[1] / "test_guard_left_alone_m16_84.py",
)
assert _spec is not None and _spec.loader is not None
M = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = M
_spec.loader.exec_module(M)

_user_data_redirected = M._user_data_redirected
_real_process_table_is_never_listed = M._real_process_table_is_never_listed
guard = M.guard
world = M.world
install_root = M.install_root
flavor = M.flavor
idle = M.idle


@pytest.mark.parametrize(
    "emulate_nt_create",
    [
        pytest.param(False, id="constructed-posix-control"),
        pytest.param(True, id="constructed-windows-create-rename"),
    ],
)
def test_refuseat_fires_on_the_rollback_that_recreates_a_deleted_file(
    guard: Any, tmp_path: Path, flavor: Any, idle: None, emulate_nt_create: bool
) -> None:
    rel = M.BINDINGS
    store = tmp_path / "store"
    refusal = M.RefuseAt(flavor.path / rel)
    real_replace = pathlib.Path.replace

    def nt_like(self: pathlib.Path, dst: Any) -> Any:
        if not os.path.lexists(dst):  # guard's create branch on Windows
            return self.rename(dst)
        return real_replace(self, dst)

    with (
        pytest.raises(RuntimeError, match=M.FAILURE),
        pytest.MonkeyPatch.context() as patched,
        guard.transaction(flavor, label="failing", store=store) as tx,
    ):
        tx.write(M.ICON, M.ICON_NEW)
        M.OD._touch("delete", tx, rel, M.NEW_LUA)
        refusal.arm(patched)
        if emulate_nt_create:
            patched.setattr(pathlib.Path, "replace", nt_like)
        raise RuntimeError(M.FAILURE)
    assert refusal.fired, "RefuseAt did not see the rollback's create of the deleted file"
