"""`luadata.read` and the `wtfconfig` readers open from the install root and
refuse a link in a parent directory (M16-49T graders for M16-49; found in the
M16-26T security review, 2026-10-22).

M16-26 moved these readers onto `snapshot.open_regular_file`, whose
`O_NOFOLLOW` checks only the last component of a path. The readers take no
install root, so `WTF -> ../Elsewhere` reads `Elsewhere/Config.wtf` while
`tree` reports the `WTF` entry as "link, not followed". M16-49 gives each
reader the install root and opens from it one component at a time
(`dir_fd`, `O_NOFOLLOW`), so, with the root given:

- a link in any parent directory between the root and the file (the flavor
  folder included) that points out of the install is refused, whether its
  target is spelled with `..` or absolute;
- a link in a parent directory that stays inside the install is refused as
  well, as `tree` reports it ("link, not followed");
- a final-component link is still refused, in or out of the install;
- the refusal is an `OSError` that is not `FileNotFoundError`: the helper's
  `UnsafeReadError`, or the open's own `ELOOP` / `ENOTDIR` / `EMLINK` (which
  of the three a refused `O_NOFOLLOW | O_DIRECTORY` open gives differs by
  platform), and it leaves no descriptor open;
- a regular path still reads byte for byte (L4) and changes nothing (L1),
  also when an unrelated link sits elsewhere in the install, and also when
  the root itself is reached through a link (what is above the root is the
  caller's choice; the walk starts there);
- a `..` component anywhere below the root is refused, whether it climbs out
  of the root lexically, stays under it lexically but resolves outside
  through a link, or resolves back inside;
- a path that is not lexically under the root is refused, never downgraded
  to M16-26's final-component check. The prefix is compared by its
  components as `pathlib` spells them, not by device and inode. So on POSIX
  a root that differs from the path's prefix only in case is refused, on
  every volume (decision). On Windows, `PureWindowsPath` compares without
  case, so `c:\\` against `C:\\` is the same prefix; there the case-variant
  root may be refused or read byte for byte, and the grader takes either. A
  root given as `str` with a trailing separator or a trailing `/.` is the
  same root and reads;
- a parent directory swapped for a link while the read is under way (POSIX,
  a race injected through `os.open` / `os.lstat` / `os.stat`) either makes
  the read refuse or leaves it reading the original file; it never returns
  the capture behind the link. The link is swapped in before call N and
  either left there or swapped back out before call N+1, so a check after
  the open sees the original folder again. A per-component `lstat` followed
  by an open of the whole path fails this, with or without a re-check
  (device and inode) after the open;
- on POSIX, with the real `O_NOFOLLOW`, the walk is graded by its shape
  too: the root is opened once with no `dir_fd`; every later `os.open` is
  one component, relative (`dir_fd`) to the descriptor the open before it
  returned, the first of them to the root's; each carries `O_NOFOLLOW`, and
  each directory open carries `O_DIRECTORY`. The race graders cannot
  enumerate every interleaving; this one closes what they leave;
- on Windows, a real NTFS junction at a parent level, out of the install or
  inside it, is refused (`_winapi.CreateJunction`, as
  `test_snapshot_junctions.py` makes them; runs on the `lab (windows)` job).

The seam (M16-49's to fill):

- each reader takes the install root as a keyword argument `root`, and the
  path stays the full path to the file, inside that root:
  `luadata.read(path, root=root)`, `wtfconfig.read_config(path, root=root)`,
  `read_bindings`, `read_macros` likewise;
- a path-shape refusal (a `..` component, a path not under the root) raises
  `snapshot.UnsafeReadError`, before anything is opened, so a caller catches
  one type for every refusal; a link met on the walk raises
  `UnsafeReadError` or the open's own `OSError` (`ELOOP`, `ENOTDIR`,
  `EMLINK`);
- the walk reads `snapshot._O_NOFOLLOW` at call time, and with it 0 (the
  Windows case, exercised on POSIX by setting it to 0) uses no `dir_fd`: no
  `os.open` call carries one. That path then checks each component with
  `lstat` and opens by path, so a window remains between the check and the
  open on Windows. These graders cannot close it; it belongs in M16-49's
  report. Note for the implementer: the existing walks
  (`profiles._DIR_FD_WALK`, `snapshot._WRITE_BY_DIR_FD`) decide at import
  time, so reusing them as they are fails the 63 graders that set
  `snapshot._O_NOFOLLOW` to 0; the decision must be made at call time.

Today the keyword does not exist, so every marked grader fails with
`TypeError`; the marker names `raises=TypeError` so a partial implementation
(the keyword accepted, the link still followed) shows as a failure, not an
expected one.

Callers with no install root (`sv dump <path>` outside any install) keep
M16-26's final-component check; that is graded by M16-26T's file
(`test_parser_reads_regular_file_m16_26.py`). The unmarked control here also
calls each reader with no root through a parent link, which follows it, as
M16-49 says such callers keep doing (and say so in their output).

The bytes read are real captures (`fixtures/macos/forever/`, rows in
`fixtures/README.md`, L8) copied into a synthetic install under `tmp_path`;
the links and the trees they point at are constructed (ids say
`constructed`). Every link's target holds a sound copy of the same capture,
so a reader that follows the link parses it and returns, and the grader
fails on that rather than on some unrelated error. The race cases put a
*different* real capture (a decoy) behind the swapped-in link, so following
it is told apart from reading the original. Symbolic-link cases are skipped
on Windows, where making one needs a privilege; the junction cases run only
there. Nothing here touches a real install.
"""

from __future__ import annotations

import errno
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

import pytest

from wowlab_core import luadata, snapshot, wtfconfig
from wowlab_core.snapshot import UnsafeReadError

pytestmark = pytest.mark.parser


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos" / "forever"
"""The `_classic_beta_` flavor folder of the macOS capture (README rows)."""

FLAVOR = "_flavor_"
"""The synthetic install's flavor folder (a name, not a flavor; L6)."""

REFUSAL_ERRNOS = {errno.ELOOP, errno.ENOTDIR, errno.EMLINK}
"""What an `O_NOFOLLOW` open of a link gives: `ELOOP` (Linux, macOS), and
`ENOTDIR` or `EMLINK` (BSDs) when `O_DIRECTORY` is also asked."""

O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)
O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)
O_NOFOLLOW_REAL = bool(O_NOFOLLOW) and bool(O_DIRECTORY)
"""The platform has both flags (POSIX). Read here, at import, before any
grader sets `snapshot._O_NOFOLLOW` to 0."""

needs_symlink = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)

MARKER = "M16-49 not implemented"


class Reader(NamedTuple):
    name: str
    rel: PurePosixPath
    """A real fixture, relative to the flavor folder."""
    read: Callable[[Path | str, Path | str], Any]
    """The reader, given the path and the install root."""
    to_bytes: Callable[[Any], bytes]
    plain: Callable[[Path], Any]
    """The reader with no root (M16-26's final-component check only)."""
    decoy: PurePosixPath
    """A different real capture, relative to the flavor folder, that this
    reader also parses: what the race grader puts behind its link."""


READERS = [
    Reader(
        "luadata.read",
        PurePosixPath("WTF/Account/90000001#6/SavedVariables/Blizzard_AuctionHouseUI.lua"),
        lambda p, root: luadata.read(p, root=root),
        lambda doc: luadata.serialize(doc),
        lambda p: luadata.read(p),
        PurePosixPath("WTF/Account/90000001#6/SavedVariables/Syndicator.lua"),
    ),
    Reader(
        "wtfconfig.read_config",
        PurePosixPath("WTF/Config.wtf"),
        lambda p, root: wtfconfig.read_config(p, root=root),
        lambda doc: doc.to_bytes(),
        lambda p: wtfconfig.read_config(p),
        PurePosixPath("WTF/Account/90000001#6/config-cache.wtf"),
    ),
    Reader(
        "wtfconfig.read_bindings",
        PurePosixPath("WTF/Account/90000001#6/bindings-cache.wtf"),
        lambda p, root: wtfconfig.read_bindings(p, root=root),
        lambda doc: doc.to_bytes(),
        lambda p: wtfconfig.read_bindings(p),
        PurePosixPath("WTF/Account/90000001#6/1/Labcharb-Labrealmd/config-cache.wtf"),
    ),
    Reader(
        "wtfconfig.read_macros",
        PurePosixPath("WTF/Account/90000001#6/1/Labcharb-Labrealmd/macros-cache.txt"),
        lambda p, root: wtfconfig.read_macros(p, root=root),
        lambda doc: doc.to_bytes(),
        lambda p: wtfconfig.read_macros(p),
        PurePosixPath("WTF/Account/90000001#6/edit-mode-cache-account.txt"),
    ),
]
SV = READERS[0]

PARSE: dict[str, Callable[[bytes], Any]] = {
    "luadata.read": luadata.parse,
    "wtfconfig.read_config": wtfconfig.parse_config,
    "wtfconfig.read_bindings": wtfconfig.parse_bindings,
    "wtfconfig.read_macros": wtfconfig.parse_macros,
}


def _ids(prefix: str) -> list[str]:
    return [f"{prefix}-{r.name}" for r in READERS]


def _components(reader: Reader) -> tuple[str, ...]:
    """The path's components below the install root."""
    return (FLAVOR, *reader.rel.parts)


class Case(NamedTuple):
    reader: Reader
    level: int
    """Index into `_components(reader)` of the directory that is a link."""
    where: str
    """`out-relative` (`..`), `out-absolute`, or `inside`."""


def _parent_cases(wheres: tuple[str, ...]) -> list[Any]:
    """Every (reader, parent directory, target kind): the flavor folder and
    every directory between it and the file."""
    cases = []
    for reader in READERS:
        comps = _components(reader)
        for level in range(len(comps) - 1):
            for where in wheres:
                below = "/".join(comps[: level + 1])
                cases.append(
                    pytest.param(
                        Case(reader, level, where),
                        id=f"constructed-parent-link-{where}-{reader.name}-{below}",
                    )
                )
    return cases


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A synthetic install root, with one flavor folder made on demand."""
    install = tmp_path / "Install"
    install.mkdir()
    return install


def _fixture_bytes(reader: Reader) -> bytes:
    data = CAPTURE.joinpath(*reader.rel.parts).read_bytes()
    assert data, f"{reader.rel} is an empty fixture; pick one with content"
    return data


def _copy_in(root: Path, reader: Reader) -> tuple[Path, bytes]:
    """The real capture at its place in the install, no link anywhere."""
    path = root.joinpath(*_components(reader))
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _fixture_bytes(reader)
    shutil.copyfile(CAPTURE.joinpath(*reader.rel.parts), path)
    return path, data


class Linked(NamedTuple):
    path: Path
    """The file's path as the caller spells it: through the link."""
    link: Path
    target_file: Path
    data: bytes


def _junction(link: Path, target: Path) -> None:
    """A real NTFS junction, as `test_snapshot_junctions.py` makes one."""
    import _winapi  # Windows only; the callers are skipped elsewhere

    _winapi.CreateJunction(str(target), str(link))


def _linked(root: Path, case: Case) -> Linked:
    """Make the directory at `case.level` a link to a directory holding the
    rest of the path, with a sound copy of the real capture at the end, so
    that following the link reads and parses. `case.where` is
    `out-relative`, `out-absolute` or `inside` (symbolic links), or
    `junction-out` / `junction-inside` (NTFS junctions, Windows only)."""
    comps = _components(case.reader)
    link = root.joinpath(*comps[: case.level + 1])
    rest = comps[case.level + 1 :]
    stays_inside = case.where.endswith("inside")
    if stays_inside:
        target_dir = link.parent / f"{link.name}.inside"
    else:
        target_dir = root.parent / "Elsewhere" / link.name
    target_file = target_dir.joinpath(*rest)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    data = _fixture_bytes(case.reader)
    shutil.copyfile(CAPTURE.joinpath(*case.reader.rel.parts), target_file)

    link.parent.mkdir(parents=True, exist_ok=True)
    if case.where.startswith("junction"):
        _junction(link, target_dir.resolve())
    elif case.where == "out-absolute":
        link.symlink_to(target_dir.resolve(), target_is_directory=True)
    else:
        link.symlink_to(os.path.relpath(target_dir, link.parent), target_is_directory=True)

    path = root.joinpath(*comps)
    inside = target_dir.resolve().is_relative_to(root.resolve())
    assert inside == stays_inside, (case.where, target_dir)
    if case.where == "out-relative":
        assert link.readlink().parts[0] == "..", link.readlink()
    assert path.read_bytes() == data, "the constructed path reaches the copy through the link"
    return Linked(path, link, target_file, data)


def _final_linked(root: Path, reader: Reader, where: str) -> Path:
    """Make the file itself a relative link, to a sound copy of the capture
    out of the install (`out`) or beside it inside (`inside`)."""
    path = root.joinpath(*_components(reader))
    path.parent.mkdir(parents=True)
    if where == "out":
        target = root.parent / "Elsewhere" / path.name
        target.parent.mkdir()
    else:
        target = path.parent / f"{path.name}.inside"
    shutil.copyfile(CAPTURE.joinpath(*reader.rel.parts), target)
    path.symlink_to(os.path.relpath(target, path.parent))
    assert path.read_bytes() == _fixture_bytes(reader)
    return path


class OpenSpy:
    """Records every `os.open` call: (path, flags, dir_fd)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, int, int | None]] = []
        real = os.open

        def spy(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            self.calls.append((os.fspath(path), flags, dir_fd))
            return real(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", spy)

    def assert_no_dir_fd(self) -> None:
        with_dir_fd = [(p, d) for p, _, d in self.calls if d is not None]
        assert not with_dir_fd, (
            f"with snapshot._O_NOFOLLOW == 0 the walk uses no dir_fd; saw {with_dir_fd}"
        )


def _open_fds() -> set[int] | None:
    """This process's open descriptors, where the platform lists them."""
    for listing in (Path("/proc/self/fd"), Path("/dev/fd")):
        if listing.is_dir():
            fds = set()
            for entry in list(listing.iterdir()):
                fd = int(entry.name)
                try:
                    os.fstat(fd)
                except OSError:
                    continue  # the listing's own descriptor, closed by now
                fds.add(fd)
            return fds
    return None


class FdWatch:
    def __init__(self) -> None:
        self.before = _open_fds()

    def assert_none_left(self) -> None:
        if self.before is None:
            return
        after = _open_fds()
        assert after is not None
        assert after <= self.before, f"descriptors left open: {sorted(after - self.before)}"


@pytest.fixture
def fds() -> Iterator[FdWatch]:
    yield FdWatch()


def _assert_refused(exc: BaseException, reader: Reader) -> None:
    assert isinstance(exc, OSError), f"{reader.name}: expected an OSError, got {exc!r}"
    assert not isinstance(exc, FileNotFoundError), (
        f"{reader.name}: the path exists through the link; a missing-file error is the wrong reason"
    )
    assert isinstance(exc, UnsafeReadError) or exc.errno in REFUSAL_ERRNOS, (
        f"{reader.name}: expected UnsafeReadError or ELOOP/ENOTDIR/EMLINK, got {exc!r}"
    )


# ─── the constructed trees are sound (passes today, no marker) ───────────────


@needs_symlink
@pytest.mark.parametrize("case", _parent_cases(("out-relative", "out-absolute", "inside")))
def test_constructed_tree_parses_through_the_link(case: Case, root: Path) -> None:
    """The control for every refusal below: the bytes behind each link are a
    real capture that parses, so a reader that follows the link returns a
    document, and only a refusal makes the graders pass. The reader called
    with no root (M16-26's final-component check, which M16-49 keeps for
    callers outside any install) follows the parent link today and after."""
    linked = _linked(root, case)
    document = PARSE[case.reader.name](linked.path.read_bytes())
    assert case.reader.to_bytes(document) == linked.data
    assert case.reader.to_bytes(case.reader.plain(linked.path)) == linked.data


# ─── a regular path reads byte for byte, with the root given ────────────────


@pytest.mark.parametrize("reader", READERS, ids=_ids("real-from-root"))
def test_a_regular_path_reads_byte_for_byte_from_the_root(
    reader: Reader, root: Path, fds: FdWatch
) -> None:
    path, data = _copy_in(root, reader)
    before = path.stat()

    document = reader.read(path, root)

    assert reader.to_bytes(document) == data, "the document keeps the file's bytes (L4)"
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns), (
        "the read changed nothing (L1)"
    )
    fds.assert_none_left()


def test_real_luadata_read_from_the_root_takes_str_paths(root: Path) -> None:
    """`luadata.read` is typed `str | Path`; a `str` path and root still work."""
    path, data = _copy_in(root, SV)
    document = luadata.read(str(path), root=str(root))
    assert luadata.serialize(document) == data


@needs_symlink
@pytest.mark.parametrize("reader", READERS, ids=_ids("real-unrelated-link-constructed"))
def test_a_link_off_the_path_does_not_stop_a_regular_read(
    reader: Reader, root: Path, tmp_path: Path
) -> None:
    """A link elsewhere in the install, out of it, beside the file's own
    folders, is not on the path and does not matter."""
    path, data = _copy_in(root, reader)
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()
    (path.parent / "Linked").symlink_to(elsewhere, target_is_directory=True)
    (root / FLAVOR / "Linked").symlink_to(elsewhere, target_is_directory=True)

    assert reader.to_bytes(reader.read(path, root)) == data


@needs_symlink
@pytest.mark.parametrize("reader", READERS, ids=_ids("real-root-through-a-link-constructed"))
def test_a_root_reached_through_a_link_reads(reader: Reader, root: Path, tmp_path: Path) -> None:
    """The walk starts at the root the caller gives; a link above it (macOS's
    `/var -> private/var`, a games folder on another drive) is the caller's
    choice, not a component of the path inside the install."""
    _, data = _copy_in(root, reader)
    alias = tmp_path / "Games"
    alias.symlink_to(root, target_is_directory=True)
    path = alias.joinpath(*_components(reader))

    assert reader.to_bytes(reader.read(path, alias)) == data


# ─── a parent-directory link is refused, out of the install or inside it ────


@needs_symlink
@pytest.mark.parametrize("case", _parent_cases(("out-relative", "out-absolute")))
def test_a_parent_link_out_of_the_install_is_refused(case: Case, root: Path, fds: FdWatch) -> None:
    """`WTF -> ../Elsewhere` and its kin: the read would leave the install."""
    linked = _linked(root, case)
    target_before = linked.target_file.read_bytes()
    link_before = linked.link.readlink()

    with pytest.raises(OSError) as caught:
        case.reader.read(linked.path, root)

    _assert_refused(caught.value, case.reader)
    assert linked.link.readlink() == link_before, "the link is left as it was"
    assert linked.target_file.read_bytes() == target_before, "the target is left as it was"
    fds.assert_none_left()


@needs_symlink
@pytest.mark.parametrize("case", _parent_cases(("inside",)))
def test_a_parent_link_inside_the_install_is_refused(case: Case, root: Path, fds: FdWatch) -> None:
    """`tree` reports any link as "link, not followed", wherever it points;
    the reader agrees, so what is read is what `tree` shows."""
    linked = _linked(root, case)

    with pytest.raises(OSError) as caught:
        case.reader.read(linked.path, root)

    _assert_refused(caught.value, case.reader)
    assert linked.link.is_symlink(), "the link is left as it was"
    fds.assert_none_left()


# ─── without O_NOFOLLOW (the Windows path, run on POSIX) ─────────────────────
#
# Seam: the walk reads `snapshot._O_NOFOLLOW` at call time, and with it 0
# uses no `dir_fd` (Windows' `os.open` takes none): no `os.open` call carries
# one. It then checks each component with `lstat` and opens by path, so a
# window between check and open remains on Windows; these graders cannot
# close it, and M16-49's report names it.


def _no_o_nofollow_cases() -> list[Any]:
    cases = _parent_cases(("out-relative", "out-absolute", "inside"))
    for reader in READERS:
        for where in ("out", "inside"):
            cases.append(
                pytest.param(
                    (reader, where),
                    id=f"constructed-final-link-{where}-{reader.name}",
                )
            )
    return cases


@needs_symlink
@pytest.mark.parametrize("case", _no_o_nofollow_cases())
def test_without_o_nofollow_a_link_is_refused_and_no_dir_fd_is_used(
    case: Case | tuple[Reader, str], root: Path, monkeypatch: pytest.MonkeyPatch, fds: FdWatch
) -> None:
    """The Windows path of the M11-11 helper (no `O_NOFOLLOW`), exercised on
    POSIX: a parent link (out by `..`, out absolute, or inside) and a final
    link are refused there too, through no `dir_fd`, and no descriptor is
    left open."""
    monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    if isinstance(case, Case):
        reader, path = case.reader, _linked(root, case).path
    else:
        reader, where = case
        path = _final_linked(root, reader, where)
    spy = OpenSpy(monkeypatch)

    with pytest.raises(OSError) as caught:
        reader.read(path, root)

    _assert_refused(caught.value, reader)
    spy.assert_no_dir_fd()
    fds.assert_none_left()


@pytest.mark.parametrize("reader", READERS, ids=_ids("real-no-o-nofollow"))
def test_without_o_nofollow_a_regular_path_reads_with_no_dir_fd(
    reader: Reader, root: Path, monkeypatch: pytest.MonkeyPatch, fds: FdWatch
) -> None:
    """The positive control for the seam: the file is opened, and read byte
    for byte, without any `os.open` that carries a `dir_fd`."""
    monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    path, data = _copy_in(root, reader)
    spy = OpenSpy(monkeypatch)

    assert reader.to_bytes(reader.read(path, root)) == data

    assert spy.calls, "the file was opened"
    spy.assert_no_dir_fd()
    fds.assert_none_left()


# ─── a final-component link is still refused with the root given ─────────────


@needs_symlink
@pytest.mark.parametrize("where", ["out", "inside"])
@pytest.mark.parametrize("reader", READERS, ids=_ids("constructed-final-link"))
def test_a_final_link_is_still_refused_from_the_root(
    reader: Reader, where: str, root: Path, fds: FdWatch
) -> None:
    path = _final_linked(root, reader, where)

    with pytest.raises(OSError) as caught:
        reader.read(path, root)

    _assert_refused(caught.value, reader)
    assert path.is_symlink(), "the link is left as it was"
    fds.assert_none_left()


# ─── a `..` component is refused (constructed) ──────────────────────────────


def _assert_shape_refused(exc: BaseException, reader: Reader, spy: OpenSpy) -> None:
    """A path-shape refusal: `UnsafeReadError`, raised before any open."""
    assert isinstance(exc, UnsafeReadError), (
        f"{reader.name}: a path-shape refusal is UnsafeReadError, got {exc!r}"
    )
    assert spy.calls == [], f"{reader.name}: opened {spy.calls} before refusing the path"


DOTDOT_SHAPES = [
    pytest.param("climbs-out", id="climbs-out"),
    pytest.param("under-root-resolves-out", id="under-root-resolves-out", marks=needs_symlink),
    pytest.param("resolves-back-inside", id="resolves-back-inside"),
]


@pytest.mark.parametrize("shape", DOTDOT_SHAPES)
@pytest.mark.parametrize("reader", READERS, ids=_ids("constructed-dotdot"))
def test_a_dotdot_component_is_refused(
    reader: Reader, shape: str, root: Path, monkeypatch: pytest.MonkeyPatch, fds: FdWatch
) -> None:
    """`climbs-out`: `root/_flavor_/WTF/../../../Elsewhere/...` leaves the root
    lexically. `under-root-resolves-out`: `root/_flavor_/Linked/../...` stays
    under the root lexically, but `Linked` points out, so the kernel's `..`
    lands outside, on a decoy capture (a lexical normalizer would read the
    original inside instead; it is refused all the same).
    `resolves-back-inside`: `root/_flavor_/WTF/../...` names the original
    file, with no link anywhere. Every `..` below the root is refused with
    `UnsafeReadError` before anything is opened."""
    original, data = _copy_in(root, reader)
    comps = _components(reader)
    elsewhere = root.parent / "Elsewhere"
    if shape == "climbs-out":
        outside = elsewhere.joinpath(*comps)
        outside.parent.mkdir(parents=True)
        shutil.copyfile(original, outside)
        path = root / FLAVOR / "WTF" / ".." / ".." / ".." / "Elsewhere"
        path = path.joinpath(*comps)
    elif shape == "under-root-resolves-out":
        decoy = elsewhere.joinpath(*reader.rel.parts)
        decoy.parent.mkdir(parents=True)
        shutil.copyfile(CAPTURE.joinpath(*reader.decoy.parts), decoy)
        (elsewhere / "deep").mkdir()
        (root / FLAVOR / "Linked").symlink_to(elsewhere / "deep", target_is_directory=True)
        path = (root / FLAVOR / "Linked" / "..").joinpath(*reader.rel.parts)
        assert path.read_bytes() == decoy.read_bytes() != data, "the kernel's `..` lands outside"
    else:
        path = (root / FLAVOR / "WTF" / "..").joinpath(*reader.rel.parts)
    assert ".." in path.parts
    assert path.is_file(), "the path names a real file; only its shape is wrong"
    spy = OpenSpy(monkeypatch)

    with pytest.raises(OSError) as caught:
        reader.read(path, root)

    _assert_shape_refused(caught.value, reader, spy)
    fds.assert_none_left()


# ─── a path not under the root is refused (constructed) ─────────────────────


NOT_UNDER = [
    pytest.param("elsewhere", id="elsewhere"),
    pytest.param(
        "elsewhere-through-a-parent-link", id="elsewhere-parent-link", marks=needs_symlink
    ),
    pytest.param("root-in-another-case", id="root-in-another-case"),
    pytest.param("root-a-string-prefix", id="root-a-string-prefix"),
]


@pytest.mark.parametrize("shape", NOT_UNDER)
@pytest.mark.parametrize("reader", READERS, ids=_ids("constructed-not-under-root"))
def test_a_path_not_under_the_root_is_refused(
    reader: Reader, shape: str, root: Path, monkeypatch: pytest.MonkeyPatch, fds: FdWatch
) -> None:
    """With `root=` given, a path that is not under it is refused with
    `UnsafeReadError` before anything is opened, never read with M16-26's
    final-component check instead. `root-in-another-case` gives the root as
    `install` for a path under `Install`. On POSIX it is refused on every
    volume, also where the two name one directory (decision: the prefix is
    compared by its components, not proved the same by device and inode).
    On Windows `PureWindowsPath` compares without case, so the two are the
    same prefix there: a refusal or a byte-for-byte read are both accepted.
    `root-a-string-prefix` gives `Inst` for a path under `Install`."""
    original, data = _copy_in(root, reader)
    comps = _components(reader)
    given_root = root
    if shape.startswith("elsewhere"):
        out = root.parent / "Out"
        path = out.joinpath(*comps)
        if shape == "elsewhere":
            path.parent.mkdir(parents=True)
            shutil.copyfile(original, path)
        else:
            target = root.parent / "Elsewhere" / "WTF"
            target.joinpath(*reader.rel.parts[1:]).parent.mkdir(parents=True)
            shutil.copyfile(original, target.joinpath(*reader.rel.parts[1:]))
            (out / FLAVOR).mkdir(parents=True)
            (out / FLAVOR / "WTF").symlink_to(target, target_is_directory=True)
    elif shape == "root-in-another-case":
        path = original
        given_root = root.parent / root.name.lower()
        assert given_root.name != root.name
    else:
        path = original
        given_root = root.parent / root.name[:4]
        assert str(path).startswith(str(given_root))
    assert path.is_file(), "the path names a real file; only where it is is wrong"
    spy = OpenSpy(monkeypatch)

    if shape == "root-in-another-case" and sys.platform == "win32":
        try:
            document = reader.read(path, given_root)
        except OSError as exc:
            _assert_shape_refused(exc, reader, spy)
        else:
            assert reader.to_bytes(document) == data, "read as the same root, byte for byte"
        fds.assert_none_left()
        return

    with pytest.raises(OSError) as caught:
        reader.read(path, given_root)

    _assert_shape_refused(caught.value, reader, spy)
    fds.assert_none_left()


@pytest.mark.parametrize("spelling", ["trailing-separator", "trailing-dot"])
@pytest.mark.parametrize("reader", READERS, ids=_ids("real-root-spelled"))
def test_a_root_spelled_with_a_trailing_separator_or_dot_reads(
    reader: Reader, spelling: str, root: Path
) -> None:
    """The positive control for the prefix rule: `Install/` and `Install/.`
    given as `str` are the same root as `pathlib` spells it, and read."""
    path, data = _copy_in(root, reader)
    given = str(root) + os.sep if spelling == "trailing-separator" else str(root) + os.sep + "."
    assert Path(given) == root

    assert reader.to_bytes(reader.read(path, given)) == data


# ─── a parent swapped for a link mid-read (POSIX race, constructed) ──────────


def _race_cases() -> list[Any]:
    """(reader, parent level to swap, call number at which to swap, whether
    the link is swapped back out): the flavor folder, `WTF` and the file's
    own folder, swapped before the reader's 1st, 2nd, ... `os.open` /
    `os.lstat` / `os.stat` call, up to two more calls than the path has
    components; `stay` leaves the link there, `back` restores the folder
    before the next call (so a re-check after an open sees the original)."""
    cases = []
    for reader in READERS:
        comps = _components(reader)
        for level in sorted({0, 1, len(comps) - 2}):
            for when in range(1, len(comps) + 3):
                for mode in ("stay", "back"):
                    below = "/".join(comps[: level + 1])
                    cases.append(
                        pytest.param(
                            reader,
                            level,
                            when,
                            mode,
                            id=f"constructed-race-{mode}-{reader.name}-{below}-call{when}",
                        )
                    )
    return cases


@needs_symlink
@pytest.mark.parametrize(("reader", "level", "when", "mode"), _race_cases())
def test_a_parent_swapped_for_a_link_mid_read_never_reads_outside(
    reader: Reader,
    level: int,
    when: int,
    mode: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    fds: FdWatch,
) -> None:
    """Just before the reader's `when`-th `os.open`, `os.lstat` or `os.stat`
    call, the parent folder at `level` is renamed away and a link to a
    folder outside the install takes its place, holding a decoy (a different
    real capture) at the same relative path. With `back`, the link is
    removed and the folder renamed back just before the next such call. The
    read must refuse, or return the original file's bytes; it must never
    return the decoy, and it leaves no descriptor open. A walk that holds
    each folder's descriptor and opens the next by name with `O_NOFOLLOW`
    passes; checking each folder and then opening the whole path does not,
    with or without a re-check after the open."""
    original, data = _copy_in(root, reader)
    comps = _components(reader)
    swapped = root.joinpath(*comps[: level + 1])
    target_dir = root.parent / "Elsewhere" / swapped.name
    decoy_file = target_dir.joinpath(*comps[level + 1 :])
    decoy_file.parent.mkdir(parents=True)
    shutil.copyfile(CAPTURE.joinpath(*reader.decoy.parts), decoy_file)
    decoy = decoy_file.read_bytes()
    assert decoy != data, "the decoy is a different capture"

    state = {"calls": 0, "armed": True, "done": False}
    aside = swapped.with_name(swapped.name + ".real")

    def swap_in() -> None:
        swapped.rename(aside)
        swapped.symlink_to(target_dir, target_is_directory=True)

    def swap_back() -> None:
        swapped.unlink()
        aside.rename(swapped)

    def counted(real: Callable[..., Any]) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            if state["armed"] and not state["done"]:
                state["calls"] += 1
                if state["calls"] == when:
                    swap_in()
                    state["done"] = mode == "stay"
                elif state["calls"] == when + 1:
                    swap_back()
                    state["done"] = True
            return real(*args, **kwargs)

        return call

    for name in ("open", "lstat", "stat"):
        monkeypatch.setattr(os, name, counted(getattr(os, name)))

    try:
        document = reader.read(original, root)
    except OSError as exc:
        state["armed"] = False
        _assert_refused(exc, reader)
        fds.assert_none_left()
        return
    finally:
        state["armed"] = False
    got = reader.to_bytes(document)
    assert got != decoy, (
        f"{reader.name} returned the capture behind the swapped-in link "
        f"(swapped in before call {when}, {mode})"
    )
    assert got == data, "the read returned the original file's bytes"
    fds.assert_none_left()


# ─── the walk's shape, POSIX with the real O_NOFOLLOW ───────────────────────


class ChainSpy:
    """Records every `os.open` call: (path, flags, dir_fd, returned fd)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, int, int | None, int]] = []
        real = os.open

        def spy(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            fd = real(path, flags, mode, dir_fd=dir_fd)
            self.calls.append((os.fspath(path), flags, dir_fd, fd))
            return fd

        monkeypatch.setattr(os, "open", spy)


@pytest.mark.skipif(not O_NOFOLLOW_REAL, reason="needs O_NOFOLLOW and O_DIRECTORY (POSIX)")
@pytest.mark.parametrize("reader", READERS, ids=_ids("real-walk-shape"))
def test_the_walk_opens_each_component_from_the_one_before(
    reader: Reader, root: Path, monkeypatch: pytest.MonkeyPatch, fds: FdWatch
) -> None:
    """With the real `O_NOFOLLOW`: the root is opened once, by path, with no
    `dir_fd`; every later `os.open` names one component, relative to the
    descriptor the open before it returned (the first to the root's), in the
    path's order, with `O_NOFOLLOW`; each folder with `O_DIRECTORY`, the file
    without it. Nothing else is opened. A walk that checks by path and opens
    the whole path, with or without a re-check, fails this whatever the
    timing of a race."""
    path, data = _copy_in(root, reader)
    spy = ChainSpy(monkeypatch)

    assert reader.to_bytes(reader.read(path, root)) == data

    comps = _components(reader)
    calls = spy.calls
    assert len(calls) == 1 + len(comps), (
        f"expected the root and {len(comps)} components opened, saw {[c[0] for c in calls]}"
    )
    first_path, _, first_dir_fd, previous = calls[0]
    assert Path(first_path) == root, f"the first open is the root, by path: {calls[0]}"
    assert first_dir_fd is None, f"the root is opened by path, with no dir_fd: {calls[0]}"
    for i, (name, flags, dir_fd, fd) in enumerate(calls[1:]):
        assert name == comps[i], f"open {i + 1} names {name!r}, expected {comps[i]!r}"
        assert dir_fd == previous, (
            f"{name!r} is opened relative to the descriptor before it ({previous}), not {dir_fd}"
        )
        assert flags & O_NOFOLLOW == O_NOFOLLOW, f"{name!r} is opened with O_NOFOLLOW"
        is_dir = i < len(comps) - 1
        assert bool(flags & O_DIRECTORY) == is_dir, (
            f"{name!r}: O_DIRECTORY on every folder and not on the file"
        )
        previous = fd
    fds.assert_none_left()


# ─── Windows: a real junction at a parent level is refused ──────────────────


windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="NTFS junctions exist only on Windows"
)


@windows_only
@pytest.mark.parametrize("case", _parent_cases(("junction-out", "junction-inside")))
def test_a_parent_junction_is_refused(case: Case, root: Path, fds: FdWatch) -> None:
    """A junction `lstat`s as a directory in CPython; the walk must still
    refuse it, out of the install or inside it, as `tree` reports it."""
    linked = _linked(root, case)

    with pytest.raises(OSError) as caught:
        case.reader.read(linked.path, root)

    _assert_refused(caught.value, case.reader)
    assert linked.link.is_junction(), "the junction is left as it was"
    fds.assert_none_left()
