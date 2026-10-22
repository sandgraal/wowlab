"""`luadata.read` and the `wtfconfig` readers open only a regular file
(M16-26T graders for M16-26; qa:F6, security:F3 in
`docs/handoffs/outside-evaluation-2026-10.md`, EV-24).

Today `luadata.read` (`longpath.open_read`) and `wtfconfig.read_config`,
`read_bindings` and `read_macros` (`longpath.read_bytes`) use a plain blocking
open. A FIFO at a SavedVariables or WTF path blocks the read until a writer
appears (`sv dump` on one never returns; a UI Preview burns its whole
timeout), and a final symbolic link is followed, so a link that points out of
the install is read while `tree` reports the same entry as "link to ...
(outside the install), not followed". M16-26 moves these reads onto the M11-11
helper `snapshot.open_regular_file` (`O_NONBLOCK | O_NOFOLLOW | O_NOCTTY`,
then an `fstat` that must show a regular file), so:

- a FIFO is refused at once with `UnsafeReadError` ("not a regular file"),
  the descriptor closed, and the FIFO left as it was;
- a final link is not followed (`ELOOP` from the open, or `UnsafeReadError`
  where the platform has no `O_NOFOLLOW`), whatever it points at;
- a regular file reads as before: the document's bytes are the file's bytes
  (L4).

The regular-file cases read real captures (`fixtures/macos/forever/`, rows in
`fixtures/README.md`, L8) copied into a synthetic install under `tmp_path`;
they pass today and must keep passing, so they carry no marker. The FIFO and
link cases are constructed hostile inputs (ids say `constructed`). A FIFO
case never hangs the suite: the read runs in a daemon thread, and after
`BOUND` seconds the grader opens the FIFO for writing so a blocked reader gets
end-of-file, then fails the test. FIFO cases are skipped where `os.mkfifo` is
missing (Windows); link cases are skipped on Windows, where making a link
needs a privilege. Nothing here touches a real install.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

import pytest

from wowlab_core import luadata, wtfconfig
from wowlab_core.snapshot import UnsafeReadError

pytestmark = pytest.mark.parser

XFAIL = pytest.mark.xfail(strict=True, reason="M16-26 not implemented")

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos" / "forever"
"""The `_classic_beta_` flavor folder of the macOS capture (README rows)."""

BOUND = 3.0
"""Seconds a read may take before it counts as blocked. A non-blocking open
of a FIFO returns in microseconds."""

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo (POSIX)")
needs_symlink = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)


class Reader(NamedTuple):
    name: str
    rel: PurePosixPath
    """A real fixture, relative to the flavor folder; the same relative path
    is used for the constructed FIFO and link."""
    read: Callable[[Path], Any]
    to_bytes: Callable[[Any], bytes]


READERS = [
    Reader(
        "luadata.read",
        PurePosixPath("WTF/Account/90000001#6/SavedVariables/Blizzard_AuctionHouseUI.lua"),
        luadata.read,
        lambda doc: luadata.serialize(doc),
    ),
    Reader(
        "wtfconfig.read_config",
        PurePosixPath("WTF/Config.wtf"),
        wtfconfig.read_config,
        lambda doc: doc.to_bytes(),
    ),
    Reader(
        "wtfconfig.read_bindings",
        PurePosixPath("WTF/Account/90000001#6/bindings-cache.wtf"),
        wtfconfig.read_bindings,
        lambda doc: doc.to_bytes(),
    ),
    Reader(
        "wtfconfig.read_macros",
        PurePosixPath("WTF/Account/90000001#6/1/Labcharb-Labrealmd/macros-cache.txt"),
        wtfconfig.read_macros,
        lambda doc: doc.to_bytes(),
    ),
]


def _ids(prefix: str) -> list[str]:
    return [f"{prefix}-{r.name}" for r in READERS]


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A synthetic install: one flavor folder under `tmp_path/Install`."""
    flavor = tmp_path / "Install" / "_flavor_"
    flavor.mkdir(parents=True)
    return flavor


def _place(flavor: Path, rel: PurePosixPath) -> Path:
    path = flavor.joinpath(*rel.parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class OpenSpy:
    """Records every `os.open` (path, fd) that succeeds."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, int]] = []
        real = os.open

        def spy(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            fd = real(path, flags, mode, dir_fd=dir_fd)
            self.calls.append((os.fspath(path), fd))
            return fd

        monkeypatch.setattr(os, "open", spy)

    def fds_for(self, path: Path) -> list[int]:
        return [fd for p, fd in self.calls if p == os.fspath(path)]


def _release(fifo: Path) -> None:
    """Open `fifo` for writing and close it, so a reader blocked on it gets
    end-of-file. Fails quietly when no reader is there yet."""
    with contextlib.suppress(OSError):
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))


def bounded(action: Callable[[], Any], fifo: Path) -> BaseException | None:
    """Run `action` in a daemon thread and return what it raised (None when it
    returned). If it has not finished within `BOUND` seconds, release the
    reader blocked on `fifo` and fail: the read blocked."""
    outcome: list[BaseException | None] = []

    def work() -> None:
        try:
            action()
        except BaseException as exc:
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=work, name="m16-26t-read", daemon=True)
    worker.start()
    worker.join(BOUND)
    if worker.is_alive():
        deadline = time.monotonic() + BOUND
        while worker.is_alive() and time.monotonic() < deadline:
            _release(fifo)
            worker.join(0.05)
        pytest.fail(
            f"the read of the FIFO did not return within {BOUND:.0f}s: "
            "it blocked waiting for a writer (released by the grader)"
        )
    assert outcome, "the worker ended without an outcome"
    return outcome[0]


# ─── a regular file reads as before (real fixtures; no marker) ──────────────


@pytest.mark.parametrize("reader", READERS, ids=_ids("real"))
def test_a_regular_file_reads_as_before(reader: Reader, install: Path) -> None:
    source = CAPTURE.joinpath(*reader.rel.parts)
    data = source.read_bytes()
    assert data, f"{reader.rel} is an empty fixture; pick one with content"
    path = _place(install, reader.rel)
    shutil.copyfile(source, path)
    before = path.stat()

    document = reader.read(path)

    assert reader.to_bytes(document) == data, "the document keeps the file's bytes (L4)"
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns), (
        "the read changed nothing (L1)"
    )


def test_real_luadata_read_keeps_taking_a_str_path(install: Path) -> None:
    """`luadata.read` is typed `str | Path`; the move keeps a `str` working."""
    reader = READERS[0]
    source = CAPTURE.joinpath(*reader.rel.parts)
    path = _place(install, reader.rel)
    shutil.copyfile(source, path)
    assert luadata.serialize(luadata.read(str(path))) == source.read_bytes()


# ─── a FIFO is refused without blocking (constructed) ───────────────────────


@needs_fifo
@XFAIL
@pytest.mark.parametrize("reader", READERS, ids=_ids("constructed-fifo"))
def test_a_fifo_is_refused_without_blocking(
    reader: Reader, install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = _place(install, reader.rel)
    os.mkfifo(fifo)
    spy = OpenSpy(monkeypatch)

    raised = bounded(lambda: reader.read(fifo), fifo)

    assert isinstance(raised, UnsafeReadError), (
        f"{reader.name} on a FIFO: expected UnsafeReadError, got {raised!r}"
    )
    assert "not a regular file" in str(raised), str(raised)
    fds = spy.fds_for(fifo)
    assert fds, "the FIFO was opened (non-blocking) and then refused by its fstat"
    for fd in fds:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF, f"descriptor {fd} left open"
    assert stat.S_ISFIFO(fifo.lstat().st_mode), "the FIFO is left as it was"


# ─── a link out of the install is not followed (constructed) ────────────────


@needs_symlink
@XFAIL
@pytest.mark.parametrize("reader", READERS, ids=_ids("constructed-link-out"))
def test_a_link_out_of_the_install_is_refused(
    reader: Reader, install: Path, tmp_path: Path
) -> None:
    """The link's target is a sound copy of the same real fixture, outside the
    install, so following it would parse; `tree` reports this entry as a link
    outside the install, not followed, and the reader must agree."""
    outside = tmp_path / "Elsewhere" / reader.rel.name
    outside.parent.mkdir()
    shutil.copyfile(CAPTURE.joinpath(*reader.rel.parts), outside)
    install_root = install.parent
    assert not outside.resolve().is_relative_to(install_root.resolve())
    link = _place(install, reader.rel)
    link.symlink_to(outside)
    target_before = outside.read_bytes()

    with pytest.raises(OSError) as caught:
        reader.read(link)

    exc = caught.value
    assert exc.errno == errno.ELOOP or isinstance(exc, UnsafeReadError), (
        f"{reader.name} on a link: expected ELOOP or UnsafeReadError, got {exc!r}"
    )
    assert not isinstance(exc, FileNotFoundError), "the link's target exists"
    assert link.is_symlink(), "the link is left as it was"
    assert outside.read_bytes() == target_before, "the target is left as it was"
