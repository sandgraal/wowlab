# Probe from review of m11/11-guard-nonblocking-reads-tests; reproduces guard's pre-write snapshot blocking forever, under the store and install locks, on a FIFO swapped in between its lstat and its open
"""M11-11T asks that "a FIFO in place of a file that guard reads during a
restore or snapshot" be handled within a bounded time with nothing written.
The M11-11T graders swap the FIFO in only after the pre-write snapshot
exists, so they never reach the snapshot's own read. That read
(`SnapshotStore._capture`, run from `guard.transaction().__enter__` and from
`guard.undo()` while guard holds both locks, `docs/LAB_PLAN.md` §6.10 item 1)
opens with `O_RDONLY | O_NOFOLLOW` and no `O_NONBLOCK` after an `lstat`, so a
FIFO swapped in at that moment blocks the open until a writer appears. The
scratch patch in PR #99 (`O_NONBLOCK` in `guard._read` only) leaves this
blocking: checked with the patch applied.

The expectation is only what the ticket states: the operation returns (or
raises) within the bound, and nothing is written into the install. It does
not grade whether the snapshot skips the FIFO or refuses.

Constructed hostile input, POSIX only (skipped where `os.mkfifo` is missing).
A watchdog opens the FIFO's write end after `BLOCKED_AFTER` seconds, so the
probe fails fast and never hangs. The positive control swaps in a regular
file at the same open and finishes today.

Marked `xfail(strict=True)` so the graders branch stays green while the
conductor decides whether M11-11 covers `snapshot.py`; delete the marker when
the read is fixed, or delete the file if the owner rules it out of scope.
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
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore


def _load_base() -> types.ModuleType:
    path = Path(__file__).resolve().parents[1] / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_review_m11_11_base", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = _load_base()
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
store = G.store
idle = G.idle
CONFIG = G.CONFIG
NEW_CONFIG = G.NEW_CONFIG

pytestmark = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo (POSIX)")

BLOCKED_AFTER = 2.0
OVERALL = 60.0


def _tree(root: Path) -> dict[str, bytes | str]:
    out: dict[str, bytes | str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in (*dirnames, *filenames):
            path = Path(dirpath) / name
            mode = path.lstat().st_mode
            rel = path.relative_to(root).as_posix()
            if stat.S_ISDIR(mode):
                out[rel] = "<dir>"
            elif stat.S_ISFIFO(mode):
                out[rel] = "<fifo>"
            elif stat.S_ISREG(mode):
                out[rel] = path.read_bytes()
            else:
                out[rel] = f"<mode {mode:o}>"
    return out


def _unblock(fifo: Path) -> None:
    for _ in range(100):
        try:
            if not stat.S_ISFIFO(fifo.lstat().st_mode):
                return
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno != errno.ENXIO:
                return
            time.sleep(0.05)
            continue
        os.close(fd)
        return


class _SwapAtFirstRead:
    """At the first read-only `os.open` of `target` (by identity) after
    `arm()`, replace it with a FIFO or a regular file, then make the call."""

    def __init__(self, target: Path, *, fifo: bool) -> None:
        self.target = target
        self.fifo = fifo
        self.identity: tuple[int, int] | None = None
        self.swapped = False
        self.opening_since: float | None = None

    def arm(self) -> None:
        st = self.target.lstat()
        self.identity = (st.st_dev, st.st_ino)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_open = os.open

        def hooked(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            if (
                self.identity is not None
                and not self.swapped
                and flags & os.O_ACCMODE == os.O_RDONLY
                and not flags & os.O_CREAT
            ):
                try:
                    st = os.lstat(path, dir_fd=dir_fd)
                except (OSError, TypeError, ValueError):
                    st = None
                if st is not None and (st.st_dev, st.st_ino) == self.identity:
                    self.target.unlink()
                    if self.fifo:
                        os.mkfifo(self.target)
                    else:
                        self.target.write_bytes(b"constructed: swapped-in regular file\n")
                    self.swapped = True
                    self.opening_since = time.monotonic() if self.fifo else None
                    try:
                        return real_open(path, flags, mode, dir_fd=dir_fd)
                    finally:
                        self.opening_since = None
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", hooked)


def _bounded(action: Callable[[], None], swap: _SwapAtFirstRead) -> BaseException | None:
    outcome: list[BaseException | None] = []

    def work() -> None:
        try:
            action()
        except BaseException as exc:
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=work, daemon=True)
    started = time.monotonic()
    worker.start()
    while worker.is_alive():
        worker.join(0.05)
        since = swap.opening_since
        blocked = since is not None and time.monotonic() - since > BLOCKED_AFTER
        if worker.is_alive() and (blocked or time.monotonic() - started > OVERALL):
            _unblock(swap.target)
            worker.join(OVERALL)
            pytest.fail(
                f"the pre-write snapshot's open of the FIFO at {swap.target.name} blocked "
                f"for {BLOCKED_AFTER:.0f}s while guard held its locks (released by the probe)"
            )
    assert outcome
    return outcome[0]


def _run(
    op: str, fifo: bool, guard: Any, flavor: Any, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    if op == "undo":
        with guard.transaction(flavor, label="change", store=store.path) as tx:
            tx.write(CONFIG, NEW_CONFIG)
    swap = _SwapAtFirstRead(flavor.path / CONFIG, fifo=fifo)
    swap.install(monkeypatch)
    real_create = SnapshotStore.create

    def create(self: SnapshotStore, *args: Any, **kwargs: Any) -> Any:
        swap.arm()  # guard holds both locks here; the next read of CONFIG is the capture's
        return real_create(self, *args, **kwargs)

    monkeypatch.setattr(SnapshotStore, "create", create)
    before = _tree(flavor.path)

    def action() -> None:
        if op == "undo":
            guard.undo(store=store.path)
        else:
            with guard.transaction(flavor, label="m11-11-probe", store=store.path):
                pass

    _bounded(action, swap)
    assert swap.swapped, "the pre-write snapshot never opened the target"
    expected = dict(before)
    if fifo:
        expected[CONFIG] = "<fifo>"
        assert _tree(flavor.path) == expected, "nothing is written into the install"


@pytest.mark.xfail(strict=True, reason="pre-write snapshot read blocks on a FIFO (M11-11 scope)")
@pytest.mark.parametrize("op", ["constructed-transaction", "constructed-undo"])
def test_fifo_swapped_in_at_the_pre_write_snapshot_read_does_not_block(
    op: str, guard: Any, flavor: Any, store: Any, idle: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(op.removeprefix("constructed-"), True, guard, flavor, store, monkeypatch)


@pytest.mark.parametrize("op", ["constructed-transaction", "constructed-undo"])
def test_control_regular_file_swapped_in_at_the_same_read_finishes(
    op: str, guard: Any, flavor: Any, store: Any, idle: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(op.removeprefix("constructed-"), False, guard, flavor, store, monkeypatch)
