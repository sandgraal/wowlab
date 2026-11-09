# Probe from review of m16/03-restore-into-target; reproduces a snapshot with no account folder writing its stray WTF/Account folder into a target account the mapping never named.
"""Round 1 of M16-03 refuses a folder under `WTF/Account/` that is no
account when the target has an account folder of that name (the graders in
`test_guard_restore_into_others.py`). The check sits after both mapping
branches in `guard._account_mapping`, but a snapshot that holds no account
folder returns an empty mapping before it, so the same stray folder
restores into the target's real account (§6.10 as amended 2026-10-22: a
snapshot with no account folder "creates no account folder", and the
graders' rule that a restore writes into a target's account folders only
as the mapping says).

Constructed inputs, on the synthetic installs of the M16-03T graders,
loaded by path (L8). Flavor folder names are test data (L6).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore


def _load() -> types.ModuleType:
    path = Path(__file__).resolve().parent.parent / "test_guard_restore_into_others.py"
    spec = importlib.util.spec_from_file_location("_m16_03_review_others", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OTHERS = _load()
T = OTHERS.T

_user_data_redirected = OTHERS._user_data_redirected
_real_process_table_is_never_listed = OTHERS._real_process_table_is_never_listed
guard = OTHERS.guard
idle = OTHERS.idle
store = OTHERS.store

ACCOUNTLESS = {T.CONFIG: b'SET portal "US"\nSET constructed "source"\n'}


def _restore(guard: Any, tmp_path: Path, store: SnapshotStore, folder: str) -> tuple[Any, dict]:
    world = tmp_path / "world"
    files = {**ACCOUNTLESS, OTHERS.stray(folder): OTHERS.STRAY_BYTES}
    source = OTHERS.make_install(world / "Source WoW", OTHERS.SOURCE_FOLDER, files)
    snapshot_id = OTHERS.snapshot_of(store, source)
    target = OTHERS.make_install(
        world / "Target WoW", OTHERS.TARGET_FOLDER, OTHERS.legacy_account(OTHERS.LEGACY)
    )
    legacy_before = OTHERS.files_under(target.path, f"WTF/Account/{OTHERS.LEGACY}")
    try:
        with guard.transaction(target, label="accountless", store=store.path) as tx:
            tx.restore(snapshot_id, into=OTHERS.into(guard, target))
    except guard.GuardError:
        pass
    return target, legacy_before


@pytest.mark.parametrize("spelling", ["LEGACYACCT", "legacyacct"], ids=lambda s: f"constructed-{s}")
def test_constructed_accountless_snapshot_never_writes_into_a_target_account(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, spelling: str
) -> None:
    target, legacy_before = _restore(guard, tmp_path, store, spelling)
    assert OTHERS.files_under(target.path, f"WTF/Account/{OTHERS.LEGACY}") == legacy_before, (
        "the snapshot's stray folder was written into the target's account folder"
    )


def test_constructed_positive_control_a_stray_folder_of_another_name_restores(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    target, legacy_before = _restore(guard, tmp_path, store, "STRAYFOLDER")
    assert (target.path / OTHERS.stray("STRAYFOLDER")).read_bytes() == OTHERS.STRAY_BYTES
    assert OTHERS.files_under(target.path, f"WTF/Account/{OTHERS.LEGACY}") == legacy_before
