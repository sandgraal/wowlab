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
- a final-component link out of the install is refused (`ELOOP` from the
  open, or `UnsafeReadError` where the platform has no `O_NOFOLLOW`; that
  fallback is exercised on POSIX by setting `snapshot._O_NOFOLLOW` to 0);
- a regular file reads as before: the document's bytes are the file's bytes
  (L4), through exactly one guarded open whose descriptor is closed when the
  read returns or refuses;
- `sv dump` on a FIFO or a final-component link exits non-zero with a
  one-line message, not a traceback and not a hang.

Not covered: a link in a *parent* directory of the path. `O_NOFOLLOW` checks
only the last component, and these readers take no install root to open from
component by component, so that is a follow-up ticket (owner's conductor
ruling on M16-26T review round 1), not M16-26.

The byte-identity cases read real captures (`fixtures/macos/forever/`, rows
in `fixtures/README.md`, L8) copied into a synthetic install under
`tmp_path`; they pass today and must keep passing, so they carry no marker.
Everything else is constructed (ids say `constructed`) or grades how the real
captures are opened. A FIFO case never hangs the suite: the read runs in a
daemon thread, and after `BOUND` seconds the grader opens the FIFO for
writing so a blocked reader gets end-of-file, then fails the test. FIFO cases
are skipped where `os.mkfifo` is missing (Windows); link cases are skipped on
Windows, where making a link needs a privilege. Nothing here touches a real
install.
"""

from __future__ import annotations

import builtins
import contextlib
import errno
import io
import os
import pathlib
import shutil
import stat
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

import pytest
from typer.testing import CliRunner

from wowlab_core import cli, longpath, luadata, snapshot, wtfconfig
from wowlab_core.snapshot import UnsafeReadError

pytestmark = pytest.mark.parser

XFAIL = pytest.mark.xfail(strict=True, reason="M16-26 not implemented")

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos" / "forever"
"""The `_classic_beta_` flavor folder of the macOS capture (README rows)."""

BOUND = 3.0
"""Seconds a read may take before it counts as blocked. A non-blocking open
of a FIFO returns in microseconds."""

O_NONBLOCK: int = getattr(os, "O_NONBLOCK", 0)
O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)

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
        lambda p: luadata.read(p),
        lambda doc: luadata.serialize(doc),
    ),
    Reader(
        "wtfconfig.read_config",
        PurePosixPath("WTF/Config.wtf"),
        lambda p: wtfconfig.read_config(p),
        lambda doc: doc.to_bytes(),
    ),
    Reader(
        "wtfconfig.read_bindings",
        PurePosixPath("WTF/Account/90000001#6/bindings-cache.wtf"),
        lambda p: wtfconfig.read_bindings(p),
        lambda doc: doc.to_bytes(),
    ),
    Reader(
        "wtfconfig.read_macros",
        PurePosixPath("WTF/Account/90000001#6/1/Labcharb-Labrealmd/macros-cache.txt"),
        lambda p: wtfconfig.read_macros(p),
        lambda doc: doc.to_bytes(),
    ),
]
SV = READERS[0]


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


def _copy_in(flavor: Path, reader: Reader) -> tuple[Path, bytes]:
    source = CAPTURE.joinpath(*reader.rel.parts)
    data = source.read_bytes()
    assert data, f"{reader.rel} is an empty fixture; pick one with content"
    path = _place(flavor, reader.rel)
    shutil.copyfile(source, path)
    return path, data


def _link_out(flavor: Path, tmp_path: Path, reader: Reader) -> tuple[Path, Path]:
    """A final-component link at the reader's path, to a sound copy of the
    same real fixture outside the install (following it would parse)."""
    outside = tmp_path / "Elsewhere" / reader.rel.name
    outside.parent.mkdir(exist_ok=True)
    shutil.copyfile(CAPTURE.joinpath(*reader.rel.parts), outside)
    assert not outside.resolve().is_relative_to(flavor.parent.resolve())
    link = _place(flavor, reader.rel)
    link.symlink_to(outside)
    return link, outside


class OpenSpy:
    """Records every `os.open` that succeeds: (path, flags, fd)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, int, int]] = []
        real = os.open

        def spy(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            fd = real(path, flags, mode, dir_fd=dir_fd)
            self.calls.append((os.fspath(path), flags, fd))
            return fd

        monkeypatch.setattr(os, "open", spy)

    def for_path(self, path: Path) -> list[tuple[int, int]]:
        """(flags, fd) of every open of `path`."""
        return [(flags, fd) for p, flags, fd in self.calls if p == os.fspath(path)]


def assert_closed(fds: list[int]) -> None:
    for fd in fds:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF, f"descriptor {fd} left open"


def assert_one_guarded_open_closed(spy: OpenSpy, path: Path) -> None:
    """Exactly one `os.open` of `path`, non-blocking and (where the platform
    has it) not following a final link, and its descriptor closed."""
    opens = spy.for_path(path)
    assert len(opens) == 1, (
        f"expected exactly one os.open of {path.name} through the guarded helper, saw {len(opens)}"
    )
    flags, fd = opens[0]
    assert flags & O_NONBLOCK == O_NONBLOCK, "the open is non-blocking"
    assert flags & O_NOFOLLOW == O_NOFOLLOW, "the open does not follow a final link"
    assert_closed([fd])


def _no_unguarded_read(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """`path` is not opened by any plain opener: not `longpath`'s, not the
    built-in `open` / `io.open` (which `os.fdopen` and `Path.read_bytes` go
    through), not `Path.open`. Any other path, and an integer descriptor (the
    guarded helper's, wrapped by `os.fdopen`), passes through."""
    target = os.fspath(path)

    def refuse(*_: Any, **__: Any) -> Any:
        raise AssertionError("read through the plain longpath opener, not the guarded helper")

    def is_target(arg: Any) -> bool:
        return isinstance(arg, (str, os.PathLike)) and os.fspath(arg) == target

    real_open = io.open
    real_path_open = pathlib.Path.open

    def plain_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if is_target(file):
            raise AssertionError(f"{path.name} reopened by a plain open, not the guarded helper")
        return real_open(file, *args, **kwargs)

    def plain_path_open(self: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        if is_target(self):
            raise AssertionError(f"{path.name} reopened by Path.open, not the guarded helper")
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(longpath, "open_read", refuse)
    monkeypatch.setattr(longpath, "read_bytes", refuse)
    monkeypatch.setattr(builtins, "open", plain_open)
    monkeypatch.setattr(io, "open", plain_open)
    monkeypatch.setattr(pathlib.Path, "open", plain_path_open)


class ReadMeter:
    """Counts every byte read during a call, by any of the ways a reader
    could read: `os.read`, and the handles `io.open` / `open` return (which
    `os.fdopen`, `Path.open` and `Path.read_bytes` go through)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.total = 0
        meter = self
        real_read = os.read
        real_open = io.open

        def counted_read(fd: int, n: int) -> bytes:
            out = real_read(fd, n)
            meter.total += len(out)
            return out

        class Handle:
            def __init__(self, inner: Any) -> None:
                self._inner = inner

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

            def __enter__(self) -> Handle:
                self._inner.__enter__()
                return self

            def __exit__(self, *exc: Any) -> Any:
                return self._inner.__exit__(*exc)

            def read(self, *args: Any) -> Any:
                out = self._inner.read(*args)
                meter.total += len(out)
                return out

            def readinto(self, buffer: Any) -> Any:
                n = self._inner.readinto(buffer)
                meter.total += n or 0
                return n

        def counted_open(*args: Any, **kwargs: Any) -> Any:
            return Handle(real_open(*args, **kwargs))

        monkeypatch.setattr(os, "read", counted_read)
        monkeypatch.setattr(io, "open", counted_open)
        monkeypatch.setattr(builtins, "open", counted_open)


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


# ─── a regular file reads as before (real fixtures) ─────────────────────────


@pytest.mark.parametrize("reader", READERS, ids=_ids("real"))
def test_a_regular_file_reads_as_before(reader: Reader, install: Path) -> None:
    """The pin: passes today and after M16-26."""
    path, data = _copy_in(install, reader)
    before = path.stat()

    document = reader.read(path)

    assert reader.to_bytes(document) == data, "the document keeps the file's bytes (L4)"
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns), (
        "the read changed nothing (L1)"
    )


def test_real_luadata_read_keeps_taking_a_str_path(install: Path) -> None:
    """`luadata.read` is typed `str | Path`; the move keeps a `str` working."""
    path, data = _copy_in(install, SV)
    assert luadata.serialize(luadata.read(str(path))) == data


@XFAIL
@pytest.mark.parametrize("reader", READERS, ids=_ids("real-one-guarded-open"))
def test_a_regular_file_is_read_through_one_guarded_open_and_closed(
    reader: Reader, install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No second, unguarded open after the helper's check, and no descriptor
    left open on the success path."""
    path, data = _copy_in(install, reader)
    _no_unguarded_read(monkeypatch, path)
    spy = OpenSpy(monkeypatch)

    document = reader.read(path)

    assert reader.to_bytes(document) == data
    assert_one_guarded_open_closed(spy, path)


@XFAIL
def test_real_luadata_read_by_str_path_is_one_guarded_open_and_closed(
    install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, data = _copy_in(install, SV)
    _no_unguarded_read(monkeypatch, path)
    spy = OpenSpy(monkeypatch)
    assert luadata.serialize(luadata.read(str(path))) == data
    assert_one_guarded_open_closed(spy, path)


@XFAIL
def test_constructed_oversized_luadata_read_is_refused_and_closed(
    install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real SavedVariables file over a lowered `MAX_FILE_BYTES` (constructed
    boundary: the bound is patched down rather than a 256 MiB file built) is
    refused with `LuaLimitError` before parsing, through the one guarded
    open, and its descriptor is closed on that path too."""
    path, data = _copy_in(install, SV)
    monkeypatch.setattr(luadata, "MAX_FILE_BYTES", len(data) - 1)
    _no_unguarded_read(monkeypatch, path)
    spy = OpenSpy(monkeypatch)

    with pytest.raises(luadata.LuaLimitError) as caught:
        luadata.read(path)

    assert (caught.value.line, caught.value.column) == (1, 1)
    assert f"{len(data)} bytes" in str(caught.value)
    assert_one_guarded_open_closed(spy, path)


def test_constructed_oversized_luadata_read_reads_at_most_the_bound_plus_one(
    install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin (passes today and after M16-26): a file over a lowered
    `MAX_FILE_BYTES` (constructed boundary, far below the file's size) is
    refused without reading more than `MAX_FILE_BYTES + 1` bytes, so a reader
    that reads the whole file before checking its size fails."""
    path, data = _copy_in(install, SV)
    bound = 100
    assert len(data) > 4 * bound, "the fixture is well over the lowered bound"
    monkeypatch.setattr(luadata, "MAX_FILE_BYTES", bound)
    meter = ReadMeter(monkeypatch)

    with pytest.raises(luadata.LuaLimitError):
        luadata.read(path)

    assert meter.total <= bound + 1, f"read {meter.total} bytes; the bound is {bound}"


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
    fds = [fd for _, fd in spy.for_path(fifo)]
    assert fds, "the FIFO was opened (non-blocking) and then refused by its fstat"
    assert_closed(fds)
    assert stat.S_ISFIFO(fifo.lstat().st_mode), "the FIFO is left as it was"


# ─── a final-component link out of the install is refused (constructed) ─────


@needs_symlink
@XFAIL
@pytest.mark.parametrize("reader", READERS, ids=_ids("constructed-link-out"))
def test_a_final_link_out_of_the_install_is_refused(
    reader: Reader, install: Path, tmp_path: Path
) -> None:
    """`tree` reports this entry as a link outside the install, not followed;
    the reader must agree."""
    link, outside = _link_out(install, tmp_path, reader)
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


@needs_symlink
@XFAIL
@pytest.mark.parametrize("reader", READERS, ids=_ids("constructed-link-out-no-o-nofollow"))
def test_without_o_nofollow_a_final_link_out_of_the_install_is_refused(
    reader: Reader, install: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows path of the helper (no `O_NOFOLLOW`: the link is refused by
    `lstat` before the open), exercised on POSIX."""
    monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    link, outside = _link_out(install, tmp_path, reader)
    spy = OpenSpy(monkeypatch)

    with pytest.raises(UnsafeReadError, match="not a regular file"):
        reader.read(link)

    assert spy.for_path(link) == [], "nothing was opened through the link"
    assert spy.for_path(outside) == [], "the target outside the install was not opened"


# ─── sv dump refuses with a message (constructed) ───────────────────────────


@needs_fifo
@needs_symlink
@XFAIL
@pytest.mark.parametrize("kind", ["constructed-fifo", "constructed-link-out"])
def test_sv_dump_refuses_a_fifo_or_a_final_link_with_a_message(
    kind: str, install: Path, tmp_path: Path
) -> None:
    if kind == "constructed-fifo":
        path = _place(install, SV.rel)
        os.mkfifo(path)
    else:
        path, _ = _link_out(install, tmp_path, SV)
    result: list[Any] = []

    def dump() -> None:
        result.append(CliRunner().invoke(cli.app, ["sv", "dump", str(path)]))

    raised = bounded(dump, path)

    assert raised is None, repr(raised)
    outcome = result[0]
    assert outcome.exit_code not in (0, None), (outcome.exit_code, outcome.stdout)
    assert outcome.exception is None or isinstance(outcome.exception, SystemExit), (
        f"an uncaught {outcome.exception!r}, not a refusal"
    )
    assert "Traceback" not in outcome.stdout + outcome.stderr
    assert outcome.stdout == "", "nothing was dumped"
    lines = outcome.stderr.strip().splitlines()
    assert lines, "the refusal is said on stderr"
    assert lines[0].startswith("wowlab: "), lines[0]
    assert path.name in lines[0], lines[0]
