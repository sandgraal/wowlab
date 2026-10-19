"""Discovery holds a flavor folder, and `.build.info`, to what it checked (M13-54).

Constructed (L8, boundary): a race cannot be captured, so each test puts the
real `.build.info` and `.flavor.info` (`fixtures/macos/`) in a tree under
`tmp_path` and swaps a flavor folder for a link to a directory, or for
another directory, or `.build.info` for a FIFO, a link or another file, at
one point between the check and the read, by wrapping the call that comes
just before that point.

Every test runs twice: on the platform as it is (POSIX: the folder is held
by a descriptor opened `O_DIRECTORY | O_NOFOLLOW`; Windows: no descriptor)
and with `snapshot._O_NOFOLLOW` set to 0, the stand-in for Windows, where
there are no folder descriptors and the folder is `lstat`ed each side of
the read. Which of the two a run took is asked of `install._folder_fds`.
A 5 s alarm stops any test whose read would block.
"""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import install as install_mod
from wowlab_core import snapshot
from wowlab_core.install import NotAnInstallError, NotTaken, read_install

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "macos"
REAL_BUILD_INFO = (FIXTURES / ".build.info").read_bytes()
REAL_FLAVOR_INFO = (FIXTURES / "forever" / ".flavor.info").read_bytes()
REAL_FOLDER = "_classic_beta_"  # the flavor column of the fixture's provenance row
RACING = "_racing_"
# Constructed: a product no real folder names, so a read through the link,
# or of the directory put in the folder's place, would show as a flavor with it.
ELSEWHERE = "m13-54-elsewhere"
ELSEWHERE_INFO = f"Product Flavor!STRING:0\n{ELSEWHERE}\n".encode()

CHANGED = NotTaken(
    name=RACING,
    reason="folder_changed",
    target=None,
    note=install_mod.NOT_A_FLAVOR_FOLDER_CHANGED,
)


class _Blocked(BaseException):
    """Raised by the alarm. Not an `OSError` (as `TimeoutError` is), so no
    `except OSError` in discovery can turn a blocked call into an outcome."""


@pytest.fixture(autouse=True)
def _alarm() -> Iterator[None]:
    """No test may block: a read that waits on a FIFO is stopped after 5 s."""
    if not hasattr(signal, "SIGALRM"):  # Windows: no FIFO test runs there
        yield
        return

    def _expired(signum: int, frame: Any) -> None:
        raise _Blocked("discovery blocked (M13-54)")

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.alarm(5)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(params=[True, False], ids=["platform", "no-o-nofollow"])
def nofollow(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    """True when the folder is held by a descriptor in this run: the
    platform as it is (POSIX: yes; Windows, which has no `O_NOFOLLOW` and no
    `dir_fd`: no), or with `snapshot._O_NOFOLLOW` set to 0, the stand-in for
    Windows (no). Asked of `install._folder_fds`, never assumed from the id,
    so on Windows both runs take the `lstat` floor and say so."""
    if not request.param:
        monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    return install_mod._folder_fds()


def _install(base: Path) -> Path:
    root = base / "World of Warcraft"
    root.mkdir(parents=True)
    (root / ".build.info").write_bytes(REAL_BUILD_INFO)
    for folder in (REAL_FOLDER, RACING):
        (root / folder).mkdir()
        (root / folder / ".flavor.info").write_bytes(REAL_FLAVOR_INFO)
    return root


def _can_symlink(base: Path) -> None:
    probe = base / "m13-54-probe"
    try:
        probe.symlink_to(base / "m13-54-target", target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    probe.unlink()


@pytest.fixture
def scene(tmp_path: Path) -> tuple[Path, Path]:
    """(install root, a directory outside it holding another `.flavor.info`).
    A test whose swap makes a link checks it can first (`_ready`)."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".flavor.info").write_bytes(ELSEWHERE_INFO)
    return _install(tmp_path / "install"), elsewhere


def _folder_to_link(folder: Path, elsewhere: Path) -> None:
    """The folder is renamed aside (it keeps its inode and its real
    `.flavor.info`) and a link to `elsewhere` put in its place."""
    folder.rename(folder.parent / "m13-54-aside")
    folder.symlink_to(elsewhere, target_is_directory=True)


def _folder_to_other_dir(folder: Path, elsewhere: Path) -> None:
    """Another real directory, not a link, in the folder's place, holding
    a regular `.flavor.info` with a product no real folder names."""
    folder.rename(folder.parent / "m13-54-aside")
    folder.mkdir()
    (folder / ".flavor.info").write_bytes(ELSEWHERE_INFO)


def _folder_to_junction(folder: Path, elsewhere: Path) -> None:
    """Windows: a junction to `elsewhere` in the folder's place. A junction
    needs no symlink privilege, so this runs on every Windows runner."""
    import _winapi  # type: ignore[import-not-found,unused-ignore]

    folder.rename(folder.parent / "m13-54-aside")
    _winapi.CreateJunction(str(elsewhere), str(folder))


_SWAPS = [
    pytest.param(_folder_to_link, id="constructed-link"),
    pytest.param(_folder_to_other_dir, id="constructed-other-dir"),
    pytest.param(_folder_to_junction, id="constructed-junction"),
]


def _ready(swap: Callable[[Path, Path], None], root: Path) -> None:
    """Skip where the swap cannot be made: a symlink without the privilege
    to make one, a junction off Windows."""
    if swap is _folder_to_link:
        _can_symlink(root.parent)
    elif swap is _folder_to_junction and sys.platform != "win32":
        pytest.skip("junctions are Windows only")


def _assert_refused(root: Path) -> None:
    got = read_install(root)
    assert [f.folder for f in got.flavors] == [REAL_FOLDER]
    assert all(f.product != ELSEWHERE for f in got.flavors)
    assert got.other_dirs == (RACING,)
    assert got.not_taken == (CHANGED,)
    assert "link" not in CHANGED.note  # never worded as a link: it may be a real folder
    assert install_mod.not_taken_why(CHANGED) == "it changed as it was read; run the command again"


def test_a_real_folder_is_still_read_constructed(scene: tuple[Path, Path], nofollow: bool) -> None:
    """No behaviour change for a real folder: both are flavors."""
    root, _ = scene
    got = read_install(root)
    assert [f.folder for f in got.flavors] == [REAL_FOLDER, RACING]
    assert got.flavors[0].product == got.flavors[1].product != ""
    assert got.other_dirs == ()
    assert got.not_taken == ()


@pytest.mark.parametrize("swap", _SWAPS)
def test_folder_swapped_after_the_scan_is_refused(
    scene: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    nofollow: bool,
    swap: Callable[[Path, Path], None],
) -> None:
    """The scan saw a real folder; by the read it is a link to a directory
    (read through before M13-54: `flavors` held the link target's product)
    or another directory. Refused by the `lstat` before the read."""
    root, elsewhere = scene
    _ready(swap, root)
    real_scan = install_mod._scan_children

    def scan(where: Path) -> Any:
        found = real_scan(where)
        swap(root / RACING, elsewhere)
        return found

    monkeypatch.setattr(install_mod, "_scan_children", scan)
    _assert_refused(root)


@pytest.mark.parametrize("swap", _SWAPS)
def test_folder_swapped_before_the_descriptor_is_opened_is_refused(
    scene: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    nofollow: bool,
    swap: Callable[[Path, Path], None],
) -> None:
    """The `lstat` before the read saw the scanned folder; then it is
    swapped. On POSIX the folder's descriptor is refused (`O_NOFOLLOW` meets
    the link; `fstat` names another directory). Without folder descriptors
    the `.flavor.info` open meets a file that is not the one its `lstat`
    saw, or the `lstat` after the read a folder that is not the scanned one."""
    root, elsewhere = scene
    _ready(swap, root)
    real_same = install_mod._same_folder
    calls: list[Path] = []

    def same_folder(directory: Path, scanned: os.stat_result) -> bool:
        ok = real_same(directory, scanned)
        calls.append(directory)
        if directory.name == RACING and calls.count(directory) == 1:
            swap(directory, elsewhere)
        return ok

    real_read = install_mod._read_flavor_info
    read: list[str] = []

    def read_flavor_info(directory: Path, folder: int | None) -> Any:
        read.append(directory.name)
        return real_read(directory, folder)

    monkeypatch.setattr(install_mod, "_same_folder", same_folder)
    monkeypatch.setattr(install_mod, "_read_flavor_info", read_flavor_info)
    _assert_refused(root)
    # With a descriptor the folder is refused before anything in it is
    # opened; without one only the `lstat`s around the read hold it.
    assert (RACING in read) is not nofollow


@pytest.mark.parametrize("swap", _SWAPS)
def test_folder_swapped_at_the_flavor_info_open_is_refused(
    scene: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    nofollow: bool,
    swap: Callable[[Path, Path], None],
) -> None:
    """Swapped just before `.flavor.info` is opened. On POSIX it is opened
    relative to the folder's descriptor, so the scanned folder's own file is
    what is opened, never the link's target. Without folder descriptors the
    open by path meets another file than its `lstat` saw and is refused
    (M13-46). Either way the `lstat` after the read then refuses the folder,
    and that reason is the one given: it is the folder that changed."""
    root, elsewhere = scene
    _ready(swap, root)
    real_open = snapshot.open_regular_file
    held: list[int | None] = []

    def open_regular_file(path: Path, **kwargs: Any) -> tuple[int, os.stat_result]:
        dir_fd = kwargs.get("dir_fd")
        if path.name == ".flavor.info" and not held:
            racing = (root / RACING).lstat()
            here = os.fstat(dir_fd) if dir_fd is not None else (path.parent).lstat()
            if (here.st_dev, here.st_ino) == (racing.st_dev, racing.st_ino):
                held.append(dir_fd)
                swap(root / RACING, elsewhere)
        fd, st = real_open(path, **kwargs)
        if held and dir_fd is not None and dir_fd == held[0]:
            assert os.read(fd, 4096) == REAL_FLAVOR_INFO  # the scanned folder's file
            os.lseek(fd, 0, os.SEEK_SET)
        return fd, st

    monkeypatch.setattr(snapshot, "open_regular_file", open_regular_file)
    _assert_refused(root)
    if nofollow:
        assert held and held[0] is not None  # opened through the folder's descriptor
    else:
        assert held == [None]


@pytest.mark.parametrize("swap", _SWAPS)
def test_folder_swapped_after_the_read_is_refused(
    scene: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    nofollow: bool,
    swap: Callable[[Path, Path], None],
) -> None:
    """Swapped once `.flavor.info` has been read: the `lstat` after the read
    no longer sees the scanned folder, so what was read is not taken."""
    root, elsewhere = scene
    _ready(swap, root)
    real_read = install_mod._read_flavor_info
    reads: list[Any] = []

    def read_flavor_info(directory: Path, folder: int | None) -> Any:
        read = real_read(directory, folder)
        if directory.name == RACING:
            reads.append(read)
            swap(directory, elsewhere)
        return read

    monkeypatch.setattr(install_mod, "_read_flavor_info", read_flavor_info)
    _assert_refused(root)
    assert reads == [("wow_classic_beta", False)]  # read, then not taken


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_searchable_unlistable_folder_is_still_read_constructed(
    scene: tuple[Path, Path], nofollow: bool
) -> None:
    """A folder that may be searched but not listed (mode 0o100): its
    `.flavor.info` is still read, as before M13-54. Its descriptor is opened
    for search only (`O_SEARCH`, `O_PATH`), which that mode allows."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads past permission bits")
    root, _ = scene
    (root / RACING).chmod(0o100)
    try:
        got = read_install(root)
    finally:
        (root / RACING).chmod(0o700)
    assert [f.folder for f in got.flavors] == [REAL_FOLDER, RACING]
    assert got.flavors[1].product == got.flavors[0].product != ""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_refused_descriptor_never_falls_back_to_the_path_constructed(
    scene: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, nofollow: bool
) -> None:
    """The M13-54 review's probe, three swaps: after the `lstat` before the
    read, a directory with no permissions is put in the folder's place, so
    the descriptor's open is refused (`EACCES`); a read by path would then
    meet a link put there next, and the real folder, put back before the
    `lstat` after the read, would pass it. The refusal is a changed folder;
    nothing is read by path, and the link's product is never a flavor.
    Without folder descriptors this is the floor's stated gap (§6.1), so
    only runs that hold the folder by a descriptor are graded here."""
    if not nofollow:
        pytest.skip("no folder descriptors in this run: the lstat floor's stated gap (§6.1)")
    if os.geteuid() == 0:
        pytest.skip("root opens past permission bits")
    root, elsewhere = scene
    _can_symlink(root.parent)
    racing, aside = root / RACING, root / "m13-54-aside"
    real_same = install_mod._same_folder
    real_read = install_mod._read_flavor_info
    seen: list[Path] = []

    def same_folder(directory: Path, scanned: os.stat_result) -> bool:
        ok = real_same(directory, scanned)
        seen.append(directory)
        if directory == racing and seen.count(racing) == 1:
            racing.rename(aside)
            racing.mkdir()
            racing.chmod(0o000)  # the open of a descriptor is refused
        return ok

    def read_flavor_info(directory: Path, folder: int | None) -> Any:
        if directory != racing:
            return real_read(directory, folder)
        racing.chmod(0o700)
        racing.rmdir()
        racing.symlink_to(elsewhere, target_is_directory=True)
        try:
            return real_read(directory, folder)
        finally:
            racing.unlink()
            aside.rename(racing)  # the real folder back for the last lstat

    monkeypatch.setattr(install_mod, "_same_folder", same_folder)
    monkeypatch.setattr(install_mod, "_read_flavor_info", read_flavor_info)
    try:
        _assert_refused(root)
    finally:
        if racing.is_dir() and not racing.is_symlink():
            racing.chmod(0o700)


# ─── the root `.build.info` ──────────────────────────────────────────────────


def _build_info_to_fifo(info: Path, elsewhere: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs on this platform")
    info.rename(info.parent / "m13-54-original.build.info")
    os.mkfifo(info)


def _build_info_to_link(info: Path, elsewhere: Path) -> None:
    info.rename(info.parent / "m13-54-original.build.info")
    info.symlink_to(elsewhere / ".build.info")


def _build_info_to_other_file(info: Path, elsewhere: Path) -> None:
    """Renamed aside so the new file cannot reuse its inode."""
    info.rename(info.parent / "m13-54-original.build.info")
    info.write_bytes(REAL_BUILD_INFO)


@pytest.mark.parametrize(
    "swap",
    [
        pytest.param(_build_info_to_fifo, id="constructed-fifo"),
        pytest.param(_build_info_to_link, id="constructed-link"),
        pytest.param(_build_info_to_other_file, id="constructed-other-file"),
    ],
)
def test_build_info_swapped_after_the_check_is_refused_never_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nofollow: bool,
    swap: Callable[[Path, Path], None],
) -> None:
    """`.build.info` was a regular file when it was checked; by the read it
    is a FIFO (which a plain read would wait on for ever), a link, or
    another file. The read returns at once and refuses it."""
    _can_symlink(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".build.info").write_bytes(REAL_BUILD_INFO)
    root = _install(tmp_path / "install")
    real_probe = install_mod._probe

    def probe(where: Path, source: Any) -> os.stat_result:
        st = real_probe(where, source)
        swap(where / ".build.info", elsewhere)
        return st

    monkeypatch.setattr(install_mod, "_probe", probe)
    with pytest.raises(NotAnInstallError) as caught:
        read_install(root)
    assert caught.value.reason == "unreadable"
    assert "it changed as it was read; run the command again" in str(caught.value)


def test_a_linked_build_info_is_still_followed_constructed(tmp_path: Path, nofollow: bool) -> None:
    """No behaviour change: a `.build.info` that is a link to a regular file
    is taken as the check takes it (followed), and read."""
    _can_symlink(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".build.info").write_bytes(REAL_BUILD_INFO)
    root = _install(tmp_path / "install")
    (root / ".build.info").unlink()
    (root / ".build.info").symlink_to(elsewhere / ".build.info")
    got = read_install(root)
    assert got.raw_build_info == REAL_BUILD_INFO
    assert [f.folder for f in got.flavors] == [REAL_FOLDER, RACING]
