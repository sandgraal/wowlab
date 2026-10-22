"""Graders for "a whole restore lists what it leaves alone" (M16-11T).

Written before M16-11, from docs/LAB_PLAN.md §18.1 item 3 ("A whole restore
lists what it leaves alone"), the M16-11 ticket in docs/BACKLOG.md, §6.10
(`tx.restore(snapshot_id, paths=None)`: a whole restore puts back every file
the snapshot holds; a named path the snapshot's subtrees cover but does not
hold is deleted), ADR-0013, ADR-0021 and invariants L1 and L2. Every grader
carries one marker line that M16-11 deletes; nothing else in this file is the
implementer's to change.

Everything here is constructed. The synthetic install, the fixtures and the
helpers are the M10-11T ones in `test_guard.py` (loaded, not copied): the
install is a tree under `tmp_path`, the store a directory under `tmp_path`,
the user data directory is redirected and the process table is injected.

The seam these graders hold `guard` to
--------------------------------------
§18.1 and the ticket say the plan lists the left-alone files; they do not
say how. The smallest shape that keeps them in "the plan" is pinned here:

- `PlanItem` gains `left_alone: bool`. Every item a write, a delete or a
  restore changes (or, in a dry run, would change) has `left_alone is
  False`, as today's items do.
- After `tx.restore(snapshot_id)` (no `paths`), `tx.plan` also holds one
  item with `left_alone is True` for every regular file now on disk under
  one of the snapshot's subtrees (`Manifest.subtrees`, the subtrees the
  restore walks) that the snapshot does not hold, i.e. every file added
  since the snapshot. Its `path` is flavor-relative and `/`-separated, as
  for any plan item, and `before == after` (nothing changes). This holds in
  a dry run and in a transaction that commits.
- A left-alone file is never written or deleted: its bytes, inode and
  mtime are unchanged after commit, the journal record does not name it,
  and a later `undo()` does not touch it.
- A file outside the snapshot's subtrees is never listed, and neither is
  a file the snapshot holds (changed or not); those keep their ordinary
  items, `left_alone is False`.
- `tx.restore(snapshot_id, paths=[...])` lists no left-alone item, even
  when added files sit under the subtrees.

Not pinned: the order of items, the `size` of a left-alone item, whether
`before` and `after` hold the file's hash or `None`, and how the CLI prints
the heading (M16-11's own acceptance).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore


def _load_m10_11t_graders() -> types.ModuleType:
    """`test_guard.py` as a module of its own, for its synthetic install,
    fixtures and helpers (as `test_guard_owner_decisions.py` loads it)."""
    path = Path(__file__).resolve().parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_m16_11t_base_graders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = _load_m10_11t_graders()

# The M10-11T fixtures, registered here under their own names: the user data
# directory and the process table are redirected for every test in this file.
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
store = G.store
idle = G.idle

Flavor = G.Flavor
CONFIG = G.CONFIG
BINDINGS = G.BINDINGS
TOC = G.TOC
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
FLAVOR_ONLY_FILES = G.FLAVOR_ONLY_FILES
NEW_CONFIG = G.NEW_CONFIG
sha = G.sha
record_for = G.record_for
changes = G.changes

# Files added after the snapshot, one under each default subtree (§6.9:
# `WTF/`, `Interface/AddOns/`, `Fonts/`, loose `Interface/` files), some in
# directories that did not exist when the snapshot was taken.
ADDED: dict[str, bytes] = {
    "WTF/Account/ACCT/SavedVariables/Added.lua": b'AddedDB = {\n\t["x"] = 1,\n}\n',
    "WTF/Account/OTHER/bindings-cache.wtf": b"bind S MOVEBACKWARD\n",
    "Interface/AddOns/NewAddon/NewAddon.toc": b"## Interface: 0\n## Title: New\n",
    "Interface/AddOns/Thing/Added.lua": b"-- added since\n",
    "Fonts/ADDED.TTF": b"\x00\x01\x00\x00added-font",
    "Interface/Icons/added.blp": b"BLP2\x05\x05",
}

# Files added after the snapshot outside every subtree it covers.
ADDED_OUTSIDE: dict[str, bytes] = {
    "Logs/Added.log": b"constructed log\n",
    "Screenshots/WoWScrnShot_020226_000000.jpg": b"\xff\xd8 constructed added",
    "Cache/ADB/enUS/Added.bin": b"XFTH constructed added",
}


def _put(base: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _state(base: Path, rels: list[str]) -> dict[str, tuple[bytes, int, int, int]]:
    out: dict[str, tuple[bytes, int, int, int]] = {}
    for rel in rels:
        st = (base / rel).lstat()
        out[rel] = ((base / rel).read_bytes(), st.st_ino, st.st_mtime_ns, st.st_mode)
    return out


def _snapshot_then_change(guard: Any, flavor: Flavor, store: SnapshotStore) -> str:
    """A snapshot of the synthetic install (the pre-write snapshot of a
    transaction), then a change it can restore: `CONFIG` rewritten and
    `BINDINGS` deleted. Returns the snapshot id."""
    with guard.transaction(flavor, label="change", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
    return str(record_for(guard, store, "change").snapshot_id)


def _left_alone(plan: Any) -> dict[str, Any]:
    return {item.path: item for item in plan if item.left_alone is True}


def _changed(plan: Any) -> dict[str, Any]:
    return {item.path: item for item in plan if item.left_alone is False}


# ─── the seam ────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_plan_item_has_left_alone_false_for_writes_and_deletes(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    with guard.transaction(flavor, label="plain", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
        plan = tx.plan
    assert {item.path for item in plan} == {CONFIG, BINDINGS}
    for item in plan:
        assert item.left_alone is False, item


# ─── a whole restore lists the added files ──────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.parametrize(
    "dry_run", [True, False], ids=["constructed-dry-run", "constructed-commit"]
)
def test_constructed_whole_restore_lists_every_added_file_as_left_alone(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)

    with guard.transaction(flavor, label="restore", store=store.path, dry_run=dry_run) as tx:
        tx.restore(snapshot_id)
        plan = tx.plan

    left = _left_alone(plan)
    assert set(left) == set(ADDED)
    for rel, item in left.items():
        assert item.before == item.after, (rel, item)
    # What the restore changes keeps its ordinary items.
    assert changes(_changed(plan).values()) == {
        CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG])),
        BINDINGS: (None, sha(ALLOWLISTED_FILES[BINDINGS])),
    }


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.parametrize("rel", sorted(ADDED), ids=lambda rel: f"constructed-{rel}")
def test_constructed_each_subtree_lists_a_single_added_file(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, rel: str
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, {rel: ADDED[rel]})

    with guard.transaction(flavor, label="restore", store=store.path, dry_run=True) as tx:
        tx.restore(snapshot_id)
        plan = tx.plan

    assert set(_left_alone(plan)) == {rel}


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_whole_restore_with_nothing_added_lists_nothing_left_alone(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)

    with guard.transaction(flavor, label="restore", store=store.path) as tx:
        tx.restore(snapshot_id)
        plan = tx.plan

    assert _left_alone(plan) == {}
    assert set(_changed(plan)) == {CONFIG, BINDINGS}


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_files_outside_the_subtrees_and_files_the_snapshot_holds_are_not_listed(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """Positive control beside the negative: the added file under a subtree
    is listed, the ones outside every subtree and the ones the snapshot holds
    (changed, deleted or untouched since) are not."""
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    snapshot = store.show(snapshot_id)
    prefix = flavor.folder + "/"
    for rel in ADDED_OUTSIDE:
        assert not any((prefix + rel).startswith(s + "/") for s in snapshot.subtrees), rel
    added = "Interface/AddOns/Thing/Added.lua"
    _put(flavor.path, {added: ADDED[added]})
    _put(flavor.path, ADDED_OUTSIDE)

    with guard.transaction(flavor, label="restore", store=store.path, dry_run=True) as tx:
        tx.restore(snapshot_id)
        plan = tx.plan

    left = _left_alone(plan)
    assert set(left) == {added}
    for rel in (*ADDED_OUTSIDE, *FLAVOR_ONLY_FILES, *ALLOWLISTED_FILES):
        assert rel not in left, rel


# ─── left alone means never written or deleted ──────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_left_alone_files_are_never_written_deleted_or_journaled(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)
    before = _state(flavor.path, sorted(ADDED))

    with guard.transaction(flavor, label="restore", store=store.path) as tx:
        tx.restore(snapshot_id)
        assert set(_left_alone(tx.plan)) == set(ADDED)

    # The restore did what it is for ...
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
    assert (flavor.path / BINDINGS).read_bytes() == ALLOWLISTED_FILES[BINDINGS]
    # ... and the added files are exactly as they were: same bytes, same
    # inode (no replace), same mtime (no rewrite), same mode.
    assert _state(flavor.path, sorted(ADDED)) == before
    # The journal names only what was touched.
    assert changes(record_for(guard, store, "restore").paths) == {
        CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG])),
        BINDINGS: (None, sha(ALLOWLISTED_FILES[BINDINGS])),
    }

    # Undoing the restore does not touch them either.
    guard.undo(store=store.path)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    assert not (flavor.path / BINDINGS).exists()
    assert _state(flavor.path, sorted(ADDED)) == before


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_dry_run_whole_restore_leaves_the_install_untouched(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)
    before = G.strict_state(flavor.path)

    with guard.transaction(flavor, label="restore", store=store.path, dry_run=True) as tx:
        tx.restore(snapshot_id)
        assert set(_left_alone(tx.plan)) == set(ADDED)

    assert G.strict_state(flavor.path) == before


# ─── a restore by name lists none ───────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.parametrize(
    "dry_run", [True, False], ids=["constructed-dry-run", "constructed-commit"]
)
def test_constructed_restore_by_paths_lists_no_left_alone_item(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)
    others = sorted(ADDED)
    before = _state(flavor.path, others)

    with guard.transaction(flavor, label="restore", store=store.path, dry_run=dry_run) as tx:
        tx.restore(snapshot_id, paths=[CONFIG, BINDINGS])
        plan = tx.plan

    assert _left_alone(plan) == {}
    assert changes(_changed(plan).values()) == {
        CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG])),
        BINDINGS: (None, sha(ALLOWLISTED_FILES[BINDINGS])),
    }
    assert {item.path for item in plan} == {CONFIG, BINDINGS}
    assert _state(flavor.path, others) == before


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_restore_naming_an_added_file_deletes_it_as_an_ordinary_item(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """§6.10: a named path the subtrees cover but the snapshot does not hold
    is deleted. Named, it is a change, not a left-alone item; the other added
    files are not listed at all."""
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)
    named = "Interface/AddOns/Thing/Added.lua"
    others = sorted(set(ADDED) - {named})
    before = _state(flavor.path, others)

    with guard.transaction(flavor, label="restore", store=store.path) as tx:
        tx.restore(snapshot_id, paths=[named])
        plan = tx.plan

    assert _left_alone(plan) == {}
    assert changes(_changed(plan).values()) == {named: (sha(ADDED[named]), None)}
    assert not (flavor.path / named).exists()
    assert _state(flavor.path, others) == before
