"""Discovery opens `.flavor.info` without following a link (M13-46).

Constructed (L8, boundary): a race cannot be captured, so each test swaps a
real `.flavor.info` (`fixtures/macos/forever/.flavor.info`) for a link, or
for another file, at one point between the check and the read, by wrapping
the call that comes just before that point. The folder is then no flavor:
it is named in `other_dirs` and nothing is read through the link.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import install as install_mod
from wowlab_core import snapshot
from wowlab_core.install import read_install

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "macos"
REAL_BUILD_INFO = (FIXTURES / ".build.info").read_bytes()
REAL_FLAVOR_INFO = (FIXTURES / "forever" / ".flavor.info").read_bytes()
REAL_FOLDER = "_classic_beta_"  # the flavor column of the fixture's provenance row
RACING = "_racing_"


def _install(base: Path) -> Path:
    root = base / "World of Warcraft"
    root.mkdir(parents=True)
    (root / ".build.info").write_bytes(REAL_BUILD_INFO)
    for folder in (REAL_FOLDER, RACING):
        (root / folder).mkdir()
        (root / folder / ".flavor.info").write_bytes(REAL_FLAVOR_INFO)
    return root


def _can_symlink(base: Path) -> None:
    probe = base / "m13-46-probe"
    try:
        probe.symlink_to(base / "m13-46-target")
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    probe.unlink()


def _to_link(info: Path, target: Path) -> None:
    info.unlink()
    info.symlink_to(target)


def _to_other_file(info: Path, target: Path) -> None:
    """A regular file again, but not the one the `lstat` saw."""
    del target
    info.unlink()
    info.write_bytes(REAL_FLAVOR_INFO)


_SWAPS = [
    pytest.param(_to_link, id="constructed-link"),
    pytest.param(_to_other_file, id="constructed-other-file"),
]


@pytest.fixture
def scene(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(install root, the racing `.flavor.info`, the link target outside)."""
    _can_symlink(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = elsewhere / ".flavor.info"
    # Constructed: a product no real folder names, so a read through the
    # link would show as a flavor with it.
    target.write_bytes(b"Product Flavor!STRING:0\nm13-46-elsewhere\n")
    root = _install(tmp_path / "install")
    return root, root / RACING / ".flavor.info", target


def _assert_refused(root: Path) -> None:
    got = read_install(root)
    assert [f.folder for f in got.flavors] == [REAL_FOLDER]
    assert got.other_dirs == (RACING,)
    assert all(f.product != "m13-46-elsewhere" for f in got.flavors)


def test_a_real_file_is_still_read_constructed(scene: tuple[Path, Path, Path]) -> None:
    """No behaviour change for a real file: both folders are flavors."""
    root, _, _ = scene
    got = read_install(root)
    assert [f.folder for f in got.flavors] == [REAL_FOLDER, RACING]
    assert got.flavors[0].product == got.flavors[1].product != ""
    assert got.other_dirs == ()


@pytest.mark.parametrize("swap", _SWAPS)
def test_swapped_after_the_scan_is_refused(
    scene: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    swap: Callable[[Path, Path], None],
) -> None:
    """The scan saw a regular `.flavor.info`; by the read it is a link (or
    another file, which is read as the file it now is: no link was taken)."""
    root, info, target = scene
    real_scan = install_mod._scan_children

    def scan(where: Path) -> Any:
        found = real_scan(where)
        swap(info, target)
        return found

    monkeypatch.setattr(install_mod, "_scan_children", scan)

    if swap is _to_link:
        _assert_refused(root)
    else:
        got = read_install(root)
        assert [f.folder for f in got.flavors] == [REAL_FOLDER, RACING]


@pytest.mark.parametrize("nofollow", [True, False], ids=["o-nofollow", "no-o-nofollow"])
@pytest.mark.parametrize("swap", _SWAPS)
def test_swapped_between_the_lstat_and_the_open_is_refused(
    scene: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    swap: Callable[[Path, Path], None],
    nofollow: bool,
) -> None:
    """The read's own `lstat` saw a regular file; the open then meets a link
    (refused by `O_NOFOLLOW`, or where the platform has none, as on Windows,
    by the `lstat` taken just before the open) or a different file (refused
    by the identity check against that `lstat`)."""
    root, info, target = scene
    if not nofollow:
        monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    real_open = snapshot.open_regular_file
    opened: list[str] = []

    def open_regular_file(path: Path, **kwargs: Any) -> tuple[int, os.stat_result]:
        if path == info:
            swap(info, target)
        fd, st = real_open(path, **kwargs)
        opened.append(str(path))
        return fd, st

    monkeypatch.setattr(snapshot, "open_regular_file", open_regular_file)

    _assert_refused(root)
    assert str(info) not in opened  # no descriptor was handed back for it
