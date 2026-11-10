"""Graders for M16-69: leftover temp files are cleaned before the pre-write
snapshot (M16-69T).

Written before M16-69, from the ticket in docs/BACKLOG.md, the M16-13
security review it names (the snapshot taken before the cleanup holds the
leftovers, so a later whole restore that is not filtered by the file map
could bring one back at a path no journal names as a temp; the reviewer
recommends cleaning up first, keeping snapshots lossless, rather than
excluding temp names from snapshots), docs/LAB_PLAN.md §6.10 amendment
item 2 (which removals happen, and that each is journaled in the new record
before its unlink) and ADR-0021. Every grader carries one marker line that
M16-69 deletes; nothing else in this file is the implementer's to change.

What changes, and what does not:

- The new record's pre-write snapshot holds no leftover temp that the
  cleanup removed, on both routes that clean (`transaction()` and `undo()`).
- The removals are still journaled in the new record's `temps_removed`, and
  the files are gone from the install.
- Snapshots stay lossless: a temp-like file no record names (the player's,
  not guard's) and a leftover whose removal failed are still on disk, so the
  snapshot holds them with their bytes. Temp names are not filtered out.
- A whole restore (`tx.restore(snapshot_id)` with `paths=None`) of the
  cleaning record's snapshot brings no removed leftover back.

Everything is constructed, as in `test_guard_owner_decisions.py`, whose
synthetic install, fixtures and dead-child helper (`leave_temps`) this file
loads rather than copies: the install is a tree under `tmp_path`, the user
data directory is redirected, the process table is injected.
"""

from __future__ import annotations

import errno
import importlib.util
import os
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import Manifest, SnapshotStore


def _load(name: str, filename: str) -> types.ModuleType:
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OD = _load("_m16_69_owner_graders", "test_guard_owner_decisions.py")
G = OD.G

_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
idle = G.idle
children = OD.children

Flavor = G.Flavor
CONFIG = G.CONFIG
FONT = G.FONT
NEWER_CONFIG = G.NEWER_CONFIG
SECOND_FONT = OD.SECOND_FONT
content = G.content
flavor_key = G.flavor_key
record_for = G.record_for
sha = G.sha
leave_temps = OD.leave_temps

# A temp-like name no journal record names: the player's file, not guard's.
LOOKALIKE = "WTF/.wowlab-0123456789abcdef0123456789abcdef.tmp"  # constructed
LOOKALIKE_BYTES = b"constructed: a file under a temp-like name that no record names\n"


def _entries_for(manifest: Manifest, rel: str) -> list[Any]:
    """Entries for a flavor-relative path, however the snapshot is rooted."""
    return [e for e in manifest.entries if e.path == rel or e.path.endswith("/" + rel)]


def _last_record(store: Path, guard: Any) -> Any:
    return guard.history(store=store)[-1]


def _enter(guard: Any, flavor: Flavor, store: Path, route: str) -> Any:
    """One enter of the flavor that cleans, by `route`; its journal record."""
    if route == "transaction":
        with guard.transaction(flavor, label="next", store=store) as tx:
            tx.write(FONT, SECOND_FONT)
        return record_for(guard, SnapshotStore(store), "next")
    guard.undo(store=store)  # undoes the dead child's record
    record = _last_record(store, guard)
    assert record.label != "child-died", "undo journals a record of its own"
    return record


class FailingUnlink:
    """`os.unlink`/`os.remove` raise `PermissionError` for one file name; any
    other path is removed as usual."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.refused = 0
        self._real = {n: getattr(os, n) for n in ("unlink", "remove")}

    def arm(self, patched: pytest.MonkeyPatch) -> None:
        for n in ("unlink", "remove"):
            patched.setattr(os, n, self._one(n))

    def _one(self, n: str) -> Callable[..., None]:
        def call(path: Any, *args: Any, **kwargs: Any) -> None:
            if not isinstance(path, int) and Path(os.fsdecode(path)).name == self.name:
                self.refused += 1
                raise PermissionError(errno.EACCES, "constructed: unlink refused", path)
            self._real[n](path, *args, **kwargs)

        return call


# ─── the pre-write snapshot holds no removed leftover ────────────────────────


@pytest.mark.parametrize(
    "route", [pytest.param(r, id=f"constructed-{r}") for r in ("transaction", "undo")]
)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_the_pre_write_snapshot_holds_no_removed_leftover(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    children: list[Any],
    route: str,
) -> None:
    """After an interrupted change, the next enter's pre-write snapshot holds
    neither leftover temp the cleanup removed; the removals are still
    journaled in that record, and the files are gone from the install."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    assert lo.a.is_file() and lo.b.is_file(), "positive control: two leftovers on disk"

    record = _enter(guard, flavor, store, route)

    assert record.state == "committed"
    assert set(record.temps_removed) == {lo.a_rel, lo.b_rel}, "the removals are journaled"
    assert record.temps_left == ()
    assert not lo.a.exists() and not lo.b.exists(), "both leftovers are removed"
    pre = SnapshotStore(store).show(record.snapshot_id)
    for rel in (lo.a_rel, lo.b_rel):
        assert _entries_for(pre, rel) == [], f"cleanup before the snapshot: {rel} not held"
    held = {e.sha256 for e in pre.entries}
    assert sha(lo.a_bytes) not in held and sha(lo.b_bytes) not in held, (
        "no entry under any name holds a removed leftover's bytes"
    )
    # Positive control: the snapshot is the one taken for this record, and it
    # is not empty — the install's other files are held as before.
    config = _entries_for(pre, CONFIG)
    assert len(config) == 1 and config[0].sha256 == sha((flavor.path / CONFIG).read_bytes())


# ─── snapshots stay lossless: only the removed leftovers are missing ─────────


@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_the_snapshot_still_holds_what_the_cleanup_leaves_on_disk(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    children: list[Any],
) -> None:
    """Cleaning first, not filtering temp names: a temp-like file no record
    names, and a leftover whose unlink failed (moved to `temps_left`), stay
    on disk and in the pre-write snapshot with their bytes; the leftover
    that was removed is in neither."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    lookalike = flavor.path / LOOKALIKE
    lookalike.write_bytes(LOOKALIKE_BYTES)
    failing = FailingUnlink(lo.b.name)

    with pytest.MonkeyPatch.context() as patched:
        failing.arm(patched)
        with guard.transaction(flavor, label="next", store=store) as tx:
            tx.write(FONT, SECOND_FONT)
    record = record_for(guard, SnapshotStore(store), "next")

    assert failing.refused >= 1, "positive control: guard tried to remove the leftover"
    assert record.state == "committed", "a failed removal does not stop the transaction"
    assert lo.a_rel in record.temps_removed
    assert lo.b_rel in record.temps_left and lo.b_rel not in record.temps_removed
    assert LOOKALIKE not in record.temps_removed and LOOKALIKE not in record.temps_left
    assert not lo.a.exists()
    assert lo.b.read_bytes() == lo.b_bytes, "a leftover whose removal failed is left alone"
    assert lookalike.read_bytes() == LOOKALIKE_BYTES, "the cleanup never deletes anything else"

    pre = SnapshotStore(store).show(record.snapshot_id)
    assert _entries_for(pre, lo.a_rel) == [], "the removed leftover is not held"
    (left,) = _entries_for(pre, lo.b_rel)
    assert left.sha256 == sha(lo.b_bytes), "lossless: the leftover still on disk is held"
    (theirs,) = _entries_for(pre, LOOKALIKE)
    assert theirs.sha256 == sha(LOOKALIKE_BYTES), "lossless: temp names are not filtered out"


# ─── a whole restore brings no removed leftover back ─────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_a_whole_restore_of_the_cleaning_snapshot_brings_no_leftover_back(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    children: list[Any],
) -> None:
    """The review's case: a restore of every file the cleaning record's
    pre-write snapshot holds (not filtered by the file map) puts the
    install back as it was before that change, without the leftovers the
    change removed reappearing at paths no later record names as temps."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    expected = content(world)
    for rel in (lo.a_rel, lo.b_rel):
        del expected[flavor_key(rel)]

    with guard.transaction(flavor, label="next", store=store) as tx:
        tx.write(CONFIG, NEWER_CONFIG)
    cleaning = record_for(guard, SnapshotStore(store), "next")
    assert set(cleaning.temps_removed) == {lo.a_rel, lo.b_rel}

    with guard.transaction(flavor, label="back", store=store) as tx:
        tx.restore(cleaning.snapshot_id)
    back = record_for(guard, SnapshotStore(store), "back")

    assert back.state == "committed"
    assert (flavor.path / CONFIG).read_bytes() != NEWER_CONFIG, "positive control: restored"
    assert not lo.a.exists() and not lo.b.exists(), "no removed leftover is brought back"
    assert content(world) == expected
