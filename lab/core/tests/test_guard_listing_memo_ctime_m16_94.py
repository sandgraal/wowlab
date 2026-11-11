"""Graders for the listing memo's ctime key (M16-94T).

Written before M16-94, from the ticket text (M16-94T and M16-94 in
docs/BACKLOG.md) and docs/LAB_PLAN.md §18.1 item 6 with its amendment of
2026-11-11. Every grader carries one marker line that M16-94 deletes;
nothing else in this file is the implementer's to change.

What the amendment asks for
---------------------------
§18.1 item 6 keys guard's per-transaction directory-listing memo by the
directory's identity (dev, ino, mtime_ns). The amendment of 2026-11-11
(owner decision): "the key also holds the directory's ctime_ns. On POSIX a
change to the directory that leaves its mtime where it was still changes
its ctime, so the memo is not reused. On Windows `st_ctime_ns` is the
creation time and does not move, so the extra field adds nothing there and
the Windows memo is unchanged."

The scenario
------------
Inside one transaction, `SV` (the M16-23T graders' directory) is memoized
by lookups that write nothing. Someone else then changes what `SV` lists (a
file added, a file removed) and puts `SV`'s mtime back to the value it had
with `os.utime`. The directory keeps its device and inode and, by
construction, its mtime, so the M16-23 key (dev, ino, mtime_ns) is the one
the memo holds. Its ctime cannot be set from user space: any change to the
directory, the `os.utime` call included, sets it to the current time. On a
file system whose ctime clock is coarse, the change and the put-back can
land within one tick of the listing; the grader then waits a few
milliseconds and puts the same mtime back again, which moves the ctime and
nothing else, until the ctime differs from what it was when the memo was
filled (a positive control asserts that it does, and that mtime, device and
inode are what the memo saw). Nothing about this is beyond what another
process can do on the owner's machine: `touch -m -d`, a sync tool or an
archiver restoring timestamps all do it.

What is graded is guard's public verdict, which a stale listing gets wrong:
- a second whole restore with nothing to change reports the file someone
  else added in `left_alone` (the public list of files the snapshot does
  not hold, added since it was taken), in a dry run and in a real
  transaction, and still touches nothing; a stale listing reports nothing.
  The same holds for a folder the snapshot holds no file in (`SV/Empty`):
  no per-file lookup lists it, only the whole restore's walk of what it
  leaves alone does, so a key checked only on per-file lookups is caught
  there (security review of #461, round 1);
- a name that differs only by case from the file someone else added is
  refused with `PathNotAllowedError` naming the path, and `SV` is listed
  again (the M16-23T listing seam). On a case-insensitive volume the stale
  path already fails closed (the name resolves to a file the listing does
  not hold), so there only the listing count tells; on a case-sensitive
  one, the verdict does;
- a name freed by someone else's delete, spelled with different case, is
  accepted and written, where a stale listing still holding the old name
  refuses it as a case collision. This one is platform-independent and
  public.

Platforms
---------
POSIX requires `utime` and every directory-entry change to update st_ctime,
and Linux and macOS (APFS) keep it in nanoseconds. On Windows, Python 3.12
documents `st_ctime` / `st_ctime_ns` as the creation time (deprecated
there; a later Python will make it the metadata-change time), so a
directory's ctime does not move when its listing changes and the
amendment's key cannot catch this scenario. The graders are POSIX only and
skip on Windows with that reason, as the §18.1 amendment says (review
update to the amendment, 2026-11-11).

How the listing is observed, and the fixtures, are the M16-23T graders'
(`test_guard_listing_memo.py`), loaded by path and reused, not copied.
Everything here is constructed (L8 allows it: guard's inputs are our own
layout): the M10-11T synthetic install under `tmp_path`, a store under
`tmp_path`, the user data directory redirected, the process table injected.
No test needs or touches a real install.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest


def _load(name: str, filename: str) -> types.ModuleType:
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


M = _load("_m16_94t_memo_graders", "test_guard_listing_memo.py")

# The M10-11T fixtures, registered here under their own names (as M16-23T
# does): the user data directory and the process table are redirected for
# every test in this file.
_user_data_redirected = M._user_data_redirected
_real_process_table_is_never_listed = M._real_process_table_is_never_listed
guard = M.guard
world = M.world
install_root = M.install_root
flavor = M.flavor
idle = M.idle

Flavor = M.Flavor
SV = M.SV
SV_FILES = M.SV_FILES
WRITTEN = M.WRITTEN
EXTERNAL = M.EXTERNAL
refused = M.refused
watching = M.watching
_put = M._put
_seed_snapshot = M._seed_snapshot
_own_snapshot = M._own_snapshot
_warm = M._warm
_assert_listed_once = M._assert_listed_once

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "on Windows, Python 3.12's st_ctime_ns is the creation time, which does not "
        "move when a directory's listing changes; the ctime key cannot see this "
        "scenario there, so these graders are POSIX only (§18.1 item 6, amended 2026-11-11)"
    ),
)

# How long the grader waits for a coarse ctime clock to tick.
CTIME_PATIENCE_S = 2.0


# ─── helpers ─────────────────────────────────────────────────────────────────


def _key(directory: Path) -> tuple[int, int, int, int]:
    """(dev, ino, mtime_ns, ctime_ns) of `directory`, by `lstat`."""
    st = directory.lstat()
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns)


def _mtime_put_back_ctime_moved(directory: Path, memoized: tuple[int, int, int, int]) -> None:
    """Put `directory`'s mtime back to the one the memo saw, and make sure its
    ctime has moved from the one the memo saw: on a coarse ctime clock, wait
    and put the same mtime back again until it ticks. Then hold the
    scenario's positive controls: same device, same inode, same mtime, a
    different ctime."""
    dev, ino, mtime_ns, ctime_ns = memoized
    deadline = time.monotonic() + CTIME_PATIENCE_S
    while True:
        st = directory.lstat()
        os.utime(directory, ns=(st.st_atime_ns, mtime_ns))
        if directory.lstat().st_ctime_ns != ctime_ns or time.monotonic() > deadline:
            break
        time.sleep(0.005)
    now = _key(directory)
    assert now[:3] == (dev, ino, mtime_ns), (
        f"positive control: {directory} must keep the (dev, ino, mtime_ns) the memo saw, "
        f"{(dev, ino, mtime_ns)}; it has {now[:3]}"
    )
    assert now[3] != ctime_ns, (
        f"positive control: {directory}'s ctime did not move from {ctime_ns} within "
        f"{CTIME_PATIENCE_S} s of changes to it"
    )


# ─── 1. a whole restore sees the file someone else added ─────────────────────


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
def test_constructed_a_whole_restore_sees_a_file_added_with_the_mtime_put_back(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """A no-op whole restore memoizes `SV`; it leaves nothing alone there
    (`left_alone` is empty). Someone else adds `Delta.lua` there and puts
    `SV`'s mtime back; its ctime moves. A second whole restore of the same
    snapshot reports `Delta.lua` in `left_alone` (a regular file the
    snapshot does not hold, added since), still changes nothing, and leaves
    `Delta.lua` as someone else wrote it. A stale listing reports nothing."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    directory = flavor.path / SV
    added = f"{SV}/Delta.lua"
    seed = _seed_snapshot(guard, flavor, store) if dry_run else None
    with guard.transaction(flavor, label="ctime-restore", store=store, dry_run=dry_run) as tx:
        snapshot_id = seed if dry_run else _own_snapshot(guard, store, "ctime-restore")
        assert snapshot_id is not None
        with watching(flavor, SV) as listings:
            tx.restore(snapshot_id)
            assert tx.plan == (), "positive control: the first restore had nothing to change"
            assert not [p for p in tx.left_alone if p.startswith(SV + "/")], tx.left_alone
            assert listings.calls[SV] == 1, "positive control: the first restore listed SV"
            memoized = _key(directory)
            (directory / "Delta.lua").write_bytes(EXTERNAL)
            _mtime_put_back_ctime_moved(directory, memoized)
            tx.restore(snapshot_id)
            assert added in tx.left_alone, (
                f"the whole restore passed over {added}, which someone else added with "
                f"{SV}'s mtime put back; left_alone: {tx.left_alone}"
            )
            assert tx.plan == (), f"a whole restore never touches {added}: {tx.plan}"
            assert listings.calls[SV] >= 2, "the restore after the change did not list SV again"
    assert (directory / "Delta.lua").read_bytes() == EXTERNAL


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
def test_constructed_a_whole_restore_walks_a_folder_it_holds_no_file_in_again(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """`SV/Empty` exists, with no file in it, when the snapshot is taken, so
    no per-file lookup of the restore ever lists it: only the walk of what a
    whole restore leaves alone does. The first whole restore lists it once
    and leaves nothing alone there. Someone else adds `SV/Empty/Late.lua`
    and puts `SV/Empty`'s mtime back; its ctime moves. The second whole
    restore lists `SV/Empty` again and reports `Late.lua` in `left_alone`,
    and changes nothing."""
    _put(flavor, SV_FILES)
    empty_rel = f"{SV}/Empty"
    empty = flavor.path / empty_rel
    empty.mkdir()
    late = f"{empty_rel}/Late.lua"
    store = tmp_path / "store"
    seed = _seed_snapshot(guard, flavor, store) if dry_run else None
    with guard.transaction(flavor, label="ctime-empty", store=store, dry_run=dry_run) as tx:
        snapshot_id = seed if dry_run else _own_snapshot(guard, store, "ctime-empty")
        assert snapshot_id is not None
        with watching(flavor, empty_rel) as listings:
            tx.restore(snapshot_id)
            assert tx.plan == (), "positive control: the first restore had nothing to change"
            assert not [p for p in tx.left_alone if p.startswith(empty_rel + "/")], tx.left_alone
            assert listings.calls[empty_rel] == 1, (
                f"positive control: the first whole restore lists {empty_rel} once, "
                f"listed {listings.calls[empty_rel]} times"
            )
            memoized = _key(empty)
            (empty / "Late.lua").write_bytes(EXTERNAL)
            _mtime_put_back_ctime_moved(empty, memoized)
            tx.restore(snapshot_id)
            assert late in tx.left_alone, (
                f"the whole restore's walk passed over {late}, which someone else added with "
                f"{empty_rel}'s mtime put back; left_alone: {tx.left_alone}"
            )
            assert listings.calls[empty_rel] >= 2, (
                f"{empty_rel} was not listed again by the second whole restore; the walk of "
                "what it leaves alone served the old listing from the memo"
            )
            assert tx.plan == (), f"a whole restore never touches {late}: {tx.plan}"
    assert (empty / "Late.lua").read_bytes() == EXTERNAL


# ─── 2. a case variant of a file someone else added is refused ───────────────


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
def test_constructed_a_file_added_with_the_mtime_put_back_is_seen_by_the_next_lookup(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """Lookups that write nothing memoize `SV`. Someone else adds `Delta.lua`
    and puts `SV`'s mtime back; its ctime moves. The next lookup lists `SV`
    again, and `delta.lua`, which differs only by case, is refused with
    `PathNotAllowedError` naming the path; nothing is written for it."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    directory = flavor.path / SV
    target = f"{SV}/delta.lua"
    with guard.transaction(flavor, label="ctime-added", store=store, dry_run=dry_run) as tx:
        own = None if dry_run else _own_snapshot(guard, store, "ctime-added")
        with watching(flavor, SV) as listings:
            _warm(tx, dry_run=dry_run, snapshot_id=own, rels=sorted(SV_FILES))
            _assert_listed_once(listings, SV, len(SV_FILES))
            memoized = _key(directory)
            (directory / "Delta.lua").write_bytes(EXTERNAL)
            _mtime_put_back_ctime_moved(directory, memoized)
            with refused(guard, "PathNotAllowedError", naming=target):
                tx.write(target, WRITTEN)
            assert listings.calls[SV] >= 2, (
                f"{SV} was not listed again after someone else changed it with its mtime "
                "put back; the memo served the old listing"
            )
        assert all(item.path != target for item in tx.plan), tx.plan
    names = sorted(p.name for p in directory.iterdir())
    assert "delta.lua" not in names, names
    assert (directory / "Delta.lua").read_bytes() == EXTERNAL


# ─── 3. a name someone else freed is free ────────────────────────────────────


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
def test_constructed_a_name_removed_with_the_mtime_put_back_is_free_for_the_next_lookup(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """Lookups that write nothing memoize `SV`. Someone else removes
    `Charlie.lua` and puts `SV`'s mtime back; its ctime moves. `CHARLIE.lua`
    no longer collides with anything on disk: it is accepted (planned in a
    dry run, written and committed in a real transaction). A stale listing
    still holding `Charlie.lua` would refuse it as a case collision."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    directory = flavor.path / SV
    target = f"{SV}/CHARLIE.lua"
    with guard.transaction(flavor, label="ctime-removed", store=store, dry_run=dry_run) as tx:
        own = None if dry_run else _own_snapshot(guard, store, "ctime-removed")
        with watching(flavor, SV) as listings:
            _warm(tx, dry_run=dry_run, snapshot_id=own, rels=sorted(SV_FILES))
            _assert_listed_once(listings, SV, len(SV_FILES))
            memoized = _key(directory)
            (directory / "Charlie.lua").unlink()
            _mtime_put_back_ctime_moved(directory, memoized)
            tx.write(target, WRITTEN)
            assert listings.calls[SV] >= 2, "the lookup after the change did not list SV again"
        assert target in {item.path for item in tx.plan}, tx.plan
    names = sorted(p.name for p in directory.iterdir())
    assert "Charlie.lua" not in names, names
    if dry_run:
        assert "CHARLIE.lua" not in names, names
    else:
        assert "CHARLIE.lua" in names, names
        assert (directory / "CHARLIE.lua").read_bytes() == WRITTEN
        record = next(r for r in guard.history(store=store) if r.label == "ctime-removed")
        assert record.state == "committed", record.state
