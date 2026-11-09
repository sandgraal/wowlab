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
  caller's choice; the walk starts there).

The seam (M16-49's to fill): each reader takes the install root as a keyword
argument `root`, and the path stays the full path to the file, inside that
root: `luadata.read(path, root=root)`, `wtfconfig.read_config(path,
root=root)`, `read_bindings`, `read_macros` likewise. Today that keyword does
not exist, so every marked grader fails with `TypeError`; the marker names
`raises=TypeError` so a partial implementation (the keyword accepted, the
link still followed) shows as a failure, not an expected one.

Callers with no install root (`sv dump <path>` outside any install) keep
M16-26's final-component check; that is graded by M16-26T's file
(`test_parser_reads_regular_file_m16_26.py`) and not repeated here.

The bytes read are real captures (`fixtures/macos/forever/`, rows in
`fixtures/README.md`, L8) copied into a synthetic install under `tmp_path`;
the links and the trees they point at are constructed (ids say
`constructed`). Every link's target holds a sound copy of the same capture,
so a reader that follows the link parses it and returns, and the grader
fails on that rather than on some unrelated error. Link cases are skipped on
Windows, where making a symbolic link needs a privilege (the ticket allows a
skip there). Nothing here touches a real install.
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

needs_symlink = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)

MARKER = "M16-49 not implemented"


class Reader(NamedTuple):
    name: str
    rel: PurePosixPath
    """A real fixture, relative to the flavor folder."""
    read: Callable[[Path | str, Path], Any]
    """The reader, given the path and the install root."""
    to_bytes: Callable[[Any], bytes]


READERS = [
    Reader(
        "luadata.read",
        PurePosixPath("WTF/Account/90000001#6/SavedVariables/Blizzard_AuctionHouseUI.lua"),
        lambda p, root: luadata.read(p, root=root),
        lambda doc: luadata.serialize(doc),
    ),
    Reader(
        "wtfconfig.read_config",
        PurePosixPath("WTF/Config.wtf"),
        lambda p, root: wtfconfig.read_config(p, root=root),
        lambda doc: doc.to_bytes(),
    ),
    Reader(
        "wtfconfig.read_bindings",
        PurePosixPath("WTF/Account/90000001#6/bindings-cache.wtf"),
        lambda p, root: wtfconfig.read_bindings(p, root=root),
        lambda doc: doc.to_bytes(),
    ),
    Reader(
        "wtfconfig.read_macros",
        PurePosixPath("WTF/Account/90000001#6/1/Labcharb-Labrealmd/macros-cache.txt"),
        lambda p, root: wtfconfig.read_macros(p, root=root),
        lambda doc: doc.to_bytes(),
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


def _linked(root: Path, case: Case) -> Linked:
    """Make the directory at `case.level` a link to a directory holding the
    rest of the path, with a sound copy of the real capture at the end, so
    that following the link reads and parses."""
    comps = _components(case.reader)
    link = root.joinpath(*comps[: case.level + 1])
    rest = comps[case.level + 1 :]
    if case.where == "inside":
        target_dir = link.parent / f"{link.name}.inside"
    else:
        target_dir = root.parent / "Elsewhere" / link.name
    target_file = target_dir.joinpath(*rest)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    data = _fixture_bytes(case.reader)
    shutil.copyfile(CAPTURE.joinpath(*case.reader.rel.parts), target_file)

    link.parent.mkdir(parents=True, exist_ok=True)
    if case.where == "out-absolute":
        link.symlink_to(target_dir.resolve(), target_is_directory=True)
    else:
        link.symlink_to(os.path.relpath(target_dir, link.parent), target_is_directory=True)

    path = root.joinpath(*comps)
    inside = target_dir.resolve().is_relative_to(root.resolve())
    assert inside == (case.where == "inside"), (case.where, target_dir)
    if case.where == "out-relative":
        assert link.readlink().parts[0] == "..", link.readlink()
    assert path.read_bytes() == data, "the constructed path reaches the copy through the link"
    return Linked(path, link, target_file, data)


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
    document, and only a refusal makes the graders pass."""
    linked = _linked(root, case)
    document = PARSE[case.reader.name](linked.path.read_bytes())
    assert case.reader.to_bytes(document) == linked.data


# ─── a regular path reads byte for byte, with the root given ────────────────


@pytest.mark.xfail(strict=True, raises=TypeError, reason=MARKER)
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


@pytest.mark.xfail(strict=True, raises=TypeError, reason=MARKER)
def test_real_luadata_read_from_the_root_takes_str_paths(root: Path) -> None:
    """`luadata.read` is typed `str | Path`; a `str` path and root still work."""
    path, data = _copy_in(root, SV)
    document = luadata.read(str(path), root=str(root))
    assert luadata.serialize(document) == data


@needs_symlink
@pytest.mark.xfail(strict=True, raises=TypeError, reason=MARKER)
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
@pytest.mark.xfail(strict=True, raises=TypeError, reason=MARKER)
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
@pytest.mark.xfail(strict=True, raises=TypeError, reason=MARKER)
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
@pytest.mark.xfail(strict=True, raises=TypeError, reason=MARKER)
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


@needs_symlink
@pytest.mark.xfail(strict=True, raises=TypeError, reason=MARKER)
@pytest.mark.parametrize("case", _parent_cases(("out-relative",)))
def test_without_o_nofollow_a_parent_link_is_refused(
    case: Case, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows path of the M11-11 helper (no `O_NOFOLLOW`), exercised on
    POSIX: the parent link is refused there too."""
    monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    linked = _linked(root, case)

    with pytest.raises(OSError) as caught:
        case.reader.read(linked.path, root)

    _assert_refused(caught.value, case.reader)


# ─── a final-component link is still refused with the root given ─────────────


@needs_symlink
@pytest.mark.xfail(strict=True, raises=TypeError, reason=MARKER)
@pytest.mark.parametrize("where", ["out", "inside"])
@pytest.mark.parametrize("reader", READERS, ids=_ids("constructed-final-link"))
def test_a_final_link_is_still_refused_from_the_root(
    reader: Reader, where: str, root: Path, tmp_path: Path, fds: FdWatch
) -> None:
    comps = _components(reader)
    path = root.joinpath(*comps)
    path.parent.mkdir(parents=True)
    if where == "out":
        target = tmp_path / "Elsewhere" / path.name
        target.parent.mkdir()
    else:
        target = path.parent / f"{path.name}.inside"
    shutil.copyfile(CAPTURE.joinpath(*reader.rel.parts), target)
    path.symlink_to(os.path.relpath(target, path.parent))
    assert path.read_bytes() == _fixture_bytes(reader)

    with pytest.raises(OSError) as caught:
        reader.read(path, root)

    _assert_refused(caught.value, reader)
    assert path.is_symlink(), "the link is left as it was"
    fds.assert_none_left()
