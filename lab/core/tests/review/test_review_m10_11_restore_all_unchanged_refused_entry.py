# Probe from review of m10/11-write-gate; reproduces: restore(snapshot_id) of guard's own pre-write snapshot is refused whole when an addon folder holds an unchanged file with an executable suffix
"""§6.10: `tx.restore(snapshot_id, paths=None)` puts back a snapshot. The
pre-write snapshot guard takes covers the whole of `Interface/`, so it holds
every file of every installed addon, including ones the gate will never write
(an addon checked out from source commonly carries a `build.sh` or a `.js`
tool). `restore` with `paths=None` refuses the whole snapshot when any entry
is refused by the lexical rules, "even an unchanged one". So one untouched
`Interface/AddOns/Dev/build.sh` makes every full restore of every snapshot of
that install fail, permanently, although restoring it needs no write at all.
Links already get this exception (an unchanged link is skipped); a refused
file whose bytes on disk already match the snapshot needs it for the same
reason.

Expected: an entry refused only because the gate never writes that kind of
file, whose on-disk bytes already equal the snapshot's, is skipped, and the
rest of the snapshot is restored. A refused entry that differs from disk
still stops the whole restore (control below, which passes today). Positive
control: the same restore without the `.sh` file succeeds.

Constructed input: a synthetic install in `tmp_path`, the process table
injected through `psutil.process_iter` exactly as the graders do.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import psutil
import pytest
from pydantic import BaseModel

from wowlab_core import guard

FLAVOR_FOLDER = "_retail_"  # tests may name a flavor folder (L6)
ORIGINAL = b'SET portal "US"\n'
CHANGED = b'SET portal "EU"\n'
SCRIPT = "Interface/AddOns/Dev/build.sh"


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


def _install(tmp_path: Path, *, with_script: bool) -> Flavor:
    root = tmp_path / "world" / "World of Warcraft"
    files = {
        ".build.info": b"Branch!STRING:0|Active!DEC:1\nus|1\n",
        f"{FLAVOR_FOLDER}/.flavor.info": b"Product Flavor!STRING:0\nwow\n",
        f"{FLAVOR_FOLDER}/WTF/Config.wtf": ORIGINAL,
        f"{FLAVOR_FOLDER}/Interface/AddOns/Dev/Dev.toc": b"## Title: Dev\n",
    }
    if with_script:
        files[f"{FLAVOR_FOLDER}/{SCRIPT}"] = b"#!/bin/sh\necho package\n"
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return Flavor(folder=FLAVOR_FOLDER, version=None, path=root / FLAVOR_FOLDER)


def _change(flavor: Flavor, store: Path) -> str:
    with guard.transaction(flavor, label="change", store=store) as tx:
        tx.write("WTF/Config.wtf", CHANGED)
    return guard.history(store=store)[-1].snapshot_id


def _restore_all(flavor: Flavor, store: Path, snapshot_id: str) -> None:
    with guard.transaction(flavor, label="restore all", store=store) as tx:
        tx.restore(snapshot_id)


def test_constructed_restore_all_of_the_pre_write_snapshot_works(tmp_path: Path) -> None:
    """Positive control: no refused entry in the snapshot."""
    flavor = _install(tmp_path, with_script=False)
    store = tmp_path / "store"
    _restore_all(flavor, store, _change(flavor, store))
    assert (flavor.path / "WTF/Config.wtf").read_bytes() == ORIGINAL


def test_constructed_refused_entry_that_differs_from_disk_still_stops_the_restore(
    tmp_path: Path,
) -> None:
    """Control: a refused entry whose restore would need a write stops it all."""
    flavor = _install(tmp_path, with_script=True)
    store = tmp_path / "store"
    snapshot_id = _change(flavor, store)
    (flavor.path / SCRIPT).write_bytes(b"#!/bin/sh\necho edited by hand\n")
    with pytest.raises(guard.PathNotAllowedError):
        _restore_all(flavor, store, snapshot_id)
    assert (flavor.path / "WTF/Config.wtf").read_bytes() == CHANGED


def test_constructed_unchanged_refused_entry_does_not_block_restore_all(tmp_path: Path) -> None:
    flavor = _install(tmp_path, with_script=True)
    store = tmp_path / "store"
    _restore_all(flavor, store, _change(flavor, store))
    assert (flavor.path / "WTF/Config.wtf").read_bytes() == ORIGINAL
