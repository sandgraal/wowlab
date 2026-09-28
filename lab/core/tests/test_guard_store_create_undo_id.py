"""Graders for guard's store creation and undo-by-id (M10-17T).

Written before M10-17, from the ticket text in docs/BACKLOG.md (M10-17T,
from the M10-14 reviews of 2026-09-27/28) and docs/LAB_PLAN.md §6.10 with
its amendments: item 1 (locks, order, the inside-any-install rule added
2026-09-23), item 4 (`store_lock`) and the amendment of 2026-09-28
(`create`, `expected_id`). Fix round 1 (PR #73 reviews) added the
creating-call check, the exact-id, hostile-lock, marker-lookup and
symlinked-user-data cases. Every grader carries one marker line that M10-17
deletes; nothing else in this file is the implementer's to change.

The seam these graders hold `guard` to
--------------------------------------
- `guard.store_lock(store: Path | None = None, create: bool = False)`.
  With `create=True` the store (and any missing parent) is created and its
  lock taken, so a first `snap create` holds the store lock like every later
  one. Before anything is created (no `mkdir`, no `open` with `O_CREAT`,
  not even briefly), the store is refused with a plain
  `GuardError` if it is inside any install: resolved (following links,
  junctions and `..`), it and every existing ancestor are examined, and a
  directory holding an entry named `.build.info` or `.flavor.info` (of any
  kind, in any case the volume treats as the same name) is an install; an
  error other than not-found while examining one counts as an install.
  `create=False`, the default, is item 4 as it stands: a missing store is a
  `GuardError` and nothing is created.
- `guard.undo(*, store: Path | None = None, expected_id: str | None = None)`.
  With `expected_id`, once the store lock is held and the journal re-read,
  a most recent record whose id is not exactly `expected_id` (a whole-string
  compare: not a prefix, not the sequence part, not empty-as-`None`)
  raises a plain `GuardError` naming
  an id, with nothing written in the install and nothing in the store (no
  pre-write snapshot, no record). A match undoes that record. Without it
  (omitted or `None`), `undo()` acts on whatever is most recent, as today.

The store-creation graders pass `create=` and the undo graders pass
`expected_id=`, so today each fails on the missing keyword (a `TypeError`,
which is neither the refusal nor the success it asserts) and nothing else.

Everything is constructed, as in `test_guard.py` and
`test_guard_owner_decisions.py`, whose synthetic install, fixtures and
helpers this file reuses (loaded, not copied): installs are trees under
`tmp_path` (our own layout, labelled `constructed`, L8), the user data
directory is redirected, the process table is injected, and second
processes run the M10-16T `CHILD_SCRIPT` with the same redirections. No test
needs or touches a real install.
"""

from __future__ import annotations

import contextlib
import errno
import importlib.util
import inspect
import os
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from wowlab_core.snapshot import SnapshotStore


def _load(name: str, filename: str) -> types.ModuleType:
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OD = _load("_m10_17t_owner_graders", "test_guard_owner_decisions.py")
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
children = OD.children

Flavor = G.Flavor
FLAVOR_FOLDER = G.FLAVOR_FOLDER
CONFIG = G.CONFIG
FONT = G.FONT
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
NEW_CONFIG = G.NEW_CONFIG
SECOND_FONT = OD.SECOND_FONT
content = G.content
strict_state = G.strict_state
symlink_or_skip = G.symlink_or_skip
attempt = OD.attempt
assert_busy = OD.assert_busy
assert_plain_guard_error = OD.assert_plain_guard_error
error_text = OD.error_text
child_says = OD.child_says
spawn = OD.spawn
eventually = OD.eventually
user_data_dir = OD.user_data_dir
_junction = OD._junction
_store_locked = OD._store_locked
_write_font = OD._write_font
Child = OD.Child


def _created_store_locked(guard: Any, store: Path | None) -> None:
    with guard.store_lock(store, create=True):
        pass


def _write_config(guard: Any, flavor: Flavor, store: Path, label: str) -> None:
    with guard.transaction(flavor, label=label, store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)


def _kid(flavor: Flavor, store: Path) -> dict[str, str]:
    return {"flavor": str(flavor.path), "store": str(store)}


# ─── the seam ────────────────────────────────────────────────────────────────


def test_constructed_store_lock_takes_create_and_undo_takes_expected_id(guard: Any) -> None:
    """`store_lock(store=None, create=False)` and
    `undo(*, store=None, expected_id=None)`: the new parameters default to
    today's behaviour; `store` stays a directory path."""
    lock = inspect.signature(guard.store_lock, eval_str=True).parameters
    assert list(lock) == ["store", "create"], list(lock)
    assert lock["store"].default is None
    assert lock["store"].annotation == (Path | None)
    assert lock["create"].default is False
    assert lock["create"].annotation is bool

    undo = inspect.signature(guard.undo, eval_str=True).parameters
    assert list(undo) == ["store", "expected_id"], list(undo)
    for name in ("store", "expected_id"):
        assert undo[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert undo[name].default is None, name
    assert undo["store"].annotation == (Path | None)
    assert undo["expected_id"].annotation == (str | None)


# ─── 1. store_lock(create=True) makes the store and holds its lock ───────────


@pytest.mark.parametrize(
    "where",
    [
        pytest.param(w, id=f"constructed-{w}")
        for w in ("missing-with-missing-parents", "missing-default-store", "existing-empty-store")
    ],
)
def test_constructed_store_lock_create_makes_the_store_and_holds_its_lock(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
    where: str,
) -> None:
    """A first `snap create` holds the store lock like every later one: the
    store and its missing parents are created (the default store too, on a
    machine with no user data directory yet), `<store>/lock` is a regular
    file, and while the block runs a second `store_lock` or a transaction on
    the store is `GuardBusyError`, in this process and in another, with
    nothing written or journaled. The created store is one `SnapshotStore`
    can write a snapshot into; once the block ends the lock is released."""
    if where == "missing-with-missing-parents":
        store_arg: Path | None = tmp_path / "fresh" / "user data" / "store"
        store = tmp_path / "fresh" / "user data" / "store"
        assert not (tmp_path / "fresh").exists()
    elif where == "missing-default-store":
        store_arg = None
        store = user_data_dir(tmp_path) / "store"
        assert not user_data_dir(tmp_path).parent.exists(), "no user data directory yet"
    else:
        store_arg = store = tmp_path / "store"
        store.mkdir()

    with guard.store_lock(store_arg, create=True):
        assert store.is_dir() and not store.is_symlink()
        lock = store / "lock"
        assert lock.is_file() and not lock.is_symlink(), "the store lock file is in the store"
        before = (strict_state(world), content(store))
        assert_busy(guard, attempt(lambda: _store_locked(guard, store)), "a nested store_lock")
        assert_busy(
            guard,
            attempt(lambda: _created_store_locked(guard, store_arg)),
            "a nested store_lock(create=True)",
        )
        assert_busy(
            guard, attempt(lambda: _write_font(guard, flavor, store, "under-it")), "a transaction"
        )
        assert (strict_state(world), content(store)) == before, "a busy call writes nothing"
        assert child_says(children, tmp_path, mode="try", **_kid(flavor, store)) == "busy"
        assert child_says(children, tmp_path, mode="try-store-lock", **_kid(flavor, store)) == (
            "busy"
        )
        made = SnapshotStore(store).create(
            install_root,
            [f"{FLAVOR_FOLDER}/WTF"],
            label="first snap create",
            flavor_folder=FLAVOR_FOLDER,
            flavor_version=flavor.version,
            client_running=False,
        )

    assert [m.id for m in SnapshotStore(store).list()] == [made.id]
    _store_locked(guard, store)  # released
    assert child_says(children, tmp_path, mode="try-store-lock", **_kid(flavor, store)) == (
        "entered"
    )
    _write_font(guard, flavor, store, "after")
    assert (flavor.path / FONT).read_bytes() == SECOND_FONT


@pytest.mark.parametrize(
    "holder",
    [
        pytest.param(h, id=f"constructed-{h}")
        for h in (
            "store-lock-here",
            "transaction-here",
            "store-lock-in-another-process",
            "transaction-in-another-process",
        )
    ],
)
def test_constructed_store_lock_create_is_busy_while_another_holds_the_store(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
    holder: str,
) -> None:
    """`create=True` on a store that exists is the same lock: while a
    `store_lock` or a transaction holds it, here or in another process, it
    is `GuardBusyError` and changes nothing; once the holder is done, the
    same call goes through."""
    store = tmp_path / "store"
    store.mkdir()
    outcomes: list[tuple[BaseException | None, object, object]] = []

    def second() -> None:
        before = (strict_state(world), content(store))
        exc = attempt(lambda: _created_store_locked(guard, store))
        outcomes.append((exc, before, (strict_state(world), content(store))))

    if holder == "store-lock-here":
        with guard.store_lock(store):
            second()
    elif holder == "transaction-here":
        with guard.transaction(flavor, label="holder", store=store) as tx:
            tx.write(CONFIG, NEW_CONFIG)
            second()
    else:
        mode = "hold-store-lock" if holder.startswith("store-lock") else "hold"
        held = spawn(children, tmp_path, mode=mode, **_kid(flavor, store))
        assert held.line() == "entered"
        second()
        held.kill()

    ((exc, before, after),) = outcomes
    assert_busy(guard, exc, f"store_lock(create=True) while {holder}")
    assert after == before, "a busy call creates, writes and journals nothing"
    eventually(lambda: _created_store_locked(guard, store))


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="mandatory byte-range locks are Windows'; POSIX flock never blocks a read",
)
def test_constructed_windows_created_store_lock_file_stays_readable_while_held(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, children: list[Child]
) -> None:
    """On Windows the store lock of a store `create=True` made is the same
    one-byte lock at 2^30: a read of the lock file while it is held succeeds
    (and finds it empty), and another process is still busy."""
    store = tmp_path / "fresh" / "store"
    with guard.store_lock(store, create=True):
        assert (store / "lock").read_bytes() == b""
        assert child_says(children, tmp_path, mode="try-store-lock", **_kid(flavor, store)) == (
            "busy"
        )


# ─── 1. ... refusing a store inside any install, before creating anything ────

UNEXAMINABLE = {"eio": errno.EIO, "eacces": errno.EACCES}

INSIDE = (
    "under-an-installs-data",
    "missing-dirs-under-an-installs-wtf",
    "is-an-install-root",
    "is-a-flavor-folder",
    "under-a-folder-with-only-a-flavor-info",
    "in-a-folder-holding-a-build-info-directory",
    "in-a-folder-holding-a-dangling-flavor-info-link",
    "store-itself-holds-a-build-info",
    "through-a-symlinked-ancestor",
    "store-is-a-dangling-symlink-into-an-install",
    "dotdot-escape-into-an-install",
    "dotdot-after-a-symlink",
    "case-variant-marker",
    "case-variant-spelling-of-an-install",
    "unexaminable-ancestor-eio",
    "unexaminable-ancestor-eacces",
    "unexaminable-marker-eio",
    "unexaminable-marker-eacces",
    "default-store-with-user-data-inside-an-install",
    "default-store-through-a-symlinked-user-data-dir",
    "windows-junction-ancestor-into-an-install",
    "windows-junction-store-into-an-install",
)

# The refusal names an install, except where the path cannot be examined (it
# may be refused while resolving) or the store itself is a link or junction
# (it may be refused as one).
NAMES_NO_INSTALL = {
    "unexaminable-ancestor-eio",
    "unexaminable-ancestor-eacces",
    "unexaminable-marker-eio",
    "unexaminable-marker-eacces",
    "store-is-a-dangling-symlink-into-an-install",
    "windows-junction-store-into-an-install",
}

_MARKER_SPELLINGS = (".build.info", ".flavor.info", ".BUILD.INFO", ".FLAVOR.INFO")


@contextlib.contextmanager
def _unexaminable(directory: Path, error: int, *, directory_itself: bool) -> Iterator[None]:
    """Examining the marker names in `directory` fails with `error`, and so
    does listing it (`scandir`, `listdir`), for an implementation that
    lists rather than looks names up. With `directory_itself`, `stat` and
    `lstat` of the directory fail too (which `Path.stat`, `lstat`,
    `exists`, `is_dir` use); without it they succeed, so only the marker
    lookup fails. Creating things in it still works."""
    spellings = {directory, directory.resolve()}
    listed = {os.fsdecode(d) for d in spellings}
    marker_names = {os.fsdecode(d / name) for d in spellings for name in _MARKER_SPELLINGS}
    looked_up = marker_names | (listed if directory_itself else set())
    blocked = {"stat": looked_up, "lstat": looked_up, "scandir": listed, "listdir": listed}
    real = {name: getattr(os, name) for name in blocked}

    def failing(name: str) -> Callable[..., Any]:
        def call(path: Any = ".", *args: Any, **kwargs: Any) -> Any:
            if not isinstance(path, int) and os.fsdecode(os.fspath(path)) in blocked[name]:
                raise OSError(error, f"constructed: {os.strerror(error)} while examining")
            return real[name](path, *args, **kwargs)

        return call

    with pytest.MonkeyPatch.context() as patched:
        for name in real:
            patched.setattr(os, name, failing(name))
        yield


@contextlib.contextmanager
def _creating_calls() -> Iterator[list[str]]:
    """Records every call that creates something: `os.mkdir` (which
    `Path.mkdir` and `os.makedirs` go through) and `os.open` with
    `O_CREAT`. The calls still run, so a create-then-check-then-remove
    implementation is caught here even though it leaves nothing behind."""
    made: list[str] = []
    real_mkdir, real_open = os.mkdir, os.open

    def mkdir(path: Any, *args: Any, **kwargs: Any) -> None:
        made.append(f"mkdir {os.fsdecode(os.fspath(path))}")
        real_mkdir(path, *args, **kwargs)

    def open_(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & os.O_CREAT:
            made.append(f"open(O_CREAT) {os.fsdecode(os.fspath(path))}")
        return real_open(path, flags, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(os, "mkdir", mkdir)
        patched.setattr(os, "open", open_)
        yield made


def _case_insensitive_or_skip(directory: Path) -> None:
    if not G.case_insensitive(directory):
        pytest.skip("this volume is case-sensitive: a case variant names another entry")


def _windows_only() -> None:
    if sys.platform != "win32":
        pytest.skip("junctions are a Windows facility")


@pytest.mark.parametrize("where", [pytest.param(w, id=f"constructed-{w}") for w in INSIDE])
def test_constructed_store_lock_create_refuses_a_store_inside_any_install_creating_nothing(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    """A store inside any install (a `.build.info`/`.flavor.info` entry of
    any kind on the resolved place or an existing ancestor, through links,
    junctions and `..`, in any case the volume folds together; an ancestor
    that cannot be examined counts as one) is refused with a plain
    `GuardError` before anything anywhere is created. Positive control: the
    same call on a store outside every install creates it."""
    root = install_root
    data = root / "Data"
    store_arg: Path | None
    unexaminable: Path | None = None
    if where == "under-an-installs-data":
        store_arg = data / "wowlab-store"
    elif where == "missing-dirs-under-an-installs-wtf":
        store_arg = root / FLAVOR_FOLDER / "WTF" / "ud" / "wowlab" / "store"
    elif where == "is-an-install-root":
        store_arg = root
    elif where == "is-a-flavor-folder":
        store_arg = root / FLAVOR_FOLDER
    elif where == "under-a-folder-with-only-a-flavor-info":
        loose = tmp_path / "loose" / "_x_"
        loose.mkdir(parents=True)
        (loose / ".flavor.info").write_bytes(b"constructed: a flavor marker alone\n")
        store_arg = loose / "deeper" / "store"
    elif where == "in-a-folder-holding-a-build-info-directory":
        (tmp_path / "odd" / ".build.info").mkdir(parents=True)
        store_arg = tmp_path / "odd" / "store"
    elif where == "in-a-folder-holding-a-dangling-flavor-info-link":
        (tmp_path / "odd").mkdir()
        symlink_or_skip(tmp_path / "odd" / ".flavor.info", tmp_path / "nowhere", is_dir=False)
        store_arg = tmp_path / "odd" / "store"
    elif where == "store-itself-holds-a-build-info":
        store_arg = tmp_path / "store"
        store_arg.mkdir()
        (store_arg / ".build.info").write_bytes(b"constructed: a marker in the store\n")
    elif where == "through-a-symlinked-ancestor":
        symlink_or_skip(tmp_path / "hop", data, is_dir=True)
        store_arg = tmp_path / "hop" / "store"
    elif where == "store-is-a-dangling-symlink-into-an-install":
        symlink_or_skip(tmp_path / "store-link", data / "new-store", is_dir=True)
        store_arg = tmp_path / "store-link"
    elif where == "dotdot-escape-into-an-install":
        (tmp_path / "elsewhere").mkdir()
        store_arg = tmp_path / "elsewhere" / ".." / "world" / root.name / "Data" / "store"
    elif where == "dotdot-after-a-symlink":
        # Lexically `tmp_path / "store"`; on disk, `Data/store` in the install.
        (data / "deep").mkdir()
        symlink_or_skip(tmp_path / "hop", data / "deep", is_dir=True)
        store_arg = tmp_path / "hop" / ".." / "store"
    elif where == "case-variant-marker":
        _case_insensitive_or_skip(tmp_path)
        (tmp_path / "odd").mkdir()
        (tmp_path / "odd" / ".Build.Info").write_bytes(b"constructed: a marker spelled oddly\n")
        store_arg = tmp_path / "odd" / "store"
    elif where == "case-variant-spelling-of-an-install":
        _case_insensitive_or_skip(tmp_path)
        store_arg = world / root.name.swapcase() / "dATA" / "store"
    elif where.startswith("unexaminable-"):
        unexaminable = tmp_path / "flaky"
        unexaminable.mkdir()
        store_arg = unexaminable / "store"
    elif where == "default-store-with-user-data-inside-an-install":
        inside = root / FLAVOR_FOLDER / "WTF" / "ud" / "wowlab"
        monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: inside)
        store_arg = None
    elif where == "default-store-through-a-symlinked-user-data-dir":
        # Also the shape of a home directory inside an install.
        symlink_or_skip(tmp_path / "ud-link", root / FLAVOR_FOLDER / "WTF", is_dir=True)
        linked = tmp_path / "ud-link" / "wowlab"
        monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: linked)
        store_arg = None
    elif where == "windows-junction-ancestor-into-an-install":
        _windows_only()
        _junction(tmp_path / "jn", data)
        store_arg = tmp_path / "jn" / "store"
    elif where == "windows-junction-store-into-an-install":
        _windows_only()
        (data / "jtarget").mkdir()
        _junction(tmp_path / "jstore", data / "jtarget")
        store_arg = tmp_path / "jstore"
    else:
        raise AssertionError(where)

    # `strict_state` has every directory's mtime, so an entry made and removed
    # again still shows; `_creating_calls` catches it even where it would not.
    before = strict_state(tmp_path)
    with contextlib.ExitStack() as stack:
        made = stack.enter_context(_creating_calls())
        if unexaminable is not None:
            error = UNEXAMINABLE[where.rsplit("-", 1)[1]]
            itself = where.startswith("unexaminable-ancestor-")
            stack.enter_context(_unexaminable(unexaminable, error, directory_itself=itself))
        exc = attempt(lambda: _created_store_locked(guard, store_arg))
    assert_plain_guard_error(guard, exc, f"store_lock(create=True) with a store {where}")
    assert made == [], f"the refusal comes before anything is created, even briefly: {made}"
    assert strict_state(tmp_path) == before, "nothing was created or changed anywhere"
    if where not in NAMES_NO_INSTALL:
        assert exc is not None and "install" in error_text(exc).lower(), (
            f"the refusal says the store is inside an install: {exc}"
        )

    if unexaminable is not None:
        # Positive control: the same place, examinable, holds no marker.
        assert store_arg is not None
        _created_store_locked(guard, store_arg)
        assert store_arg.is_dir()
    else:
        fine = tmp_path / "fine" / "store"
        _created_store_locked(guard, fine)
        assert (fine / "lock").is_file()


HOSTILE_LOCKS = (
    "lock-is-a-dangling-symlink-into-an-install",
    "lock-is-a-symlink-to-a-file-outside",
    "lock-is-a-hard-link",
    "lock-is-a-directory",
)


@pytest.mark.parametrize(
    "lock_kind", [pytest.param(k, id=f"constructed-existing-store-{k}") for k in HOSTILE_LOCKS]
)
def test_constructed_store_lock_create_on_an_existing_store_follows_the_lock_file_rules(
    guard: Any, tmp_path: Path, world: Path, install_root: Path, idle: None, lock_kind: str
) -> None:
    """`create=True` on a store that exists opens `<store>/lock` by the same
    rules as every lock file (item 1, item 4): `O_NOFOLLOW` (a link there is
    refused, never followed, so a dangling one never creates its target), a
    regular file (not a directory) with one link (a hard link to another
    file is refused). A plain `GuardError`, nothing created or changed
    anywhere; positive control: with a lock file of its own, the call goes
    through."""
    store = tmp_path / "store"
    store.mkdir()
    lock = store / "lock"
    target = install_root / "Data" / "pwned.lock"
    if lock_kind == "lock-is-a-dangling-symlink-into-an-install":
        symlink_or_skip(lock, target, is_dir=False)
    elif lock_kind == "lock-is-a-symlink-to-a-file-outside":
        symlink_or_skip(lock, world / "outside" / "target.txt", is_dir=False)
    elif lock_kind == "lock-is-a-hard-link":
        elsewhere = tmp_path / "elsewhere.bin"
        elsewhere.write_bytes(b"constructed: another file\n")
        try:
            os.link(elsewhere, lock)
        except OSError as exc:
            pytest.skip(f"this volume cannot hard-link: {exc}")
    else:
        lock.mkdir()

    before = strict_state(tmp_path)
    exc = attempt(lambda: _created_store_locked(guard, store))
    assert_plain_guard_error(guard, exc, f"store_lock(create=True) where the {lock_kind}")
    assert strict_state(tmp_path) == before, "nothing created, written or followed"
    assert not target.exists() and not target.is_symlink(), "no lock file inside the install"

    if lock.is_dir() and not lock.is_symlink():
        lock.rmdir()
    else:
        lock.unlink()
    _created_store_locked(guard, store)
    assert lock.is_file() and not lock.is_symlink() and lock.stat().st_nlink == 1


# ─── 1. ... and create=False keeps today's behaviour ─────────────────────────


def test_constructed_store_lock_without_create_never_creates_the_store(
    guard: Any, tmp_path: Path, install_root: Path, idle: None
) -> None:
    """`create=False`, spelled out and by default, is item 4 as it stands: a
    store that does not exist is a plain `GuardError` and nothing is
    created, outside an install or in one; an existing store is locked.
    (Graded together: the default alone is today's behaviour.)"""

    def spelled_out(store: Path) -> None:
        with guard.store_lock(store, create=False):
            pass

    calls: dict[str, Callable[[Path], None]] = {
        "create=False": spelled_out,
        "create omitted": lambda store: _store_locked(guard, store),
    }
    for how, call in calls.items():
        for missing in (tmp_path / "nowhere" / "store", install_root / "Data" / "store"):
            before = content(tmp_path)
            exc = attempt(lambda: call(missing))  # noqa: B023
            assert_plain_guard_error(guard, exc, f"store_lock({how}) on missing {missing}")
            assert content(tmp_path) == before, "nothing was created"

    existing = tmp_path / "store"
    existing.mkdir()
    with guard.store_lock(existing, create=False):
        for how, call in calls.items():
            assert_busy(guard, attempt(lambda: call(existing)), f"a nested store_lock({how})")  # noqa: B023
    with guard.store_lock(existing):
        assert_busy(guard, attempt(lambda: spelled_out(existing)), "store_lock(create=False)")


# ─── 2. undo(expected_id=...) ────────────────────────────────────────────────


def _last_id(guard: Any, store: Path) -> str:
    records = guard.history(store=store)
    assert records, "the journal holds a record"
    return str(records[-1].id)


def _store_bytes(store: Path) -> dict[str, bytes | str]:
    """Everything in the store: the journal, the snapshots, the lock file."""
    return content(store)


@contextlib.contextmanager
def _just_before_the_store_lock(store: Path, action: Callable[[], None]) -> Iterator[list[bool]]:
    """Run `action` once, immediately before this process first asks the
    operating system for the exclusive lock on `<store>/lock` (§6.10 item 1,
    mechanism: `fcntl.flock` on POSIX, `msvcrt.locking(LK_NBLCK)` on
    Windows). For `undo()` that is after its unlocked read of the most
    recent record and before the lock is held."""
    lock = store / "lock"
    fired: list[bool] = []

    def ours(fd: Any) -> bool:
        number = fd if isinstance(fd, int) else fd.fileno()
        return lock.is_file() and G._file_id(os.fstat(number)) == G._file_id(lock.stat())

    with pytest.MonkeyPatch.context() as patched:
        if sys.platform == "win32":
            import msvcrt

            real_locking = msvcrt.locking

            def locking(fd: int, mode: int, nbytes: int) -> None:
                if mode == msvcrt.LK_NBLCK and not fired and ours(fd):
                    fired.append(True)
                    action()
                real_locking(fd, mode, nbytes)

            patched.setattr(msvcrt, "locking", locking)
        else:
            import fcntl

            real_flock = fcntl.flock

            def flock(fd: Any, operation: int) -> None:
                if operation & fcntl.LOCK_EX and not fired and ours(fd):
                    fired.append(True)
                    action()
                real_flock(fd, operation)

            patched.setattr(fcntl, "flock", flock)
        yield fired


def test_constructed_undo_with_the_most_recent_id_undoes_that_record(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None
) -> None:
    """The id the caller approved is still the most recent: the undo runs as
    today, journaled like any transaction, and the undo record's own id
    undoes it in turn."""
    store = tmp_path / "store"
    _write_config(guard, flavor, store, "approved")
    approved = _last_id(guard, store)

    guard.undo(store=store, expected_id=approved)
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
    records = guard.history(store=store)
    assert len(records) == 2 and records[0].id == approved
    assert records[-1].state == "committed"

    guard.undo(store=store, expected_id=records[-1].id)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    assert len(guard.history(store=store)) == 3


UNDO_RACES = (
    "committed-in-this-process-after-approval",
    "committed-in-another-process-after-approval",
    "committed-while-undo-reaches-for-the-store-lock",
    "expected-id-names-no-record",
    "expected-id-is-empty",
    "expected-id-is-the-bare-sequence-of-the-last-id",
    "expected-id-from-another-store-with-the-same-sequence",
)
# No transaction B: the approved record A is still the most recent, and the
# expected id is not exactly its id.
NOT_A_RACE = {
    "expected-id-names-no-record",
    "expected-id-is-empty",
    "expected-id-is-the-bare-sequence-of-the-last-id",
    "expected-id-from-another-store-with-the-same-sequence",
}


@pytest.mark.parametrize("race", [pytest.param(r, id=f"constructed-{r}") for r in UNDO_RACES])
def test_constructed_undo_refuses_when_the_most_recent_record_is_not_the_expected_one(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
    race: str,
) -> None:
    """The caller approved an undo of record A. By the time `undo()` holds
    the store lock and re-reads the journal, another transaction B is the
    most recent (committed before the call, here or in another process, or
    in the window between undo's unlocked read and its lock), or A never
    existed. Or A is still the most recent but the expected id is not
    exactly its id: empty (which is not `None`), only its sequence part, or
    the id of another store's record with the same sequence number (the ids
    match only if compared loosely). The undo is a plain `GuardError` naming
    an id; nothing is written in the install, and the store gains no
    snapshot and no record. Positive control: an undo that expects the id
    that really is the most recent undoes that record, and only it."""
    store = tmp_path / "store"
    _write_config(guard, flavor, store, "approved")
    approved = _last_id(guard, store)
    expected = approved
    if race == "expected-id-names-no-record":
        expected = "00000000-0badc0de"
    elif race == "expected-id-is-empty":
        expected = ""
    elif race == "expected-id-is-the-bare-sequence-of-the-last-id":
        assert "-" in approved, f"record ids are <sequence>-<suffix>: {approved!r}"
        expected = approved.split("-")[0]
    elif race == "expected-id-from-another-store-with-the-same-sequence":
        other_store = tmp_path / "other-store"
        _write_font(guard, flavor, other_store, "first-in-another-store")
        expected = _last_id(guard, other_store)
        assert expected.split("-")[0] == approved.split("-")[0], (expected, approved)
        assert expected != approved, "two stores' first records have different ids"
    state: dict[str, object] = {}

    def commit_b_elsewhere() -> None:
        answer = child_says(
            children,
            tmp_path,
            mode="commit",
            label="committed-in-between",
            writes=[[FONT, SECOND_FONT.hex()]],
            **_kid(flavor, store),
        )
        assert answer == "committed", answer

    def settle() -> None:
        state["before"] = (strict_state(world), _store_bytes(store))

    if race == "committed-in-this-process-after-approval":
        _write_font(guard, flavor, store, "committed-in-between")
    elif race == "committed-in-another-process-after-approval":
        commit_b_elsewhere()
    if race != "committed-while-undo-reaches-for-the-store-lock":
        settle()
        exc = attempt(lambda: guard.undo(store=store, expected_id=expected))
    else:

        def in_the_window() -> None:
            assert _last_id(guard, store) == approved, "undo's unlocked read saw the approved id"
            commit_b_elsewhere()
            settle()

        with _just_before_the_store_lock(store, in_the_window) as fired:
            exc = attempt(lambda: guard.undo(store=store, expected_id=expected))

    assert_plain_guard_error(guard, exc, f"undo(expected_id=...) when {race}")
    if race == "committed-while-undo-reaches-for-the-store-lock":
        assert fired, "undo asked for the store lock"
    after = (strict_state(world), _store_bytes(store))
    assert after == state["before"], "nothing written in the install, nothing in the store"
    latest = _last_id(guard, store)
    assert exc is not None
    names = latest in error_text(exc) or (expected != "" and expected in error_text(exc))
    assert names, f"the refusal names the expected or the most recent record id: {exc}"

    # Positive control: the lock was released, and expecting the record that
    # really is the most recent undoes it and nothing else.
    if race in NOT_A_RACE:
        assert latest == approved
        font = (flavor.path / FONT).read_bytes()
        guard.undo(store=store, expected_id=latest)
        assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
        assert (flavor.path / FONT).read_bytes() == font, "only A is undone"
    else:
        assert latest != approved
        guard.undo(store=store, expected_id=latest)
        assert (flavor.path / FONT).read_bytes() == ALLOWLISTED_FILES[FONT]
        assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG, "A is left as it is"


def test_constructed_undo_without_expected_id_acts_on_whatever_is_most_recent(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    """`expected_id=None`, or no `expected_id`, is today's `undo()`: it
    undoes the most recent record whatever the caller last saw."""
    store = tmp_path / "store"
    _write_config(guard, flavor, store, "first")
    _write_font(guard, flavor, store, "second")

    guard.undo(store=store, expected_id=None)
    assert (flavor.path / FONT).read_bytes() == ALLOWLISTED_FILES[FONT]
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG

    guard.undo(store=store)  # undoes the undo
    assert (flavor.path / FONT).read_bytes() == SECOND_FONT
    assert len(guard.history(store=store)) == 4
