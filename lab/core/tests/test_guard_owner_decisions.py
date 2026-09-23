"""Graders for the 2026-09-22 amendment to `wowlab_core.guard` (M10-16T).

Written before M10-16, from docs/LAB_PLAN.md §6.10 "Amended 2026-09-22"
items 1 to 4, ADR-0021 and invariants L1 and L2. Every grader carries one
marker line that M10-16 deletes; nothing else in this file is the
implementer's to change.

Everything here is constructed. The synthetic install, the fixtures and the
helpers are the ones in `test_guard.py` (loaded below, not copied): the
install is a tree under `tmp_path`, the store a directory under `tmp_path`,
the user data directory is redirected to `tmp_path / "userdata" / "wowlab"`
and the process table is injected. Some graders start a second Python
process (`CHILD_SCRIPT`) that imports `wowlab_core.guard` with the same
redirection and an empty process table; it never lists the real process
table and touches nothing outside `tmp_path`.

The seam, as the amendment fixes it
-----------------------------------
- `guard.GuardBusyError` and `guard.ChangedSinceSnapshotError` are
  `GuardError` subclasses in `guard.__all__`; neither is a
  `ClientRunningError`, a `PathNotAllowedError` or the other.
- `guard.store_lock(store: Path | None = None)` is a context manager that
  holds the store lock alone.
- The store lock file is `<store>/lock`. The install lock file is
  `<user data dir>/locks/<key>.lock`, `<key>` the SHA-256 hex of
  `"<st_dev>:<st_ino>"` (decimal, ASCII) of `os.stat` of the install root,
  the directory guard validated. `<user data dir>` is
  `platformdirs.user_data_path("wowlab")` as `wowlab_core.snapshot` reads it.
- `HistoryRecord.temps_removed` and `HistoryRecord.temps_left` are tuples of
  flavor-relative, `/`-separated paths, spelled as the journal named them.
- The journal names each temp path as a literal string in a file under the
  store outside `objects/` and `manifests/` (the M10-11T convention for the
  write-ahead journal). A grader that forges a temp path rewrites every
  spelling of it there: plain and JSON-escaped, `/` and the platform's
  separator.
- A leftover temp file is made the way a crash makes one: a child process
  running a real transaction dies (`os._exit`) inside the rename. A second
  leftover is stranded in the same transaction by a rename the child refuses
  and an unlink of that temp the child ignores. Their names are whatever
  guard chose; the graders find them by listing the directory.

Readings of the amendment these graders take, confirmed by the conductor
and written into §6.10 as "Clarified 2026-09-22" (PR #53):
- "Nothing is written for that path" (item 3) holds through rollback: a
  path whose only operation was refused keeps what someone else put there,
  after exit too. This matches the PR #48 probe (`test_review_m10_11_first_
  touch_bytes_outside_snapshot.py`): a refusal leaves the changed bytes.
- "The hash it last read or wrote there" is guard's own record of the path
  within the transaction, so a path this transaction wrote, then changed by
  someone else, then touched again, is refused. On rollback, a path whose
  disk content differs from what the transaction last wrote or read there
  is left as it is and the record ends `rollback_incomplete`.
- An injected lock failure other than "held" is a `GuardError` that is never
  a `GuardBusyError`. The injected-failure graders are POSIX-only (Windows
  reports a held `msvcrt.locking` lock as a permission error).
- `store_lock` never creates the store directory; a missing store is a
  `GuardError`.
- A temp path an earlier record names that no longer exists is listed in
  neither `temps_removed` nor `temps_left`.
- "Restore" in item 1 is `tx.restore` inside `transaction()`; there is no
  module-level restore to lock.

Not graded, by decision: a file changed between `undo()`'s own pre-write
snapshot and its first touch. There is no public point at which a test can
make that change.
"""

from __future__ import annotations

import builtins
import contextlib
import errno
import hashlib
import importlib.util
import io
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import types
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotError, SnapshotStore


def _load_m10_11t_graders() -> types.ModuleType:
    """`test_guard.py` as a module of its own, for its synthetic install,
    fixtures and helpers (the review probes load it the same way)."""
    path = Path(__file__).resolve().parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_m10_16t_base_graders", path)
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

Flavor = G.Flavor
FLAVOR_FOLDER = G.FLAVOR_FOLDER
OTHER_FLAVOR = G.OTHER_FLAVOR
CONFIG = G.CONFIG
BINDINGS = G.BINDINGS
NEW_SAVED = G.NEW_SAVED
TOC = G.TOC
ADDON_LUA = G.ADDON_LUA
ICON = G.ICON
FONT = G.FONT
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
NEW_CONFIG = G.NEW_CONFIG
NEWER_CONFIG = G.NEWER_CONFIG
NEW_LUA = G.NEW_LUA
sha = G.sha
content = G.content
strict_state = G.strict_state
record_for = G.record_for
symlink_or_skip = G.symlink_or_skip

EXTERNAL = b"constructed: written by someone else after the pre-write snapshot\n"
EXTERNAL_FONT = b"\x00\x01\x00\x00constructed: someone else's font"
RETRIED = b"constructed: the retry the refusal asked for\n"
SECOND_FONT = b"\x00\x01\x00\x00constructed: the second writer's font"
ICON_NEW = b"BLP2 constructed: first edit"
ICON_NEWER = b"BLP2 constructed: second edit"
PRESET = b"constructed: bytes a lock file already held\n"

# The temp-file name item 2 cleans up, exactly as the amendment spells it.
TEMP_NAME = re.compile(r"\.wowlab-[0-9a-f]{32}\.tmp")

CHILD_TIMEOUT = 120.0
DIED = 17


# ─── errors ──────────────────────────────────────────────────────────────────


def assert_busy(guard: Any, exc: BaseException | None, what: str = "the second call") -> None:
    assert exc is not None, f"{what} went through while the locks were held"
    busy = getattr(guard, "GuardBusyError", None)
    assert busy is not None, f"guard.GuardBusyError does not exist; got {type(exc).__name__}: {exc}"
    assert isinstance(exc, busy), f"expected GuardBusyError, got {type(exc).__name__}: {exc}"
    assert isinstance(exc, guard.GuardError)
    assert not isinstance(exc, guard.ClientRunningError)


def assert_plain_guard_error(guard: Any, exc: BaseException | None, what: str) -> None:
    """A `GuardError` that is not "busy" and not "client running"."""
    assert exc is not None, f"{what} went through"
    assert isinstance(exc, guard.GuardError), f"{what}: {type(exc).__name__}: {exc}"
    assert not isinstance(exc, guard.ClientRunningError), f"{what}: {exc}"
    busy = getattr(guard, "GuardBusyError", None)
    assert busy is not None, "guard.GuardBusyError does not exist"
    assert not isinstance(exc, busy), f"{what} is not a held lock, but got GuardBusyError: {exc}"


def attempt(call: Callable[[], object]) -> BaseException | None:
    """Run `call`; return what it raised (a test outcome is re-raised)."""
    try:
        call()
    except (pytest.fail.Exception, pytest.skip.Exception):
        raise
    except BaseException as exc:
        return exc
    return None


class Caught:
    value: BaseException | None = None


@contextlib.contextmanager
def refused(guard: Any, name: str, *, naming: str | None = None) -> Iterator[Caught]:
    """The block must raise `guard.<name>` (naming `naming` in its message)."""
    caught = Caught()
    try:
        yield caught
    except (pytest.fail.Exception, pytest.skip.Exception):
        raise
    except BaseException as exc:
        expected = getattr(guard, name, None)
        if expected is None:
            raise AssertionError(
                f"guard.{name} does not exist; got {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(exc, expected):
            raise AssertionError(f"expected {name}, got {type(exc).__name__}: {exc}") from exc
        if naming is not None:
            assert naming in str(exc), f"{name} must name {naming!r}: {exc}"
        caught.value = exc
        return
    pytest.fail(f"expected {name}; the call went through")


def error_text(exc: BaseException) -> str:
    """The message of `exc` and any notes added to it."""
    return "\n".join((str(exc), *getattr(exc, "__notes__", ())))


def asks_for_a_retry(exc: BaseException | None) -> None:
    assert exc is not None
    assert re.search(r"retry|try again", str(exc), re.IGNORECASE), (
        f"the refusal tells the caller to retry: {exc}"
    )


# ─── places ──────────────────────────────────────────────────────────────────


def user_data_dir(tmp_path: Path) -> Path:
    """Where `_user_data_redirected` points `platformdirs.user_data_path("wowlab")`."""
    return tmp_path / "userdata" / "wowlab"


def install_key(root: Path) -> str:
    st = root.stat()
    return hashlib.sha256(f"{st.st_dev}:{st.st_ino}".encode("ascii")).hexdigest()


def install_lock_file(tmp_path: Path, root: Path) -> Path:
    return user_data_dir(tmp_path) / "locks" / f"{install_key(root)}.lock"


def respelled(flavor: Flavor, path: Path) -> Flavor:
    return flavor.model_copy(update={"path": path})


def store_state(guard: Any, store: Path) -> tuple[object, ...]:
    """What a refused call must not change: the journal and the snapshots."""
    records = tuple((r.id, r.state, r.paths) for r in guard.history(store=store))
    manifests = tuple(m.id for m in SnapshotStore(store).list()) if store.exists() else ()
    return records, manifests


def journal_files(store: Path) -> list[Path]:
    """Files under the store outside `objects/` and `manifests/`."""
    skip = (store / "objects", store / "manifests")
    return [
        p
        for p in store.rglob("*")
        if p.is_file() and not p.is_symlink() and not any(d in p.parents for d in skip)
    ]


def spellings(rel: str) -> list[bytes]:
    """A flavor-relative path as the journal may hold it: plain and JSON-escaped,
    with `/` and with the platform's separator."""
    out: list[bytes] = []
    for text in dict.fromkeys((rel, rel.replace("/", os.sep))):
        for spelled in G._json_spellings(text):
            if spelled not in out:
                out.append(spelled)
    return out


def forge_journal(store: Path, old: str, new: str) -> int:
    """Rewrite every spelling of `old` as `new` in the journal; return the count."""
    snapshots = SnapshotStore(store)
    byte_pairs: list[tuple[bytes, bytes]] = []
    for o, n in dict.fromkeys(((old, new), (old.replace("/", os.sep), new.replace("/", os.sep)))):
        # The escaped spelling first: with backslashes the plain one is inside
        # it. Each distinct pair is applied once (`new` may contain `old`).
        for pair in reversed(list(zip(G._json_spellings(o), G._json_spellings(n), strict=True))):
            if pair not in byte_pairs:
                byte_pairs.append(pair)
    return sum(
        G.byte_replace(snapshots, o, n, skip=(snapshots.objects_dir, snapshots.manifests_dir))
        for o, n in byte_pairs
    )


def listing(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


# ─── a second process ────────────────────────────────────────────────────────

CHILD_SCRIPT = r'''
"""A second wowlab process for the M10-16T graders (constructed)."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import platformdirs
import psutil

args = json.loads(sys.argv[1])
user_data = Path(args["user_data"])
platformdirs.user_data_path = lambda *a, **k: user_data
# Constructed: no game client, and the real process table is never listed.
psutil.process_iter = lambda *a, **k: iter(())

from wowlab_core import guard  # noqa: E402

flavor = SimpleNamespace(path=Path(args["flavor"]), version=None)
store = Path(args["store"])
mode = args["mode"]


def say(word):
    sys.stdout.write(word + "\n")
    sys.stdout.flush()


def outcome(exc):
    busy = getattr(guard, "GuardBusyError", None)
    if busy is not None and isinstance(exc, busy):
        return "busy"
    return "error:" + type(exc).__name__


def locked():
    if mode.endswith("store-lock"):
        return guard.store_lock(store)
    return guard.transaction(
        flavor, label="child", store=store, dry_run=bool(args.get("dry_run", False))
    )


if mode in ("hold", "hold-store-lock"):
    try:
        with locked():
            say("entered")
            sys.stdin.readline()  # held until the parent kills this process
    except Exception as exc:
        say(outcome(exc))
elif mode in ("try", "try-store-lock"):
    try:
        with locked():
            say("entered")
    except Exception as exc:
        say(outcome(exc))
elif mode == "commit":
    try:
        with guard.transaction(flavor, label=args["label"], store=store) as tx:
            for rel, data in args["writes"]:
                tx.write(rel, bytes.fromhex(data))
        say("committed")
    except Exception as exc:
        say(outcome(exc))
elif mode == "die":
    strand = set(args["strand"])
    die_on = args["die_on"]
    stranded = set()
    real = {name: getattr(os, name) for name in ("replace", "rename", "unlink", "remove")}

    def moving(name):
        def call(src, dst, *a, **k):
            target = os.path.basename(os.fsdecode(dst))
            if target == die_on:
                os._exit(17)  # dies inside the rename: nothing after it runs
            if target in strand:
                stranded.add(os.path.basename(os.fsdecode(src)))
                raise PermissionError(13, "constructed: the rename is refused")
            return real[name](src, dst, *a, **k)

        return call

    def removing(name):
        def call(path, *a, **k):
            if os.path.basename(os.fsdecode(path)) in stranded:
                return None  # the stranded temp file stays behind
            return real[name](path, *a, **k)

        return call

    os.replace = moving("replace")
    os.rename = moving("rename")
    os.unlink = removing("unlink")
    os.remove = removing("remove")
    with guard.transaction(flavor, label=args.get("label", "child-died"), store=store) as tx:
        for rel, data in args["writes"]:
            try:
                tx.write(rel, bytes.fromhex(data))
            except guard.GuardError:
                pass
    say("survived")
    sys.exit(3)
'''

_EOF = "\x00eof"


class Child:
    """A second process running `CHILD_SCRIPT` with `args` (JSON)."""

    def __init__(self, tmp_path: Path, **args: Any) -> None:
        script = tmp_path / "m10_16t_child.py"
        if not script.exists():
            script.write_text(CHILD_SCRIPT, encoding="utf-8")
        args.setdefault("user_data", str(user_data_dir(tmp_path)))
        self.proc = subprocess.Popen(
            [sys.executable, str(script), json.dumps(args)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
        )
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line.strip())
        self._lines.put(_EOF)

    def _stderr(self) -> str:
        if self.proc.poll() is None or self.proc.stderr is None:
            return ""
        return self.proc.stderr.read()[-4000:]

    def line(self) -> str:
        try:
            got = self._lines.get(timeout=CHILD_TIMEOUT)
        except queue.Empty:
            self.kill()
            pytest.fail("the child process said nothing in time")
        if got == _EOF:
            self.proc.wait(timeout=CHILD_TIMEOUT)
            pytest.fail(f"the child process ended early ({self.proc.returncode}): {self._stderr()}")
        return got

    def wait(self) -> int:
        return self.proc.wait(timeout=CHILD_TIMEOUT)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=CHILD_TIMEOUT)


@pytest.fixture
def children() -> Iterator[list[Child]]:
    started: list[Child] = []
    yield started
    for child in started:
        child.kill()


def spawn(children: list[Child], tmp_path: Path, **args: Any) -> Child:
    child = Child(tmp_path, **args)
    children.append(child)
    return child


def child_says(children: list[Child], tmp_path: Path, **args: Any) -> str:
    """Run a child that answers once, and return its answer."""
    child = spawn(children, tmp_path, **args)
    answer = child.line()
    child.wait()
    return answer


def eventually(call: Callable[[], object]) -> None:
    """`call`, retried on `GuardBusyError` on Windows only, where the system
    may take a moment to release the locks of a process it just ended
    (LockFileEx documentation). POSIX releases them when the descriptors
    close, which has happened once the process has been waited for."""
    import time

    deadline = time.monotonic() + (15.0 if sys.platform == "win32" else 0.0)
    while True:
        try:
            call()
            return
        except Exception as exc:
            busy = getattr(sys.modules["wowlab_core.guard"], "GuardBusyError", None)
            if busy is None or not isinstance(exc, busy) or time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


# ─── hooks ───────────────────────────────────────────────────────────────────

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT


class DuringInstallReplace:
    """Runs `action` once, just before the first `os.replace`/`os.rename`
    whose target is inside `root`."""

    def __init__(self, root: Path, action: Callable[[], None]) -> None:
        self.roots = [root, root.resolve()]
        self.action = action
        self.fired = False
        self._real = {n: getattr(os, n) for n in ("replace", "rename")}

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in self._real:
            monkeypatch.setattr(os, name, self._wrap(name))

    def _wrap(self, name: str) -> Callable[..., None]:
        def call(
            src: Any, dst: Any, *, src_dir_fd: int | None = None, dst_dir_fd: int | None = None
        ) -> None:
            spelled = G._spelled(dst, dst_dir_fd, self.roots)
            inside = spelled is None or any(spelled.startswith(str(r) + os.sep) for r in self.roots)
            if inside and not self.fired:
                self.fired = True
                self.action()
            self._real[name](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        return call


class BeforeCreateIn:
    """Runs `action` once, just before the first file is created in
    `directory` under a name other than `target` (guard's temp file)."""

    def __init__(self, directory: Path, target: str, action: Callable[[], None]) -> None:
        self.dirs = {str(directory), str(directory.resolve())}
        self.roots = [directory, directory.resolve()]
        self.target = target
        self.action = action
        self.fired = False
        self._os_open = os.open
        self._io_open = io.open

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "open", self._open)
        monkeypatch.setattr(io, "open", self._py_open)
        monkeypatch.setattr(builtins, "open", self._py_open)

    def _maybe(self, path: Any, creating: bool, dir_fd: int | None) -> None:
        if self.fired or not creating or isinstance(path, int):
            return
        spelled = G._spelled(path, dir_fd, self.roots)
        if spelled is None:
            return
        where = Path(spelled)
        if str(where.parent) in self.dirs and where.name != self.target:
            self.fired = True
            self.action()

    def _open(self, path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        self._maybe(path, bool(flags & os.O_CREAT), dir_fd)
        return self._os_open(path, flags, mode, dir_fd=dir_fd)

    def _py_open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        self._maybe(file, any(c in mode for c in "wxa"), None)
        return self._io_open(file, mode, *args, **kwargs)


class AfterJournalFsync:
    """Runs `action` once, right after the first `os.fsync`/`os.fdatasync` of
    a file or directory under the store outside `objects/` and `manifests/`
    (the write-ahead journal naming the path, which comes after guard read
    it and before the unlink)."""

    def __init__(self, store: Path, action: Callable[[], None]) -> None:
        self.store = store
        self.action = action
        self.fired = False
        self._fsync = os.fsync
        self._fdatasync = getattr(os, "fdatasync", None)

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "fsync", self._sync(self._fsync))
        if self._fdatasync is not None:
            monkeypatch.setattr(os, "fdatasync", self._sync(self._fdatasync))

    def _journal_ids(self) -> set[tuple[int, int]]:
        skip = (self.store / "objects", self.store / "manifests")
        found = {G._file_id(self.store.stat())}
        for p in self.store.rglob("*"):
            if not p.is_symlink() and not any(d == p or d in p.parents for d in skip):
                found.add(G._file_id(p.stat()))
        return found

    def _sync(self, real: Callable[[Any], None]) -> Callable[[Any], None]:
        def call(fd: Any) -> None:
            real(fd)
            if self.fired:
                return
            number = fd if isinstance(fd, int) else fd.fileno()
            if G._file_id(os.fstat(number)) in self._journal_ids():
                self.fired = True
                self.action()

        return call


# ─── (4) the public surface ──────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_busy_and_changed_errors_are_exported_guard_errors(guard: Any) -> None:
    busy = getattr(guard, "GuardBusyError", None)
    changed = getattr(guard, "ChangedSinceSnapshotError", None)
    assert busy is not None and changed is not None, "item 4: both errors are exported"
    assert {"GuardBusyError", "ChangedSinceSnapshotError", "store_lock"} <= set(guard.__all__)
    for error in (busy, changed):
        assert issubclass(error, guard.GuardError)
        assert not issubclass(error, guard.ClientRunningError)
        assert not issubclass(error, guard.PathNotAllowedError)
    assert not issubclass(busy, changed)
    assert not issubclass(changed, busy)


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_history_records_carry_temps_removed_and_temps_left(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    fields = guard.HistoryRecord.model_fields
    assert {"temps_removed", "temps_left"} <= set(fields), "item 4: HistoryRecord gains both"
    store = tmp_path / "store"
    with guard.transaction(flavor, label="plain", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    record = record_for(guard, SnapshotStore(store), "plain")
    assert record.temps_removed == ()
    assert record.temps_left == ()


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_format_1_journal_record_still_reads_through_history_and_undo(
    guard: Any, tmp_path: Path, install_root: Path, flavor: Flavor, idle: None
) -> None:
    """Item 4: format-1 records (what the M10-11 gate wrote; this one is laid
    out byte for byte as it wrote them) read as naming no temp paths and as
    having run no cleanup, and `undo()` still acts on them."""
    store = tmp_path / "store"
    root = install_root.resolve()
    pre = SnapshotStore(store).create(
        root,
        [f"{FLAVOR_FOLDER}/{top}" for top in ("WTF", "Interface", "Fonts")],
        label="guard pre-write: format one",
        flavor_folder=FLAVOR_FOLDER,
        flavor_version=flavor.version,
        client_running=False,
    )
    (flavor.path / CONFIG).write_bytes(NEW_CONFIG)
    record = {
        "created_at": "2026-09-22T12:00:00.000000Z",
        "created_dirs": [],
        "flavor_path": str(root / FLAVOR_FOLDER),
        "flavor_version": flavor.version,
        "format": 1,
        "id": "00000001-0123abcd",
        "install_root": str(root),
        "label": "format one",
        "note": None,
        "paths": [
            {"after": sha(NEW_CONFIG), "before": sha(ALLOWLISTED_FILES[CONFIG]), "path": CONFIG}
        ],
        "snapshot_id": pre.id,
        "state": "committed",
    }
    (store / "journal").mkdir(parents=True)
    text = json.dumps(record, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
    (store / "journal" / "00000001-0123abcd.json").write_bytes(text.encode("ascii"))

    (loaded,) = guard.history(store=store)
    assert (loaded.label, loaded.state, loaded.snapshot_id) == ("format one", "committed", pre.id)
    assert loaded.temps_removed == ()
    assert loaded.temps_left == ()

    guard.undo(store=store)
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_store_lock_is_the_store_lock_alone_in_this_process_and_others(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
) -> None:
    """Item 4: `store_lock(store)` takes `<store>/lock` and nothing else, with
    the same mechanism and the same in-process refusal as a transaction."""
    import inspect

    parameters = inspect.signature(guard.store_lock).parameters
    assert list(parameters) == ["store"]
    assert parameters["store"].default is None
    assert inspect.signature(guard.store_lock, eval_str=True).parameters["store"].annotation == (
        Path | None
    )

    store = tmp_path / "store"
    store.mkdir()
    other_store = tmp_path / "store-2"
    kid = {"flavor": str(flavor.path), "store": str(store)}

    with guard.store_lock(store):
        assert (store / "lock").is_file() and not (store / "lock").is_symlink()
        before = (strict_state(world), store_state(guard, store))
        assert_busy(guard, attempt(lambda: _store_locked(guard, store)), "a nested store_lock")
        busy = attempt(lambda: _write_font(guard, flavor, store, "under-store-lock"))
        assert_busy(guard, busy, "a transaction on the locked store")
        assert (strict_state(world), store_state(guard, store)) == before
        # In another process too.
        assert child_says(children, tmp_path, mode="try", **kid) == "busy"
        assert child_says(children, tmp_path, mode="try-store-lock", **kid) == "busy"
        # The store lock alone: the same install through another store is free.
        _write_font(guard, flavor, other_store, "another-store")

    # Released: the store takes a transaction, and store_lock is refused while
    # one is open, here and in another process.
    with guard.transaction(flavor, label="after", store=store):
        assert_busy(guard, attempt(lambda: _store_locked(guard, store)), "store_lock")
        assert child_says(children, tmp_path, mode="try-store-lock", **kid) == "busy"
    holder = spawn(children, tmp_path, mode="hold", **kid)
    assert holder.line() == "entered"
    assert_busy(guard, attempt(lambda: _store_locked(guard, store)), "store_lock")
    holder.kill()
    holder = spawn(children, tmp_path, mode="hold-store-lock", **kid)
    assert holder.line() == "entered"
    assert_busy(guard, attempt(lambda: _write_font(guard, flavor, store, "vs-child")), "tx")
    holder.kill()
    eventually(lambda: _store_locked(guard, store))


def _store_locked(guard: Any, store: Path) -> None:
    with guard.store_lock(store):
        pass


def _write_font(guard: Any, flavor: Flavor, store: Path, label: str) -> None:
    with guard.transaction(flavor, label=label, store=store) as tx:
        tx.write(FONT, SECOND_FONT)


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_store_lock_follows_the_lock_file_rules(
    guard: Any, tmp_path: Path, world: Path, idle: None
) -> None:
    """Item 4: `O_NOFOLLOW`, a regular file, never truncated, written or
    deleted. A link at `<store>/lock` is refused and its target untouched; a
    lock file that is already there keeps its bytes and its identity."""
    target = world / "outside" / "target.txt"
    linked = tmp_path / "linked-store"
    linked.mkdir()
    symlink_or_skip(linked / "lock", target, is_dir=False)
    before = target.read_bytes()
    exc = attempt(lambda: _store_locked(guard, linked))
    assert_plain_guard_error(guard, exc, "store_lock through a link")
    assert target.read_bytes() == before
    assert (linked / "lock").is_symlink()

    kept = tmp_path / "kept-store"
    kept.mkdir()
    (kept / "lock").write_bytes(PRESET)
    identity = G._file_id((kept / "lock").stat())
    with guard.store_lock(kept):
        assert_busy(guard, attempt(lambda: _store_locked(guard, kept)), "nested")
    assert (kept / "lock").read_bytes() == PRESET
    assert G._file_id((kept / "lock").stat()) == identity


# ─── (1) one writer at a time ────────────────────────────────────────────────


def _unicode_install(world: Path) -> tuple[Path, Path]:
    """An install whose root name has a precomposed letter; returns the
    root as created (NFC) and its decomposed spelling (NFD)."""
    name = "Wörld of Warcraft"
    root = world / unicodedata.normalize("NFC", name)
    G._put(root, G.ROOT_FILES)
    G._put(root / FLAVOR_FOLDER, G.FLAVOR_ONLY_FILES)
    G._put(root / FLAVOR_FOLDER, ALLOWLISTED_FILES)
    variant = world / unicodedata.normalize("NFD", name)
    if not variant.exists() or not root.samefile(variant):
        pytest.skip("this volume does not treat Unicode spellings of a name as one directory")
    return root, variant


def _junction(link: Path, target: Path) -> None:
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"cannot create a junction here: {made.stderr.strip()}")


def relate(
    relation: str, world: Path, install_root: Path, flavor: Flavor
) -> tuple[Flavor, Flavor, bool]:
    """(first caller's flavor, second caller's flavor, same store?)."""
    same_store = relation.startswith("same-store")
    if relation.endswith("same-flavor"):
        return flavor, flavor, same_store
    if relation.endswith("another-flavor-of-the-install"):
        return flavor, G._other_flavor(install_root), same_store
    if relation.endswith("another-install"):
        return flavor, G._other_install(world), same_store
    if relation.endswith("symlinked-root"):
        link = world / "WoW-link"
        symlink_or_skip(link, install_root, is_dir=True)
        return flavor, respelled(flavor, link / FLAVOR_FOLDER), same_store
    if relation.endswith("case-variant-root"):
        if not G.case_insensitive(world):
            pytest.skip("this volume is case-sensitive: a case variant is another directory")
        variant = world / install_root.name.swapcase()
        return flavor, respelled(flavor, variant / FLAVOR_FOLDER), same_store
    if relation.endswith("unicode-variant-root"):
        nfc, nfd = _unicode_install(world)
        return (
            respelled(flavor, nfc / FLAVOR_FOLDER),
            respelled(flavor, nfd / FLAVOR_FOLDER),
            same_store,
        )
    if relation.endswith("junction-root"):
        if sys.platform != "win32":
            pytest.skip("junctions are a Windows facility")
        link = world / "WoW-junction"
        _junction(link, install_root)
        return flavor, respelled(flavor, link / FLAVOR_FOLDER), same_store
    raise AssertionError(relation)


def _second(kind: str, guard: Any, flavor: Flavor, store: Path) -> None:
    if kind == "transaction":
        _write_font(guard, flavor, store, "second")
    elif kind == "dry-run":
        with guard.transaction(flavor, label="second", store=store, dry_run=True) as tx:
            tx.write(FONT, SECOND_FONT)
    elif kind == "undo":
        guard.undo(store=store)
    elif kind == "store-lock":
        with guard.store_lock(store):
            pass
    else:
        raise AssertionError(kind)


def _hold(
    kind: str,
    guard: Any,
    flavor: Flavor,
    store: Path,
    during: Callable[[], None],
) -> None:
    """Run `during` while the first caller (`kind`) holds its locks."""
    if kind == "transaction":
        with guard.transaction(flavor, label="holder", store=store) as tx:
            tx.write(CONFIG, NEW_CONFIG)
            during()
    elif kind == "dry-run":
        with guard.transaction(flavor, label="holder", store=store, dry_run=True) as tx:
            tx.write(CONFIG, NEW_CONFIG)
            during()
    elif kind == "store-lock":
        store.mkdir(parents=True, exist_ok=True)
        with guard.store_lock(store):
            during()
    elif kind in ("restore", "undo"):
        with guard.transaction(flavor, label="holder-prep", store=store) as tx:
            tx.write(CONFIG, NEW_CONFIG)
        source = record_for(guard, SnapshotStore(store), "holder-prep").snapshot_id
        hook = DuringInstallReplace(flavor.path.parent, during)
        with pytest.MonkeyPatch.context() as patched:
            hook.arm(patched)
            if kind == "undo":
                guard.undo(store=store)
            else:
                with guard.transaction(flavor, label="holder", store=store) as tx:
                    tx.restore(source, paths=[CONFIG])
        assert hook.fired, f"the {kind} changed a file while holding its locks"
    else:
        raise AssertionError(kind)


FULL_RELATIONS = (
    "same-store-same-flavor",
    "other-store-same-flavor",
    "other-store-another-flavor-of-the-install",
    "same-store-another-install",
    "other-store-symlinked-root",
    "other-store-case-variant-root",
    "other-store-unicode-variant-root",
    "other-store-junction-root",
)
BASIC_RELATIONS = (
    "same-store-same-flavor",
    "other-store-same-flavor",
    "same-store-another-install",
)
BUSY_CASES = (
    *(("transaction", s, r) for s in ("transaction", "dry-run", "undo") for r in FULL_RELATIONS),
    *((h, "transaction", r) for h in ("dry-run", "restore", "undo") for r in BASIC_RELATIONS),
    *(("transaction", "store-lock", r) for r in BASIC_RELATIONS if r.startswith("same-store")),
    *(("store-lock", "transaction", r) for r in BASIC_RELATIONS if r.startswith("same-store")),
)


@pytest.mark.parametrize(
    ("holder", "second", "relation"),
    [pytest.param(h, s, r, id=f"constructed-{h}-then-{s}-{r}") for h, s, r in BUSY_CASES],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_second_caller_is_busy_while_the_first_holds_the_locks(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    holder: str,
    second: str,
    relation: str,
) -> None:
    """Item 1: the same store, or the same install by identity (another
    flavor of it, a symlinked, case-variant, Unicode-variant or junction
    spelling of its root), is busy while a transaction, a dry run, a restore,
    an undo or a `store_lock` holds it, in the same process. The refused call
    writes and journals nothing; once the first caller is done, the same call
    goes through."""
    first, other, same_store = relate(relation, world, install_root, flavor)
    first_store = tmp_path / "store"
    second_store = first_store if same_store else tmp_path / "store-2"
    if second == "undo":
        _write_font(guard, other, second_store, "to-undo")  # something for undo to act on
    if second == "store-lock":
        second_store.mkdir(parents=True, exist_ok=True)
    outcomes: list[tuple[BaseException | None, object, object]] = []

    def during() -> None:
        before = (strict_state(world), store_state(guard, second_store))
        exc = attempt(lambda: _second(second, guard, other, second_store))
        after = (strict_state(world), store_state(guard, second_store))
        outcomes.append((exc, before, after))

    _hold(holder, guard, first, first_store, during)

    assert len(outcomes) == 1
    exc, before, after = outcomes[0]
    assert_busy(guard, exc)
    assert after == before, "a busy call writes and journals nothing, in the install or the store"
    _second(second, guard, other, second_store)  # released on exit


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_locks_are_per_store_and_per_install_not_global(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None
) -> None:
    """Item 1 names two locks, one per store and one per install. Another
    install through another store is not excluded; either shared half is."""
    other = G._other_install(world)
    s1, s2 = tmp_path / "store", tmp_path / "store-2"
    with guard.transaction(flavor, label="holder", store=s1) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        _write_font(guard, other, s2, "unrelated")  # neither lock is shared
        assert (other.path / FONT).read_bytes() == SECOND_FONT
        assert_busy(guard, attempt(lambda: _write_font(guard, flavor, s2, "same-install")))
        assert_busy(guard, attempt(lambda: _write_font(guard, other, s1, "same-store")))
    assert record_for(guard, SnapshotStore(s2), "unrelated").state == "committed"


@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param(s, id=f"constructed-{s}")
        for s in ("canonical", "symlinked-root", "case-variant-root", "default-store")
    ],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_lock_files_live_in_the_store_and_the_user_data_directory(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    spelling: str,
) -> None:
    """Item 1: `<store>/lock` and `<user data dir>/locks/<key>.lock`, the key
    from the root's identity whatever its spelling; nothing inside the
    install (L1); both remain after exit (never deleted)."""
    store: Path | None = tmp_path / "store"
    caller = flavor
    if spelling == "symlinked-root":
        link = world / "WoW-link"
        symlink_or_skip(link, install_root, is_dir=True)
        caller = respelled(flavor, link / FLAVOR_FOLDER)
    elif spelling == "case-variant-root":
        if not G.case_insensitive(world):
            pytest.skip("this volume is case-sensitive: a case variant is another directory")
        caller = respelled(flavor, world / install_root.name.swapcase() / FLAVOR_FOLDER)
    elif spelling == "default-store":
        store = None
    store_dir = store if store is not None else user_data_dir(tmp_path) / "store"
    expected_lock = install_lock_file(tmp_path, install_root)
    before = strict_state(world)

    with guard.transaction(caller, label="where", store=store):
        for lock in (store_dir / "lock", expected_lock):
            assert lock.is_file() and not lock.is_symlink(), f"no lock file at {lock}"
            assert stat.S_ISREG(lock.lstat().st_mode)
        assert sorted(p.name for p in expected_lock.parent.iterdir()) == [expected_lock.name]
        assert strict_state(world) == before, "no lock, temp or journal file in the install"

    assert (store_dir / "lock").is_file() and expected_lock.is_file()
    assert sorted(p.name for p in expected_lock.parent.iterdir()) == [expected_lock.name]


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_dry_run_takes_the_locks_and_writes_only_the_lock_files(
    guard: Any, tmp_path: Path, world: Path, install_root: Path, flavor: Flavor, idle: None
) -> None:
    store = tmp_path / "store"
    before = content(tmp_path)
    with guard.transaction(flavor, label="plan", store=store, dry_run=True) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
        assert_busy(guard, attempt(lambda: _write_font(guard, flavor, store, "during-plan")))
        assert_busy(
            guard, attempt(lambda: _write_font(guard, flavor, tmp_path / "s2", "during-plan"))
        )
    after = content(tmp_path)
    key = install_key(install_root)
    added = set(after) - set(before)
    assert added == {
        "store",
        "store/lock",
        "userdata",
        "userdata/wowlab",
        "userdata/wowlab/locks",
        f"userdata/wowlab/locks/{key}.lock",
    } | ({"s2", "s2/lock"} if "s2/lock" in after else set()), sorted(added)
    assert {k: after[k] for k in before} == before, "nothing else changed anywhere"


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_a_busy_call_leaves_the_holders_locks_held(
    guard: Any,
    tmp_path: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
) -> None:
    """A refused call must not release what the holder holds: not by
    unlocking, not by closing a descriptor of the lock file (which would drop
    a `lockf` lock of the whole process; item 1 uses `flock`), and not in the
    process's own record of the locks it holds."""
    s1 = tmp_path / "store"
    with guard.transaction(flavor, label="holder", store=s1) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        for _ in range(2):
            assert_busy(guard, attempt(lambda: _write_font(guard, flavor, s1, "again")))
            assert_busy(
                guard, attempt(lambda: _write_font(guard, flavor, tmp_path / "s2", "again"))
            )
        for lock in (s1 / "lock", install_lock_file(tmp_path, install_root)):
            os.close(os.open(lock, os.O_RDONLY))
        same_store = {"flavor": str(flavor.path), "store": str(s1)}
        same_install = {"flavor": str(flavor.path), "store": str(tmp_path / "s3")}
        assert child_says(children, tmp_path, mode="try", **same_store) == "busy"
        assert child_says(children, tmp_path, mode="try", **same_install) == "busy"


@pytest.mark.parametrize(
    "mode",
    [pytest.param(m, id=f"constructed-{m}") for m in ("transaction", "dry-run", "store-lock")],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_a_lock_held_by_a_process_that_died_does_not_block(
    guard: Any,
    tmp_path: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
    mode: str,
) -> None:
    """Item 1: the operating system releases the locks when the process
    ends, so a killed holder never leaves a stale lock."""
    store = tmp_path / "store"
    store.mkdir()
    holder = spawn(
        children,
        tmp_path,
        mode="hold-store-lock" if mode == "store-lock" else "hold",
        dry_run=mode == "dry-run",
        flavor=str(flavor.path),
        store=str(store),
    )
    assert holder.line() == "entered"
    assert_busy(guard, attempt(lambda: _write_font(guard, flavor, store, "while-held")))
    if mode != "store-lock":
        assert_busy(
            guard, attempt(lambda: _write_font(guard, flavor, tmp_path / "s2", "while-held"))
        )

    holder.kill()

    eventually(lambda: _write_font(guard, flavor, store, "after-the-kill"))
    assert (flavor.path / FONT).read_bytes() == SECOND_FONT


def _refuse_on_enter(how: str, guard: Any, flavor: Flavor, store: Path, mp: Any, tmp: Path) -> None:
    if how == "client-running":
        G.use_probe(mp, G.running_table(tmp))
        with pytest.raises(guard.ClientRunningError):
            _write_font(guard, flavor, store, "refused")
        G.use_probe(mp, G.table(*G.bystanders(tmp)))
    elif how == "pre-write-snapshot-fails":

        def broken(self: SnapshotStore, *a: Any, **k: Any) -> None:
            raise SnapshotError("constructed: the snapshot failed")

        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(SnapshotStore, "create", broken)
            with pytest.raises(guard.GuardError):
                _write_font(guard, flavor, store, "refused")
    else:
        raise AssertionError(how)


@pytest.mark.parametrize(
    "how",
    [
        pytest.param(h, id=f"constructed-{h}")
        for h in (
            "exception-in-the-body",
            "keyboard-interrupt-in-the-body",
            "client-running",
            "pre-write-snapshot-fails",
            "install-lock-busy-after-the-store-lock",
        )
    ],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_locks_are_released_when_the_call_ends_in_an_error(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    """Item 1: released on exit, including on an exception, and including a
    refusal on enter after the locks were taken (they are taken before the
    client check and the snapshot, so the lock files exist afterwards)."""
    store = tmp_path / "store"
    if how in ("exception-in-the-body", "keyboard-interrupt-in-the-body"):
        error: type[BaseException] = (
            RuntimeError if how == "exception-in-the-body" else KeyboardInterrupt
        )
        with pytest.raises(error), guard.transaction(flavor, label="fails", store=store) as tx:
            tx.write(CONFIG, NEW_CONFIG)
            assert_busy(guard, attempt(lambda: _write_font(guard, flavor, store, "during")))
            raise error("constructed failure")
    elif how == "install-lock-busy-after-the-store-lock":
        other = G._other_install(world)
        s2 = tmp_path / "store-2"
        with guard.transaction(flavor, label="holder", store=store):
            assert_busy(guard, attempt(lambda: _write_font(guard, flavor, s2, "busy-install")))
            # The store lock it took on the way is free again: another install
            # through that store goes through while the holder is still open.
            _write_font(guard, other, s2, "store-2-free")
    else:
        _refuse_on_enter(how, guard, flavor, store, monkeypatch, tmp_path)
        assert (store / "lock").is_file(), "the store lock is taken before the refusal"
        assert install_lock_file(tmp_path, install_root).is_file()

    _write_font(guard, flavor, store, "after")
    _write_font(guard, flavor, tmp_path / "store-3", "after-other-store")
    assert (flavor.path / FONT).read_bytes() == SECOND_FONT


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_busy_is_decided_before_the_client_check(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1, order: the locks come before the client check, so a busy call
    never lists processes (and a transaction or undo refused as busy says
    busy, not "client running")."""
    store = tmp_path / "store"
    listed: list[int] = []

    def watching() -> Iterator[Any]:
        listed.append(1)
        return iter(G.bystanders(tmp_path))

    with guard.transaction(flavor, label="holder", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        G.use_probe(monkeypatch, watching)
        assert_busy(guard, attempt(lambda: _write_font(guard, flavor, store, "t")))
        assert_busy(guard, attempt(lambda: guard.undo(store=store)), "undo")
        assert_busy(guard, attempt(lambda: _write_font(guard, flavor, tmp_path / "s2", "t")))
        assert listed == [], "a busy call listed processes"
    G.use_probe(monkeypatch, watching)
    _write_font(guard, flavor, store, "after")
    assert listed, "positive control: an admitted call runs the client check"


LOCK_DEFECTS = (
    "store-lock-is-a-directory",
    "store-lock-is-a-symlink-to-a-file-outside",
    "store-lock-is-a-dangling-symlink",
    "store-lock-is-a-symlink-into-the-install",
    "install-lock-is-a-directory",
    "install-lock-is-a-symlink-into-the-install",
    "install-lock-is-a-fifo",
    "locks-directory-is-a-symlink-into-the-install",
)


def _plant(defect: str, tmp_path: Path, world: Path, install_root: Path, store: Path) -> Path:
    """Put `defect` in place; return the path that must be left as it is."""
    config = install_root / FLAVOR_FOLDER / CONFIG
    install_lock = install_lock_file(tmp_path, install_root)
    store.mkdir(parents=True, exist_ok=True)
    install_lock.parent.mkdir(parents=True, exist_ok=True)
    lock = store / "lock" if defect.startswith("store-lock") else install_lock
    if lock.is_symlink() or lock.is_file():
        lock.unlink()  # the test's own step: an earlier call made the lock file
    if defect.endswith("is-a-directory"):
        lock.mkdir()
    elif defect.endswith("symlink-to-a-file-outside"):
        symlink_or_skip(lock, world / "outside" / "target.txt", is_dir=False)
    elif defect.endswith("dangling-symlink"):
        symlink_or_skip(lock, world / "outside" / "made-by-a-lock", is_dir=False)
    elif defect == "locks-directory-is-a-symlink-into-the-install":
        shutil.rmtree(install_lock.parent)
        symlink_or_skip(install_lock.parent, config.parent, is_dir=True)
        return install_lock.parent
    elif defect.endswith("symlink-into-the-install"):
        symlink_or_skip(lock, config, is_dir=False)
    elif defect.endswith("fifo"):
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFOs are a POSIX thing")
        os.mkfifo(lock)
    else:
        raise AssertionError(defect)
    return lock


@pytest.mark.parametrize(
    "route", [pytest.param(r, id=f"constructed-{r}") for r in ("transaction", "dry-run", "undo")]
)
@pytest.mark.parametrize("defect", [pytest.param(d, id=f"constructed-{d}") for d in LOCK_DEFECTS])
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_a_lock_that_cannot_be_taken_safely_is_a_guard_error_and_nothing_proceeds(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    defect: str,
    route: str,
) -> None:
    """Item 1, lock files: opened with `O_NOFOLLOW`, a regular file by
    `fstat`, part of the store-overlap check; any failure to open or lock
    other than "held" is a `GuardError`, and guard never proceeds unlocked.
    The defect is left exactly as it was, and nothing lands in the install."""
    store = tmp_path / "store"
    if route == "undo":
        with guard.transaction(flavor, label="to-undo", store=store) as tx:
            tx.write(CONFIG, NEW_CONFIG)
    planted = _plant(defect, tmp_path, world, install_root, store)
    planted_before = (
        planted.readlink() if planted.is_symlink() else stat.S_IFMT(planted.lstat().st_mode)
    )
    before = (strict_state(world), store_state(guard, store))

    def call() -> None:
        if route == "undo":
            guard.undo(store=store)
        else:
            with guard.transaction(
                flavor, label="defect", store=store, dry_run=route == "dry-run"
            ) as tx:
                tx.write(CONFIG, NEWER_CONFIG)

    assert_plain_guard_error(guard, attempt(call), f"{route} with {defect}")
    assert (strict_state(world), store_state(guard, store)) == before
    after = planted.readlink() if planted.is_symlink() else stat.S_IFMT(planted.lstat().st_mode)
    assert after == planted_before, "the defect is left as it was"
    assert not (world / "outside" / "made-by-a-lock").exists(), (
        "O_NOFOLLOW: no file made through a link"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl.flock is the POSIX mechanism")
@pytest.mark.parametrize(
    "failure",
    [pytest.param(f, id=f"constructed-{f}") for f in ("held", "unsupported", "io-error")],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_flock_outcomes_are_busy_or_a_guard_error(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    failure: str,
) -> None:
    """Item 1, mechanism: `flock(LOCK_EX | LOCK_NB)`; "would block" is
    `GuardBusyError`, any other failure a plain `GuardError`; either way
    nothing is written or journaled."""
    import fcntl

    real = fcntl.flock
    error = {
        "held": BlockingIOError(errno.EWOULDBLOCK, "constructed: held"),
        "unsupported": OSError(errno.ENOLCK, "constructed: no locks on this volume"),
        "io-error": OSError(errno.EIO, "constructed: I/O error"),
    }[failure]

    def flock(fd: Any, operation: int) -> None:
        if operation & fcntl.LOCK_EX:
            raise error
        real(fd, operation)

    store = tmp_path / "store"
    before = (strict_state(world), store_state(guard, store))
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(fcntl, "flock", flock)
        exc = attempt(lambda: _write_font(guard, flavor, store, "injected"))
    if failure == "held":
        assert_busy(guard, exc)
    else:
        assert_plain_guard_error(guard, exc, f"a lock that failed with {failure}")
    assert (strict_state(world), store_state(guard, store)) == before


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_locks_use_flock_or_msvcrt_locking_and_never_lockf(
    guard: Any, tmp_path: Path, install_root: Path, flavor: Flavor, idle: None
) -> None:
    """Item 1, mechanism: `flock(fd, LOCK_EX | LOCK_NB)` on POSIX, and
    `msvcrt.locking(fd, LK_NBLCK, 1)` on Windows, on each of the two lock
    files; never `lockf` or `fcntl(F_SETLK)`."""
    store = tmp_path / "store"
    locked: list[tuple[tuple[int, int], int]] = []
    with pytest.MonkeyPatch.context() as patched:
        if sys.platform == "win32":
            import msvcrt

            real_locking = msvcrt.locking

            def locking(fd: int, mode: int, nbytes: int) -> None:
                if mode == msvcrt.LK_NBLCK:
                    assert nbytes == 1
                    locked.append((G._file_id(os.fstat(fd)), mode))
                real_locking(fd, mode, nbytes)

            patched.setattr(msvcrt, "locking", locking)
            wanted = msvcrt.LK_NBLCK
        else:
            import fcntl

            real_flock = fcntl.flock

            def flock(fd: Any, operation: int) -> None:
                if operation & fcntl.LOCK_EX:
                    number = fd if isinstance(fd, int) else fd.fileno()
                    locked.append((G._file_id(os.fstat(number)), operation))
                real_flock(fd, operation)

            def forbidden(*a: Any, **k: Any) -> None:
                pytest.fail("lockf is not the mechanism (item 1)")

            real_fcntl = fcntl.fcntl
            lock_commands = {
                getattr(fcntl, n)
                for n in ("F_SETLK", "F_SETLKW", "F_OFD_SETLK", "F_OFD_SETLKW")
                if hasattr(fcntl, n)
            }

            def fcntl_call(fd: Any, cmd: int, *a: Any) -> Any:
                if cmd in lock_commands:
                    pytest.fail("fcntl(F_SETLK) is not the mechanism (item 1)")
                return real_fcntl(fd, cmd, *a)

            patched.setattr(fcntl, "flock", flock)
            patched.setattr(fcntl, "lockf", forbidden)
            patched.setattr(fcntl, "fcntl", fcntl_call)
            wanted = fcntl.LOCK_EX | fcntl.LOCK_NB
        _write_font(guard, flavor, store, "watched")
    assert locked, "no lock was taken through the item 1 mechanism"
    expected = {
        G._file_id((store / "lock").stat()),
        G._file_id(install_lock_file(tmp_path, install_root).stat()),
    }
    assert {ident for ident, _ in locked} == expected
    assert all(operation == wanted for _, operation in locked), locked


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_lock_files_are_never_truncated_written_or_deleted(
    guard: Any, tmp_path: Path, install_root: Path, flavor: Flavor, idle: None
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    install_lock = install_lock_file(tmp_path, install_root)
    install_lock.parent.mkdir(parents=True)
    for lock in (store / "lock", install_lock):
        lock.write_bytes(PRESET)
    identities = [G._file_id(p.stat()) for p in (store / "lock", install_lock)]

    with guard.transaction(flavor, label="edit", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        assert_busy(guard, attempt(lambda: _write_font(guard, flavor, store, "during")))
    with guard.transaction(flavor, label="plan", store=store, dry_run=True) as tx:
        tx.write(CONFIG, NEWER_CONFIG)
    guard.undo(store=store)
    with guard.store_lock(store):
        pass

    for lock, identity in zip((store / "lock", install_lock), identities, strict=True):
        assert lock.read_bytes() == PRESET, f"{lock} was written"
        assert G._file_id(lock.stat()) == identity, f"{lock} was replaced or recreated"


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_undo_plans_from_the_record_it_reads_under_the_store_lock(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
) -> None:
    """Item 1, order: `undo()` finds the flavor from an unlocked read, takes
    the store lock, re-reads the journal and plans only from that. Here
    another process commits a transaction on another install through the
    same store in between; the record undo re-reads names another install,
    so undo raises `GuardError` and changes nothing in either install."""
    store = tmp_path / "store"
    with guard.transaction(flavor, label="mine", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    other = G._other_install(world)
    fired: list[str] = []
    real_open = os.open
    lock_path = str(store / "lock")

    def watching(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if not fired and not isinstance(path, int) and os.fsdecode(path) == lock_path:
            fired.append(
                child_says(
                    children,
                    tmp_path,
                    mode="commit",
                    label="theirs",
                    writes=[[CONFIG, NEWER_CONFIG.hex()]],
                    flavor=str(other.path),
                    store=str(store),
                )
            )
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(os, "open", watching)
        exc = attempt(lambda: guard.undo(store=store))
    assert fired == ["committed"], "undo opened the store lock, and the other commit landed first"
    assert_plain_guard_error(guard, exc, "undo of a record that changed install under it")
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    assert (other.path / CONFIG).read_bytes() == NEWER_CONFIG


# ─── (2) leftover temp files ─────────────────────────────────────────────────


class Leftovers:
    """Two temp files a dead transaction left: `a` stranded (in the icon
    directory), `b` left by the death itself (beside Config.wtf)."""

    def __init__(self, flavor_path: Path, a: str, b: str) -> None:
        self.a_rel, self.b_rel = a, b
        self.a, self.b = flavor_path / a, flavor_path / b
        self.a_bytes, self.b_bytes = self.a.read_bytes(), self.b.read_bytes()


def leave_temps(
    children: list[Child], tmp_path: Path, flavor: Flavor, store: Path, label: str = "child-died"
) -> Leftovers:
    icon_dir = flavor.path / Path(ICON).parent
    config_dir = flavor.path / Path(CONFIG).parent
    icon_before = listing(icon_dir) if icon_dir.is_dir() else set()
    config_before = listing(config_dir)
    config_bytes = (flavor.path / CONFIG).read_bytes()
    child = spawn(
        children,
        tmp_path,
        mode="die",
        label=label,
        writes=[[ICON, ICON_NEW.hex()], [CONFIG, NEW_CONFIG.hex()]],
        strand=[Path(ICON).name],
        die_on=Path(CONFIG).name,
        flavor=str(flavor.path),
        store=str(store),
    )
    assert child.wait() == DIED, "the child died inside the rename of Config.wtf"
    (a,) = listing(icon_dir) - icon_before - {Path(ICON).name}
    (b,) = listing(config_dir) - config_before
    for name in (a, b):
        assert TEMP_NAME.fullmatch(name), f"guard's temp name {name!r} is the amendment's pattern"
    assert (flavor.path / CONFIG).read_bytes() == config_bytes, "the death came before the rename"
    return Leftovers(
        flavor.path, f"{Path(ICON).parent.as_posix()}/{a}", f"{Path(CONFIG).parent.as_posix()}/{b}"
    )


def _last_record(guard: Any, store: Path) -> Any:
    return guard.history(store=store)[-1]


@pytest.mark.parametrize(
    "route", [pytest.param(r, id=f"constructed-{r}") for r in ("transaction", "undo")]
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_temps_a_dead_transaction_left_are_removed_on_the_next_enter(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
    route: str,
) -> None:
    """Item 2: the next enter of the flavor removes the temp files an earlier
    record names, after its pre-write snapshot (which therefore holds them)
    and its own record, and lists them in `temps_removed`."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    expected = content(world)
    for rel in (lo.a_rel, lo.b_rel):
        del expected[G.flavor_key(rel)]

    if route == "transaction":
        with guard.transaction(flavor, label="next", store=store):
            assert not lo.a.exists() and not lo.b.exists(), "removed on enter"
        record = record_for(guard, SnapshotStore(store), "next")
    else:
        guard.undo(store=store)
        assert not lo.a.exists() and not lo.b.exists(), "removed on undo's enter"
        record = _last_record(guard, store)
        assert record.label != "child-died", "undo journals a record of its own"

    assert content(world) == expected
    assert set(record.temps_removed) == {lo.a_rel, lo.b_rel}
    assert record.temps_left == ()
    pre = SnapshotStore(store).show(record.snapshot_id)
    assert G.entry_for(pre, lo.a_rel).sha256 == sha(lo.a_bytes), "snapshot first, then cleanup"
    assert G.entry_for(pre, lo.b_rel).sha256 == sha(lo.b_bytes)


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_a_temp_path_already_cleaned_is_not_considered_again(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, children: list[Child]
) -> None:
    """Item 2, the window: it starts at the most recent record of this flavor
    whose cleanup finished. A file that later appears at a path an older
    record named, and that was already removed once, is not guard's."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    with guard.transaction(flavor, label="cleans", store=store):
        pass
    assert set(record_for(guard, SnapshotStore(store), "cleans").temps_removed) == {
        lo.a_rel,
        lo.b_rel,
    }
    lo.b.write_bytes(b"constructed: someone's file under a temp-like name\n")

    with guard.transaction(flavor, label="later", store=store):
        pass

    assert lo.b.read_bytes() == b"constructed: someone's file under a temp-like name\n"
    later = record_for(guard, SnapshotStore(store), "later")
    assert later.temps_removed == ()


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_another_flavors_transactions_neither_clean_nor_move_the_window(
    guard: Any,
    tmp_path: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
) -> None:
    """Item 2: only records whose install root and flavor folder are this
    flavor's are considered, and the window is per flavor: transactions on
    another flavor (even one that also leaves a same-named file here) do not
    remove this flavor's temps or close its window."""
    store = tmp_path / "store"
    other = G._other_flavor(install_root)
    mine = leave_temps(children, tmp_path, flavor, store, label="mine-died")
    theirs = leave_temps(children, tmp_path, other, store, label="theirs-died")
    # A regular file with a temp name at a path only the other flavor's record names.
    lookalike = flavor.path / Path(theirs.b_rel)
    lookalike.write_bytes(theirs.b_bytes)

    with guard.transaction(other, label="theirs-next", store=store):
        pass
    theirs_record = record_for(guard, SnapshotStore(store), "theirs-next")
    assert set(theirs_record.temps_removed) == {theirs.a_rel, theirs.b_rel}
    assert mine.a.exists() and mine.b.exists(), "another flavor never cleans this one"
    assert lookalike.exists()

    with guard.transaction(flavor, label="mine-next", store=store):
        pass
    mine_record = record_for(guard, SnapshotStore(store), "mine-next")
    assert set(mine_record.temps_removed) == {mine.a_rel, mine.b_rel}, "the window stayed open"
    assert not mine.a.exists() and not mine.b.exists()
    assert lookalike.read_bytes() == theirs.b_bytes, "named only by another flavor's record"


def _not_a_temp(
    case: str, lo: Leftovers, world: Path, install_root: Path, flavor: Flavor, store: Path
) -> str:
    """Make `case` out of the leftover `b`; return the path the journal now
    names for it (which cleanup must leave alone and list in `temps_left`)."""
    name = Path(lo.b_rel).name
    fdir = flavor.path
    forged: str | None = None
    if case == "link-at-the-path":
        lo.b.unlink()
        symlink_or_skip(lo.b, world / "outside" / "target.txt", is_dir=False)
        return lo.b_rel
    if case == "dangling-link-at-the-path":
        lo.b.unlink()
        symlink_or_skip(lo.b, world / "outside" / "nothing-here", is_dir=False)
        return lo.b_rel
    if case == "directory-at-the-path":
        lo.b.unlink()
        G._put(lo.b, {"inside.txt": b"constructed: a directory with a temp-like name\n"})
        return lo.b_rel
    if case == "name-with-a-suffix":
        forged = f"WTF/{name}.bak"
        lo.b.rename(fdir / forged)
    elif case == "uppercase-hex-on-disk":
        forged = f"WTF/.wowlab-{name[len('.wowlab-') : -len('.tmp')].upper()}.tmp"
        temporary = fdir / "WTF" / "constructed-rename-step"
        lo.b.rename(temporary)
        temporary.rename(fdir / forged)
    elif case == "case-variant-spelling-in-the-record":
        if not G.case_insensitive(fdir / "WTF"):
            pytest.skip("this volume is case-sensitive: the variant names nothing on disk")
        forged = f"WTF/{name.upper()}"
    elif case == "not-a-temp-name":
        forged = CONFIG
    elif case == "outside-the-allowlist":
        forged = f"Logs/{name}"
        G._put(fdir, {forged: lo.b_bytes})
    elif case == "at-the-install-root":
        forged = f"../{name}"
        G._put(install_root, {name: lo.b_bytes})
    elif case == "under-data":
        forged = f"../Data/{name}"
        G._put(install_root, {f"Data/{name}": lo.b_bytes})
    elif case == "another-flavor-folder":
        G.add_other_flavor(install_root)
        forged = f"../{OTHER_FLAVOR}/WTF/{name}"
        G._put(install_root / OTHER_FLAVOR, {f"WTF/{name}": lo.b_bytes})
    elif case == "outside-the-install":
        forged = f"../../outside/{name}"
        G._put(world / "outside", {name: lo.b_bytes})
    elif case == "absolute-path":
        target = world / "outside" / name
        target.write_bytes(lo.b_bytes)
        forged = target.as_posix()
    elif case == "unc-path":
        forged = f"//constructed-server/share/{name}"
    elif case == "through-a-link":
        symlink_or_skip(fdir / "WTF" / "escape", world / "outside", is_dir=True)
        (world / "outside" / name).write_bytes(lo.b_bytes)
        forged = f"WTF/escape/{name}"
    else:
        raise AssertionError(case)
    replaced = forge_journal(store, lo.b_rel, forged)
    assert replaced >= 1, "item 2: the journal names the temp path it created"
    return forged


NOT_A_TEMP = (
    "link-at-the-path",
    "dangling-link-at-the-path",
    "directory-at-the-path",
    "name-with-a-suffix",
    "uppercase-hex-on-disk",
    "case-variant-spelling-in-the-record",
    "not-a-temp-name",
    "outside-the-allowlist",
    "at-the-install-root",
    "under-data",
    "another-flavor-folder",
    "outside-the-install",
    "absolute-path",
    "unc-path",
    "through-a-link",
)
# These fail a write rule on their spelling alone, so they are listed in
# `temps_left` without being looked up (clarified 2026-09-22).
NEVER_LOOKED_UP = frozenset(
    {
        "outside-the-allowlist",
        "at-the-install-root",
        "under-data",
        "another-flavor-folder",
        "outside-the-install",
        "absolute-path",
        "unc-path",
    }
)


class LookupSpy:
    """Records every `os.stat`, `os.lstat`, `os.open`, `os.listdir`,
    `os.scandir` or `os.access` of a path in `watched` (compared after
    lexical normalisation) or containing `marker`."""

    NAMES = ("stat", "lstat", "open", "listdir", "scandir", "access")

    def __init__(self, watched: set[str], marker: str) -> None:
        self.watched = watched
        self.marker = marker
        self.seen: list[str] = []
        self._real = {n: getattr(os, n) for n in self.NAMES}

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in self.NAMES:
            monkeypatch.setattr(os, name, self._wrap(name))

    def _wrap(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            if args and isinstance(args[0], (str, bytes, os.PathLike)):
                spelled = os.fsdecode(args[0])
                normal = os.path.normpath(Path(spelled).absolute())
                if normal in self.watched or self.marker in spelled:
                    self.seen.append(f"{name}({spelled})")
            return self._real[name](*args, **kwargs)

        return call


@pytest.mark.parametrize("case", [pytest.param(c, id=f"constructed-{c}") for c in NOT_A_TEMP])
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_cleanup_removes_only_regular_temp_files_inside_the_allowlist(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
    case: str,
) -> None:
    """Item 2: a temp path is removed only if its last component fullmatches
    the pattern as the directory lists it, the path passes the write rules
    and the target is a regular file. Anything else a record names (a link,
    a directory, another name, a path outside the allowlist or the install,
    through a link, or forged to point elsewhere) is left alone and listed in
    `temps_left`, and the transaction carries on. The other leftover, a
    genuine one, is removed in the same enter."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    left = _not_a_temp(case, lo, world, install_root, flavor, store)
    expected = content(world)
    del expected[G.flavor_key(lo.a_rel)]
    watched: set[str] = set()
    if case in NEVER_LOOKED_UP and not left.startswith("//"):
        for base in (flavor.path, flavor.path.resolve()):
            watched.add(os.path.normpath(base / left))
    spy = LookupSpy(watched, "constructed-server")

    with pytest.MonkeyPatch.context() as patched:
        if case in NEVER_LOOKED_UP:
            spy.arm(patched)
        with guard.transaction(flavor, label="next", store=store) as tx:
            tx.write(FONT, SECOND_FONT)
    expected[G.flavor_key(FONT)] = SECOND_FONT
    assert spy.seen == [], "a path that fails a write rule is never looked up"

    record = record_for(guard, SnapshotStore(store), "next")
    assert record.state == "committed", "a path left alone does not stop the transaction"
    assert lo.a_rel in record.temps_removed
    assert left in record.temps_left, (left, record.temps_left)
    assert left not in record.temps_removed
    assert content(world) == expected, "only the genuine leftover is gone"


@pytest.mark.parametrize(
    "route",
    [
        pytest.param(r, id=f"constructed-{r}")
        for r in ("write-existing", "write-new", "restore", "rollback", "undo")
    ],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_temp_path_is_journaled_and_fsynced_before_the_temp_file_exists(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, route: str
) -> None:
    """Item 2: before creating a temp file inside the install, guard records
    its flavor-relative path in the journal, written and fsynced first. Every
    file created in the install is checked at the moment of its creation."""
    store = tmp_path / "store"
    fsynced: set[tuple[int, int]] = set()
    seen: list[str] = []
    problems: list[str] = []
    roots = [flavor.path, flavor.path.resolve()]
    real_open, real_io_open = os.open, io.open
    real_fsync, real_fdatasync = os.fsync, getattr(os, "fdatasync", None)

    def check(path: Any, dir_fd: int | None) -> None:
        spelled = G._spelled(path, dir_fd, roots)
        if spelled is None:
            return
        where = Path(spelled)
        base = next((r for r in roots if where.is_relative_to(r)), None)
        if base is None:
            return
        rel = where.relative_to(base).as_posix()
        seen.append(rel)
        holding = [
            p for p in journal_files(store) if any(s in p.read_bytes() for s in spellings(rel))
        ]
        if not holding:
            problems.append(f"{rel}: created before the journal named it")
        elif not any(G._file_id(p.stat()) in fsynced for p in holding):
            problems.append(f"{rel}: named in the journal, but not fsynced")

    def os_open(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if flags & os.O_CREAT and not isinstance(path, int):
            check(path, dir_fd)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def py_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if not isinstance(file, int) and any(c in mode for c in "wxa"):
            check(file, None)
        return real_io_open(file, mode, *args, **kwargs)

    def syncing(real: Callable[[Any], None]) -> Callable[[Any], None]:
        def call(fd: Any) -> None:
            fsynced.add(G._file_id(os.fstat(fd if isinstance(fd, int) else fd.fileno())))
            real(fd)

        return call

    source = None
    if route in ("restore", "undo"):
        with guard.transaction(flavor, label="prep", store=store) as tx:
            tx.write(CONFIG, NEW_CONFIG)
        source = record_for(guard, SnapshotStore(store), "prep").snapshot_id

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(os, "open", os_open)
        patched.setattr(io, "open", py_open)
        patched.setattr(builtins, "open", py_open)
        patched.setattr(os, "fsync", syncing(real_fsync))
        if real_fdatasync is not None:
            patched.setattr(os, "fdatasync", syncing(real_fdatasync))
        if route == "undo":
            guard.undo(store=store)
        elif route == "rollback":
            with (
                pytest.raises(RuntimeError),
                guard.transaction(flavor, label="r", store=store) as tx,
            ):
                tx.write(CONFIG, NEW_CONFIG)
                raise RuntimeError("constructed failure")
        else:
            with guard.transaction(flavor, label="t", store=store) as tx:
                if route == "write-existing":
                    tx.write(CONFIG, NEW_CONFIG)
                elif route == "write-new":
                    tx.write(NEW_SAVED, NEW_LUA)
                else:
                    tx.restore(source, paths=[CONFIG])

    assert seen, "positive control: a temp file was created in the install"
    assert problems == []


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_each_removal_is_journaled_and_fsynced_before_the_unlink(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, children: list[Child]
) -> None:
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    targets = {
        str(p): rel
        for rel, path in ((lo.a_rel, lo.a), (lo.b_rel, lo.b))
        for p in (path, path.resolve())
    }
    roots = [flavor.path, flavor.path.resolve()]
    fsynced: set[tuple[int, int]] = set()
    seen: list[str] = []
    problems: list[str] = []
    real = {n: getattr(os, n) for n in ("unlink", "remove", "fsync")}

    def fsync(fd: Any) -> None:
        fsynced.add(G._file_id(os.fstat(fd if isinstance(fd, int) else fd.fileno())))
        real["fsync"](fd)

    def removing(name: str) -> Callable[..., None]:
        def call(path: Any, *args: Any, dir_fd: int | None = None, **kwargs: Any) -> None:
            rel = targets.get(G._spelled(path, dir_fd, roots) or "")
            if rel is not None:
                seen.append(rel)
                holding = [
                    p
                    for p in journal_files(store)
                    if any(s in p.read_bytes() for s in spellings(rel))
                    and G._file_id(p.stat()) in fsynced
                ]
                if not holding:
                    problems.append(f"{rel}: unlinked before a fsynced journal entry named it")
            real[name](path, *args, dir_fd=dir_fd, **kwargs)

        return call

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(os, "fsync", fsync)
        for name in ("unlink", "remove"):
            patched.setattr(os, name, removing(name))
        with guard.transaction(flavor, label="next", store=store):
            pass

    assert sorted(seen) == sorted({lo.a_rel, lo.b_rel}), "positive control: both were unlinked"
    assert problems == []


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_a_removal_that_fails_is_left_listed_and_does_not_stop_the_transaction(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, children: list[Child]
) -> None:
    """Item 2: a removal whose unlink fails moves from `temps_removed` to
    `temps_left` in the same record; the transaction carries on."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    refused_names = {lo.b.name}
    real = {n: getattr(os, n) for n in ("unlink", "remove")}

    def removing(name: str) -> Callable[..., None]:
        def call(path: Any, *args: Any, **kwargs: Any) -> None:
            if not isinstance(path, int) and Path(os.fsdecode(path)).name in refused_names:
                raise PermissionError(errno.EACCES, "constructed: the file is locked")
            real[name](path, *args, **kwargs)

        return call

    with pytest.MonkeyPatch.context() as patched:
        for name in ("unlink", "remove"):
            patched.setattr(os, name, removing(name))
        with guard.transaction(flavor, label="next", store=store) as tx:
            tx.write(FONT, SECOND_FONT)

    record = record_for(guard, SnapshotStore(store), "next")
    assert record.state == "committed"
    assert (flavor.path / FONT).read_bytes() == SECOND_FONT
    assert not lo.a.exists() and lo.a_rel in record.temps_removed
    assert lo.b.read_bytes() == lo.b_bytes
    assert lo.b_rel in record.temps_left
    assert lo.b_rel not in record.temps_removed


@pytest.mark.parametrize(
    "first",
    [pytest.param(f, id=f"constructed-{f}") for f in ("client-running", "store-busy", "dry-run")],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_cleanup_waits_for_an_admitted_enter_that_is_not_a_dry_run(
    guard: Any,
    tmp_path: Path,
    flavor: Flavor,
    idle: None,
    children: list[Child],
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    """Item 2: cleanup runs after the locks, the client check, the pre-write
    snapshot and the new record, and not in a dry run. So a refused enter or
    a dry run leaves the leftovers; the next admitted enter removes them."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    if first == "client-running":
        G.use_probe(monkeypatch, G.running_table(tmp_path))
        with pytest.raises(guard.ClientRunningError):
            _write_font(guard, flavor, store, "refused")
        G.use_probe(monkeypatch, G.table(*G.bystanders(tmp_path)))
    elif first == "store-busy":
        with guard.store_lock(store):
            assert_busy(guard, attempt(lambda: _write_font(guard, flavor, store, "busy")))
    else:
        with guard.transaction(flavor, label="plan", store=store, dry_run=True) as tx:
            tx.write(FONT, SECOND_FONT)
    assert lo.a.read_bytes() == lo.a_bytes and lo.b.read_bytes() == lo.b_bytes

    with guard.transaction(flavor, label="admitted", store=store):
        pass
    assert not lo.a.exists() and not lo.b.exists()


# ─── (3) a file changed after the pre-write snapshot ─────────────────────────


def _change(kind: str, path: Path) -> bytes | None:
    """Someone else changes `path`; return what it holds now (None: absent)."""
    if kind == "modify":
        path.write_bytes(EXTERNAL)
        return EXTERNAL
    if kind == "remove":
        path.unlink()
        return None
    if kind == "create":
        assert not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(EXTERNAL)
        return EXTERNAL
    raise AssertionError(kind)


def _held(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


# (id, operation, path, change). Before each case a committed "prep"
# transaction wrote Config.wtf and deleted bindings-cache.wtf, so its pre-write
# snapshot is a restore source that differs from the disk.
CHANGED_CASES = (
    ("write-modified", "write", CONFIG, "modify"),
    ("write-deleted", "write", CONFIG, "remove"),
    ("write-created", "write", NEW_SAVED, "create"),
    ("delete-modified", "delete", CONFIG, "modify"),
    ("delete-deleted", "delete", CONFIG, "remove"),
    ("delete-created", "delete", NEW_SAVED, "create"),
    ("restore-named-modified", "restore-named", CONFIG, "modify"),
    ("restore-named-deleted", "restore-named", CONFIG, "remove"),
    ("restore-named-created", "restore-named", BINDINGS, "create"),
    ("restore-all-modified", "restore-all", CONFIG, "modify"),
)


def _prep(guard: Any, flavor: Flavor, store: Path) -> str:
    with guard.transaction(flavor, label="prep", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
    return str(record_for(guard, SnapshotStore(store), "prep").snapshot_id)


def _operate(op: str, tx: Any, rel: str, source: str) -> None:
    if op == "write":
        tx.write(rel, NEWER_CONFIG)
    elif op == "delete":
        tx.delete(rel)
    elif op == "restore-named":
        tx.restore(source, paths=[rel])
    elif op == "restore-all":
        tx.restore(source)
    else:
        raise AssertionError(op)


@pytest.mark.parametrize(
    ("op", "rel", "change"),
    [pytest.param(op, rel, ch, id=f"constructed-{name}") for name, op, rel, ch in CHANGED_CASES],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_first_touch_of_a_path_changed_since_the_snapshot_is_refused(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    op: str,
    rel: str,
    change: str,
) -> None:
    """Item 3: at the first touch (write, delete, restore), a path whose disk
    state differs from the pre-write snapshot's entry (content, or absent
    against present) raises `ChangedSinceSnapshotError` naming it and asking
    for a retry. Nothing is written for it, then or at rollback; every later
    operation raises `GuardError` without touching the disk; the transaction
    cannot commit although the caller caught the error. Controls in the same
    transaction: a path this transaction wrote is not refused on its second
    touch, and a changed path it never touches does not block it. The retry
    goes through."""
    store = tmp_path / "store"
    source = _prep(guard, flavor, store)
    target = flavor.path / rel
    font_control = op != "restore-all"  # a whole restore touches Fonts/ too
    before = content(world)
    reached_end = False
    exit_error: BaseException | None = None

    try:
        with guard.transaction(flavor, label="changed", store=store) as tx:
            tx.write(ICON, ICON_NEW)
            tx.write(ICON, ICON_NEWER)
            assert (flavor.path / ICON).read_bytes() == ICON_NEWER
            if font_control:
                (flavor.path / FONT).write_bytes(EXTERNAL_FONT)
            now = _change(change, target)
            with refused(guard, "ChangedSinceSnapshotError", naming=rel) as caught:
                _operate(op, tx, rel, source)
            asks_for_a_retry(caught.value)
            assert _held(target) == now, "nothing was written for the changed path"

            mid = content(world)
            pre_id = record_for(guard, SnapshotStore(store), "changed").snapshot_id
            for later in (
                lambda: tx.write(TOC, b"## Title: constructed after the refusal\n"),
                lambda: tx.delete(ADDON_LUA),
                lambda: tx.restore(pre_id, paths=[TOC]),
            ):
                with pytest.raises(guard.GuardError):
                    later()
            assert content(world) == mid, "later operations touch nothing"
            reached_end = True
    except guard.GuardError as exc:  # the exit may say it could not commit
        exit_error = exc

    assert reached_end, f"the transaction body did not finish: {exit_error!r}"
    assert not isinstance(exit_error, guard.ClientRunningError)
    record = record_for(guard, SnapshotStore(store), "changed")
    assert record.state == "rolled_back", record.state
    expected = dict(before)
    key = G.flavor_key(rel)
    if now is None:
        expected.pop(key, None)
    else:
        expected[key] = now
    if font_control:
        expected[G.flavor_key(FONT)] = EXTERNAL_FONT
    assert content(world) == expected, "rolled back, and the changed path kept its new state"

    # Clarified 2026-09-22: an undo() of the record leaves the refused path as it is.
    guard.undo(store=store)
    assert _held(target) == now, "undo left the path whose only operation was refused"

    with guard.transaction(flavor, label="retry", store=store) as tx:
        tx.write(rel, RETRIED)
    assert target.read_bytes() == RETRIED
    assert record_for(guard, SnapshotStore(store), "retry").state == "committed"


def _swap(kind: str, path: Path) -> Callable[[], None]:
    def swap() -> None:
        current = path.read_bytes()
        side = path.with_name(path.name + ".constructed-side")
        if kind == "another-file":
            side.write_bytes(EXTERNAL)
            side.replace(path)
        elif kind == "identical-bytes-in-a-new-file":
            side.write_bytes(current)
            side.replace(path)
        elif kind == "rewritten-in-place-same-size-and-mtime":
            st = path.stat()
            with path.open("r+b") as handle:
                handle.write(b"#" * len(current))
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        elif kind == "removed":
            path.unlink()
        else:
            raise AssertionError(kind)

    return swap


SWAPS = (
    "another-file",
    "identical-bytes-in-a-new-file",
    "rewritten-in-place-same-size-and-mtime",
    "removed",
)
SWAP_CASES = (
    *(("write", CONFIG, s) for s in SWAPS),
    *(("restore", CONFIG, s) for s in SWAPS),
    *(("delete", BINDINGS, s) for s in SWAPS),
    ("create", NEW_SAVED, "appeared"),
)


@pytest.mark.parametrize(
    ("op", "rel", "swap"),
    [pytest.param(op, rel, s, id=f"constructed-{op}-{s}") for op, rel, s in SWAP_CASES],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_file_swapped_after_guard_read_it_is_refused_before_the_replace_or_unlink(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    op: str,
    rel: str,
    swap: str,
) -> None:
    """Item 3: after the temp file is written and fsynced, and before the
    replace or unlink, guard re-reads the target and requires the hash it
    read (for a create: still absent), then its identity, size and mtime. A
    file swapped (or rewritten in place, keeping size and mtime, or removed,
    or created) after the first-touch read is refused with
    `ChangedSinceSnapshotError`; the swapped-in state survives, no temp file
    is left, and the transaction does not commit."""
    store = tmp_path / "store"
    source = _prep(guard, flavor, store) if op == "restore" else ""
    if op == "delete":  # _prep would have deleted it; this case needs it present
        assert (flavor.path / BINDINGS).exists()
    target = flavor.path / rel
    parent_names = listing(target.parent)
    witness: dict[str, object] = {}

    def swapped() -> None:
        if swap == "appeared":
            target.write_bytes(EXTERNAL)
        else:
            _swap(swap, target)()
        witness["bytes"] = _held(target)
        witness["id"] = G._file_id(target.stat()) if target.exists() else None

    if op == "delete":
        hook: Any = AfterJournalFsync(store, swapped)
    else:
        hook = BeforeCreateIn(target.parent, target.name, swapped)

    with refused(guard, "ChangedSinceSnapshotError", naming=rel):  # noqa: SIM117
        with guard.transaction(flavor, label="swapped", store=store) as tx:
            with pytest.MonkeyPatch.context() as patched:
                hook.arm(patched)
                if op == "write":
                    tx.write(rel, NEWER_CONFIG)
                elif op == "create":
                    tx.write(rel, NEW_LUA)
                elif op == "restore":
                    tx.restore(source, paths=[rel])
                else:
                    tx.delete(rel)

    assert hook.fired, "positive control: the swap happened inside the operation"
    assert _held(target) == witness["bytes"], "nothing was written over the swapped-in state"
    assert (G._file_id(target.stat()) if target.exists() else None) == witness["id"]
    if target.exists():
        expected_names = parent_names | {target.name}
    else:
        expected_names = parent_names - {target.name}
    assert listing(target.parent) == expected_names, "no temp file is left behind"
    # Clarified 2026-09-22: a path whose only operation was refused makes the
    # record `rollback_incomplete` only if the disk no longer matches what
    # guard read there (content), and an undo() of the record leaves it.
    state = record_for(guard, SnapshotStore(store), "swapped").state
    moved = swap != "identical-bytes-in-a-new-file"
    assert state == ("rollback_incomplete" if moved else "rolled_back"), state
    guard.undo(store=store)
    assert _held(target) == witness["bytes"], "undo left the refused path as it is"


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_dry_run_has_no_snapshot_to_compare_and_is_not_refused(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    """Item 3 does not apply to a dry run (it has no pre-write snapshot): the
    plan simply shows the disk. A real transaction in the same situation is
    refused."""
    store = tmp_path / "store"
    config = flavor.path / CONFIG
    with guard.transaction(flavor, label="plan", store=store, dry_run=True) as tx:
        config.write_bytes(EXTERNAL)
        tx.write(CONFIG, NEW_CONFIG)
        assert [(item.path, item.before) for item in tx.plan] == [(CONFIG, sha(EXTERNAL))]
    with refused(guard, "ChangedSinceSnapshotError", naming=CONFIG):  # noqa: SIM117
        with guard.transaction(flavor, label="real", store=store) as tx:
            config.write_bytes(EXTERNAL + b"again\n")
            tx.write(CONFIG, NEW_CONFIG)
    assert config.read_bytes() == EXTERNAL + b"again\n"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="os.rename replaces on POSIX; only Windows has the non-replacing rename item 3 names",
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_windows_create_whose_rename_finds_the_target_is_refused(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    """Item 3: on Windows a create uses `os.rename`, which does not replace;
    a file that appears after guard's last check makes the rename fail, and
    that is `ChangedSinceSnapshotError`, with the appeared file intact."""
    store = tmp_path / "store"
    target = flavor.path / NEW_SAVED
    names = listing(target.parent)
    real_rename = os.rename
    fired: list[bool] = []

    def rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if not fired and not isinstance(dst, int) and Path(os.fsdecode(dst)).name == target.name:
            fired.append(True)
            target.write_bytes(EXTERNAL)
        real_rename(src, dst, *args, **kwargs)

    with refused(guard, "ChangedSinceSnapshotError", naming=NEW_SAVED):  # noqa: SIM117
        with guard.transaction(flavor, label="create", store=store) as tx:
            with pytest.MonkeyPatch.context() as patched:
                patched.setattr(os, "rename", rename)
                tx.write(NEW_SAVED, NEW_LUA)
    assert fired, "positive control: the create went through os.rename"
    assert target.read_bytes() == EXTERNAL
    assert listing(target.parent) == names | {target.name}, "no temp file is left behind"


# ─── clarified 2026-09-22 (PR #53) ───────────────────────────────────────────

# (id, first operation, path, what someone else does next, second operation)
SECOND_TOUCH_CASES = (
    ("write-after-write", "write", CONFIG, "modify", "write"),
    ("delete-after-write", "write", CONFIG, "modify", "delete"),
    ("write-after-delete", "delete", BINDINGS, "create", "write"),
)


def _touch(op: str, tx: Any, rel: str, data: bytes) -> None:
    if op == "write":
        tx.write(rel, data)
    else:
        tx.delete(rel)


@pytest.mark.parametrize(
    ("first", "rel", "change", "second"),
    [pytest.param(f, r, c, s, id=f"constructed-{n}") for n, f, r, c, s in SECOND_TOUCH_CASES],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_a_path_changed_after_this_transaction_touched_it_is_refused(
    guard: Any,
    tmp_path: Path,
    flavor: Flavor,
    idle: None,
    first: str,
    rel: str,
    change: str,
    second: str,
) -> None:
    """Item 3 as clarified: "the hash it last read or wrote there" is guard's
    own record of the path within the transaction. A path this transaction
    wrote (or deleted), then changed by someone else, then touched again,
    raises `ChangedSinceSnapshotError`; the other writer's state stays, also
    through rollback, which ends `rollback_incomplete` for that path."""
    store = tmp_path / "store"
    target = flavor.path / rel
    reached_end = False
    exit_error: BaseException | None = None
    try:
        with guard.transaction(flavor, label="twice", store=store) as tx:
            tx.write(ICON, ICON_NEW)
            _touch(first, tx, rel, NEW_CONFIG)
            now = _change(change, target)
            with refused(guard, "ChangedSinceSnapshotError", naming=rel) as caught:
                _touch(second, tx, rel, NEWER_CONFIG)
            asks_for_a_retry(caught.value)
            assert _held(target) == now, "nothing was written over the other writer's state"
            mid = content(flavor.path)
            for later in (
                lambda: tx.write(TOC, b"## Title: constructed after the refusal\n"),
                lambda: tx.delete(ADDON_LUA),
            ):
                with pytest.raises(guard.GuardError):
                    later()
            assert content(flavor.path) == mid, "later operations touch nothing"
            reached_end = True
    except guard.GuardError as exc:
        exit_error = exc
    assert reached_end, f"the transaction body did not finish: {exit_error!r}"
    assert exit_error is not None, "a transaction with a refused path cannot commit"
    assert rel in error_text(exit_error), "the error at exit names the path left as it is"
    assert _held(target) == now, "rollback left the other writer's state alone"
    assert (flavor.path / ICON).read_bytes() == ALLOWLISTED_FILES[ICON], "the rest rolled back"
    record = record_for(guard, SnapshotStore(store), "twice")
    assert record.state == "rollback_incomplete"
    assert record.rolled_back is False


ROLLBACK_CASES = (
    ("overwritten-after-a-write", "write", CONFIG, "modify"),
    ("removed-after-a-write", "write", CONFIG, "remove"),
    ("recreated-after-a-delete", "delete", BINDINGS, "create"),
    ("changed-after-a-create", "write", NEW_SAVED, "modify"),
)


@pytest.mark.parametrize(
    ("op", "rel", "change"),
    [pytest.param(o, r, c, id=f"constructed-{n}") for n, o, r, c in ROLLBACK_CASES],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_rollback_leaves_a_path_someone_else_changed_and_says_so(
    guard: Any,
    tmp_path: Path,
    flavor: Flavor,
    idle: None,
    op: str,
    rel: str,
    change: str,
) -> None:
    """Item 3 as clarified: on rollback, a path whose disk content differs
    from what this transaction last wrote or read there is left as it is,
    and the record ends `rollback_incomplete` (`rolled_back` false). Rollback
    never overwrites bytes no snapshot or journal entry holds, and never
    deletes a file someone else put where this transaction created one.
    Every other touched path is still rolled back."""
    store = tmp_path / "store"
    target = flavor.path / rel
    with (
        pytest.raises(RuntimeError, match="constructed failure") as raised,
        guard.transaction(flavor, label="failing", store=store) as tx,
    ):
        tx.write(ICON, ICON_NEW)
        tx.write(FONT, SECOND_FONT)
        _touch(op, tx, rel, NEW_LUA)
        now = _change(change, target)
        # Someone else puts Fonts' pre-transaction bytes back: that path needs
        # nothing and counts as rolled back.
        (flavor.path / FONT).write_bytes(ALLOWLISTED_FILES[FONT])
        raise RuntimeError("constructed failure")

    assert _held(target) == now, "the other writer's state was left as it is"
    assert rel in error_text(raised.value), "the error at exit names the path left as it is"
    assert (flavor.path / FONT).read_bytes() == ALLOWLISTED_FILES[FONT]
    assert (flavor.path / ICON).read_bytes() == ALLOWLISTED_FILES[ICON], "the rest rolled back"
    record = record_for(guard, SnapshotStore(store), "failing")
    assert record.state == "rollback_incomplete"
    assert record.rolled_back is False


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_store_lock_never_creates_a_missing_store(
    guard: Any, tmp_path: Path, idle: None, _user_data_redirected: Path
) -> None:
    """Item 4 as clarified: `store_lock` never creates the store directory; a
    missing store is a `GuardError` (never `GuardBusyError`). Positive
    control: an existing store is locked, through `<store>/lock`."""
    missing = tmp_path / "missing-store"
    assert_plain_guard_error(
        guard, attempt(lambda: _store_locked(guard, missing)), "a missing store"
    )
    assert not missing.exists()
    assert_plain_guard_error(guard, attempt(lambda: _store_locked(guard, None)), "no default store")
    assert not _user_data_redirected.exists()

    present = tmp_path / "store"
    present.mkdir()
    with guard.store_lock(present):
        assert (present / "lock").is_file()


@pytest.mark.parametrize(
    "gone",
    [pytest.param(g, id=f"constructed-{g}") for g in ("temp-file", "parent-directory")],
)
@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_a_named_temp_that_no_longer_exists_is_listed_nowhere(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, children: list[Child], gone: str
) -> None:
    """Item 2 as clarified: a temp path an earlier record names that no longer
    exists (its last component or a parent directory missing) is listed in
    neither `temps_removed` nor `temps_left`; the one that still exists is
    removed in the same enter."""
    store = tmp_path / "store"
    lo = leave_temps(children, tmp_path, flavor, store)
    if gone == "temp-file":  # someone else already removed it
        lo.b.unlink()
        vanished, kept, kept_rel = lo.b_rel, lo.a, lo.a_rel
    else:  # its directory is gone (Interface/Icons, with the icon in it)
        shutil.rmtree(lo.a.parent)
        vanished, kept, kept_rel = lo.a_rel, lo.b, lo.b_rel

    with guard.transaction(flavor, label="next", store=store):
        pass

    assert not kept.exists(), "the leftover that still existed is removed"
    record = record_for(guard, SnapshotStore(store), "next")
    assert record.temps_removed == (kept_rel,)
    assert vanished not in record.temps_left


@pytest.mark.xfail(strict=True, reason="M10-16 not implemented")
def test_constructed_undo_of_a_rollback_incomplete_record_restores_and_can_be_undone(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    """Item 3 as clarified: `undo()` of a `rollback_incomplete` record restores
    its journaled paths from the pre-write snapshot, replacing what rollback
    left; the undo's own pre-write snapshot holds those bytes, so a second
    `undo()` puts them back."""
    store = tmp_path / "store"
    config = flavor.path / CONFIG
    with (
        pytest.raises(RuntimeError, match="constructed failure"),
        guard.transaction(flavor, label="failing", store=store) as tx,
    ):
        tx.write(ICON, ICON_NEW)
        tx.write(CONFIG, NEW_CONFIG)
        config.write_bytes(EXTERNAL)
        raise RuntimeError("constructed failure")
    assert record_for(guard, SnapshotStore(store), "failing").state == "rollback_incomplete"
    assert config.read_bytes() == EXTERNAL

    guard.undo(store=store)
    assert config.read_bytes() == ALLOWLISTED_FILES[CONFIG], "undo restored the journaled path"
    assert (flavor.path / ICON).read_bytes() == ALLOWLISTED_FILES[ICON]

    guard.undo(store=store)
    assert config.read_bytes() == EXTERNAL, "a second undo put back what the first replaced"
