"""Graders for guard's directory-listing memo (M16-23T).

Written before M16-23, from the ticket text (M16-23T and M16-23 in
docs/BACKLOG.md) and docs/LAB_PLAN.md §18.1 item 6, read with §6.10. Every
grader carries one marker line that M16-23 deletes; nothing else in this file
is the implementer's to change.

What §18.1 item 6 asks for
--------------------------
"The gate keeps a per-transaction directory-listing memo, keyed by the
directory's identity (dev, ino, mtime_ns) and invalidated by the
transaction's own writes. The commit-time re-check is unchanged." Today
every lookup of a name lists its parent directory again, so a whole restore
of a tree with N files in one directory lists that directory N times, and
the check grows with the square of the tree.

What the graders hold the code to
---------------------------------
- The memo exists: inside one transaction, a directory whose identity has
  not changed and that the transaction has not written is listed once,
  however many lookups go through it. A whole restore with nothing to change
  lists every directory of the flavor folder at most once, in a dry run and
  in a real transaction.
- The walk scales linearly (`serial`): the no-op whole-restore check of a
  constructed 8,000-file tree takes at most 5 times its time on a
  constructed 2,000-file tree of the same shape. The absolute 20,000-file
  target of §18.1 is M16-27's nightly job, not this file's.
- A change of identity mid-transaction invalidates the memo: a file added
  beside the target by someone else, a directory replaced by another one of
  the same name, and the transaction's own write, directory creation and
  delete (with the directory's mtime put back, as on a file system whose
  clock did not tick, so only the own-write rule can catch it) are all seen
  by the next lookup. What is graded is the gate's verdict, which a stale
  listing gets wrong: a name that differs only by case from one now there is
  refused with `PathNotAllowedError`, and a name freed by the transaction's
  own delete is accepted.
- The commit-time re-check is unchanged: with the directory listing
  memoized, a target replaced, rewritten in place (same size and mtime, so
  the directory and the file's `stat` look unchanged) or removed between
  guard's read and its replace or unlink is still refused with
  `ChangedSinceSnapshotError` naming the path, the swapped-in state is left
  as it is, and the transaction does not commit.
- The key is read before the listing: someone else's change that lands
  right after a listing was read in full (the directory's mtime moves) is
  seen by the next lookup, so an old listing is never kept under the new
  key. Reading the key before and after, and keeping the listing only when
  both agree, also satisfies this.
- The memo lasts one transaction: a second transaction's first lookup lists
  the directory again (`wowlab ui` is one long-running process).
- A listing error is not cached: when listing a directory fails once
  (injected), that lookup raises a `GuardError` naming the path and carrying
  the injected error as a cause, and the next lookup in the same directory
  lists it again: it is neither refused with the old error nor answered from
  an empty listing (a name that collides by case with one on disk is still
  refused), and later lookups are served from the new listing.

How a listing is observed (the seam)
------------------------------------
A directory listing is a call of `os.listdir` or `os.scandir` (what
`pathlib.Path.iterdir`, `os.walk` and `wowlab_core.longpath` call), by path
or by descriptor, looked up at call time. For the length of a test both are
wrapped; a call is attributed to a directory by its real path (an
extended-length `\\\\?\\` prefix is dropped; a descriptor is resolved
through `/proc/self/fd` or `F_GETPATH`). Listings are counted only while the
transaction's body runs, after its pre-write snapshot exists, so the
snapshot's own walk is not counted. A memo that lists through some other
call would not be seen; that is the seam this file pins.

The same-mtime case
-------------------
The memo's key cannot see a change that leaves a directory's (dev, ino,
mtime_ns) as it was, which a file system with a coarse clock allows. Where
a grader makes such a change itself (someone else's file added beside the
target), it makes sure the directory's mtime moved, so that the key does
change and the grader tests the memo, not the clock. The own-write cases do
the opposite on purpose: the spec names the transaction's own writes as an
invalidation of their own.
Someone else's change within one clock tick is a known residual risk of
§18.1's key, accepted by the conductor in review round 1 (2026-10-22) and
not graded: on a case-insensitive volume it fails closed, and at worst it
lets a case-variant sibling through, never a write outside the allowlist.

The timing grader
-----------------
CPU time (`time.process_time()`) of `tx.restore(snapshot_id)` alone, in a dry
run (no pre-write snapshot to time), on two constructed trees of the same
shape. The container and CI runners are shared, so each size is measured
several times and the minimum is kept: load can only add time, so the
minimum is the best estimate of the work. The 2,000-file tree is measured
first; the 8,000-file tree passes as soon as one of its runs is within the
ratio, so a loaded machine costs extra runs, and fails only when every
run is slow. Each 8,000-file run is stopped by a CPU-time alarm
(`ITIMER_PROF`) once it has used 5 times the 2,000-file minimum: a run past
that point has already failed, and today's quadratic walk would otherwise
take minutes per run. The grader is listed in `conftest.py` SERIAL_TESTS.
Measured 2026-10-22 in a container with a load average of 45 on 4 cores:
today the 2,000-file check takes about 2.5 s CPU and the 8,000-file one
about 24 s (9.5 times); with a stand-in memo (listings cached by
(dev, ino, mtime_ns)) 0.45 s and 1.7 s (3.8 times).

Everything here is constructed (L8 allows it: guard's inputs are our own
layout, not a client format): the install is the M10-11T synthetic tree
from `test_guard.py` under `tmp_path`, grown with constructed files, the
store is under `tmp_path`, the user data directory is redirected, and the
process table is injected. No test needs or touches a real install.
"""

from __future__ import annotations

import contextlib
import errno
import importlib.util
import math
import os
import signal
import sys
import threading
import time
import types
from collections import Counter
from collections.abc import Callable, Iterator
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


OD = _load("_m16_23t_owner_graders", "test_guard_owner_decisions.py")
G = OD.G

# The M10-11T fixtures, registered here under their own names: the user data
# directory and the process table are redirected for every test in this file.
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
idle = G.idle

Flavor = G.Flavor
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
SAVED = G.SAVED
CONFIG = G.CONFIG
sha = G.sha
refused = OD.refused
AfterTempFsync = OD.AfterTempFsync
AfterJournalFsync = OD.AfterJournalFsync
EXTERNAL = OD.EXTERNAL
_swap = OD._swap

# The directory the per-directory graders work in, flavor-relative. It holds
# `SAVED` (from the M10-11T tree) and the constructed files below.
SV = SAVED.rsplit("/", 1)[0]
SV_FILES: dict[str, bytes] = {
    f"{SV}/Alpha.lua": b"AlphaDB = {\n}\n",
    f"{SV}/Bravo.lua": b"BravoDB = {\n}\n",
    f"{SV}/Charlie.lua": b"CharlieDB = {\n}\n",
}
WRITTEN = b"constructed: written by this transaction\n"


# ─── helpers ─────────────────────────────────────────────────────────────────


def _put(flavor: Flavor, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = flavor.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _fd_path(fd: int) -> str | None:
    """The path an open directory descriptor names, where the platform says."""
    with contextlib.suppress(OSError):
        return str(Path(f"/proc/self/fd/{fd}").readlink())
    try:
        import fcntl

        get_path = getattr(fcntl, "F_GETPATH", None)
        if get_path is not None:
            raw = fcntl.fcntl(fd, get_path, b"\0" * 1024)
            return os.fsdecode(raw.split(b"\0", 1)[0])
    except (ImportError, OSError):
        pass
    return None


def _norm(arg: Any) -> str | None:
    """The real path of a listing call's argument, comparable across spellings."""
    if arg is None:
        text: str | None = str(Path.cwd())
    elif isinstance(arg, int):
        text = _fd_path(arg)
    else:
        text = os.fsdecode(os.fspath(arg))
    if text is None:
        return None
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.realpath(text))


class InjectedListingError(PermissionError):
    """The listing failure a grader injects; recognisable in a cause chain."""


class _ScandirAfter:
    """The iterator `os.scandir` returned, which runs `action` once the
    listing has been read to its end or closed, whichever comes first."""

    def __init__(self, it: Any, action: Callable[[], None]) -> None:
        self._it = it
        self._action: Callable[[], None] | None = action

    def _done(self) -> None:
        action, self._action = self._action, None
        if action is not None:
            action()

    def __iter__(self) -> _ScandirAfter:
        return self

    def __next__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            self._it.close()
            self._done()
            raise

    def close(self) -> None:
        self._it.close()
        self._done()

    def __enter__(self) -> _ScandirAfter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Listings:
    """Counts `os.listdir` / `os.scandir` calls per watched directory, makes
    the next `fail[d]` listings of directory `d` raise, and runs `after[d]`
    once, right after the next listing of `d` has been read in full (after
    `os.listdir` returns; when the `os.scandir` iterator is exhausted or
    closed)."""

    def __init__(self, flavor: Flavor, *rels: str) -> None:
        self.flavor = flavor
        self._key = {_norm(flavor.path / rel if rel else flavor.path): rel for rel in rels}
        self.calls: Counter[str] = Counter()
        self.fail: Counter[str] = Counter()
        self.after: dict[str, Callable[[], None]] = {}
        self.raised: list[BaseException] = []

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "listdir", self._wrap(os.listdir, scandir=False))
        monkeypatch.setattr(os, "scandir", self._wrap(os.scandir, scandir=True))

    def _wrap(self, real: Callable[..., Any], *, scandir: bool) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            where = args[0] if args else kwargs.get("path")
            rel = self._key.get(_norm(where))
            if rel is None:
                return real(*args, **kwargs)
            self.calls[rel] += 1
            if self.fail[rel] > 0:
                self.fail[rel] -= 1
                named = where if isinstance(where, int) or where is None else os.fspath(where)
                exc = InjectedListingError(
                    errno.EACCES, "constructed: injected listing error", named
                )
                self.raised.append(exc)
                raise exc
            result = real(*args, **kwargs)
            action = self.after.pop(rel, None)
            if action is None:
                return result
            if scandir:
                return _ScandirAfter(result, action)
            action()
            return result

        return call


@contextlib.contextmanager
def watching(flavor: Flavor, *rels: str) -> Iterator[Listings]:
    listings = Listings(flavor, *rels)
    with pytest.MonkeyPatch.context() as patched:
        listings.arm(patched)
        yield listings


def _causes(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _directories(flavor: Flavor) -> list[str]:
    """Every directory under the flavor folder's allowlisted subtrees, and the
    flavor folder itself (as `""`), flavor-relative."""
    found = [""]
    for top in ("WTF", "Interface", "Fonts"):
        base = flavor.path / top
        if not base.is_dir():
            continue
        found.append(top)
        for dirpath, dirnames, _ in os.walk(base, followlinks=False):
            for name in dirnames:
                found.append((Path(dirpath) / name).relative_to(flavor.path).as_posix())
    return found


def _seed_snapshot(guard: Any, flavor: Flavor, store: Path) -> str:
    """A snapshot of the flavor folder as it is: the pre-write snapshot of a
    transaction that writes `CONFIG`'s own bytes back."""
    with guard.transaction(flavor, label="seed", store=store) as tx:
        tx.write(CONFIG, (flavor.path / CONFIG).read_bytes())
    return str(guard.history(store=store)[-1].snapshot_id)


def _own_snapshot(guard: Any, store: Path, label: str) -> str:
    """Inside a transaction's body: its pre-write snapshot's id."""
    records = [r for r in guard.history(store=store) if r.label == label]
    assert len(records) == 1, records
    return str(records[0].snapshot_id)


def _warm(tx: Any, *, dry_run: bool, snapshot_id: str | None, rels: list[str]) -> None:
    """Lookups in `SV` that write nothing: planned writes in a dry run, a
    restore of unchanged paths in a real transaction."""
    if dry_run:
        for rel in rels:
            tx.write(rel, WRITTEN)
    else:
        assert snapshot_id is not None
        tx.restore(snapshot_id, paths=rels)
        assert tx.plan == (), "positive control: the warm-up restore changed nothing"


def _assert_listed_once(listings: Listings, rel: str, lookups: int) -> None:
    assert listings.calls[rel] == 1, (
        f"{rel or 'the flavor folder'} was listed {listings.calls[rel]} times for {lookups} "
        "lookups that wrote nothing; with the listing memo it is listed once per transaction"
    )


def _moved_on(directory: Path, before_ns: int) -> None:
    """Make sure `directory`'s mtime is no longer `before_ns` (a coarse clock
    may not have ticked), so the memo's key has changed."""
    st = directory.stat()
    if st.st_mtime_ns == before_ns:
        os.utime(directory, ns=(st.st_atime_ns, before_ns + 1_000_000))
    assert directory.stat().st_mtime_ns != before_ns


def _put_back_mtime(directory: Path, mtime_ns: int) -> None:
    """Undo the mtime change a write made: a clock that did not tick."""
    st = directory.stat()
    os.utime(directory, ns=(st.st_atime_ns, mtime_ns))


# ─── 1. the memo: an unchanged directory is listed once ──────────────────────


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_a_no_op_whole_restore_lists_each_directory_once(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """A whole restore with nothing to change looks up every file of the
    snapshot and writes nothing: every directory, the flavor folder
    included, is listed at most once, and the directory with the most files
    exactly once (the counter's positive control)."""
    _put(flavor, SV_FILES)
    _put(flavor, {f"Interface/AddOns/Many/f{i:02d}.lua": b"-- %d\n" % i for i in range(30)})
    store = tmp_path / "store"
    seed = _seed_snapshot(guard, flavor, store) if dry_run else None
    dirs = _directories(flavor)
    with guard.transaction(flavor, label="noop", store=store, dry_run=dry_run) as tx:
        snapshot_id = seed if dry_run else _own_snapshot(guard, store, "noop")
        assert snapshot_id is not None
        with watching(flavor, *dirs) as listings:
            tx.restore(snapshot_id)
        assert tx.plan == (), "positive control: the restore had nothing to change"
    assert listings.calls["Interface/AddOns/Many"] == 1, dict(listings.calls)
    over = {d or "<flavor folder>": n for d, n in listings.calls.items() if n > 1}
    assert not over, f"listed more than once in one no-op restore: {over}"


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_lookups_that_write_nothing_list_their_directory_once(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """Planned writes in a dry run, and a restore of unchanged paths in a
    real transaction, each look up a name in `SV`; `SV` is listed once."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    with guard.transaction(flavor, label="warm", store=store, dry_run=dry_run) as tx:
        own = None if dry_run else _own_snapshot(guard, store, "warm")
        with watching(flavor, SV) as listings:
            _warm(tx, dry_run=dry_run, snapshot_id=own, rels=sorted(SV_FILES))
    _assert_listed_once(listings, SV, len(SV_FILES))


# ─── 2. the walk scales linearly (serial) ────────────────────────────────────

SMALL = 2_000
LARGE = 8_000
RATIO = 5.0
SMALL_RUNS = 3
LARGE_RUNS = 3


class _OverBudget(BaseException):
    """Raised by the CPU-time alarm inside a timed run that went over budget."""


@contextlib.contextmanager
def _cpu_deadline(seconds: float) -> Iterator[None]:
    """Interrupt the block once the process has used `seconds` of CPU time
    (`ITIMER_PROF`, the clock `process_time` reads), so that a run already
    over budget is not finished: today's quadratic walk would take minutes.
    Where there is no such timer, or off the main thread, the block runs to
    the end."""
    setitimer = getattr(signal, "setitimer", None)
    prof = getattr(signal, "SIGPROF", None)
    if (
        setitimer is None
        or prof is None
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def alarm(signum: int, frame: Any) -> None:
        raise _OverBudget

    previous = signal.signal(prof, alarm)
    try:
        setitimer(signal.ITIMER_PROF, seconds)
        yield
    finally:
        setitimer(signal.ITIMER_PROF, 0)
        signal.signal(prof, previous)


def _grow(flavor: Flavor, files: int) -> None:
    """A constructed tree shaped like a player's: one file in eight is a
    SavedVariables file in one account folder, the rest are addon files in
    folders of 40 inside one `Interface/AddOns`. Both big directories grow
    with the tree. The timings on this shape are in the module docstring."""
    for i in range(files):
        if i % 8 == 0:
            rel = f"{SV}/Constructed{i:05d}.lua"
        else:
            rel = f"Interface/AddOns/Constructed{i // 40:04d}/Libs/f{i % 40:02d}.lua"
        path = flavor.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"-- constructed %d\n" % i)


def _flavor_of(tmp_path: Path, name: str, files: int) -> Flavor:
    """A fresh copy of the M10-11T install under `tmp_path/name`, grown."""
    root = tmp_path / name / "World of Warcraft"
    G._put(root, G.ROOT_FILES)
    G._put(root / G.FLAVOR_FOLDER, G.FLAVOR_ONLY_FILES)
    G._put(root / G.FLAVOR_FOLDER, ALLOWLISTED_FILES)
    flavor = Flavor(
        folder=G.FLAVOR_FOLDER,
        product="wow",
        version="12.1.5.65432",
        build=65432,
        build_key=None,
        path=root / G.FLAVOR_FOLDER,
    )
    _grow(flavor, files)
    return flavor


def _no_op_restore_seconds(
    guard: Any,
    flavor: Flavor,
    store: Path,
    snapshot_id: str,
    clock: Callable[[], float],
    budget: float | None = None,
) -> float:
    """CPU seconds of one no-op whole restore in a dry run; `inf` when it was
    interrupted at `budget`."""
    with guard.transaction(flavor, label="timed", store=store, dry_run=True) as tx:
        start = clock()
        try:
            with _cpu_deadline(budget) if budget is not None else contextlib.nullcontext():
                tx.restore(snapshot_id)
        except _OverBudget:
            return math.inf
        seconds = clock() - start
        assert tx.plan == (), "positive control: the timed restore had nothing to change"
    return seconds


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_the_no_op_whole_restore_check_scales_linearly(
    guard: Any, tmp_path: Path, idle: None
) -> None:
    """§18.1 item 6, the PR-path half: 4 times the files costs at most 5
    times the CPU time (linear plus noise; today's walk is quadratic in the
    size of a directory). Minimum of several runs per size; see the module
    docstring."""
    small = _flavor_of(tmp_path, "small", SMALL)
    large = _flavor_of(tmp_path, "large", LARGE)
    small_store, large_store = tmp_path / "small-store", tmp_path / "large-store"
    small_id = _seed_snapshot(guard, small, small_store)
    large_id = _seed_snapshot(guard, large, large_store)

    clock = time.process_time
    small_best = min(
        _no_op_restore_seconds(guard, small, small_store, small_id, clock)
        for _ in range(SMALL_RUNS)
    )
    assert small_best > 0, "the process clock did not advance over a 2,000-file restore"
    budget = RATIO * small_best
    large_runs: list[float] = []
    for _ in range(LARGE_RUNS):
        large_runs.append(
            _no_op_restore_seconds(guard, large, large_store, large_id, clock, budget)
        )
        if min(large_runs) <= budget:
            break
    best = min(large_runs)
    shown = f"{best:.3f} s" if math.isfinite(best) else f"over {budget:.3f} s (interrupted)"
    assert best <= budget, (
        f"the no-op whole-restore check took {shown} CPU at {LARGE} files against "
        f"{small_best:.3f} s at {SMALL} (best of {len(large_runs)} and {SMALL_RUNS} runs): "
        f"more than {RATIO:g} times; the walk is not linear"
    )


# ─── 3. a change of identity mid-transaction invalidates the memo ────────────

EXTERNAL_CHANGES = ("file-added-beside", "directory-replaced")
OWN_CHANGES = ("own-write", "own-directory", "own-delete")


@pytest.mark.parametrize(
    "change", [pytest.param(c, id=f"constructed-{c}") for c in EXTERNAL_CHANGES]
)
@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "real"])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_someone_elses_change_of_identity_is_seen_by_the_next_lookup(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, change: str, dry_run: bool
) -> None:
    """With `SV` memoized, someone else adds `Delta.lua` (the directory's
    mtime moves), or replaces `SV` by another directory holding it (its inode
    changes). The next lookup lists `SV` again: `delta.lua`, which differs
    only by case, is refused with `PathNotAllowedError` naming the path, and
    nothing is written for it."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    directory = flavor.path / SV
    target = f"{SV}/delta.lua"
    with guard.transaction(flavor, label="external", store=store, dry_run=dry_run) as tx:
        own = None if dry_run else _own_snapshot(guard, store, "external")
        with watching(flavor, SV) as listings:
            _warm(tx, dry_run=dry_run, snapshot_id=own, rels=sorted(SV_FILES))
            _assert_listed_once(listings, SV, len(SV_FILES))
            if change == "file-added-beside":
                before_ns = directory.stat().st_mtime_ns
                (directory / "Delta.lua").write_bytes(EXTERNAL)
                _moved_on(directory, before_ns)
            else:
                aside = directory.with_name(directory.name + ".constructed-aside")
                directory.rename(aside)
                directory.mkdir()
                (directory / "Delta.lua").write_bytes(EXTERNAL)
            with refused(guard, "PathNotAllowedError", naming=target):
                tx.write(target, WRITTEN)
            assert listings.calls[SV] >= 2, "the lookup after the change did not list again"
        assert all(item.path != target for item in tx.plan), tx.plan
    names = sorted(p.name for p in directory.iterdir())
    assert "delta.lua" not in names, names
    assert (directory / "Delta.lua").read_bytes() == EXTERNAL


@pytest.mark.parametrize("change", [pytest.param(c, id=f"constructed-{c}") for c in OWN_CHANGES])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_the_transactions_own_writes_invalidate_the_memo(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, change: str
) -> None:
    """With `SV` memoized, the transaction itself writes a new file, creates
    a directory, or deletes a file there; then `SV`'s mtime is put back, as
    on a file system whose clock did not tick, so the key alone cannot tell.
    The next lookup still sees what the transaction did: a case variant of
    what it created is refused with `PathNotAllowedError` naming the path,
    and a case variant of what it deleted is accepted and written."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    directory = flavor.path / SV
    with guard.transaction(flavor, label="own", store=store) as tx:
        own = _own_snapshot(guard, store, "own")
        with watching(flavor, SV) as listings:
            _warm(tx, dry_run=False, snapshot_id=own, rels=sorted(SV_FILES))
            _assert_listed_once(listings, SV, len(SV_FILES))
        mtime_ns = directory.stat().st_mtime_ns
        if change == "own-write":
            tx.write(f"{SV}/Echo.lua", WRITTEN)
            _put_back_mtime(directory, mtime_ns)
            with refused(guard, "PathNotAllowedError", naming=f"{SV}/ECHO.lua"):
                tx.write(f"{SV}/ECHO.lua", WRITTEN)
        elif change == "own-directory":
            tx.write(f"{SV}/Sub/x.lua", WRITTEN)
            _put_back_mtime(directory, mtime_ns)
            with refused(guard, "PathNotAllowedError", naming=f"{SV}/SUB/y.lua"):
                tx.write(f"{SV}/SUB/y.lua", WRITTEN)
        else:
            tx.delete(f"{SV}/Alpha.lua")
            _put_back_mtime(directory, mtime_ns)
            tx.write(f"{SV}/ALPHA.lua", WRITTEN)
    names = sorted(p.name for p in directory.iterdir())
    if change == "own-write":
        assert "Echo.lua" in names and "ECHO.lua" not in names, names
    elif change == "own-directory":
        assert "Sub" in names and "SUB" not in names, names
    else:
        assert "Alpha.lua" not in names and "ALPHA.lua" in names, names
        assert (directory / "ALPHA.lua").read_bytes() == WRITTEN
    record = next(r for r in guard.history(store=store) if r.label == "own")
    assert record.state == "committed", record.state


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_a_change_right_after_the_listing_is_not_stored_under_the_new_key(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """Someone else adds `Delta.lua` (the directory's mtime moves) right after
    the first listing of `SV` has been read in full, before guard can read
    `SV`'s identity again. A memo that lists first and reads the key after
    would keep the old listing under the new key. The key is read before
    the listing (or before and after, keeping the listing only when both
    agree), so the next lookup lists again: `delta.lua` is refused with
    `PathNotAllowedError` naming the path. Lookups after that are served
    from the new listing."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    directory = flavor.path / SV
    target = f"{SV}/delta.lua"
    first, *rest = sorted(SV_FILES)

    def add_delta() -> None:
        before_ns = directory.stat().st_mtime_ns
        (directory / "Delta.lua").write_bytes(EXTERNAL)
        _moved_on(directory, before_ns)

    with guard.transaction(flavor, label="racy", store=store, dry_run=dry_run) as tx:
        own = None if dry_run else _own_snapshot(guard, store, "racy")
        with watching(flavor, SV) as listings:
            listings.after[SV] = add_delta
            _warm(tx, dry_run=dry_run, snapshot_id=own, rels=[first])
            assert SV not in listings.after, "positive control: Delta.lua came after a listing"
            assert (directory / "Delta.lua").exists()
            with refused(guard, "PathNotAllowedError", naming=target):
                tx.write(target, WRITTEN)
            _warm(tx, dry_run=dry_run, snapshot_id=own, rels=rest)
            assert listings.calls[SV] == 2, (
                f"{SV} was listed {listings.calls[SV]} times; once before the change, once "
                "after it, and the lookups after that are served from the memo"
            )
        assert all(item.path != target for item in tx.plan), tx.plan
    assert "delta.lua" not in sorted(p.name for p in directory.iterdir())


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_the_memo_lasts_one_transaction(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """The memo is per transaction (§18.1 item 6), and `wowlab ui` runs for a
    long time in one process: a second transaction's first lookup in `SV`
    lists it again, although nothing changed in between, and its later
    lookups are served from its own memo."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    rels = sorted(SV_FILES)
    listings = Listings(flavor, SV)
    for n, label in enumerate(("first", "second"), start=1):
        with guard.transaction(flavor, label=label, store=store, dry_run=dry_run) as tx:
            own = None if dry_run else _own_snapshot(guard, store, label)
            # Counted in the body only: the pre-write snapshot's walk is not a lookup.
            with pytest.MonkeyPatch.context() as patched:
                listings.arm(patched)
                _warm(tx, dry_run=dry_run, snapshot_id=own, rels=rels[:1])
                assert listings.calls[SV] == n, (
                    f"transaction {label!r}: its first lookup in {SV} must list it "
                    f"(listed {listings.calls[SV]} times in {n} transactions)"
                )
                _warm(tx, dry_run=dry_run, snapshot_id=own, rels=rels[1:])
                assert listings.calls[SV] == n, (
                    f"transaction {label!r}: {SV} was listed {listings.calls[SV]} times in "
                    f"{n} transactions; within one it is listed once"
                )


# ─── 4. the commit-time re-check still refuses a changed path ────────────────

RECHECK_CASES = [
    pytest.param(op, when, swap, id=f"constructed-{op}-{when}-{swap}")
    for op, when in (
        ("write", "after-the-journal-fsync"),
        ("write", "after-the-temp-fsync"),
        ("delete", "after-the-journal-fsync"),
    )
    for swap in ("another-file", "rewritten-in-place-same-size-and-mtime", "removed")
]


@pytest.mark.parametrize(("op", "when", "swap"), RECHECK_CASES)
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_the_commit_time_recheck_still_refuses_a_changed_path(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, op: str, when: str, swap: str
) -> None:
    """With `SV` memoized, the target is changed by someone else after
    guard read it and before its replace or unlink. The in-place rewrite
    keeps the file's size and mtime and leaves `SV` itself untouched, so
    only the content hash tells. Each is refused with
    `ChangedSinceSnapshotError` naming the path; the target keeps what was
    swapped in; the transaction does not commit."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    rel = f"{SV}/Bravo.lua"
    target = flavor.path / rel
    action = _swap(swap, target)
    witness: dict[str, bytes | None] = {}

    def swap_and_witness() -> None:
        action()
        witness["held"] = target.read_bytes() if target.exists() else None

    hook: Any = (
        AfterTempFsync(target.parent, swap_and_witness)
        if when == "after-the-temp-fsync"
        else AfterJournalFsync(store, swap_and_witness)
    )
    with contextlib.suppress(guard.GuardError):  # noqa: SIM117
        with guard.transaction(flavor, label="recheck", store=store) as tx:
            own = _own_snapshot(guard, store, "recheck")
            with watching(flavor, SV) as listings:
                _warm(tx, dry_run=False, snapshot_id=own, rels=sorted(SV_FILES))
                _assert_listed_once(listings, SV, len(SV_FILES))
            with pytest.MonkeyPatch.context() as patched:
                hook.arm(patched)
                with refused(guard, "ChangedSinceSnapshotError", naming=rel):
                    if op == "write":
                        tx.write(rel, WRITTEN)
                    else:
                        tx.delete(rel)
    assert hook.fired, "positive control: the swap happened inside the operation"
    held = target.read_bytes() if target.exists() else None
    assert held == witness["held"], "the refused path does not hold what was swapped in"
    assert held != WRITTEN
    state = next(r for r in guard.history(store=store) if r.label == "recheck").state
    assert state != "committed", state


# ─── 5. a listing error is not cached ────────────────────────────────────────


@pytest.mark.parametrize("dry_run", [True, False], ids=["constructed-dry-run", "constructed-real"])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-23 not implemented")
def test_constructed_an_injected_listing_error_is_not_cached(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """The first listing of `SV` fails. That lookup raises a `GuardError`
    naming the path, caused by the injected error. The next lookup lists
    `SV` again and answers from what is there: `alpha.lua` collides with
    `Alpha.lua` and is refused with `PathNotAllowedError` (an empty listing
    cached in place of the error would accept it). After that, lookups that
    write nothing are served from the new listing, and a plain write goes
    through."""
    _put(flavor, SV_FILES)
    store = tmp_path / "store"
    first, collides, fine = f"{SV}/Foxtrot.lua", f"{SV}/alpha.lua", f"{SV}/Golf.lua"
    with guard.transaction(flavor, label="flaky", store=store, dry_run=dry_run) as tx:
        own = None if dry_run else _own_snapshot(guard, store, "flaky")
        with watching(flavor, SV) as listings:
            listings.fail[SV] = 1
            with pytest.raises(guard.GuardError) as caught:
                tx.write(first, WRITTEN)
            assert not isinstance(caught.value, guard.ClientRunningError)
            assert not isinstance(caught.value, guard.PathNotAllowedError), caught.value
            assert first in str(caught.value), f"the error must name {first!r}: {caught.value}"
            assert listings.raised, "positive control: the injected error was raised"
            assert listings.raised[0] in _causes(caught.value), "the injected error is the cause"

            with refused(guard, "PathNotAllowedError", naming=collides):
                tx.write(collides, WRITTEN)
            assert listings.calls[SV] == 2, "the lookup after the error did not list again"

            _warm(tx, dry_run=dry_run, snapshot_id=own, rels=sorted(SV_FILES))
            assert listings.calls[SV] == 2, (
                f"{SV} was listed {listings.calls[SV]} times; after the failed listing and "
                "the one after it, lookups that write nothing are served from the memo"
            )
            tx.write(fine, WRITTEN)
        planned = {item.path for item in tx.plan}
        assert fine in planned and first not in planned and collides not in planned, planned
    names = sorted(p.name for p in (flavor.path / SV).iterdir())
    assert "alpha.lua" not in names and "Foxtrot.lua" not in names, names
    assert ("Golf.lua" in names) is not dry_run, names
