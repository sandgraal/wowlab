"""Graders for guard creating the store root and `journal/` private (M16-66T).

Written before M16-66, from the ticket text (M16-66T and M16-66 in
docs/BACKLOG.md, from the M16-41 reviews of 2026-11-10), docs/LAB_PLAN.md
§16 item 10 ("The store is created 0700, and its files 0600"), §6.9 (the
store under the user data directory) and §6.10 (guard's store lock and
journal). Every grader that fails today carries one marker line that M16-66
deletes; nothing else in this file is the implementer's to change.

What the ticket asks for
------------------------
`guard` makes the store root (taking the store lock on a store that does not
exist yet) and `journal/` (writing the first record) with the default
directory mode, 0755 under umask 0022. M16-41 makes them private only at
the next store write, so for a moment, and for a dry run or a bare
`store_lock(create=True)` for good, other accounts on the computer can list
what guard puts there. The graders: under umask 0022 (and, for the
transaction, the looser 0002 and 0000 as well), the first guard write
leaves the root and `journal/` at 0700 *before anything is written into
them*, and at 0700 afterwards.

What the graders do not ask
---------------------------
Nothing about the parents guard makes on the way to a missing store (the
user data directory is the platform's), nothing about `<user data dir>/
locks/`, nothing about a root that already existed before the call (M16-41
fixes that on the next store write), and nothing about how the mode is
reached: `mkdir` with mode 0o700, or `mkdir` then `chmod` before the first
entry goes in, both pass. A window between the `mkdir` and the `chmod` in
which nothing is put in the directory is allowed by the ticket's wording.

How "before anything is written into them" is observed (the seam)
-----------------------------------------------------------------
A process-wide audit hook (`sys.addaudithook`, PEP 578), installed once and
inert outside a test, sees every call that puts an entry into a directory
or opens one to write: the `open` event with a create or write flag (the
builtin `open`, `io.FileIO`, `os.open`, so `Path.touch`, `Path.write_bytes`
and `tempfile` too), `os.mkdir` (`Path.mkdir`, `os.makedirs`), and the
destination of `os.rename` (`os.replace` raises the same event), `os.link`
and `os.symlink`. For each, the parent directory of the new entry is found
by path, or through `dir_fd` when one is given, and matched to the store
root or `journal/` by identity (device and inode), and its mode at that
moment is recorded. The audit events fire before the call runs, so the mode
recorded is the mode the directory had when the entry went in. An `open`
by a path relative to a `dir_fd` cannot be placed (the event carries no
`dir_fd`); each grader therefore also requires that it saw at least one
entry go into each directory it judges, so a grader that cannot see fails
rather than passes. `test_the_watch_sees_an_entry_go_in_and_the_mode_then`
is the grader's own control and carries no marker.

Everything is constructed (L8: the store is our own format and file modes
are a boundary case of it). The install is `test_guard.py`'s synthetic tree
under `tmp_path`, loaded, not copied, the user data directory is redirected
and the process table is injected. No test needs or touches a real install.
Mode bits are a POSIX matter: every grader is skipped elsewhere. Running as
root does not change what `stat` reports, so nothing here depends on the
user, as in `test_snapshot_store_modes.py`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import stat
import sys
import threading
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from wowlab_core.snapshot import SnapshotStore


def _load_m10_11t_graders() -> types.ModuleType:
    """`test_guard.py` as a module of its own, for its synthetic install,
    fixtures and helpers (as `test_guard_owner_decisions.py` loads it)."""
    path = Path(__file__).resolve().parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_m16_66t_base_graders", path)
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
idle = G.idle

CONFIG = G.CONFIG
NEW_CONFIG = G.NEW_CONFIG

posix_only = pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX matter")

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
LABEL = "m16-66t constructed write"


# ─── the watch ───────────────────────────────────────────────────────────────

_WRITE_FLAGS = os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_TRUNC | os.O_APPEND


@dataclass(frozen=True)
class Seen:
    """One entry put into (or opened to write in) a watched directory."""

    where: str  # the watched directory's label
    event: str
    target: str
    mode: int  # the directory's permission bits when the call was made

    def __str__(self) -> str:
        return f"{self.event} {self.target} while {self.where} was {oct(self.mode)}"


@dataclass
class Watch:
    """Directories by label; what was put into each, and at what mode."""

    dirs: dict[str, Path]
    seen: list[Seen] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def in_(self, where: str) -> list[Seen]:
        return [s for s in self.seen if s.where == where]

    def observe(self, event: str, target: Any, dir_fd: Any) -> None:
        if isinstance(target, int) or target is None:
            return  # an open of a descriptor puts nothing anywhere
        name = os.fsdecode(os.fspath(target))
        entry = Path(name)
        try:
            if isinstance(dir_fd, int) and dir_fd >= 0 and not entry.is_absolute():
                # `.` relative to the descriptor is the descriptor's directory.
                st = os.stat(entry.parent, dir_fd=dir_fd, follow_symlinks=False)
            else:
                st = entry.absolute().parent.lstat()
        except FileNotFoundError:
            return
        for where, path in self.dirs.items():
            try:
                mine = path.lstat()
            except FileNotFoundError:
                continue
            if (mine.st_dev, mine.st_ino) == (st.st_dev, st.st_ino):
                self.seen.append(Seen(where, event, name, stat.S_IMODE(st.st_mode)))


_ACTIVE: list[Watch] = []
_IN_HOOK = threading.local()
_HOOKED: list[bool] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    """Inert unless a watch is active; never raises (an exception out of an
    audit hook would abort the call it audits)."""
    if not _ACTIVE or getattr(_IN_HOOK, "on", False):
        return
    if event == "open":
        target, dir_fd = args[0], -1
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        if not flags & _WRITE_FLAGS:
            return
    elif event == "os.mkdir":
        target, dir_fd = args[0], args[2]
    elif event in ("os.rename", "os.link"):
        target, dir_fd = args[1], args[3]
    elif event == "os.symlink":
        target, dir_fd = args[1], args[2]
    else:
        return
    _IN_HOOK.on = True
    try:
        for watch in list(_ACTIVE):
            try:
                watch.observe(event, target, dir_fd)
            except Exception as exc:  # recorded, judged by the grader
                watch.errors.append(f"{event} {target!r}: {exc!r}")
    finally:
        _IN_HOOK.on = False


@contextlib.contextmanager
def watching(**dirs: Path) -> Iterator[Watch]:
    if not _HOOKED:  # an audit hook cannot be removed: install it once
        sys.addaudithook(_audit)
        _HOOKED.append(True)
    watch = Watch(dirs)
    _ACTIVE.append(watch)
    try:
        yield watch
    finally:
        _ACTIVE.remove(watch)


@contextlib.contextmanager
def umask(mask: int) -> Iterator[None]:
    old = os.umask(mask)
    try:
        yield
    finally:
        os.umask(old)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def assert_private_before_anything_went_in(watch: Watch, where: str) -> None:
    assert not watch.errors, f"the watch could not place a call: {watch.errors[:3]}"
    seen = watch.in_(where)
    assert seen, (
        f"the grader saw nothing put into {where}; it cannot judge (a create "
        "it cannot place, such as an `open` relative to a dir_fd?)"
    )
    early = [str(s) for s in seen if s.mode != PRIVATE_DIR]
    assert not early, (
        f"{len(early)} of {len(seen)} entries went into {where} before it was "
        f"{oct(PRIVATE_DIR)}: {early[:4]}"
    )


# ─── the stores ──────────────────────────────────────────────────────────────

FRESH_EXPLICIT = "fresh-explicit-store"
FRESH_DEFAULT = "fresh-default-store"
MADE_BY_A_SNAPSHOT = "store-made-by-a-snapshot-no-journal"


def _store(kind: str, tmp_path: Path) -> tuple[Path | None, Path]:
    """What to pass as `store=` and where the store is. Constructed: a fresh
    store whose parent is missing too, the default store under the
    redirected user data directory, or a store a snapshot already made (its
    root private, by M16-41) that has no `journal/` yet."""
    if kind == FRESH_DEFAULT:
        path = platformdirs.user_data_path("wowlab") / "store"
        assert str(path).startswith(str(tmp_path)), "the user data directory is not redirected"
        assert not path.exists()
        return None, path
    path = tmp_path / "data" / "store"
    if kind == MADE_BY_A_SNAPSHOT:
        src = tmp_path / "constructed-src"
        (src / "WTF").mkdir(parents=True)
        (src / "WTF" / "Config.wtf").write_bytes(b'SET constructed "1"\n')
        SnapshotStore(path).create(src, ["WTF"], label="m16-66t constructed: an earlier snap")
        assert mode(path) == PRIVATE_DIR and not (path / "journal").exists()
    else:
        assert not path.exists()
    return path, path


def _first_write(guard: Any, flavor: Any, store_arg: Path | None) -> None:
    with guard.transaction(flavor, label=LABEL, store=store_arg) as tx:
        tx.write(CONFIG, NEW_CONFIG)


def _assert_the_write_landed(guard: Any, flavor: Any, store: Path) -> None:
    """Positive control: the write went through the gate and is journaled."""
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    assert [r.label for r in guard.history(store=store)] == [LABEL]


MASKS = [0o022, 0o002, 0o000]


# ─── the grader's own control ────────────────────────────────────────────────


@posix_only
def test_the_watch_sees_an_entry_go_in_and_the_mode_then_constructed(tmp_path: Path) -> None:
    """No marker: shows the watch places every kind of call it claims to,
    and records the mode the directory had at the call, not after it."""
    box = tmp_path / "box"
    box.mkdir()
    box.chmod(0o755)
    with watching(box=box) as watch:
        fd = os.open(box / "a", os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
        (box / "b").write_bytes(b"constructed\n")
        (box / "c").mkdir()
        dir_fd = os.open(box, os.O_RDONLY)
        try:
            os.mkdir("d", dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        (box / "b").replace(box / "e")
        os.link(box / "a", box / "f")
        (box / "g").symlink_to("a")
        box.chmod(PRIVATE_DIR)
        (box / "h").touch()
        os.open(box / "a", os.O_RDONLY)  # a read puts nothing in: not seen
    assert not watch.errors
    names = [(Path(s.target).name, s.mode) for s in watch.in_("box")]
    assert names == [
        ("a", 0o755),
        ("b", 0o755),
        ("c", 0o755),
        ("d", 0o755),
        ("e", 0o755),
        ("f", 0o755),
        ("g", 0o755),
        ("h", PRIVATE_DIR),
    ]
    with pytest.raises(AssertionError, match="before it was 0o700"):
        assert_private_before_anything_went_in(watch, "box")
    with pytest.raises(AssertionError, match="saw nothing"):
        assert_private_before_anything_went_in(watch, "elsewhere")


# ─── a transaction's first write ─────────────────────────────────────────────


@posix_only
@pytest.mark.parametrize("kind", [FRESH_EXPLICIT, FRESH_DEFAULT])
@pytest.mark.parametrize("mask", MASKS, ids=oct)
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-66 not implemented")
def test_the_first_write_makes_the_store_root_0700_before_anything_goes_in_constructed(
    guard: Any, flavor: Any, idle: None, tmp_path: Path, mask: int, kind: str
) -> None:
    store_arg, store = _store(kind, tmp_path)
    with umask(mask), watching(root=store) as watch:
        _first_write(guard, flavor, store_arg)
    _assert_the_write_landed(guard, flavor, store)
    assert_private_before_anything_went_in(watch, "root")
    assert mode(store) == PRIVATE_DIR
    assert mode(store / "lock") == PRIVATE_FILE


@posix_only
@pytest.mark.parametrize("kind", [FRESH_EXPLICIT, FRESH_DEFAULT, MADE_BY_A_SNAPSHOT])
@pytest.mark.parametrize("mask", MASKS, ids=oct)
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-66 not implemented")
def test_the_first_write_makes_journal_0700_before_a_record_goes_in_constructed(
    guard: Any, flavor: Any, idle: None, tmp_path: Path, mask: int, kind: str
) -> None:
    store_arg, store = _store(kind, tmp_path)
    journal = store / "journal"
    with umask(mask), watching(journal=journal) as watch:
        _first_write(guard, flavor, store_arg)
    _assert_the_write_landed(guard, flavor, store)
    assert_private_before_anything_went_in(watch, "journal")
    assert mode(journal) == PRIVATE_DIR
    records = [p for p in journal.iterdir() if p.name.endswith(".json")]
    assert len(records) == 1
    assert mode(records[0]) == PRIVATE_FILE


@posix_only
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-66 not implemented")
def test_after_the_first_write_root_journal_and_their_files_are_private_constructed(
    guard: Any, flavor: Any, idle: None, tmp_path: Path
) -> None:
    """The ticket's end state at umask 0022, by `lstat` alone (no watch)."""
    store_arg, store = _store(FRESH_EXPLICIT, tmp_path)
    with umask(0o022):
        _first_write(guard, flavor, store_arg)
    _assert_the_write_landed(guard, flavor, store)
    journal = store / "journal"
    wrong = {
        rel: oct(mode(store / rel))
        for rel in [".", "lock", "journal", *(f"journal/{p.name}" for p in journal.iterdir())]
        if mode(store / rel) != (PRIVATE_DIR if (store / rel).is_dir() else PRIVATE_FILE)
    }
    assert not wrong, f"not private after the first guard write: {wrong}"


# ─── the other calls that create the store ───────────────────────────────────


@posix_only
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-66 not implemented")
def test_a_dry_run_on_a_new_store_makes_the_root_0700_before_its_lock_constructed(
    guard: Any, flavor: Any, idle: None, tmp_path: Path
) -> None:
    """A dry run takes the locks (§6.10: the lock files and their directories
    are the only things it writes) and takes no snapshot, so nothing after
    it would make the root private: it must be made so as it is created."""
    store_arg, store = _store(FRESH_EXPLICIT, tmp_path)
    before = (flavor.path / CONFIG).read_bytes()
    dry = guard.transaction(flavor, label=LABEL, store=store_arg, dry_run=True)
    with umask(0o022), watching(root=store) as watch, dry as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert (flavor.path / CONFIG).read_bytes() == before  # control: a dry run
    assert not (store / "journal").exists()
    assert_private_before_anything_went_in(watch, "root")
    assert mode(store) == PRIVATE_DIR


@posix_only
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-66 not implemented")
def test_store_lock_create_makes_the_root_0700_before_its_lock_constructed(
    guard: Any, tmp_path: Path
) -> None:
    """`store_lock(create=True)` (the first `snap create`) makes the root
    too; the snapshot it guards fixes the root later, but the lock file has
    gone in by then, and a caller that stops there leaves it 0755."""
    _, store = _store(FRESH_EXPLICIT, tmp_path)
    with umask(0o022), watching(root=store) as watch, guard.store_lock(store, create=True):
        assert (store / "lock").is_file()  # control: the lock was taken
    assert_private_before_anything_went_in(watch, "root")
    assert mode(store) == PRIVATE_DIR
    assert mode(store / "lock") == PRIVATE_FILE
