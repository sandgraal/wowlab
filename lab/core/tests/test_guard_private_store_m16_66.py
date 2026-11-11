"""Graders for guard creating the store root and `journal/` private (M16-66T).

Written before M16-66, from the ticket text (M16-66T and M16-66 in
docs/BACKLOG.md, from the M16-41 reviews of 2026-11-10), docs/LAB_PLAN.md
§18.1 item 10 ("The store is created 0700, and its files 0600"), §6.9 (the
store under the user data directory) and §6.10 (guard's store lock and
journal). Every grader that fails today carries one marker line that M16-66
deletes; nothing else in this file is the implementer's to change. Fix round
1 (#424 reviews, 2026-11-10) added the mkdir-mode graders and closed the
mkdir-then-chmod window below; no assertion was removed or weakened.

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

Created 0700, not chmodded to it
--------------------------------
Both directories are made with mode 0o700 in the `mkdir` call itself
(`os.mkdir`'s mode argument, `Path.mkdir(mode=0o700)`, `os.makedirs(...,
mode=0o700)`); a `mkdir` with the default mode followed by a `chmod` fails,
however short the window. On POSIX, read permission on a directory is
checked when it is opened, not at each later read: another account that
opens the root or `journal/` while it is 0755 keeps a descriptor that lists
every name added after the `chmod` (`lock`, `journal`, record ids, and
object names, which are SHA-256 digests of the saved bytes). The security
review of #424 reproduced that with a forked child of another uid. The
`os.mkdir` audit event carries the requested mode, and
`test_guard_makes_the_root_and_journal_with_mode_0700_in_the_mkdir_itself`
asserts it is 0o700 for every `mkdir` that creates either directory.

What the graders do not ask
---------------------------
For a missing store, the parent directories guard makes on the way to it
are not covered (the user data directory is the platform's, and a separate
ticket covers it), and neither are `<user data dir>/locks/` and the install
lock files in it. Nothing is asked of a root that already existed before
the call either (M16-41 fixes that on the next store write).

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
rather than passes. The mkdir-mode grader closes the rest of that gap: it
judges how each directory was made, not what was put into it, so an entry
the watch could not place cannot have gone into a directory made 0755.
`test_the_watch_sees_an_entry_go_in_and_the_mode_then`
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
    """Directories by label; what was put into each, and at what mode; and
    every `mkdir` that could create one of them, with the mode it asked for
    (`made`, whose `mode` is the requested mode, not the directory's)."""

    dirs: dict[str, Path]
    seen: list[Seen] = field(default_factory=list)
    made: list[Seen] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def in_(self, where: str) -> list[Seen]:
        return [s for s in self.seen if s.where == where]

    def made_modes(self) -> dict[str, list[str]]:
        """Watched directory -> the distinct modes its creating `mkdir`
        calls asked for (permission bits only), as octal text."""
        out: dict[str, set[str]] = {}
        for s in self.made:
            out.setdefault(s.where, set()).add(oct(s.mode & 0o777))
        return {where: sorted(modes) for where, modes in out.items()}

    def observe(self, event: str, target: Any, dir_fd: Any, requested: Any = None) -> None:
        if isinstance(target, int) or target is None:
            return  # an open of a descriptor puts nothing anywhere
        name = os.fsdecode(os.fspath(target))
        entry = Path(name)
        if event == "os.mkdir" and isinstance(requested, int):
            self._maybe_made(entry, dir_fd, requested)
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

    def _maybe_made(self, entry: Path, dir_fd: Any, requested: int) -> None:
        """A `mkdir` of a watched directory that does not exist yet: same
        name, in the same parent directory (by identity). A `mkdir` of one
        that exists creates nothing and is not judged."""
        try:
            if isinstance(dir_fd, int) and dir_fd >= 0 and not entry.is_absolute():
                parent = os.stat(entry.parent, dir_fd=dir_fd)
            else:
                parent = entry.absolute().parent.stat()
        except FileNotFoundError:
            return  # the call will fail: no parent to make it in
        for where, path in self.dirs.items():
            if entry.name != path.name or os.path.lexists(path):
                continue
            try:
                theirs = path.parent.stat()
            except FileNotFoundError:
                continue
            if (theirs.st_dev, theirs.st_ino) == (parent.st_dev, parent.st_ino):
                self.made.append(Seen(where, "os.mkdir", os.fsdecode(entry), requested))


_ACTIVE: list[Watch] = []
_IN_HOOK = threading.local()
_HOOKED: list[bool] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    """Inert unless a watch is active; never raises (an exception out of an
    audit hook would abort the call it audits)."""
    if not _ACTIVE or getattr(_IN_HOOK, "on", False):
        return
    requested = None
    if event == "open":
        target, dir_fd = args[0], -1
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        if not flags & _WRITE_FLAGS:
            return
    elif event == "os.mkdir":
        target, dir_fd, requested = args[0], args[2], args[1]
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
                watch.observe(event, target, dir_fd, requested)
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


# ─── created 0700, not chmodded to it (fix round 1) ──────────────────────────


@posix_only
def test_the_watch_sees_the_mode_a_mkdir_asks_for_constructed(tmp_path: Path) -> None:
    """No marker: the watch records the mode each creating `mkdir` of a
    watched directory asks for, by path or through `dir_fd`, through
    `Path.mkdir`, also with a missing parent, and ignores a
    `mkdir` of a directory that exists already or of another name."""
    a, b, c = tmp_path / "a", tmp_path / "deep" / "b", tmp_path / "c"
    with watching(a=a, b=b, c=c) as watch:
        a.mkdir()  # default mode, then a chmod: judged by the mkdir
        a.chmod(PRIVATE_DIR)
        a.mkdir(exist_ok=True, mode=PRIVATE_DIR)  # exists: creates nothing
        b.mkdir(parents=True, mode=PRIVATE_DIR)  # its parent made on the way
        dir_fd = os.open(tmp_path, os.O_RDONLY)
        try:
            os.mkdir("c", PRIVATE_DIR, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        (tmp_path / "other").mkdir()
    assert not watch.errors
    assert watch.made_modes() == {"a": ["0o777"], "b": ["0o700"], "c": ["0o700"]}


MADE_SCENARIOS = {
    "transaction-fresh-explicit-store": {"root", "journal"},
    "transaction-fresh-default-store": {"root", "journal"},
    "transaction-store-made-by-a-snapshot": {"journal"},
    "dry-run-fresh-explicit-store": {"root"},
    "store-lock-create-fresh-explicit-store": {"root"},
}


@posix_only
@pytest.mark.parametrize("scenario", list(MADE_SCENARIOS))
def test_guard_makes_the_root_and_journal_with_mode_0700_in_the_mkdir_itself_constructed(
    guard: Any, flavor: Any, idle: None, tmp_path: Path, scenario: str
) -> None:
    """Every `mkdir` that creates the store root or `journal/` asks for
    0o700 itself: a default-mode `mkdir` then a `chmod` leaves a window in
    which another account can open the directory and keep listing it. The
    requested mode does not depend on the umask; 0022 is set as elsewhere."""
    kind = {
        "transaction-fresh-default-store": FRESH_DEFAULT,
        "transaction-store-made-by-a-snapshot": MADE_BY_A_SNAPSHOT,
    }.get(scenario, FRESH_EXPLICIT)
    store_arg, store = _store(kind, tmp_path)
    with umask(0o022), watching(root=store, journal=store / "journal") as watch:
        if scenario.startswith("transaction-"):
            _first_write(guard, flavor, store_arg)
        elif scenario.startswith("dry-run-"):
            dry = guard.transaction(flavor, label=LABEL, store=store_arg, dry_run=True)
            with dry as tx:
                tx.write(CONFIG, NEW_CONFIG)
        else:
            with guard.store_lock(store, create=True):
                pass
    if scenario.startswith("transaction-"):
        _assert_the_write_landed(guard, flavor, store)  # control
    else:
        assert (store / "lock").is_file()  # control: the lock was taken
    assert not watch.errors, f"the watch could not place a call: {watch.errors[:3]}"
    want = {where: [oct(PRIVATE_DIR)] for where in MADE_SCENARIOS[scenario]}
    assert watch.made_modes() == want, "the mode each creating mkdir asked for"
