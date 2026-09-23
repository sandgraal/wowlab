# Probe from review of m10/11-guard-tests-3; reproduces: bytes changed on disk after the pre-write snapshot are overwritten by a committed transaction, are in no snapshot, and undo() refuses
"""ADR-0021: a write "takes a content-addressed snapshot first", and "undo is
a property of the platform". `docs/LAB_PLAN.md` §6.10: `guard.undo()`
restores the pre-write snapshot of the most recent transaction.

If a file changes on disk inside the transaction body, after the pre-write
snapshot and before `tx.write` first touches it (the client started after
the enter-time check, or a second wowlab process committed its own
transaction), the gate overwrites those bytes, the transaction commits, and
the overwritten bytes are held nowhere: not in the pre-write snapshot, not in
the store. `undo()` then refuses, because the snapshot does not hold the
`before` the journal names. The overwritten bytes cannot be recovered at all.

Expected (the reviewer's reading; the owner rules): a transaction never
commits an overwrite of bytes that no snapshot holds. Either the write is
refused before anything changes ("changed since the pre-write snapshot"), or
the bytes are put in the store so `undo()` can restore them. Both keep the
bytes on disk just before the write recoverable; the probe accepts either.

Marked `xfail(strict=True)` while the owner rules, so the branch stays green;
delete the marker when the gate is fixed, or delete the file if the owner
rules the behaviour within ADR-0021.

Positive control: without the change in the body, the same transaction
commits and `undo()` puts the original bytes back.

Constructed input: a synthetic install in `tmp_path`, the process table
injected through `psutil.process_iter` as the graders do.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import platformdirs
import psutil
import pytest
from pydantic import BaseModel

from wowlab_core import guard
from wowlab_core.snapshot import SnapshotStore

FLAVOR_FOLDER = "_retail_"  # tests may name a flavor folder (L6)
CONFIG = "WTF/Config.wtf"
ORIGINAL = b'SET portal "US"\n'
CHANGED_IN_BODY = b'SET portal "EU"\n'
WRITTEN = b'SET portal "KR"\n'


class Flavor(BaseModel, frozen=True):
    folder: str
    version: str | None
    path: Path


class _Bystander:
    pid = 101

    def __init__(self, exe: str) -> None:
        self._exe = exe

    def name(self) -> str:
        return "python3"

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return []

    def status(self) -> str:
        return "running"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        platformdirs, "user_data_path", lambda *a, **k: tmp_path / "userdata" / "wowlab"
    )
    bystander = _Bystander(str(tmp_path / "elsewhere" / "python3"))
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([bystander]))


def _install(tmp_path: Path) -> Flavor:
    root = tmp_path / "world" / "World of Warcraft"
    files = {
        ".build.info": b"Branch!STRING:0|Active!DEC:1\nus|1\n",
        f"{FLAVOR_FOLDER}/.flavor.info": b"Product Flavor!STRING:0\nwow\n",
        f"{FLAVOR_FOLDER}/{CONFIG}": ORIGINAL,
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return Flavor(folder=FLAVOR_FOLDER, version=None, path=root / FLAVOR_FOLDER)


def _store_holds(store: Path, data: bytes) -> bool:
    digest = hashlib.sha256(data).hexdigest()
    try:
        SnapshotStore(store).read_object(digest)
    except Exception:
        return False
    return True


def test_positive_control_undo_restores_the_original(tmp_path: Path) -> None:
    flavor = _install(tmp_path)
    store = tmp_path / "store"
    with guard.transaction(flavor, label="edit", store=store) as tx:
        tx.write(CONFIG, WRITTEN)
    guard.undo(store=store)
    assert (flavor.path / CONFIG).read_bytes() == ORIGINAL


def test_bytes_changed_after_the_snapshot_stay_recoverable(tmp_path: Path) -> None:
    flavor = _install(tmp_path)
    store = tmp_path / "store"
    config = flavor.path / CONFIG

    refused = False
    try:
        with guard.transaction(flavor, label="edit", store=store) as tx:
            # Constructed: something other than this transaction changes the
            # file after the pre-write snapshot was taken.
            config.write_bytes(CHANGED_IN_BODY)
            tx.write(CONFIG, WRITTEN)
    except guard.GuardError:
        refused = True

    if refused:
        assert config.read_bytes() == CHANGED_IN_BODY, "a refusal leaves the file as it was"
        return
    # Committed: the overwritten bytes must be recoverable, and undo must get them back.
    assert _store_holds(store, CHANGED_IN_BODY), "the overwritten bytes are in no snapshot"
    guard.undo(store=store)
    assert config.read_bytes() == CHANGED_IN_BODY
