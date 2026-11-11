"""Graders for the folders guard creates under the user data directory (M16-78T).

Written before M16-78, from the ticket text (M16-78T and M16-78 in
docs/BACKLOG.md, with the owner's answer of 2026-11-11) and docs/LAB_PLAN.md
§6.10, amendment of 2026-11-11 (b): "Every folder wowlab creates under the
user data directory is made 0700 in the `mkdir` itself, whatever the umask.
That covers the store root and `journal/` (2026-11-10, (b)), the parents
made on the way to a missing store, `locks/`, and each module's folder."
Every grader that fails today carries one marker line that M16-78 deletes;
nothing else in this file is the implementer's to change.

What is graded
--------------
The two kinds of folder `guard` itself makes that M16-66 left at the default
mode:

* `<user data dir>/locks/` (it holds `<sha256(dev:inode)>.lock`, the install
  lock), and the user data directory itself when `guard` has to make it on
  the way to `locks/`;
* every parent `guard` makes on the way to a missing store: the user data
  directory for the default store, and each missing ancestor of a store
  passed explicitly.

Each is checked two ways: its mode after the call is 0700 under a
permissive umask (0022 and 0000), by `lstat`; and the `mkdir` that created
it asked for 0o700 itself (the `os.mkdir` audit event carries the requested
mode), so a default-mode `mkdir` followed by a `chmod` fails, however short
the window (the reasoning is in `test_guard_private_store_m16_66.py`: a
descriptor opened on the directory while it is 0755 keeps listing it). Each
grader also requires that the watch saw a creating `mkdir` for every folder
it judges, so a grader that cannot see fails rather than passes.

What is not graded here
-----------------------
The store root and `journal/` (M16-66's graders, on main, untouched); the
module folders `gamedata/`, `looks/`, `pages/` and `db2lake/` (made outside
`guard`; their tests are M16-78's implementer's); the lock files' own mode;
and the platform's data directory that holds `wowlab/` (`~/.local/share`
and the like), which exists before wowlab runs and is created by every
scenario here before the call.

Everything is constructed (L8: the store and the user data directory are
our own; file modes are a boundary case). The install is `test_guard.py`'s
synthetic tree, built in `tmp_path`; the user data directory is redirected
there and the process table is injected. No test needs or touches a real
install. Mode bits are a POSIX matter: every grader is skipped elsewhere.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
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


def _load_m10_11t_graders() -> types.ModuleType:
    """`test_guard.py` as a module of its own, for its synthetic install,
    fixtures and helpers (as `test_guard_private_store_m16_66.py` loads it)."""
    path = Path(__file__).resolve().parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_m16_78t_base_graders", path)
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

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="file modes are a POSIX matter; Windows has no mode bits"
)

PRIVATE_DIR = 0o700
LABEL = "m16-78t constructed write"
MASKS = [0o022, 0o000]


# ─── the watch: every mkdir that creates a judged folder ─────────────────────


@dataclass(frozen=True)
class Made:
    where: str  # the judged folder's label
    target: str
    requested: int  # the mode the mkdir asked for


@dataclass
class Watch:
    dirs: dict[str, Path]
    made: list[Made] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def made_modes(self) -> dict[str, list[str]]:
        out: dict[str, set[str]] = {}
        for m in self.made:
            out.setdefault(m.where, set()).add(oct(m.requested & 0o777))
        return {where: sorted(modes) for where, modes in out.items()}

    def observe(self, target: Any, requested: Any, dir_fd: Any) -> None:
        """A `mkdir` of a judged folder that does not exist yet: same name,
        in the same parent directory (by identity), by path or `dir_fd`."""
        if isinstance(target, int) or target is None or not isinstance(requested, int):
            return
        entry = Path(os.fsdecode(os.fspath(target)))
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
                self.made.append(Made(where, os.fsdecode(entry), requested))


_ACTIVE: list[Watch] = []
_IN_HOOK = threading.local()
_HOOKED: list[bool] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    """Inert unless a watch is active; never raises (an exception out of an
    audit hook would abort the call it audits)."""
    if event != "os.mkdir" or not _ACTIVE or getattr(_IN_HOOK, "on", False):
        return
    _IN_HOOK.on = True
    try:
        for watch in list(_ACTIVE):
            try:
                watch.observe(args[0], args[1], args[2])
            except Exception as exc:  # recorded, judged by the grader
                watch.errors.append(f"os.mkdir {args[0]!r}: {exc!r}")
    finally:
        _IN_HOOK.on = False


@contextlib.contextmanager
def watching(dirs: dict[str, Path]) -> Iterator[Watch]:
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


def assert_made_private(watch: Watch, judged: dict[str, Path]) -> None:
    """Every judged folder exists, is 0700, and was created by a `mkdir`
    that asked for 0o700 itself."""
    assert not watch.errors, f"the watch could not place a call: {watch.errors[:3]}"
    missing = sorted(where for where, path in judged.items() if not path.is_dir())
    assert not missing, f"guard did not make {missing}; the scenario is wrong"
    wrong = {where: oct(mode(path)) for where, path in judged.items() if mode(path) != PRIVATE_DIR}
    assert not wrong, f"not {oct(PRIVATE_DIR)} after the call: {wrong}"
    want = {where: [oct(PRIVATE_DIR)] for where in judged}
    got = {where: modes for where, modes in watch.made_modes().items() if where in judged}
    assert got == want, (
        "the mode each creating mkdir asked for (a folder absent here was "
        "made by a call the watch could not place, or not by a mkdir)"
    )


# ─── the scenarios ───────────────────────────────────────────────────────────

DEFAULT_STORE = "default-store-user-data-dir-missing"
EXPLICIT_STORE_MISSING_PARENTS = "explicit-store-missing-parents-user-data-dir-missing"
EXPLICIT_STORE_EXISTS = "explicit-store-exists-user-data-dir-missing"
LOCKS_MISSING = "user-data-dir-exists-locks-missing"
SCENARIOS = [DEFAULT_STORE, EXPLICIT_STORE_MISSING_PARENTS, EXPLICIT_STORE_EXISTS, LOCKS_MISSING]


def _setup(kind: str, tmp_path: Path) -> tuple[Path | None, Path, dict[str, Path]]:
    """What to pass as `store=`, where the store is, and the folders guard
    must make (label -> path). The platform's data directory, which holds
    the user data directory, always exists first, as on a real machine."""
    user_data = platformdirs.user_data_path("wowlab")
    assert str(user_data).startswith(str(tmp_path)), "the user data directory is not redirected"
    user_data.parent.mkdir(parents=True, exist_ok=True)
    assert not user_data.exists()
    locks = user_data / "locks"
    if kind == DEFAULT_STORE:
        # The store's only missing parent is the user data directory itself.
        return None, user_data / "store", {"user-data-dir": user_data, "locks": locks}
    if kind == EXPLICIT_STORE_MISSING_PARENTS:
        store = tmp_path / "elsewhere" / "deep" / "store"
        assert not (tmp_path / "elsewhere").exists()
        return (
            store,
            store,
            {
                "store-parent-1": tmp_path / "elsewhere",
                "store-parent-2": tmp_path / "elsewhere" / "deep",
                "user-data-dir": user_data,
                "locks": locks,
            },
        )
    store = tmp_path / "elsewhere" / "store"
    store.mkdir(parents=True, mode=PRIVATE_DIR)
    if kind == EXPLICIT_STORE_EXISTS:
        return store, store, {"user-data-dir": user_data, "locks": locks}
    assert kind == LOCKS_MISSING
    user_data.mkdir(mode=PRIVATE_DIR)
    return store, store, {"locks": locks}


def _assert_the_write_landed(guard: Any, flavor: Any, store: Path) -> None:
    """Positive control: the write went through the gate and is journaled."""
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    assert [r.label for r in guard.history(store=store)] == [LABEL]


# ─── the grader's own control ────────────────────────────────────────────────


@posix_only
def test_the_watch_sees_the_mode_a_mkdir_asks_for_constructed(tmp_path: Path) -> None:
    """No marker: the watch records the mode each creating `mkdir` of a
    judged folder asks for, by path, through `dir_fd`, and for a parent
    `Path.mkdir(parents=True)` makes on the way (which, in Python 3.12, asks
    for the default mode whatever `mode=` says), and ignores a `mkdir` of a
    folder that already exists or of another name."""
    a, b, c = tmp_path / "a", tmp_path / "deep" / "b", tmp_path / "c"
    deep = tmp_path / "deep"
    with watching({"a": a, "b": b, "c": c, "deep": deep}) as watch:
        a.mkdir()
        a.chmod(PRIVATE_DIR)
        a.mkdir(exist_ok=True, mode=PRIVATE_DIR)  # exists: creates nothing
        b.mkdir(parents=True, mode=PRIVATE_DIR)
        dir_fd = os.open(tmp_path, os.O_RDONLY)
        try:
            os.mkdir("c", PRIVATE_DIR, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        (tmp_path / "other").mkdir()
    assert not watch.errors
    assert watch.made_modes() == {
        "a": ["0o777"],
        "b": ["0o700"],
        "c": ["0o700"],
        "deep": ["0o777"],
    }
    assert_made_private(watch, {"b": b, "c": c})
    with pytest.raises(AssertionError, match="not 0o700 after the call"):
        assert_made_private(watch, {"a": a, "deep": deep})
    unseen = tmp_path / "unseen"
    unseen.mkdir(mode=PRIVATE_DIR)  # made outside the watch: cannot be judged
    with pytest.raises(AssertionError, match="mkdir asked for"):
        assert_made_private(watch, {"b": b, "unseen": unseen})


# ─── a transaction ───────────────────────────────────────────────────────────


@posix_only
@pytest.mark.xfail(strict=True, reason="M16-78 not implemented")
@pytest.mark.parametrize("kind", SCENARIOS)
@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_a_transaction_makes_locks_and_the_store_parents_0700_constructed(
    guard: Any, flavor: Any, idle: None, tmp_path: Path, mask: int, kind: str
) -> None:
    store_arg, store, judged = _setup(kind, tmp_path)
    tx_ = guard.transaction(flavor, label=LABEL, store=store_arg)
    with umask(mask), watching(judged) as watch, tx_ as tx:
        tx.write(CONFIG, NEW_CONFIG)
    _assert_the_write_landed(guard, flavor, store)
    assert list((judged["locks"]).glob("*.lock")), "control: the install lock is in locks/"
    assert_made_private(watch, judged)


# ─── the other calls that make them ──────────────────────────────────────────


@posix_only
@pytest.mark.xfail(strict=True, reason="M16-78 not implemented")
@pytest.mark.parametrize("kind", SCENARIOS)
@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_a_dry_run_makes_locks_and_the_store_parents_0700_constructed(
    guard: Any, flavor: Any, idle: None, tmp_path: Path, mask: int, kind: str
) -> None:
    """A dry run takes both locks (§6.10: the lock files and their
    directories are the only things it writes), so it makes the same
    folders as a transaction and must make them private too."""
    store_arg, store, judged = _setup(kind, tmp_path)
    before = (flavor.path / CONFIG).read_bytes()
    dry = guard.transaction(flavor, label=LABEL, store=store_arg, dry_run=True)
    with umask(mask), watching(judged) as watch, dry as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert (flavor.path / CONFIG).read_bytes() == before  # control: a dry run
    assert (store / "lock").is_file()  # control: the store lock was taken
    assert list((judged["locks"]).glob("*.lock")), "control: the install lock is in locks/"
    assert_made_private(watch, judged)


@posix_only
@pytest.mark.xfail(strict=True, reason="M16-78 not implemented")
@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_store_lock_create_makes_the_store_parents_0700_constructed(
    guard: Any, tmp_path: Path, mask: int
) -> None:
    """`store_lock(create=True)` on a store whose parents are missing makes
    them on the way; it takes no install lock, so `locks/` is not judged."""
    _, store, judged = _setup(EXPLICIT_STORE_MISSING_PARENTS, tmp_path)
    parents = {k: v for k, v in judged.items() if k.startswith("store-parent-")}
    with umask(mask), watching(parents) as watch, guard.store_lock(store, create=True):
        assert (store / "lock").is_file()  # control: the lock was taken
    assert_made_private(watch, parents)


@posix_only
@pytest.mark.xfail(strict=True, reason="M16-78 not implemented")
@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_store_lock_create_on_the_default_store_makes_the_user_data_dir_0700_constructed(
    guard: Any, tmp_path: Path, mask: int
) -> None:
    _, store, judged = _setup(DEFAULT_STORE, tmp_path)
    parents = {"user-data-dir": judged["user-data-dir"]}
    with umask(mask), watching(parents) as watch, guard.store_lock(None, create=True):
        assert (store / "lock").is_file()  # control: the lock was taken
    assert_made_private(watch, parents)


@posix_only
@pytest.mark.xfail(strict=True, reason="M16-78 not implemented")
@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_undo_makes_the_user_data_dir_and_locks_0700_constructed(
    guard: Any, flavor: Any, idle: None, tmp_path: Path, mask: int
) -> None:
    """`undo` takes the install lock itself (fix round 1, #460 security
    review): with an explicit store and the user data directory gone since
    the transaction it undoes, it makes the user data directory and
    `locks/`, and must make them private, whatever route the fix takes."""
    store_arg, store, judged = _setup(EXPLICIT_STORE_EXISTS, tmp_path)
    with guard.transaction(flavor, label=LABEL, store=store_arg) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    _assert_the_write_landed(guard, flavor, store)
    shutil.rmtree(judged["user-data-dir"])
    assert not judged["user-data-dir"].exists()
    expected_id = guard.history(store=store)[-1].id
    with umask(mask), watching(judged) as watch:
        guard.undo(store=store, expected_id=expected_id)
    labels = [r.label for r in guard.history(store=store)]
    assert labels == [LABEL, f"undo: {LABEL}"], "control: the undo landed"
    assert (flavor.path / CONFIG).read_bytes() != NEW_CONFIG  # control: restored
    assert list(judged["locks"].glob("*.lock")), "control: the install lock is in locks/"
    assert_made_private(watch, judged)
