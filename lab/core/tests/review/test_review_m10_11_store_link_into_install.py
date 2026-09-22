# Probe from review of m10/11-write-gate; reproduces: a link inside the store (journal/, objects/, manifests/) carries guard's journal and the pre-write snapshot into the install, Data/ included
"""L1: "No temp files, caches or lock files inside the install. Caches and the
snapshot store live under the user data directory." `guard` enforces this with
`_refuse_store_overlap`, which resolves the store path itself and nothing
below it. A store whose `journal/` (or `objects/`, or `manifests/`)
subdirectory is a link into the install passes that check, and the
transaction then writes its journal record (or snapshot objects, or the
manifest) through the link into the install: here into `Data/`, which no
code in this repository may touch (ADR-0023). The gate's docstring says links
planted in advance never carry a write out of the install; this one carries a
write into it, outside every check the gate makes.

Expected: the transaction refuses on enter (GuardError) and nothing lands in
the install. Positive control: the same transaction with a plain store
commits and its journal record is under the store.

Constructed input: a synthetic install in `tmp_path`, the process table
injected through `psutil.process_iter` exactly as the graders do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from pydantic import BaseModel

from wowlab_core import guard

FLAVOR_FOLDER = "_retail_"  # tests may name a flavor folder (L6)


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


def _install(tmp_path: Path) -> tuple[Path, Flavor]:
    root = tmp_path / "world" / "World of Warcraft"
    files = {
        ".build.info": b"Branch!STRING:0|Active!DEC:1\nus|1\n",
        "Data/data/data.001": b"\x00" * 16,
        f"{FLAVOR_FOLDER}/.flavor.info": b"Product Flavor!STRING:0\nwow\n",
        f"{FLAVOR_FOLDER}/WTF/Config.wtf": b'SET portal "US"\n',
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root, Flavor(folder=FLAVOR_FOLDER, version=None, path=root / FLAVOR_FOLDER)


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this platform or account cannot create symlinks: {exc}")


def test_constructed_plain_store_commits_with_its_journal_under_the_store(
    tmp_path: Path,
) -> None:
    """Positive control."""
    root, flavor = _install(tmp_path)
    store = tmp_path / "store"
    before = _tree(root / "Data")
    with guard.transaction(flavor, label="control", store=store) as tx:
        tx.write("WTF/Config.wtf", b'SET portal "EU"\n')
    assert guard.history(store=store)[-1].state == "committed"
    assert list((store / "journal").glob("*.json"))
    assert _tree(root / "Data") == before


@pytest.mark.parametrize("subdir", ["journal", "objects", "manifests"])
def test_constructed_store_subdirectory_linked_into_the_install_is_refused(
    tmp_path: Path, subdir: str
) -> None:
    root, flavor = _install(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    _link(store / subdir, root / "Data")
    before = _tree(root / "Data")
    raised: Any = None
    try:
        with guard.transaction(flavor, label="linked store", store=store) as tx:
            tx.write("WTF/Config.wtf", b'SET portal "EU"\n')
    except guard.GuardError as exc:
        raised = exc
    assert _tree(root / "Data") == before, "guard wrote into Data/ through a store link"
    assert raised is not None, "a store that reaches into the install must be refused"
