"""Graders for "guard reads never block" (M11-11T).

Written before M11-11, from the ticket text (M11-11T, a follow-up from the
M11-08 security review on PR #96) and docs/LAB_PLAN.md §6.10 with its
amendments: the gate reads a target at its first touch to compare it with
the pre-write snapshot, reads it again before each replace or unlink
(item 3), reads it during `tx.restore` and a dry run, and reads it once more
on rollback. Every one of those reads happens while guard holds the store
lock and the install lock (item 1), so a read that never returns keeps both
locks forever. The graders marked below carry one marker line that M11-11
deletes; nothing else in this file is the implementer's to change.

Everything here is constructed, and POSIX-only: FIFOs are made with
`os.mkfifo`, and every test is skipped where it is missing. The synthetic
install, the store, the redirected user data directory and the injected
process table are the M10-11T ones from `test_guard.py` (loaded, not
copied). No test needs or touches a real install.

What the graders hold `guard` to
--------------------------------
- A FIFO that takes the place of a file after guard's walk found a regular
  file there, and before guard opens it to read, is refused within a bounded
  time: the operation raises a `GuardError` (never a `ClientRunningError` or
  a `GuardBusyError`) whose text, notes or causes name the path, and nothing
  is written: the FIFO is still a FIFO, every other entry of the flavor
  folder is as it was, and no temp file is left. A transaction that saw the
  refusal does not commit. On rollback the path is left as it is and the
  record ends `rollback_incomplete` (item 3 as clarified 2026-09-22); the
  body's own exception is what leaves the `with`, carrying a note that
  names the path.
- The refusal is graded on type, path and the untouched install, not on
  wording: the existing descriptor check ("changed while it was being read")
  and a new "not a regular file" both satisfy it.

How the swap is made (the hook)
-------------------------------
`os.open` is looked up at call time. For the length of one test it is
wrapped: once armed, the Nth call that opens the armed target read-only (no
write access, no `O_CREAT`), recognised by the `(st_dev, st_ino)` of the
file the target held when the grader armed the hook, first replaces the
target with a FIFO (or, in the control, with another regular file) and then
makes the real call. That is exactly "between the walk and the open". The
hook is armed only after the pre-write snapshot exists (inside the `with`,
or, for `undo()`, when `SnapshotStore.create` returns), so the snapshot
module's own reads are never swapped: M11-11 changes `guard.py` alone.

Bounded time, never a hang
--------------------------
The operation runs in a worker thread. The hook records when it starts the
real `os.open` on the FIFO; if that call has not returned after
`BLOCKED_AFTER` seconds (a non-blocking open returns at once), or the
operation has not finished after `OVERALL` seconds, the grader opens the
FIFO's write end, which releases the blocked reader, waits for the worker,
and fails. So today each marked grader fails in about two seconds, for the
missing behaviour only, and CI never hangs.

The FIFO already in place before the operation
----------------------------------------------
The ticket's first bullet also covers a FIFO in place of a file before the
read. Guard's walk already refuses that by `lstat` ("not a regular file"),
so those graders pass today and carry no marker: a strict `xfail` on them
would turn the suite red. They stay as regression pins beside the marked
graders, as does the control that swaps in a regular file instead of a FIFO
with the same hook and shows the only thing missing today is the
non-blocking open.
"""

from __future__ import annotations

import errno
import importlib.util
import os
import stat
import sys
import threading
import time
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore


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
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
NEW_CONFIG = G.NEW_CONFIG
NEWER_CONFIG = G.NEWER_CONFIG
record_for = G.record_for

pytestmark = pytest.mark.skipif(
    not hasattr(os, "mkfifo"), reason="FIFOs are a POSIX thing (os.mkfifo is missing)"
)

BLOCKED_AFTER = 2.0
"""Seconds guard's `os.open` of the FIFO may take before it counts as blocked."""
OVERALL = 60.0
"""Seconds a whole operation may take before it counts as hung."""

LABEL = "m11-11t"
FIFO = "<fifo>"
SWAPPED_IN = b"constructed: a regular file swapped in between the walk and the open\n"


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


def unblock(fifo: Path) -> None:
    """Open the write end of `fifo` and close it again, so a reader blocked in
    `open` gets a writer and then end-of-file."""
    for _ in range(100):
        try:
            if not stat.S_ISFIFO(fifo.lstat().st_mode):
                return
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno != errno.ENXIO:  # ENXIO: no reader has it open yet
                return
            time.sleep(0.05)
            continue
        os.close(fd)
        return


class Swap:
    """The hook: at the `at_read`-th read-only `os.open` of the armed target,
    replace the target with a FIFO (`kind="fifo"`) or with another regular
    file (`kind="regular"`), then make the real call."""

    def __init__(self, target: Path, flavor_dir: Path, *, kind: str, at_read: int) -> None:
        self.target = target
        self.flavor_dir = flavor_dir
        self.kind = kind
        self.at_read = at_read
        self.placed: bytes | str = FIFO if kind == "fifo" else SWAPPED_IN
        self.identity: tuple[int, int] | None = None
        self.before: dict[str, bytes | str] | None = None
        self.reads = 0
        self.swapped = False
        self.opening_fifo_since: float | None = None

    def arm(self) -> None:
        self.before = tree(self.flavor_dir)
        st = self.target.lstat()
        self.identity = (st.st_dev, st.st_ino)

    def _is_read_of_target(self, path: Any, flags: int, dir_fd: int | None) -> bool:
        if self.identity is None or self.swapped:
            return False
        if flags & os.O_ACCMODE != os.O_RDONLY or flags & os.O_CREAT:
            return False
        try:
            st = os.lstat(path, dir_fd=dir_fd)
        except (OSError, TypeError, ValueError):
            return False
        return (st.st_dev, st.st_ino) == self.identity

    def _replace_target(self) -> None:
        self.target.unlink()
        if self.kind == "fifo":
            os.mkfifo(self.target)
        else:
            self.target.write_bytes(SWAPPED_IN)
        self.swapped = True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_open = os.open

        def hooked_open(
            path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
        ) -> int:
            if self._is_read_of_target(path, flags, dir_fd):
                self.reads += 1
                if self.reads == self.at_read:
                    self._replace_target()
                    self.opening_fifo_since = time.monotonic() if self.kind == "fifo" else None
                    try:
                        return real_open(path, flags, mode, dir_fd=dir_fd)
                    finally:
                        self.opening_fifo_since = None
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", hooked_open)


def run_bounded(action: Callable[[], None], fifo: Path, swap: Swap | None = None) -> Any:
    """Run `action` in a worker thread and return what it raised (or None).
    Fails, after releasing any reader blocked on `fifo`, if guard's open of
    the FIFO blocks for `BLOCKED_AFTER` seconds or the whole action takes
    `OVERALL` seconds."""
    outcome: list[BaseException | None] = []

    def work() -> None:
        try:
            action()
        except BaseException as exc:
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=work, name="m11-11t-guard-operation", daemon=True)
    started = time.monotonic()
    worker.start()
    while worker.is_alive():
        worker.join(0.05)
        since = None if swap is None else swap.opening_fifo_since
        blocked = since is not None and time.monotonic() - since > BLOCKED_AFTER
        hung = time.monotonic() - started > OVERALL
        if worker.is_alive() and (blocked or hung):
            unblock(fifo)
            worker.join(OVERALL)
            if blocked:
                pytest.fail(
                    f"guard's open of the FIFO at {fifo.name} did not return within "
                    f"{BLOCKED_AFTER:.0f}s: it blocks waiting for a writer while guard holds "
                    "the store and install locks (released by the grader)"
                )
            pytest.fail(
                f"the guard operation did not finish within {OVERALL:.0f}s "
                "(any FIFO reader was released by the grader)"
            )
    assert outcome, "the worker ended without an outcome"
    return outcome[0]


def commit_a_change(guard: Any, flavor: Flavor, store: SnapshotStore) -> str:
    """A committed transaction that changed CONFIG; its pre-write snapshot
    (returned) holds the original bytes, so a restore or undo has work."""
    with guard.transaction(flavor, label="change", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    snapshot_id: str = record_for(guard, store, "change").snapshot_id
    return snapshot_id


class BodyFailedError(Exception):
    """Raised by a transaction body to make guard roll back."""


# ─── the cases: every read guard makes of a target, with a swap before it ────


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
    snapshot_id = commit_a_change(guard, flavor, store) if case.needs_change else ""
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

    raised = run_bounded(action, swap.target, swap)
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
        assert isinstance(raised, guard.GuardError), f"refused with a GuardError, not {raised!r}"
        assert not isinstance(raised, guard.ClientRunningError | guard.GuardBusyError), repr(raised)
        assert CONFIG in error_text(raised), f"the refusal names the path: {error_text(raised)!r}"
        if case.kind == "tx":
            assert record_for(guard, store, LABEL).state != "committed"
    assert swap.before is not None
    expected = dict(swap.before)
    expected[CONFIG] = swap.placed
    assert tree(flavor.path) == expected, "nothing is written: no replace, no temp file left"


# ─── graders: a FIFO swapped in between the walk and the open ────────────────


@pytest.mark.xfail(strict=True, reason="M11-11 not implemented")
@pytest.mark.parametrize("case_id", [pytest.param(c, id=f"constructed-{c}") for c in CASES])
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
    swap = Swap(flavor.path / CONFIG, flavor.path, kind="fifo", at_read=case.at_read)
    raised, body_error = _run_case(case, swap, guard, flavor, store, monkeypatch)
    _assert_refused_and_untouched(case, swap, raised, body_error, guard, flavor, store)


# ─── control: the same hook swapping in a regular file (passes today) ────────


@pytest.mark.parametrize("case_id", [pytest.param(c, id=f"constructed-{c}") for c in CASES])
def test_constructed_control_regular_file_swapped_in_at_the_same_open_is_left_alone(
    case_id: str,
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control, no marker: with the same hook at the same read, a
    regular file swapped in is already seen by guard's descriptor check today
    and left alone, within the bound. So the hook reaches guard's read path,
    and the marked graders above fail for the blocking open only."""
    case = CASES[case_id]
    swap = Swap(flavor.path / CONFIG, flavor.path, kind="regular", at_read=case.at_read)
    raised, body_error = _run_case(case, swap, guard, flavor, store, monkeypatch)
    _assert_refused_and_untouched(case, swap, raised, body_error, guard, flavor, store)


# ─── pins: a FIFO already in place before the operation (passes today) ───────


PLANTED: dict[str, Case] = {
    k: CASES[k]
    for k in (
        "restore-named-path",
        "restore-whole-snapshot",
        "restore-dry-run",
        "write-first-touch-against-the-pre-write-snapshot",
        "delete-first-touch-against-the-pre-write-snapshot",
        "undo-read-after-its-pre-write-snapshot",
    )
}


@pytest.mark.parametrize("case_id", [pytest.param(c, id=f"constructed-{c}") for c in PLANTED])
def test_constructed_fifo_in_place_before_the_operation_is_refused_as_not_a_regular_file(
    case_id: str, guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """The ticket's first bullet for a FIFO that is already there when the
    operation starts: guard's walk refuses it by `lstat` as not a regular
    file, within the bound, and writes nothing. Passes today (no marker); a
    regression pin for M11-11."""
    case = PLANTED[case_id]
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

    raised = run_bounded(action, target)
    assert isinstance(raised, guard.GuardError), f"refused with a GuardError, not {raised!r}"
    text = error_text(raised)
    assert CONFIG in text, f"the refusal names the path: {text!r}"
    assert "not a regular file" in text, f"refused as not a regular file: {text!r}"
    assert tree(flavor.path) == before, "nothing is written"
