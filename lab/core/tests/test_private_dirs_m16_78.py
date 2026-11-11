"""Every folder wowlab creates under the user data directory is private (M16-78).

docs/LAB_PLAN.md §6.10, amendment of 2026-11-11 (b): every folder wowlab
creates under the user data directory is made 0700 in the `mkdir` itself,
whatever the umask; the parents made on the way to a store given explicitly
outside it are 0700 too; a system folder above the user data directory is
not wowlab's and keeps its mode. `guard`'s folders (`locks/`, the store's
parents) are graded by M16-78T in `test_guard_private_dirs_m16_78.py`; this
file has one test per other module that creates a folder there (`gamedata/`,
`db2lake/`, `looks/`, `pages/`), the snapshot store's own parents, and the
helper's edges.

Each test judges two things: the mode of every folder made, by `lstat`
after the call, and the mode the creating `mkdir` asked for, through the
`os.mkdir` audit event, so a default-mode `mkdir` followed by a `chmod`
fails. Everything is constructed (file modes are a boundary case, L8); the
wago tables are the recorded fixtures. The user data directory is redirected
into `tmp_path`. Modes are a POSIX matter: every test is skipped elsewhere.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

import platformdirs
import pytest

from wowlab_core import snapshot
from wowlab_core.db2lake import Lake
from wowlab_core.gamedata import GameData
from wowlab_core.looks import Look
from wowlab_core.lookspage import PAGE_HEADER, default_page_path, write_page
from wowlab_core.lookstore import LookStore, SavedLook
from wowlab_core.snapshot import SnapshotStore, make_private_dirs

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="file modes are a POSIX matter; Windows has no mode bits"
)

WAGO = Path(__file__).resolve().parent / "fixtures" / "wago"
LAKE_BUILD = "1.60.1.70058"
LOOKS_BUILD = "1.60.1.70009"
PRIVATE = 0o700
MASKS = [0o022, 0o000]
PAGE = PAGE_HEADER.decode("ascii")


# ─── the watch ───────────────────────────────────────────────────────────────

_ACTIVE: list[dict[str, int]] = []
_IN_HOOK = threading.local()
_HOOKED: list[bool] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    """Records `path -> requested mode` for every `os.mkdir` by path while a
    watch is active; inert otherwise, and never raises."""
    if event != "os.mkdir" or not _ACTIVE or getattr(_IN_HOOK, "on", False):
        return
    _IN_HOOK.on = True
    try:
        target, mode, dir_fd = args[0], args[1], args[2]
        if isinstance(target, (str, bytes, os.PathLike)) and isinstance(mode, int):
            path = Path(os.fsdecode(os.fspath(target)))
            if dir_fd is None or dir_fd == -1 or path.is_absolute():
                for made in _ACTIVE:
                    made[str(path.absolute())] = mode
    except Exception:  # an exception out of an audit hook aborts the call
        pass
    finally:
        _IN_HOOK.on = False


@contextlib.contextmanager
def watching() -> Iterator[dict[str, int]]:
    if not _HOOKED:  # an audit hook cannot be removed: install it once
        sys.addaudithook(_audit)
        _HOOKED.append(True)
    made: dict[str, int] = {}
    _ACTIVE.append(made)
    try:
        yield made
    finally:
        _ACTIVE.remove(made)


@contextlib.contextmanager
def umask(mask: int) -> Iterator[None]:
    old = os.umask(mask)
    try:
        yield
    finally:
        os.umask(old)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def folders(root: Path) -> list[Path]:
    """`root` and every folder below it."""
    return [root, *sorted(p for p in root.rglob("*") if p.is_dir() and not p.is_symlink())]


def assert_private(made: dict[str, int], judged: list[Path]) -> None:
    """Each judged folder is 0700 and was made by a `mkdir` that asked for
    0700 itself."""
    assert judged, "control: there is something to judge"
    wrong = {str(p): oct(mode(p)) for p in judged if mode(p) != PRIVATE}
    assert not wrong, f"not 0o700 after the call: {wrong}"
    asked = {str(p): oct(made[str(p)] & 0o777) if str(p) in made else None for p in judged}
    assert asked == {str(p): oct(PRIVATE) for p in judged}, (
        "the mode each creating mkdir asked for (None: not made by a mkdir the watch saw)"
    )


# ─── the user data directory ─────────────────────────────────────────────────


@pytest.fixture
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user data directory, redirected and missing; the platform's data
    directory that holds it exists, as on a real machine."""
    redirected = tmp_path / "share" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    redirected.parent.mkdir()
    return redirected


class _Recordings:
    """Serves the recorded `wago/<table>.<build>.csv`."""

    name = "fixtures"

    def fetch_builds(self, dest: BinaryIO) -> str:
        raise AssertionError("not needed")

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        with (WAGO / f"{table}.{build}.csv").open("rb") as handle:
            shutil.copyfileobj(handle, dest)
        return f"fixture:{table}.{build}.csv"


# ─── one test per module ─────────────────────────────────────────────────────


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_gamedata_cache_folders_are_0700_constructed(user_data: Path, mask: int) -> None:
    with umask(mask), watching() as made:
        path = GameData(_Recordings()).table("TraitTree", LAKE_BUILD)
    assert path.is_file() and user_data in path.parents  # control: cached by default
    assert (user_data / "gamedata").is_dir()
    assert_private(made, folders(user_data))


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_db2lake_folder_is_0700_constructed(user_data: Path, tmp_path: Path, mask: int) -> None:
    """The lake under the user data directory is private; the cache given
    explicitly outside it keeps the default mode (the rule covers only the
    user data directory, and a store's parents)."""
    cache = tmp_path / "mine" / "cache"
    with umask(mask), watching() as made:
        lake = Lake(LAKE_BUILD, gamedata=GameData(_Recordings(), cache_dir=cache))
        with lake:
            lake.load("TraitTree")
    assert lake.path.is_file() and lake.path.parent == user_data / "db2lake"  # control
    assert_private(made, folders(user_data))
    assert mode(tmp_path / "mine") == 0o777 & ~mask
    assert mode(cache) == 0o777 & ~mask


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_looks_folder_is_0700_constructed(user_data: Path, mask: int) -> None:
    saved = SavedLook(
        saved_build=LOOKS_BUILD, look=Look(name="mine", race_id=1, body_type=0, choices={9: 1})
    )
    with umask(mask), watching() as made:
        path = LookStore().save(saved)
    assert path.parent == user_data / "looks"  # control: the default folder
    assert_private(made, folders(user_data))


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_pages_folder_is_0700_constructed(user_data: Path, mask: int) -> None:
    with umask(mask), watching() as made:
        out = write_page(PAGE + "\n", default_page_path())
    assert out.parent == user_data / "pages"  # control: the default page
    assert_private(made, folders(user_data))


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_a_page_given_outside_the_user_data_directory_keeps_the_default_mode_constructed(
    user_data: Path, tmp_path: Path, mask: int
) -> None:
    with umask(mask):
        out = write_page(PAGE + "\n", tmp_path / "desk" / "top" / "looks.html")
    assert out.is_file()
    assert mode(tmp_path / "desk") == mode(tmp_path / "desk" / "top") == 0o777 & ~mask
    assert not user_data.exists()


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_snapshot_store_root_makes_its_missing_parents_0700_constructed(
    user_data: Path, tmp_path: Path, mask: int
) -> None:
    """`SnapshotStore` given a path whose parents are missing makes each
    0700 (LAB_PLAN §6.10 (b): the parents of a store given explicitly)."""
    root = tmp_path / "elsewhere" / "deep" / "store"
    with umask(mask), watching() as made:
        SnapshotStore(root).put_object(b"constructed object\n")
    assert root.is_dir()  # control
    # The folders below the root are made through its descriptor (M16-41's
    # graders judge them); every folder judged here was made by path.
    assert all(mode(p) == PRIVATE for p in folders(root))
    assert_private(made, [tmp_path / "elsewhere", tmp_path / "elsewhere" / "deep", root])
    assert not user_data.exists()


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_default_snapshot_store_makes_the_user_data_dir_0700_constructed(
    user_data: Path, mask: int
) -> None:
    with umask(mask), watching() as made:
        SnapshotStore(snapshot.default_store_path()).put_object(b"constructed object\n")
    assert (user_data / "store").is_dir()  # control
    assert all(mode(p) == PRIVATE for p in folders(user_data))
    assert_private(made, [user_data, user_data / "store"])


# ─── the helper's edges ──────────────────────────────────────────────────────


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_a_missing_system_folder_above_the_user_data_dir_keeps_the_default_mode_constructed(
    user_data: Path, mask: int
) -> None:
    """The platform's data directory is not wowlab's: made when missing,
    at the default mode; the user data directory and below are 0700."""
    user_data.parent.rmdir()
    with umask(mask), watching() as made:
        make_private_dirs(user_data / "gamedata")
    assert mode(user_data.parent) == 0o777 & ~mask
    assert made[str(user_data.parent)] == 0o777
    assert_private(made, [user_data, user_data / "gamedata"])


def test_a_umask_that_takes_owner_bits_still_gives_0700_constructed(user_data: Path) -> None:
    with umask(0o277), watching() as made:
        make_private_dirs(user_data / "looks")
    assert_private(made, [user_data, user_data / "looks"])


def test_an_existing_folder_is_left_as_it_is_constructed(user_data: Path) -> None:
    """Making a folder is not fixing one: an existing folder keeps its mode
    (a store made by an older version is fixed by the store itself, M16-41)."""
    user_data.mkdir(mode=0o755)
    user_data.chmod(0o755)
    with umask(0o022), watching() as made:
        make_private_dirs(user_data / "pages")
    assert mode(user_data) == 0o755
    assert_private(made, [user_data / "pages"])


def test_a_file_in_the_way_raises_as_path_mkdir_does_constructed(user_data: Path) -> None:
    user_data.mkdir(mode=PRIVATE)
    (user_data / "looks").write_bytes(b"")
    with pytest.raises(FileExistsError):
        make_private_dirs(user_data / "looks")
    with pytest.raises(NotADirectoryError):
        make_private_dirs(user_data / "looks" / "below")


@pytest.mark.parametrize("mask", MASKS, ids=oct)
def test_a_store_that_holds_the_user_data_dir_is_still_0700_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mask: int
) -> None:
    """A store given explicitly at a path that holds the user data directory
    (XDG_DATA_HOME=<store>/share) is wowlab's own folder, not the platform's
    data directory: it is made 0700 (#474 security review). A parent made on
    the way to it also holds the user data directory, and keeps the default
    mode, as the platform's data directory would."""
    store = tmp_path / "mine" / "store"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: store / "share" / "wowlab")
    with umask(mask), watching() as made:
        SnapshotStore(store).put_object(b"constructed object\n")
    assert_private(made, [store])
    assert mode(tmp_path / "mine") == 0o777 & ~mask


def test_a_dangling_link_above_the_folder_raises_as_path_mkdir_does_constructed(
    user_data: Path, tmp_path: Path
) -> None:
    """`Path.mkdir(parents=True, exist_ok=True)` says FileExistsError for a
    link to nothing on the way; so does the folder maker, and makes nothing."""
    dangling = tmp_path / "dang"
    dangling.symlink_to(tmp_path / "nowhere")
    with pytest.raises(FileExistsError):
        make_private_dirs(dangling / "store", outside=True)
    assert not (tmp_path / "nowhere").exists()


@pytest.mark.parametrize("private_on_windows", [False, True])
def test_off_posix_only_the_target_asked_for_gets_mode_0700_simulated_constructed(
    user_data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private_on_windows: bool
) -> None:
    """Windows, simulated on POSIX by turning the mode handling off: every
    folder is made at the default mode (it inherits its access list), except
    the target of a call that asks `private_on_windows` (guard's store root
    and `journal/`, mode 0700 as before M16-78)."""
    monkeypatch.setattr(snapshot, "_PRIVATE_MODES", False)
    monkeypatch.setattr(snapshot, "_MKDIR_MODE", 0o777)
    store = tmp_path / "elsewhere" / "store"
    with watching() as made:
        make_private_dirs(store, outside=True, private_on_windows=private_on_windows)
        make_private_dirs(user_data / "locks")
    assert made == {
        str(tmp_path / "elsewhere"): 0o777,
        str(store): 0o700 if private_on_windows else 0o777,
        str(user_data): 0o777,
        str(user_data / "locks"): 0o777,
    }
