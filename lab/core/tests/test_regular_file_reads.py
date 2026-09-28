"""The shared regular-file read and the store reads swept onto it (M11-11).

`snapshot.open_regular_file` / `read_regular_file` are what guard's install
and journal reads and the store's capture, object and manifest reads use: an
open that never blocks, never adopts a terminal and never follows a final
link, then an `fstat` that must show a regular file (the one the caller looked
at, when it says which), then a bounded read, with the descriptor closed on
every path. The M11-11T graders (`test_guard_nonblocking_reads.py`) cover
guard's reads, the capture and `read_object`; this file covers the helper on
its own and the store reads the ticket's sweep moved onto it, which have no
graders: the manifest `_load`, the object `_rehash` (through `create`'s reuse
check and `verify`), and `create`'s read of a manifest already at its id.

Everything here is constructed (hostile and boundary inputs, L8), under
`tmp_path`; nothing touches a real install or the real user data directory.
FIFO cases are POSIX-only (skipped where `os.mkfifo` is missing); link cases
are skipped on Windows, where making a link needs a privilege. The Windows
path (no `O_NOFOLLOW`) is exercised on POSIX by setting the module's
`_O_NOFOLLOW` to 0.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import os
import stat
import sys
import threading
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import snapshot
from wowlab_core.snapshot import (
    ManifestIntegrityError,
    ObjectCorruptError,
    SnapshotExistsError,
    SnapshotStore,
    UnsafeReadError,
    open_regular_file,
    read_regular_file,
)

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
BOUND = 5.0
"""Seconds a read may take before it counts as blocked."""

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo (POSIX)")
needs_symlink = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "Install"
    (root / "WTF").mkdir(parents=True)
    (root / "WTF" / "Config.wtf").write_bytes(b'SET portal "constructed"\n')
    (root / "WTF" / "other.wtf").write_bytes(b"constructed other\n")
    return root


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "userdata" / "store")


class OpenSpy:
    """Records every `os.open` (path, flags, fd) and can act right after one."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.after: Callable[[str], None] | None = None
        real = os.open

        def spy(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            fd = real(path, flags, mode, dir_fd=dir_fd)
            self.calls.append((os.fspath(path), flags, fd))
            if self.after is not None:
                self.after(os.fspath(path))
            return fd

        monkeypatch.setattr(os, "open", spy)

    def fds_for(self, path: Path) -> list[int]:
        return [fd for p, _, fd in self.calls if p == os.fspath(path)]


def assert_closed(fds: list[int]) -> None:
    assert fds, "the path was opened"
    for fd in fds:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF, f"descriptor {fd} left open"


def bounded(action: Callable[[], Any], fifo: Path | None = None) -> BaseException | None:
    """Run `action` in a thread; fail (after releasing a reader blocked on
    `fifo`) if it has not finished within `BOUND` seconds."""
    outcome: list[BaseException | None] = []

    def work() -> None:
        try:
            action()
        except BaseException as exc:
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(BOUND)
    if worker.is_alive():
        if fifo is not None:
            with contextlib.suppress(OSError):
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(BOUND)
        pytest.fail(f"the read did not return within {BOUND:.0f}s")
    return outcome[0]


# ─── the helper ──────────────────────────────────────────────────────────────


def test_constructed_regular_file_is_read_whole_with_the_nonblocking_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file.bin"
    body = bytes(range(256)) * 9000  # past one read chunk
    path.write_bytes(body)
    spy = OpenSpy(monkeypatch)
    assert read_regular_file(path) == body
    [(_, flags, _)] = spy.calls
    for name in ("O_NONBLOCK", "O_NOCTTY", "O_NOFOLLOW", "O_BINARY"):
        wanted = getattr(os, name, 0)
        assert flags & wanted == wanted, f"{name} is in the open flags"
    assert flags & os.O_ACCMODE == os.O_RDONLY
    assert not flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND)
    assert_closed(spy.fds_for(path))


@needs_fifo
def test_constructed_fifo_is_refused_without_blocking_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    spy = OpenSpy(monkeypatch)
    raised = bounded(lambda: read_regular_file(fifo), fifo)
    assert isinstance(raised, UnsafeReadError), repr(raised)
    assert "not a regular file" in str(raised)
    assert_closed(spy.fds_for(fifo))
    assert stat.S_ISFIFO(fifo.lstat().st_mode), "the FIFO is left as it was"


@needs_symlink
def test_constructed_symlink_is_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"constructed")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError) as caught:
        read_regular_file(link)
    assert caught.value.errno == errno.ELOOP or isinstance(caught.value, UnsafeReadError)


def test_constructed_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        read_regular_file(tmp_path)


def test_constructed_missing_path_is_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_regular_file(tmp_path / "absent")


def test_constructed_file_replaced_since_the_lstat_is_refused_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"first")
    seen = path.lstat()
    keep = tmp_path / "keep"
    path.rename(keep)  # keeps the old inode alive, so the new one cannot reuse it
    path.write_bytes(b"first")
    spy = OpenSpy(monkeypatch)
    with pytest.raises(UnsafeReadError, match="replaced"):
        read_regular_file(path, expect=seen)
    assert_closed(spy.fds_for(path))
    assert read_regular_file(keep, expect=seen) == b"first"


def test_constructed_limit_is_inclusive_and_one_past_it_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"x" * 10)
    assert read_regular_file(path, limit=10) == b"x" * 10
    spy = OpenSpy(monkeypatch)
    with pytest.raises(UnsafeReadError, match="more than 9"):
        read_regular_file(path, limit=9)
    assert_closed(spy.fds_for(path))


def test_constructed_file_that_grows_past_the_limit_is_read_one_byte_past_it_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fstat` reports a size under the limit (as for a file still being
    written); the read still stops one byte past the limit and refuses."""
    path = tmp_path / "file"
    path.write_bytes(b"y" * (3 << 20))
    real_fstat = os.fstat
    real_read = os.read
    read_total = 0

    def small_fstat(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        fields = list(st)
        fields[stat.ST_SIZE] = 1
        return os.stat_result(fields)

    def counting_read(fd: int, n: int) -> bytes:
        nonlocal read_total
        chunk = real_read(fd, n)
        read_total += len(chunk)
        return chunk

    monkeypatch.setattr(os, "fstat", small_fstat)
    monkeypatch.setattr(os, "read", counting_read)
    spy = OpenSpy(monkeypatch)
    with pytest.raises(UnsafeReadError, match="more than 1000"):
        read_regular_file(path, limit=1000)
    assert read_total == 1001
    monkeypatch.setattr(os, "fstat", real_fstat)
    assert_closed(spy.fds_for(path))


def test_constructed_open_regular_file_hands_back_an_open_checked_descriptor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"constructed")
    fd, st = open_regular_file(path, expect=path.lstat())
    try:
        assert stat.S_ISREG(st.st_mode)
        assert os.read(fd, 100) == b"constructed"
    finally:
        os.close(fd)


# ─── the Windows path, emulated: no O_NOFOLLOW ───────────────────────────────


@needs_symlink
def test_constructed_without_o_nofollow_a_link_is_refused_before_any_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    target = tmp_path / "target"
    target.write_bytes(b"constructed")
    link = tmp_path / "link"
    link.symlink_to(target)
    spy = OpenSpy(monkeypatch)
    with pytest.raises(UnsafeReadError, match="not a regular file"):
        read_regular_file(link)
    assert spy.calls == [], "nothing was opened through the link"


def test_constructed_without_o_nofollow_a_swap_during_the_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path is checked after the open to still name the opened file."""
    monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    path = tmp_path / "file"
    path.write_bytes(b"constructed")
    spy = OpenSpy(monkeypatch)

    def swap(opened: str) -> None:
        if opened == os.fspath(path):
            spy.after = None
            path.rename(tmp_path / "moved")
            path.write_bytes(b"constructed")

    spy.after = swap
    with pytest.raises(UnsafeReadError, match="changed as it was opened"):
        read_regular_file(path)
    assert_closed(spy.fds_for(path))


@needs_symlink
def test_constructed_without_o_nofollow_read_object_refuses_a_linked_object(
    source: Path, store: SnapshotStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = store.create(source, ["WTF"], now=T0)
    digest = next(e.sha256 for e in manifest.entries if e.sha256 is not None)
    obj = store.object_path(digest)
    elsewhere = tmp_path / "elsewhere"
    obj.rename(elsewhere)
    obj.symlink_to(elsewhere)
    monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    spy = OpenSpy(monkeypatch)
    with pytest.raises(ObjectCorruptError, match="cannot be read"):
        store.read_object(digest)
    assert spy.calls == []


# ─── store: read_object ──────────────────────────────────────────────────────


def test_constructed_read_object_still_reads_a_sound_object(
    source: Path, store: SnapshotStore
) -> None:
    manifest = store.create(source, ["WTF"], now=T0)
    for entry in manifest.entries:
        assert entry.sha256 is not None
        data = store.read_object(entry.sha256)
        assert hashlib.sha256(data).hexdigest() == entry.sha256
        assert data == (source / entry.path).read_bytes()


# ─── store: manifest _load ───────────────────────────────────────────────────


@needs_symlink
def test_constructed_manifest_that_is_a_symlink_to_a_sound_copy_does_not_load(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    manifest = store.create(source, ["WTF"], now=T0)
    path = store.manifests_dir / f"{manifest.id}.json"
    elsewhere = tmp_path / "elsewhere.json"
    path.rename(elsewhere)
    path.symlink_to(elsewhere)
    with pytest.raises(ManifestIntegrityError, match="does not load"):
        store.show(manifest.id)
    assert [m.name for m in store.list_lenient().invalid] == [path.name]
    assert [m.name for m in store.verify().invalid_manifests] == [path.name]


@needs_fifo
def test_constructed_manifest_swapped_for_a_fifo_before_its_open_does_not_block(
    source: Path, store: SnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`show` checks the manifest by `is_file` and then reads it; a FIFO put
    there in between is refused within the bound."""
    manifest = store.create(source, ["WTF"], now=T0)
    path = store.manifests_dir / f"{manifest.id}.json"
    real_is_file = Path.is_file

    def is_file_then_swap(self: Path) -> bool:
        answer = real_is_file(self)
        if self == path and answer:
            path.unlink()
            os.mkfifo(path)
        return answer

    monkeypatch.setattr(Path, "is_file", is_file_then_swap)
    raised = bounded(lambda: store.show(manifest.id), path)
    assert isinstance(raised, ManifestIntegrityError), repr(raised)
    assert stat.S_ISFIFO(path.lstat().st_mode)


# ─── store: _rehash, through create's reuse check and verify ─────────────────


def _config_object(store: SnapshotStore, source: Path) -> Path:
    digest = hashlib.sha256((source / "WTF" / "Config.wtf").read_bytes()).hexdigest()
    return store.object_path(digest)


@needs_fifo
def test_constructed_object_that_is_a_fifo_is_reported_by_verify_without_blocking(
    source: Path, store: SnapshotStore
) -> None:
    store.create(source, ["WTF"], now=T0)
    obj = _config_object(store, source)
    obj.unlink()
    os.mkfifo(obj)
    outcome: list[Any] = []
    raised = bounded(lambda: outcome.append(store.verify()), obj)
    assert raised is None, repr(raised)
    assert obj.relative_to(store.objects_dir).as_posix() in outcome[0].corrupt_objects


@needs_symlink
def test_constructed_object_that_is_a_symlink_is_not_followed_by_verify(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    store.create(source, ["WTF"], now=T0)
    obj = _config_object(store, source)
    elsewhere = tmp_path / "elsewhere"
    obj.rename(elsewhere)
    obj.symlink_to(elsewhere)
    report = store.verify()
    name = obj.parent.name + obj.name
    assert name in report.corrupt_objects, "a linked object does not count as sound"


@needs_symlink
def test_constructed_linked_object_is_replaced_by_a_real_one_on_the_next_create(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    """`create`'s reuse check re-hashes an existing object without following a
    link, so a linked object is not reused: a regular object takes its place,
    and the file the link pointed at is left alone."""
    store.create(source, ["WTF"], now=T0)
    obj = _config_object(store, source)
    elsewhere = tmp_path / "elsewhere"
    obj.rename(elsewhere)
    obj.symlink_to(elsewhere)
    before = elsewhere.read_bytes()
    before_mtime = elsewhere.stat().st_mtime_ns
    store.create(source, ["WTF"], now=datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC))
    assert stat.S_ISREG(obj.lstat().st_mode), "the link was replaced by a regular object"
    assert zlib.decompress(obj.read_bytes()) == (source / "WTF" / "Config.wtf").read_bytes()
    assert elsewhere.read_bytes() == before
    assert elsewhere.stat().st_mtime_ns == before_mtime, "the link target was not touched"


@needs_fifo
def test_constructed_object_swapped_for_a_fifo_at_the_reuse_check_open_does_not_block(
    source: Path, store: SnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create`'s reuse check re-hashes an object already in the store. A FIFO
    swapped in for the object just as the re-hash opens it (hooked at
    `os.open` and `io.open`, recognised by the object's inode) never blocks
    it: the object is not reused, and a regular one is written in its place."""
    store.create(source, ["WTF"], now=T0)
    obj = _config_object(store, source)
    identity = (obj.lstat().st_dev, obj.lstat().st_ino)
    swapped: list[bool] = []
    real_os_open = os.open
    real_io_open = io.open

    def swap_if_object(path: Any) -> None:
        if swapped or isinstance(path, int):
            return
        try:
            st = os.lstat(path)
        except OSError:
            return
        if (st.st_dev, st.st_ino) == identity:
            swapped.append(True)
            held = real_os_open(obj, os.O_RDONLY)  # no inode reuse while we swap
            try:
                obj.unlink()
                os.mkfifo(obj)
            finally:
                os.close(held)

    def hooked_os_open(
        path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_CREAT:
            swap_if_object(path)
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    def hooked_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if "r" in mode and "+" not in mode:
            swap_if_object(file)
        return real_io_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", hooked_os_open)
    monkeypatch.setattr(io, "open", hooked_io_open)
    later = datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC)
    raised = bounded(lambda: store.create(source, ["WTF"], now=later), obj)
    assert swapped, "the reuse check never opened the object"
    assert raised is None, repr(raised)
    assert stat.S_ISREG(obj.lstat().st_mode), "a regular object took the FIFO's place"
    assert zlib.decompress(obj.read_bytes()) == (source / "WTF" / "Config.wtf").read_bytes()


# ─── store: create's read of a manifest already at its id ────────────────────


def test_constructed_identical_manifest_at_the_id_is_still_a_no_op(
    source: Path, store: SnapshotStore
) -> None:
    first = store.create(source, ["WTF"], now=T0)
    again = store.create(source, ["WTF"], now=T0)
    assert again == first


def test_constructed_longer_manifest_at_the_id_is_refused_after_a_bounded_read(
    source: Path, store: SnapshotStore
) -> None:
    first = store.create(source, ["WTF"], now=T0)
    path = store.manifests_dir / f"{first.id}.json"
    path.write_bytes(path.read_bytes() + b" " * (1 << 20))
    with pytest.raises(SnapshotExistsError):
        store.create(source, ["WTF"], now=T0)


@needs_fifo
def test_constructed_fifo_at_the_manifest_id_is_refused_without_blocking(
    source: Path, store: SnapshotStore
) -> None:
    first = store.create(source, ["WTF"], now=T0)
    path = store.manifests_dir / f"{first.id}.json"
    path.unlink()
    os.mkfifo(path)
    raised = bounded(lambda: store.create(source, ["WTF"], now=T0), path)
    assert isinstance(raised, SnapshotExistsError), repr(raised)
    assert stat.S_ISFIFO(path.lstat().st_mode), "the FIFO is not replaced"


@needs_symlink
def test_constructed_symlink_at_the_manifest_id_is_refused_not_followed(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    first = store.create(source, ["WTF"], now=T0)
    path = store.manifests_dir / f"{first.id}.json"
    elsewhere = tmp_path / "elsewhere.json"
    path.rename(elsewhere)
    path.symlink_to(elsewhere)
    with pytest.raises(SnapshotExistsError):
        store.create(source, ["WTF"], now=T0)
    assert path.is_symlink(), "the link is not replaced"
