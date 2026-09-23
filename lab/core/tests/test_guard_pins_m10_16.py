"""Regression pins for `wowlab_core.guard` behaviours the M10-16 reviews
verified with scratch probes (M10-16T follow-up).

No marker: each grader passes against the merged gate (M10-16, PR #59) and
fails against a scratch mutant that removes the behaviour. Expectations come
from docs/LAB_PLAN.md §6.10, amendment items 1 to 4, the 2026-09-22
clarification and the 2026-09-23 additions (PR #60), and the public API.

Everything is constructed, as in `test_guard.py` and
`test_guard_owner_decisions.py`, whose synthetic install, fixtures and
helpers this file reuses (loaded, not copied): the install is a tree under
`tmp_path`, the user data directory is redirected, the process table is
injected, and second processes run `CHILD_SCRIPT` with the same
redirections.

`undo()` hands its own transaction the locks it took. That transaction
checks they still cover its store and install (by the identity of each
lock file); the check is reached through public `undo()` when a held
lock file is replaced while undo plans (pinned below).
"""

from __future__ import annotations

import builtins
import contextlib
import errno
import importlib.util
import io
import json
import os
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from wowlab_core.snapshot import Manifest, SnapshotStore, manifest_bytes, tree_fingerprint


def _load(name: str, filename: str) -> types.ModuleType:
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OD = _load("_m10_16_pins_owner_graders", "test_guard_owner_decisions.py")
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
FLAVOR_FOLDER = G.FLAVOR_FOLDER
CONFIG = G.CONFIG
ICON = G.ICON
FONT = G.FONT
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
NEW_CONFIG = G.NEW_CONFIG
NEWER_CONFIG = G.NEWER_CONFIG
content = G.content
strict_state = G.strict_state
record_for = G.record_for
symlink_or_skip = G.symlink_or_skip
attempt = OD.attempt
assert_busy = OD.assert_busy
assert_plain_guard_error = OD.assert_plain_guard_error
install_lock_file = OD.install_lock_file
store_state = OD.store_state
child_says = OD.child_says
spawn = OD.spawn
_write_font = OD._write_font
_store_locked = OD._store_locked

WINDOWS_LOCK_OFFSET = 1 << 30  # §6.10 as reworded 2026-09-23


def _file_id(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (st.st_dev, st.st_ino)


def journal_bytes(store: Path) -> dict[str, bytes]:
    directory = store / "journal"
    if not directory.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


def manifest_ids(store: Path) -> list[str]:
    directory = store / "manifests"
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


# ─── 1. the Windows lock sits at offset 2^30 ─────────────────────────────────


class FakeMsvcrt(types.ModuleType):
    """`msvcrt.locking`, recording (mode, nbytes, file position) per call."""

    LK_UNLCK = 0
    LK_LOCK = 1
    LK_NBLCK = 2
    LK_RLCK = 3
    LK_NBRLCK = 4

    def __init__(self) -> None:
        super().__init__("msvcrt")
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((mode, nbytes, os.lseek(fd, 0, os.SEEK_CUR)))


def test_constructed_pin_windows_lock_and_unlock_one_byte_at_offset_2_to_the_30(
    guard: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1, mechanism (reworded 2026-09-23): on Windows the lock is one
    byte at offset 2^30, far past the end of the empty lock file, and the
    unlock uses the same offset; nothing is written, so the file stays empty.
    Graded with a stand-in `msvcrt` on every platform."""
    store = tmp_path / "store"
    store.mkdir()
    fake = FakeMsvcrt()
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(sys, "platform", "win32")
        patched.setitem(sys.modules, "msvcrt", fake)
        with guard.store_lock(store):
            locked = list(fake.calls)
    assert locked == [(fake.LK_NBLCK, 1, WINDOWS_LOCK_OFFSET)], locked
    assert fake.calls == [
        (fake.LK_NBLCK, 1, WINDOWS_LOCK_OFFSET),
        (fake.LK_UNLCK, 1, WINDOWS_LOCK_OFFSET),
    ], fake.calls
    assert (store / "lock").stat().st_size == 0, "the lock file is never extended or written"


@pytest.mark.skipif(sys.platform != "win32", reason="mandatory byte-range locks are Windows'")
def test_constructed_pin_windows_lock_files_stay_readable_while_held(
    guard: Any, tmp_path: Path, install_root: Path, flavor: Flavor, idle: None
) -> None:
    """The reason for the offset: a read of the lock files while a
    transaction holds them succeeds (a lock at offset 0 failed it with a
    permission error on the PR #59 `lab (windows)` run)."""
    store = tmp_path / "store"
    with guard.transaction(flavor, label="held", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        for lock in (store / "lock", install_lock_file(tmp_path, install_root)):
            assert lock.read_bytes() == b""


# ─── 2. nothing is created inside any install ────────────────────────────────


def _other_install(tmp_path: Path) -> Path:
    """Directories shaped like another install; its markers come later."""
    other = tmp_path / "Other WoW"
    (other / "Data").mkdir(parents=True)
    (other / FLAVOR_FOLDER / "WTF").mkdir(parents=True)
    return other


def _mark_other_install(other: Path) -> None:
    (other / ".build.info").write_bytes(b"Branch!STRING:0\nus\n")
    (other / FLAVOR_FOLDER / ".flavor.info").write_bytes(b"Product Flavor!STRING:0\nwow\n")


INSIDE_WHERES = (
    "store-under-another-installs-data",
    "store-under-another-installs-wtf",
    "store-is-another-install-root",
    "store-through-a-symlink-into-another-install",
    "store-beside-a-build-info-directory",
    "store-beside-a-dangling-flavor-info-link",
    "store-under-an-ancestor-that-cannot-be-examined",
    "user-data-under-another-install",
)


@pytest.mark.parametrize(
    "route",
    [
        pytest.param(r, id=f"constructed-{r}")
        for r in ("transaction", "dry-run", "undo", "store-lock")
    ],
)
@pytest.mark.parametrize("where", [pytest.param(w, id=f"constructed-{w}") for w in INSIDE_WHERES])
def test_constructed_pin_nothing_is_created_inside_any_install(
    guard: Any,
    tmp_path: Path,
    flavor: Flavor,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    route: str,
) -> None:
    """Item 1, lock files (added 2026-09-23): the store, `<store>/lock`,
    `locks/` and the install lock are refused, before anything is created,
    when inside any install: a directory holding `.build.info` or
    `.flavor.info` of any kind, on the path or an ancestor, resolved through
    links; an ancestor that cannot be examined counts as an install. For
    transaction, dry run, undo and `store_lock` alike. Positive control: the
    same call with a store outside any install goes through."""
    if where == "user-data-under-another-install" and route == "store-lock":
        pytest.skip("store_lock takes no install lock, so the user data directory is not used")
    other = _other_install(tmp_path)
    store = tmp_path / "store"
    plant: Callable[[], None] = lambda: _mark_other_install(other)  # noqa: E731
    flaky = tmp_path / "flaky"
    unexaminable: set[str] = set()
    if where == "store-under-another-installs-data":
        store = other / "Data" / "store"
    elif where == "store-under-another-installs-wtf":
        store = other / FLAVOR_FOLDER / "WTF" / "store"
    elif where == "store-is-another-install-root":
        store = other
    elif where == "store-through-a-symlink-into-another-install":
        symlink_or_skip(tmp_path / "store-link", other / "Data", is_dir=True)
        store = tmp_path / "store-link" / "store"
    elif where == "store-beside-a-build-info-directory":
        store = tmp_path / "odd" / "store"
        plant = lambda: (tmp_path / "odd" / ".build.info").mkdir(parents=True)  # noqa: E731
    elif where == "store-beside-a-dangling-flavor-info-link":
        store = tmp_path / "odd" / "store"
        (tmp_path / "odd").mkdir()

        def plant() -> None:
            symlink_or_skip(tmp_path / "odd" / ".flavor.info", tmp_path / "nowhere", is_dir=False)

    elif where == "store-under-an-ancestor-that-cannot-be-examined":
        # Examining `flaky` fails with an I/O error (injected below); it
        # counts as an install although `store` itself could be created.
        store = flaky / "store"
        flaky.mkdir()
        plant = lambda: unexaminable.update(  # noqa: E731
            str(flaky / name) for name in (".build.info", ".flavor.info")
        )

    if route == "undo":
        _write_font(guard, flavor, store, "to-undo")  # before the markers exist
    if route == "store-lock":
        store.mkdir(parents=True, exist_ok=True)
    if where == "user-data-under-another-install":
        data = other / "Data" / "wowlab"
        monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    plant()
    before = content(tmp_path)
    real = {"stat": os.stat, "lstat": os.lstat}

    def failing(name: str) -> Callable[..., Any]:
        # `Path.lstat` is `os.stat(..., follow_symlinks=False)`: both are wrapped.
        def call(path: Any, *args: Any, **kwargs: Any) -> Any:
            if not isinstance(path, int) and os.fsdecode(path) in unexaminable:
                raise OSError(errno.EIO, "constructed: I/O error while examining")
            return real[name](path, *args, **kwargs)

        return call

    def call(target: Path) -> None:
        if route == "undo":
            guard.undo(store=target)
        elif route == "store-lock":
            _store_locked(guard, target)
        else:
            with guard.transaction(
                flavor, label="inside", store=target, dry_run=route == "dry-run"
            ) as tx:
                tx.write(FONT, OD.SECOND_FONT)

    with pytest.MonkeyPatch.context() as patched:
        for name in real:
            patched.setattr(os, name, failing(name))
        exc = attempt(lambda: call(store))
    assert_plain_guard_error(guard, exc, f"{route} ({where})")
    assert content(tmp_path) == before, "nothing was created or written anywhere"

    if route != "undo" and where != "user-data-under-another-install":
        fine = tmp_path / "fine-store"
        fine.mkdir()
        call(fine)  # positive control


# ─── 3. a lock file with more than one link ──────────────────────────────────


@pytest.mark.parametrize(
    ("route", "which"),
    [
        pytest.param(r, w, id=f"constructed-{r}-{w}-lock")
        for r, w in (
            ("transaction", "store"),
            ("transaction", "install"),
            ("dry-run", "install"),
            ("undo", "store"),
            ("store-lock", "store"),
        )
    ],
)
def test_constructed_pin_a_lock_file_with_another_link_is_refused(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    route: str,
    which: str,
) -> None:
    """Item 1, lock files (added 2026-09-23): a lock file must be a regular
    file with a link count of 1; a hard link to another file is refused with
    a plain `GuardError`, nothing written or journaled, the other file intact."""
    store = tmp_path / "store"
    if route == "undo":
        _write_font(guard, flavor, store, "to-undo")
    store.mkdir(parents=True, exist_ok=True)
    lock = store / "lock" if which == "store" else install_lock_file(tmp_path, install_root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        lock.unlink()
    elsewhere = tmp_path / "elsewhere.bin"
    elsewhere.write_bytes(b"constructed: another file\n")
    try:
        os.link(elsewhere, lock)
    except OSError as exc:
        pytest.skip(f"this volume cannot hard-link: {exc}")
    before = (strict_state(world), store_state(guard, store), elsewhere.read_bytes())

    def call() -> None:
        if route == "undo":
            guard.undo(store=store)
        elif route == "store-lock":
            _store_locked(guard, store)
        else:
            with guard.transaction(
                flavor, label="linked", store=store, dry_run=route == "dry-run"
            ) as tx:
                tx.write(CONFIG, NEWER_CONFIG)

    assert_plain_guard_error(guard, attempt(call), f"{route} with a hard-linked {which} lock")
    assert (strict_state(world), store_state(guard, store), elsewhere.read_bytes()) == before

    lock.unlink()  # positive control: with a lock file of its own, the call goes through
    call()


# ─── 4. an unreadable journal ────────────────────────────────────────────────


def _fail_journal_reads(store: Path, patched: pytest.MonkeyPatch) -> None:
    """Every read of a journal record raises an I/O error."""
    journal = store / "journal"
    real_os_open, real_io_open = os.open, io.open

    def is_record(path: Any) -> bool:
        if isinstance(path, int):
            return False
        where = Path(os.fsdecode(path))
        return where.parent == journal and where.suffix == ".json"

    def os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if is_record(path) and not flags & (os.O_WRONLY | os.O_RDWR):
            raise OSError(errno.EIO, "constructed: I/O error")
        return real_os_open(path, flags, *args, **kwargs)

    def py_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if is_record(file) and not any(c in mode for c in "wxa+"):
            raise OSError(errno.EIO, "constructed: I/O error")
        return real_io_open(file, mode, *args, **kwargs)

    patched.setattr(os, "open", os_open)
    patched.setattr(io, "open", py_open)
    patched.setattr(builtins, "open", py_open)


DAMAGED_RECORD = b"{ constructed: not a record"


@pytest.mark.parametrize(
    "how", [pytest.param(h, id=f"constructed-{h}") for h in ("damaged-record", "io-error")]
)
def test_constructed_pin_an_unreadable_journal_stops_the_transaction_before_the_snapshot(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None, how: str
) -> None:
    """Item 1, lock files (added 2026-09-23): on enter, a journal that cannot
    be read in full raises `GuardError` under the locks and before the
    pre-write snapshot: no snapshot, no record, nothing in the install. A
    dry run, which journals nothing, still works."""
    store = tmp_path / "store"
    _write_font(guard, flavor, store, "earlier")
    if how == "damaged-record":
        (store / "journal" / "99999999-0badf00d.json").write_bytes(DAMAGED_RECORD)
    before = (strict_state(world), journal_bytes(store), manifest_ids(store))
    with pytest.MonkeyPatch.context() as patched:
        if how == "io-error":
            _fail_journal_reads(store, patched)
        exc = attempt(lambda: _write_config(guard, flavor, store))
        with guard.transaction(flavor, label="plan", store=store, dry_run=True) as tx:
            tx.write(CONFIG, NEWER_CONFIG)
            assert [item.path for item in tx.plan] == [CONFIG], "the dry run still plans"
    assert_plain_guard_error(guard, exc, f"a transaction over a journal with {how}")
    assert "journal" in str(exc).lower()
    assert (strict_state(world), journal_bytes(store), manifest_ids(store)) == before, (
        "no snapshot taken, nothing written or journaled"
    )


def _write_config(guard: Any, flavor: Flavor, store: Path) -> None:
    with guard.transaction(flavor, label="refused", store=store) as tx:
        tx.write(CONFIG, NEWER_CONFIG)


def test_constructed_pin_the_journal_is_read_under_the_store_lock(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, children: list[Any]
) -> None:
    """The journal check comes after the locks: while another process holds
    the store lock, a transaction over a damaged journal is busy, not
    refused for the journal."""
    store = tmp_path / "store"
    _write_font(guard, flavor, store, "earlier")
    (store / "journal" / "99999999-0badf00d.json").write_bytes(DAMAGED_RECORD)
    holder = spawn(
        children, tmp_path, mode="hold-store-lock", flavor=str(flavor.path), store=str(store)
    )
    assert holder.line() == "entered"
    assert_busy(guard, attempt(lambda: _write_config(guard, flavor, store)))
    holder.kill()
    OD.eventually(lambda: _store_locked(guard, store))
    assert_plain_guard_error(guard, attempt(lambda: _write_config(guard, flavor, store)), "journal")


# ─── 5. journal and manifest paths are looked up only when local absolute ────

NOT_LOCAL = {
    "unc": "//constructed-server.invalid/share/World of Warcraft",
    "relative": "constructed-relative/World of Warcraft",
    "empty": "",
    # Rooted but without a drive letter: not local and absolute on Windows.
    "rooted-without-a-drive": "/constructed-rooted/World of Warcraft",
}

LOOKUP_CASES = [
    pytest.param(route, kind, id=f"constructed-{route}-constructed-{kind}")
    for route in ("undo", "restore", "cleanup")
    for kind in ("unc", "relative", "empty")
] + [
    pytest.param(
        route,
        "rooted-without-a-drive",
        id=f"constructed-{route}-constructed-rooted-without-a-drive",
        marks=pytest.mark.skipif(
            sys.platform != "win32", reason="a rooted path without a drive is local on POSIX"
        ),
    )
    for route in ("restore", "cleanup")
]


class LookupSpy:
    """Records `os.stat`, `os.lstat`, `os.path.realpath` (what `Path.resolve`
    and `Path.samefile` come down to) of `watched` paths: any path containing
    `marker`, or the empty path and `.` when `marker` is empty."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.seen: list[str] = []
        self._real = {"stat": os.stat, "lstat": os.lstat}
        self._realpath = os.path.realpath

    def _watched(self, arg: Any) -> bool:
        if isinstance(arg, int):
            return False
        spelled = os.fsdecode(arg)
        return spelled in ("", ".") if not self.marker else self.marker in spelled

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name, real in self._real.items():
            monkeypatch.setattr(os, name, self._wrap(name, real))
        realpath = self._realpath

        def spy_realpath(path: Any, *args: Any, **kwargs: Any) -> Any:
            if self._watched(path):
                self.seen.append(f"realpath({path!r})")
            return realpath(path, *args, **kwargs)

        monkeypatch.setattr(os.path, "realpath", spy_realpath)

    def _wrap(self, name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def call(path: Any, *args: Any, **kwargs: Any) -> Any:
            if self._watched(path):
                self.seen.append(f"{name}({path!r})")
            return real(path, *args, **kwargs)

        return call


def _marker(kind: str) -> str:
    if kind == "empty":
        return ""
    # The distinctive part, whichever separator a lookup spells it with.
    return NOT_LOCAL[kind].split("/World")[0].lstrip("/")


def _edit_last_record(store: Path, label: str, **fields: str) -> None:
    edited = 0
    for record_file in sorted((store / "journal").glob("*.json")):
        data = json.loads(record_file.read_bytes())
        if data.get("label") == label:
            data.update(fields)
            record_file.write_text(json.dumps(data), encoding="utf-8")
            edited += 1
    assert edited == 1, "one journal record carries the label"


@pytest.mark.parametrize(("route", "kind"), LOOKUP_CASES)
def test_constructed_pin_paths_from_the_store_are_looked_up_only_when_local_absolute(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    children: list[Any],
    route: str,
    kind: str,
) -> None:
    """Item 1 (added 2026-09-23): a path taken from a journal record or a
    manifest is looked up only when it is local and absolute. A record whose
    install and flavor paths are a UNC, relative or empty path is refused by
    `undo()`; a manifest with such an install root is another install's to
    `tx.restore`; an earlier record with such paths is not this flavor's to
    clean up after. None of them is ever stat'ed, lstat'ed or resolved."""
    store = tmp_path / "store"
    forged = NOT_LOCAL[kind]
    forged_flavor = f"{forged}/{FLAVOR_FOLDER}" if forged else ""
    spy = LookupSpy(_marker(kind))
    if route == "undo":
        with guard.transaction(flavor, label="mine", store=store) as tx:
            tx.write(CONFIG, NEW_CONFIG)
        _edit_last_record(store, "mine", install_root=forged, flavor_path=forged_flavor)
        before = content(world)
        with pytest.MonkeyPatch.context() as patched:
            spy.arm(patched)
            exc = attempt(lambda: guard.undo(store=store))
        assert_plain_guard_error(guard, exc, f"undo of a record naming a {kind} path")
        assert content(world) == before
    elif route == "restore":
        with guard.transaction(flavor, label="mine", store=store) as tx:
            tx.write(CONFIG, NEW_CONFIG)
        base = SnapshotStore(store).show(
            record_for(guard, SnapshotStore(store), "mine").snapshot_id
        )
        try:
            forged_manifest = Manifest(
                **{
                    **base.model_dump(),
                    "install_root": forged,
                    "id": "20260923T120000.000000Z-"
                    + tree_fingerprint(base.subtrees, base.excluded, base.entries),
                }
            )
        except ValueError as exc:
            pytest.skip(f"the manifest model itself refuses this install root: {exc}")
        (SnapshotStore(store).manifests_dir / f"{forged_manifest.id}.json").write_bytes(
            manifest_bytes(forged_manifest)
        )
        before = content(world)
        with (
            guard.transaction(flavor, label="restore", store=store) as tx,
            pytest.MonkeyPatch.context() as patched,
        ):
            spy.arm(patched)
            exc = attempt(lambda: tx.restore(forged_manifest.id, paths=[CONFIG]))
        assert_plain_guard_error(guard, exc, f"restore of a manifest naming a {kind} install")
        assert content(world) == before
    else:
        lo = OD.leave_temps(children, tmp_path, flavor, store)
        _edit_last_record(store, "child-died", install_root=forged, flavor_path=forged_flavor)
        with pytest.MonkeyPatch.context() as patched:
            spy.arm(patched)
            with guard.transaction(flavor, label="next", store=store):
                pass
        record = record_for(guard, SnapshotStore(store), "next")
        assert lo.a.exists() and lo.b.exists(), "another install's temps are not cleaned here"
        assert record.temps_removed == ()
    assert spy.seen == [], f"a {kind} path from the store was looked up: {spy.seen}"


# ─── 6. a forked child neither unlocks nor commits ───────────────────────────


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is a POSIX facility")
def test_constructed_pin_a_forked_child_leaving_the_with_neither_unlocks_nor_commits(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, children: list[Any]
) -> None:
    """Item 1: a lock belongs to the process that took it. A forked child
    that unwinds the parent's `with` touches neither the locks (an
    independent process stays busy) nor the journal nor the install; the
    parent then commits normally."""
    store = tmp_path / "store"
    cm = guard.transaction(flavor, label="forked", store=store)
    with contextlib.ExitStack() as stack:
        tx = stack.enter_context(cm)
        tx.write(CONFIG, NEW_CONFIG)
        journal = journal_bytes(store)
        pid = os.fork()
        if pid == 0:  # the child leaves the parent's `with`, then exits
            code = 0
            try:
                stack.close()
            except BaseException:
                code = 0
            finally:
                os._exit(code)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert journal_bytes(store) == journal, "the child neither committed nor rolled back"
        assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
        same_store = {"flavor": str(flavor.path), "store": str(store)}
        same_install = {"flavor": str(flavor.path), "store": str(tmp_path / "s2")}
        assert child_says(children, tmp_path, mode="try", **same_store) == "busy"
        assert child_says(children, tmp_path, mode="try", **same_install) == "busy"
    assert record_for(guard, SnapshotStore(store), "forked").state == "committed"
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG


# ─── 8. an interrupted mutation breaks the transaction ───────────────────────


@pytest.mark.parametrize(
    "when",
    [pytest.param(w, id=f"constructed-{w}") for w in ("before-the-rename", "after-the-rename")],
)
def test_constructed_pin_an_interrupted_mutation_breaks_the_transaction(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, when: str
) -> None:
    """A `KeyboardInterrupt` out of `os.replace` (before or after the rename
    lands), caught in the body: the mutation may or may not have landed, so
    later operations raise `GuardError` and touch nothing, the record is not
    committed, and Config.wtf holds its original bytes after exit."""
    store = tmp_path / "store"
    config = flavor.path / CONFIG
    real_replace = os.replace
    fired: list[bool] = []

    def replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if not fired and not isinstance(dst, int) and Path(os.fsdecode(dst)).name == config.name:
            fired.append(True)
            if when == "after-the-rename":
                real_replace(src, dst, *args, **kwargs)
            raise KeyboardInterrupt
        real_replace(src, dst, *args, **kwargs)

    reached_end = False
    with contextlib.suppress(guard.GuardError):  # noqa: SIM117 - the exit may say it rolled back
        with guard.transaction(flavor, label="interrupted", store=store) as tx:
            with pytest.MonkeyPatch.context() as patched:
                patched.setattr(os, "replace", replace)
                patched.setattr(os, "rename", replace)
                with pytest.raises(KeyboardInterrupt):
                    tx.write(CONFIG, NEW_CONFIG)
            with pytest.raises(guard.GuardError):
                tx.write(ICON, OD.ICON_NEW)
            assert (flavor.path / ICON).read_bytes() == ALLOWLISTED_FILES[ICON]
            reached_end = True
    assert fired, "positive control: the interrupt came out of the rename"
    assert reached_end
    assert record_for(guard, SnapshotStore(store), "interrupted").state != "committed"
    assert config.read_bytes() == ALLOWLISTED_FILES[CONFIG]


# ─── 9. the store lock comes before the install lock ─────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="observed through fcntl.flock (POSIX)")
@pytest.mark.parametrize(
    "route", [pytest.param(r, id=f"constructed-{r}") for r in ("transaction", "dry-run", "undo")]
)
def test_constructed_pin_the_store_lock_is_taken_before_the_install_lock(
    guard: Any, tmp_path: Path, install_root: Path, flavor: Flavor, idle: None, route: str
) -> None:
    """Item 1, order: the store lock, then the install lock."""
    import fcntl

    store = tmp_path / "store"
    _write_font(guard, flavor, store, "first")  # both lock files exist from here on
    order: list[tuple[int, int]] = []
    real = fcntl.flock

    def flock(fd: Any, operation: int) -> None:
        if operation & fcntl.LOCK_EX:
            st = os.fstat(fd if isinstance(fd, int) else fd.fileno())
            order.append((st.st_dev, st.st_ino))
        real(fd, operation)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(fcntl, "flock", flock)
        if route == "undo":
            guard.undo(store=store)
        else:
            with guard.transaction(
                flavor, label="ordered", store=store, dry_run=route == "dry-run"
            ) as tx:
                tx.write(CONFIG, NEW_CONFIG)
    assert order == [
        _file_id(store / "lock"),
        _file_id(install_lock_file(tmp_path, install_root)),
    ], order


# ─── 10. cleanup re-checks the directory chain after the unlink ──────────────


def test_constructed_pin_cleanup_rechecks_the_chain_after_the_unlink(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None, children: list[Any]
) -> None:
    """Item 2: the chain is re-checked after the unlink as well as before.
    `WTF` becomes a link out of the install the moment the leftover's unlink
    lands: the removal may have happened somewhere else, so it counts as
    failed (`temps_left`, not `temps_removed`); the other leftover is
    removed as usual."""
    G._can_symlink(tmp_path)
    store = tmp_path / "store"
    lo = OD.leave_temps(children, tmp_path, flavor, store)
    outside = world / "outside"

    def swap() -> None:
        wtf = flavor.path / "WTF"
        wtf.rename(wtf.with_name("WTF_real"))
        wtf.symlink_to(outside, target_is_directory=True)

    hook = G.AfterMutation(lo.b.name, swap)
    with pytest.MonkeyPatch.context() as patched:
        hook.arm(patched)
        attempt(lambda: _enter_once(guard, flavor, store))
    assert hook.fired, "positive control: the leftover beside Config.wtf was unlinked"
    record = record_for(guard, SnapshotStore(store), "next")
    assert lo.b_rel in record.temps_left
    assert lo.b_rel not in record.temps_removed
    assert lo.a_rel in record.temps_removed


def _enter_once(guard: Any, flavor: Flavor, store: Path) -> None:
    with guard.transaction(flavor, label="next", store=store):
        pass


# ─── 11. the user data directory and locks/ are plain directories ────────────

LINKED_PLACES = (
    "user-data-is-a-symlink",
    "locks-is-a-symlink",
    "user-data-is-a-junction",
    "locks-is-a-junction",
)


@pytest.mark.parametrize(
    "route", [pytest.param(r, id=f"constructed-{r}") for r in ("transaction", "dry-run", "undo")]
)
@pytest.mark.parametrize("where", [pytest.param(w, id=f"constructed-{w}") for w in LINKED_PLACES])
def test_constructed_pin_a_linked_user_data_directory_or_locks_is_refused(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    route: str,
) -> None:
    """Item 1, lock files: the user data directory and `locks/` must be real
    directories; a symbolic link (or, on Windows, a junction) at either is
    refused with a plain `GuardError`, even one that points outside every
    install, and nothing is created through it."""
    if where.endswith("junction") and sys.platform != "win32":
        pytest.skip("junctions are a Windows facility")
    store = tmp_path / "store"
    if route == "undo":
        _write_font(guard, flavor, store, "to-undo")
    target = tmp_path / "link-target"
    target.mkdir()
    make: Callable[[Path, Path], None]
    if where.endswith("junction"):
        make = OD._junction
    else:

        def make(link: Path, to: Path) -> None:
            symlink_or_skip(link, to, is_dir=True)

    if where.startswith("user-data"):
        data = tmp_path / "userdata-link"
        make(data, target)
    else:
        data = tmp_path / "userdata-plain"
        data.mkdir()
        make(data / "locks", target)
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    before = (strict_state(world), store_state(guard, store))

    def call() -> None:
        if route == "undo":
            guard.undo(store=store)
        else:
            with guard.transaction(
                flavor, label="linked", store=store, dry_run=route == "dry-run"
            ) as tx:
                tx.write(CONFIG, NEWER_CONFIG)

    assert_plain_guard_error(guard, attempt(call), f"{route} with {where}")
    assert (strict_state(world), store_state(guard, store)) == before
    assert list(target.iterdir()) == [], "nothing was created through the link"


# ─── 7. undo's transaction checks the locks it is handed ─────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot remove a lock file held open")
@pytest.mark.parametrize(
    "which", [pytest.param(w, id=f"constructed-{w}-lock") for w in ("store", "install")]
)
def test_constructed_pin_undo_refuses_when_a_held_lock_file_is_replaced(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    which: str,
) -> None:
    """Item 1: `undo()`'s own transaction runs under the locks `undo()`
    holds, and only if they still cover this store and this install. Here the
    held lock file is removed and recreated (a file nobody holds) while undo
    plans under its locks (inside `SnapshotStore.show`): undo raises a plain
    `GuardError` and changes nothing. Positive control: the next undo, which
    takes the new lock file, goes through."""
    store = tmp_path / "store"
    with guard.transaction(flavor, label="mine", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    lock = store / "lock" if which == "store" else install_lock_file(tmp_path, install_root)
    real_show = SnapshotStore.show
    replaced: list[bool] = []

    def show(self: SnapshotStore, *args: Any, **kwargs: Any) -> Any:
        if not replaced:
            replaced.append(True)
            lock.unlink()
            lock.write_bytes(b"")
        return real_show(self, *args, **kwargs)

    before = content(world)
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(SnapshotStore, "show", show)
        exc = attempt(lambda: guard.undo(store=store))
    assert replaced, "positive control: undo planned from the store under its locks"
    assert_plain_guard_error(guard, exc, f"undo after its {which} lock file was replaced")
    assert content(world) == before

    guard.undo(store=store)
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
