"""Graders for "guard reads never block" (M11-11T).

Written before M11-11, from the ticket text (M11-11T, a follow-up from the
M11-08 security review on PR #96, scope widened by the conductor in fix
round 1 to the store and capture reads of `snapshot.py`) and
docs/LAB_PLAN.md §6.9 and §6.10 with their amendments. The gate reads a
target at its first touch to compare it with the pre-write snapshot, reads it
again before each replace or unlink (item 3), reads it during `tx.restore`
and a dry run, and reads it once more on rollback; a restore or undo reads
objects from the store; every transaction reads the journal on enter (item
1). All of that happens while guard holds the store lock and the install
lock, so a read that never returns keeps both locks forever. A snapshot
(`snap create`, `profile save`, guard's pre-write snapshot) reads every file
it captures. Each grader function marked below carries one marker line that
M11-11 deletes; nothing else in this file is the implementer's to change.
The pre-write snapshot's own read is graded by the review probe
`review/test_review_m11_11_prewrite_snapshot_read_blocks_on_fifo.py`.

Everything here is constructed, and POSIX-only: FIFOs are made with
`os.mkfifo`, and every test is skipped where it is missing. The synthetic
install, the store, the redirected user data directory and the injected
process table are the M10-11T ones from `test_guard.py` (loaded, not
copied). No test needs or touches a real install.

What the graders hold the code to
---------------------------------
- Install reads (`guard`): a FIFO that takes the place of a file after
  guard's walk found a regular file there, and before guard opens it to
  read, is refused within a bounded time: the operation raises a
  `GuardError` (never a `ClientRunningError` or a `GuardBusyError`) whose
  text, notes or causes name the path, and nothing is written: the FIFO is
  still a FIFO, every other entry of the flavor folder is as it was, and no
  temp file is left. A transaction that saw the refusal does not commit. On
  rollback the path is left as it is and the record ends
  `rollback_incomplete` (item 3 as clarified 2026-09-22); the body's own
  exception is what leaves the `with`, carrying a note that names the path.
  The `-empty-target` cases hold a file whose expected content is empty,
  where a FIFO read as `b""` would hash equal: only a type check refuses it.
- Capture reads (`SnapshotStore.create`): a FIFO swapped in between the
  capture's `lstat` and its open ends the capture within the bound, with the
  path not recorded as a file (skipped, as §6.9 says `create` ignores a
  FIFO, or refused with a `SnapshotError`) and nothing in the install
  changed.
- Store reads (`SnapshotStore.read_object`, reached from `tx.restore` and
  `undo()`): an object that is a FIFO, or a symbolic link to a sound object
  elsewhere, is refused within the bound: a `GuardError`, nothing written in
  the install, the transaction not committed. The spec sets no size cap on
  an object, so none is graded here; one is the implementer's call.
- Journal reads (`guard.history`, `transaction()` on enter, `undo()`): a
  record swapped for a FIFO, or for a symbolic link (to a sound copy of the
  record, or to a copy padded past the record size guard accepts), between
  guard's `lstat` and its open, is refused within the bound with a
  `GuardError`, and nothing is written in the install or the store (§6.10
  item 1: a journal that cannot be read in full raises under the locks and
  before the pre-write snapshot). A link swapped in on `undo()` is not
  graded: its second read, under the store lock, already refuses the link
  by `lstat` today.
- Refusals are graded on type, path and untouched state, not on wording:
  the existing descriptor check ("changed while it was being read") and a
  new "not a regular file" both satisfy them.

How the swap is made (the hook)
-------------------------------
`os.open` and `io.open` (what `Path.read_bytes` calls) are looked up at call
time. For the length of one test both are wrapped: once armed, the Nth call
that opens the armed target read-only, recognised by the `(st_dev, st_ino)`
of the file the target held when the grader armed the hook, first replaces
the target (with a FIFO, a symbolic link, or, in the control, a regular file
with the same bytes) and then makes the real call. That is exactly "between
the walk and the open". A descriptor on the replaced file stays open until
the real call returns, so the replacement can never reuse its inode. Install
hooks are armed only after the pre-write snapshot exists (inside the `with`,
or, for `undo()`, when `SnapshotStore.create` returns).

Bounded time, never a hang
--------------------------
The operation runs in a worker thread. Once it has run `BLOCKED_AFTER`
seconds, the grader tries every 50 ms to open the write end of each FIFO it
planted, without blocking. That succeeds only while something has the FIFO
open to read, which a non-blocking reader never does for long; success
releases the blocked reader, the grader waits for the worker and fails. An
operation that runs `OVERALL` seconds fails too. So today each marked grader
fails in about two seconds, for the missing behaviour only, and CI never
hangs.

Unmarked tests in this file
---------------------------
The ticket's first bullet also covers a FIFO already in place of a file
before the operation. Guard's walk refuses that by `lstat` ("not a regular
file") today, so those graders carry no marker (a strict `xfail` on them
would turn the suite red); they are regression pins. The control swaps in a
regular file with the same bytes at the same reads: the hash cannot tell it
apart, and guard's descriptor and identity checks already refuse it today,
so the hook reaches guard's read path and the marked graders fail for the
blocking open only.
"""

from __future__ import annotations

import errno
import importlib.util
import io
import json
import os
import stat
import sys
import threading
import time
import types
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotError, SnapshotStore


def _load_m10_11t_graders() -> types.ModuleType:
    """`test_guard.py` as a module of its own, for its synthetic install,
    fixtures and helpers (as `test_guard_owner_decisions.py` loads it)."""
    path = Path(__file__).resolve().parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_m11_11t_base_graders", path)
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
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
NEW_CONFIG = G.NEW_CONFIG
NEWER_CONFIG = G.NEWER_CONFIG
sha = G.sha
record_for = G.record_for

pytestmark = pytest.mark.skipif(
    not hasattr(os, "mkfifo"), reason="FIFOs are a POSIX thing (os.mkfifo is missing)"
)

BLOCKED_AFTER = 2.0
"""Seconds after which a reader still holding a planted FIFO counts as blocked."""
OVERALL = 60.0
"""Seconds a whole operation may take before it counts as hung."""

LABEL = "m11-11t"
FIFO = "<fifo>"
OVER_THE_RECORD_CAP = (64 << 20) + 1
"""Bytes in the padded journal copy: past the 64 MiB guard's journal loader
accepts for one record (`not a regular file of a sane size`)."""


# ─── helpers ─────────────────────────────────────────────────────────────────


def tree(root: Path) -> dict[str, bytes | str]:
    """Every entry under `root` by `lstat`: file bytes, `<dir>`, `<fifo>`, or
    `-> target` for a link. Never opens a FIFO, never follows a link."""
    out: dict[str, bytes | str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in (*dirnames, *filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                out[rel] = "-> " + str(path.readlink())
            elif stat.S_ISDIR(mode):
                out[rel] = "<dir>"
            elif stat.S_ISFIFO(mode):
                out[rel] = FIFO
            elif stat.S_ISREG(mode):
                out[rel] = path.read_bytes()
            else:
                out[rel] = f"<mode {mode:o}>"
    return out


def names(root: Path) -> set[str]:
    """Every entry under `root`, by relative path, without opening any."""
    return {
        (Path(d) / n).relative_to(root).as_posix()
        for d, dirs, files in os.walk(root, followlinks=False)
        for n in (*dirs, *files)
    }


def error_text(exc: BaseException) -> str:
    """The message of `exc`, its notes, and those of its causes and contexts."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current))
        parts.extend(getattr(current, "__notes__", ()))
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def _has_blocked_reader(fifo: Path) -> bool:
    """Open the write end of `fifo` without blocking. It opens only while a
    reader has the FIFO open; opening it releases a reader blocked in `open`,
    and closing it gives that reader end-of-file."""
    try:
        if not stat.S_ISFIFO(fifo.lstat().st_mode):
            return False
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ENXIO, errno.ENOENT):
            return False  # ENXIO: nobody has it open to read
        raise
    os.close(fd)
    return True


def run_bounded(action: Callable[[], Any], fifos: Iterable[Path]) -> Any:
    """Run `action` in a worker thread and return what it raised (or None).
    Fails, after releasing any reader blocked on one of `fifos`, when a
    reader still holds one after `BLOCKED_AFTER` seconds, or when the action
    runs `OVERALL` seconds."""
    watched = tuple(fifos)
    outcome: list[BaseException | None] = []

    def work() -> None:
        try:
            action()
        except BaseException as exc:
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=work, name="m11-11t-operation", daemon=True)
    started = time.monotonic()
    worker.start()
    while worker.is_alive():
        worker.join(0.05)
        elapsed = time.monotonic() - started
        if not worker.is_alive() or elapsed < BLOCKED_AFTER:
            continue
        blocked = [f.name for f in watched if _has_blocked_reader(f)]
        if blocked:
            # The probe itself releases the reader, so the worker may finish at
            # once; a reader that held the FIFO this long was blocked all the same.
            worker.join(OVERALL)
            pytest.fail(
                f"the operation still had the FIFO {blocked[0]} open to read after "
                f"{BLOCKED_AFTER:.0f}s: its read blocks waiting for a writer "
                "(released by the grader)"
            )
        if elapsed > OVERALL:
            for fifo in watched:
                _has_blocked_reader(fifo)
            worker.join(OVERALL)
            pytest.fail(f"the operation did not finish within {OVERALL:.0f}s")
    assert outcome, "the worker ended without an outcome"
    return outcome[0]


def _read_only(flags: int) -> bool:
    return flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_CREAT


class Swap:
    """The hook: at the `at_read`-th read-only open (`os.open` or `io.open`) of
    the armed target, replace the target, then make the real call.

    `kind` is `fifo`, `regular` (a new file with the same bytes) or `symlink`
    (a link to `link_to`)."""

    def __init__(
        self,
        target: Path,
        *,
        kind: str,
        at_read: int = 1,
        watch: Path | None = None,
        link_to: Path | None = None,
    ) -> None:
        self.target = target
        self.kind = kind
        self.at_read = at_read
        self.watch = watch
        self.link_to = link_to
        self.identity: tuple[int, int] | None = None
        self.before: dict[str, bytes | str] | None = None
        self.placed: bytes | str | None = None
        self.reads = 0
        self.swapped = False
        self._real_os_open = os.open

    def arm(self) -> None:
        if self.watch is not None:
            self.before = tree(self.watch)
        st = self.target.lstat()
        self.identity = (st.st_dev, st.st_ino)

    def _is_read_of_target(self, path: Any, dir_fd: int | None = None) -> bool:
        if self.identity is None or self.swapped or isinstance(path, int):
            return False
        try:
            st = os.lstat(path, dir_fd=dir_fd)
        except (OSError, TypeError, ValueError):
            return False
        return (st.st_dev, st.st_ino) == self.identity

    def _replace_target(self) -> int:
        """Replace the target; return a descriptor on the replaced file, which
        the caller keeps open until the real call returns (no inode reuse)."""
        self.swapped = True
        held = self._real_os_open(self.target, os.O_RDONLY | os.O_NONBLOCK)
        chunks: list[bytes] = []
        while chunk := os.read(held, 1 << 16):
            chunks.append(chunk)
        self.target.unlink()
        if self.kind == "fifo":
            os.mkfifo(self.target)
            self.placed = FIFO
        elif self.kind == "regular":
            fd = self._real_os_open(self.target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                os.write(fd, b"".join(chunks))
            finally:
                os.close(fd)
            self.placed = b"".join(chunks)
        else:
            assert self.link_to is not None
            self.target.symlink_to(self.link_to)
            self.placed = f"-> {self.link_to}"
        return held

    def _hit(self, is_target: bool) -> bool:
        if not is_target:
            return False
        self.reads += 1
        return self.reads == self.at_read

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_os_open = self._real_os_open
        real_io_open = io.open

        def hooked_os_open(
            path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
        ) -> int:
            if _read_only(flags) and self._hit(self._is_read_of_target(path, dir_fd)):
                held = self._replace_target()
                try:
                    return real_os_open(path, flags, mode, dir_fd=dir_fd)
                finally:
                    os.close(held)
            return real_os_open(path, flags, mode, dir_fd=dir_fd)

        def hooked_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            reading = "r" in mode and "+" not in mode
            if reading and self._hit(self._is_read_of_target(file)):
                held = self._replace_target()
                try:
                    return real_io_open(file, mode, *args, **kwargs)
                finally:
                    os.close(held)
            return real_io_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(os, "open", hooked_os_open)
        monkeypatch.setattr(io, "open", hooked_io_open)


def commit_a_change(
    guard: Any, flavor: Flavor, store: SnapshotStore, rel: str = CONFIG, data: bytes = NEW_CONFIG
) -> str:
    """A committed transaction that wrote `data` at `rel`; its pre-write
    snapshot (returned) holds the bytes from before, so a restore or undo has
    a snapshot to use."""
    with guard.transaction(flavor, label="change", store=store.path) as tx:
        tx.write(rel, data)
    snapshot_id: str = record_for(guard, store, "change").snapshot_id
    return snapshot_id


class BodyFailedError(Exception):
    """Raised by a transaction body to make guard roll back."""


def assert_guard_refusal(guard: Any, raised: Any, naming: str) -> None:
    assert isinstance(raised, guard.GuardError), f"refused with a GuardError, not {raised!r}"
    assert not isinstance(raised, guard.ClientRunningError | guard.GuardBusyError), repr(raised)
    assert naming in error_text(raised), f"the refusal names {naming}: {error_text(raised)!r}"


# ─── install reads: every read guard makes of a target ───────────────────────


@dataclass(frozen=True)
class Case:
    at_read: int
    """Which read-only open of the target is swapped (item 3: the first touch
    reads it, the re-read before a replace or unlink is the second)."""
    needs_change: bool
    """A committed change first, so a restore or undo has a snapshot to use."""
    kind: str
    """`tx` (a transaction body), `dry-run`, `rollback` or `undo`."""
    op: Callable[[Any, str], None]
    """What the body does, given the transaction and the snapshot id."""
    empty: bool = False
    """CONFIG holds no bytes before the grader starts: a FIFO read as `b""`
    would hash like it."""


def _noop(tx: Any, snapshot_id: str) -> None:
    pass


CASES: dict[str, Case] = {
    "restore-named-path": Case(1, True, "tx", lambda tx, snap: tx.restore(snap, paths=[CONFIG])),
    "restore-whole-snapshot": Case(1, True, "tx", lambda tx, snap: tx.restore(snap)),
    "restore-dry-run": Case(1, True, "dry-run", lambda tx, snap: tx.restore(snap, paths=[CONFIG])),
    "write-first-touch-against-the-pre-write-snapshot": Case(
        1, False, "tx", lambda tx, snap: tx.write(CONFIG, NEWER_CONFIG)
    ),
    "delete-first-touch-against-the-pre-write-snapshot": Case(
        1, False, "tx", lambda tx, snap: tx.delete(CONFIG)
    ),
    "write-re-read-before-the-replace": Case(
        2, False, "tx", lambda tx, snap: tx.write(CONFIG, NEWER_CONFIG)
    ),
    "delete-re-read-before-the-unlink": Case(2, False, "tx", lambda tx, snap: tx.delete(CONFIG)),
    "rollback-read": Case(1, False, "rollback", lambda tx, snap: tx.write(CONFIG, NEWER_CONFIG)),
    "undo-read-after-its-pre-write-snapshot": Case(1, True, "undo", _noop),
    # Security finding 4 on PR #99: a FIFO reads as b"", which hashes like an
    # empty file, so only a type check refuses it in these three.
    "restore-named-path-empty-target": Case(
        1, True, "tx", lambda tx, snap: tx.restore(snap, paths=[CONFIG]), empty=True
    ),
    "restore-dry-run-empty-target": Case(
        1, True, "dry-run", lambda tx, snap: tx.restore(snap, paths=[CONFIG]), empty=True
    ),
    "rollback-read-empty-target": Case(
        1, False, "rollback", lambda tx, snap: tx.write(CONFIG, NEWER_CONFIG), empty=True
    ),
}


def _run_case(
    case: Case,
    swap: Swap,
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, BodyFailedError | None]:
    """Set up `case`, install the hook, run the operation bounded; returns what
    it raised and, for a rollback, the body's own exception."""
    snapshot_id = ""
    if case.empty:
        (flavor.path / CONFIG).write_bytes(b"")  # constructed: an empty file
        if case.needs_change:  # the snapshot holds CONFIG empty, like the disk
            snapshot_id = commit_a_change(guard, flavor, store, BINDINGS, b"bind S MOVEBACK\n")
    elif case.needs_change:
        snapshot_id = commit_a_change(guard, flavor, store)
    swap.install(monkeypatch)
    body_error: list[BodyFailedError] = []

    if case.kind == "undo":
        real_create = SnapshotStore.create

        def create_then_arm(self: SnapshotStore, *args: Any, **kwargs: Any) -> Any:
            manifest = real_create(self, *args, **kwargs)
            swap.arm()  # undo's own pre-write snapshot exists; its reads follow
            return manifest

        monkeypatch.setattr(SnapshotStore, "create", create_then_arm)

        def action() -> None:
            guard.undo(store=store.path)

    else:

        def action() -> None:
            dry_run = case.kind == "dry-run"
            with guard.transaction(flavor, label=LABEL, store=store.path, dry_run=dry_run) as tx:
                if case.kind == "rollback":
                    case.op(tx, snapshot_id)  # lands
                    swap.arm()
                    failure = BodyFailedError("constructed: the body fails after a write landed")
                    body_error.append(failure)
                    raise failure
                swap.arm()
                case.op(tx, snapshot_id)

    raised = run_bounded(action, [swap.target])
    return raised, (body_error[0] if body_error else None)


def _assert_refused_and_untouched(
    case: Case,
    swap: Swap,
    raised: Any,
    body_error: BodyFailedError | None,
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
) -> None:
    assert swap.swapped, (
        f"guard never opened {CONFIG} to read it after the grader armed the hook "
        f"({swap.reads} matching opens, wanted read {swap.at_read})"
    )
    if case.kind == "rollback":
        assert raised is body_error, f"the body's exception leaves the with, not {raised!r}"
        assert CONFIG in error_text(raised), "the error at exit names the path left as it is"
        record = record_for(guard, store, LABEL)
        assert record.state == "rollback_incomplete", record.state
        assert record.rolled_back is False
    else:
        assert_guard_refusal(guard, raised, CONFIG)
        if case.kind == "tx":
            assert record_for(guard, store, LABEL).state != "committed"
    assert swap.before is not None and swap.placed is not None
    expected = dict(swap.before)
    expected[CONFIG] = swap.placed
    assert tree(flavor.path) == expected, "nothing is written: no replace, no temp file left"


def _case_params(cases: Iterable[str]) -> list[Any]:
    return [pytest.param(c, id=f"constructed-{c}") for c in cases]


@pytest.mark.parametrize("case_id", _case_params(CASES))
def test_constructed_fifo_swapped_in_between_the_walk_and_the_open_is_refused_without_blocking(
    case_id: str,
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ticket's second bullet, at every read guard makes of a target during
    a restore, a dry run, a write or delete compared with the pre-write
    snapshot, the re-read before a replace or unlink, a rollback and an undo:
    guard opens the FIFO without waiting for a writer, sees by `fstat` that it
    is not the regular file its walk found, and refuses. Nothing is written."""
    case = CASES[case_id]
    swap = Swap(flavor.path / CONFIG, kind="fifo", at_read=case.at_read, watch=flavor.path)
    raised, body_error = _run_case(case, swap, guard, flavor, store, monkeypatch)
    _assert_refused_and_untouched(case, swap, raised, body_error, guard, flavor, store)


@pytest.mark.parametrize("case_id", _case_params(CASES))
def test_constructed_control_same_bytes_in_a_new_file_at_the_same_open_is_left_alone(
    case_id: str,
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control, no marker: with the same hook at the same read, a new
    regular file with the same bytes (so the hash cannot tell it apart) is
    already refused today by guard's descriptor check (`fstat` identity
    against the walk) or its `lstat` identity check before the change, and
    left alone within the bound. So the hook reaches guard's read path, and
    the marked graders above fail for the blocking open only."""
    case = CASES[case_id]
    swap = Swap(flavor.path / CONFIG, kind="regular", at_read=case.at_read, watch=flavor.path)
    raised, body_error = _run_case(case, swap, guard, flavor, store, monkeypatch)
    _assert_refused_and_untouched(case, swap, raised, body_error, guard, flavor, store)


PLANTED = (
    "restore-named-path",
    "restore-whole-snapshot",
    "restore-dry-run",
    "write-first-touch-against-the-pre-write-snapshot",
    "delete-first-touch-against-the-pre-write-snapshot",
    "undo-read-after-its-pre-write-snapshot",
)


@pytest.mark.parametrize("case_id", _case_params(PLANTED))
def test_constructed_fifo_in_place_before_the_operation_is_refused_as_not_a_regular_file(
    case_id: str, guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """The ticket's first bullet for a FIFO that is already there when the
    operation starts: guard's walk refuses it by `lstat` as not a regular
    file, within the bound, and writes nothing. Passes today (no marker); a
    regression pin for M11-11."""
    case = CASES[case_id]
    snapshot_id = commit_a_change(guard, flavor, store) if case.needs_change else ""
    target = flavor.path / CONFIG
    target.unlink()
    os.mkfifo(target)
    before = tree(flavor.path)

    def action() -> None:
        if case.kind == "undo":
            guard.undo(store=store.path)
            return
        dry_run = case.kind == "dry-run"
        with guard.transaction(flavor, label=LABEL, store=store.path, dry_run=dry_run) as tx:
            case.op(tx, snapshot_id)

    raised = run_bounded(action, [target])
    assert_guard_refusal(guard, raised, CONFIG)
    assert "not a regular file" in error_text(raised), error_text(raised)
    assert tree(flavor.path) == before, "nothing is written"


# ─── capture reads: SnapshotStore.create ─────────────────────────────────────


@pytest.mark.parametrize(
    "purpose",
    [
        pytest.param(None, id="constructed-snap-create"),
        pytest.param("profile", id="constructed-profile-save"),
    ],
)
def test_constructed_fifo_swapped_in_during_a_capture_is_not_captured_and_never_blocks(
    purpose: str | None, flavor: Flavor, store: SnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 1, item 3(a): `SnapshotStore._capture` checks a path by
    `lstat` and then opens it. A FIFO swapped in between (the capture a
    `snap create` or a `profile save` makes; guard's pre-write snapshot is the
    review probe's) ends the capture within the bound: the path is not
    recorded as a file (skipped, or refused with a `SnapshotError`), the other
    files still are, and nothing in the install changes."""
    swap = Swap(flavor.path / CONFIG, kind="fifo", watch=flavor.path)
    swap.install(monkeypatch)
    swap.arm()
    made: list[Any] = []

    def action() -> None:
        made.append(store.create(flavor.path, ["WTF"], label=LABEL, purpose=purpose))

    raised = run_bounded(action, [swap.target])
    assert swap.swapped, "the capture never opened the file the hook armed"
    assert raised is None or isinstance(raised, SnapshotError), repr(raised)
    if raised is None:
        entries = {e.path: e for e in made[0].entries}
        captured = [p for p in entries if p == CONFIG or p.endswith("/" + CONFIG)]
        assert not [p for p in captured if entries[p].kind == "file"], (
            f"a FIFO was captured as a file: {[entries[p] for p in captured]!r}"
        )
        assert any(p.endswith(BINDINGS) for p in entries), "the other files are still captured"
    assert swap.before is not None
    assert tree(flavor.path) == {**swap.before, CONFIG: FIFO}, "nothing in the install changes"


# ─── store reads: SnapshotStore.read_object from restore and undo ────────────


def _restore_named(guard: Any, flavor: Flavor, store: SnapshotStore, snap: str) -> None:
    with guard.transaction(flavor, label=LABEL, store=store.path) as tx:
        tx.restore(snap, paths=[CONFIG])


def _undo(guard: Any, flavor: Flavor, store: SnapshotStore, snap: str) -> None:
    guard.undo(store=store.path)


STORE_OPS = {"restore": _restore_named, "undo": _undo}


def _plant_object(store: SnapshotStore, digest: str, kind: str, elsewhere: Path) -> Path:
    """Replace the object for `digest` with a FIFO, or move it to `elsewhere`
    and leave a symbolic link to it in its place."""
    obj = store.object_path(digest)
    assert obj.is_file(), f"the store holds no object for {digest}"
    if kind == "fifo":
        obj.unlink()
        os.mkfifo(obj)
    else:
        elsewhere.parent.mkdir(parents=True, exist_ok=True)
        obj.rename(elsewhere)
        obj.symlink_to(elsewhere)
    return obj


@pytest.mark.parametrize("op", [pytest.param(k, id=f"constructed-{k}") for k in STORE_OPS])
def test_constructed_object_that_is_a_fifo_is_refused_without_blocking(
    op: str, guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, tmp_path: Path
) -> None:
    """Fix round 1, item 3(b): the object a restore or undo needs sits at a
    predictable path. Planted as a FIFO (no race needed), reading it is
    refused within the bound, with a `GuardError`, and nothing is written."""
    snap = commit_a_change(guard, flavor, store)
    obj = _plant_object(store, sha(ALLOWLISTED_FILES[CONFIG]), "fifo", tmp_path / "unused")
    before = tree(flavor.path)
    raised = run_bounded(lambda: STORE_OPS[op](guard, flavor, store, snap), [obj])
    assert isinstance(raised, guard.GuardError), f"refused with a GuardError, not {raised!r}"
    assert tree(flavor.path) == before, "nothing is written"
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    if op == "restore":
        assert record_for(guard, store, LABEL).state != "committed"


@pytest.mark.parametrize("op", [pytest.param(k, id=f"constructed-{k}") for k in STORE_OPS])
def test_constructed_object_that_is_a_symlink_is_not_followed(
    op: str, guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, tmp_path: Path
) -> None:
    """Fix round 1, item 3(b): an object replaced by a symbolic link to the
    sound object moved outside the store is not followed: the restore or undo
    is refused within the bound with a `GuardError`, and nothing is written,
    although the bytes behind the link would hash correctly."""
    snap = commit_a_change(guard, flavor, store)
    elsewhere = tmp_path / "elsewhere" / "object"
    obj = _plant_object(store, sha(ALLOWLISTED_FILES[CONFIG]), "symlink", elsewhere)
    before = tree(flavor.path)
    raised = run_bounded(lambda: STORE_OPS[op](guard, flavor, store, snap), [obj])
    assert isinstance(raised, guard.GuardError), f"refused with a GuardError, not {raised!r}"
    assert tree(flavor.path) == before, "nothing is written: the link was not followed"
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG


# ─── journal reads: guard._load_journal ──────────────────────────────────────


def _latest_record(store: SnapshotStore) -> Path:
    """The newest journal record: a JSON file under the store outside
    `objects/` and `manifests/` (the M10-11T convention), newest by name."""
    found = [
        p
        for p in store.path.rglob("*.json")
        if p.is_file()
        and not ({"objects", "manifests", "tmp"} & set(p.relative_to(store.path).parts))
    ]
    assert found, f"no journal record under {store.path}"
    return max(found, key=lambda p: p.name)


def _journal_history(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    guard.history(store=store.path)


def _journal_enter(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    with guard.transaction(flavor, label=LABEL, store=store.path) as tx:
        tx.write(CONFIG, NEWER_CONFIG)


def _journal_undo(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    guard.undo(store=store.path)


JOURNAL_OPS = {
    "history": _journal_history,
    "transaction-enter": _journal_enter,
    "undo": _journal_undo,
}

# Not graded: a link swapped in for the record `undo()` reads. `undo()` reads
# the journal once unlocked and again under the store lock, and the second
# read's `lstat` already refuses the link today.
JOURNAL_SWAPS = [
    *(pytest.param("fifo", op, id=f"constructed-fifo-{op}") for op in JOURNAL_OPS),
    *(
        pytest.param("symlink-to-a-sound-copy", op, id=f"constructed-symlink-to-a-sound-copy-{op}")
        for op in ("history", "transaction-enter")
    ),
    pytest.param(
        "symlink-to-a-copy-past-the-size-cap",
        "history",
        id="constructed-symlink-to-a-copy-past-the-size-cap-history",
    ),
]


@pytest.mark.parametrize(("kind", "op"), JOURNAL_SWAPS)
def test_constructed_journal_record_swapped_before_its_open_is_refused(
    kind: str,
    op: str,
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix round 1, item 3(c): guard checks a journal record by `lstat` (a
    regular file of a sane size) and then reads it. A FIFO swapped in between
    is refused within the bound; a symbolic link is not followed, so neither a
    sound copy of the record nor a copy past the size check gets in. The
    refusal is a `GuardError`, and nothing is written in the install or the
    store."""
    commit_a_change(guard, flavor, store)
    record = _latest_record(store)
    link_to = None
    if kind != "fifo":
        link_to = tmp_path / "elsewhere" / record.name
        link_to.parent.mkdir(parents=True)
        body = record.read_bytes()
        json.loads(body)  # a sound record; trailing spaces keep it sound JSON
        if kind == "symlink-to-a-copy-past-the-size-cap":
            body += b" " * (OVER_THE_RECORD_CAP - len(body))
        link_to.write_bytes(body)
    swap = Swap(record, kind="fifo" if kind == "fifo" else "symlink", link_to=link_to)
    swap.install(monkeypatch)
    install_before = tree(flavor.path)
    store_before = names(store.path)
    swap.arm()
    raised = run_bounded(lambda: JOURNAL_OPS[op](guard, flavor, store), [record])
    assert swap.swapped, "guard never opened the journal record the hook armed"
    assert isinstance(raised, guard.GuardError), f"refused with a GuardError, not {raised!r}"
    assert not isinstance(raised, guard.ClientRunningError | guard.GuardBusyError), repr(raised)
    assert tree(flavor.path) == install_before, "nothing is written in the install"
    assert names(store.path) == store_before, "no snapshot or record is added to the store"
