"""The first-run survey's link targets are plain on Windows (M13-51).

On Windows `readlink` answers an absolute link's target with the
extended-length prefix (`\\\\?\\C:\\...`); the survey prints the target in
`{link.path} -> {target}` and fills `SyncHint.link_target` from it, and
every path the Lab shows is plain (docs/LAB_PLAN.md §6.9, M13-15). The
prefix is dropped where the text has a plain spelling, a relative text is
kept as it is, and nothing changes off Windows (as M13-43 did for
`layout.link_facts`).

Every input here is `constructed`: the text `readlink` answers stood in on
every host, and on Windows real links (a junction, an absolute and a
relative symlink) in `WTF/` of the captured tree copied into `tmp_path` by
`test_cli`'s `root` fixture. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401  (autouse: the store lives in tmp_path)
)

from wowlab_core import firstrun, longpath

windows = sys.platform == "win32"
only_windows = pytest.mark.skipif(not windows, reason="extended-length paths are Windows only")


def _stood_in(monkeypatch: pytest.MonkeyPatch, *, on_windows: bool, stored: str) -> None:
    monkeypatch.setattr(longpath, "ON_WINDOWS", on_windows)
    monkeypatch.setattr(longpath, "readlink", lambda _path: Path(stored))


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        pytest.param("\\\\?\\C:\\Elsewhere\\WTF", "C:\\Elsewhere\\WTF", id="absolute"),
        pytest.param("\\\\?\\UNC\\server\\share\\WTF", "\\\\server\\share\\WTF", id="absolute-unc"),
        pytest.param(
            "\\\\?\\C:\\Elsewhere\\..\\WTF", "\\\\?\\C:\\Elsewhere\\..\\WTF", id="no-plain"
        ),
        pytest.param("..\\Interface", "..\\Interface", id="relative"),
    ],
)
def test_a_link_target_on_windows_is_plain_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored: str, shown: str
) -> None:
    """Constructed: the text `readlink` answers on Windows, stood in on every
    host. An absolute target loses the prefix; one with no plain spelling (a
    `..` component) keeps it, as `longpath.plain_text` says; a relative target
    has no prefix and is kept as it is."""
    _stood_in(monkeypatch, on_windows=True, stored=stored)
    assert firstrun._link_target(tmp_path / "link") == shown


def test_a_link_target_off_windows_is_kept_as_read_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: off Windows a target that happens to begin `\\\\?\\` is a
    name like any other, kept as read."""
    stored = "\\\\?\\C:\\Elsewhere\\WTF"
    _stood_in(monkeypatch, on_windows=False, stored=stored)
    assert firstrun._link_target(tmp_path / "link") == stored


def test_an_unreadable_link_target_is_none_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: a target that cannot be read stays unknown on Windows too."""

    def refuse(_path: Path) -> Path:
        raise OSError("not a link")

    monkeypatch.setattr(longpath, "ON_WINDOWS", True)
    monkeypatch.setattr(longpath, "readlink", refuse)
    assert firstrun._link_target(tmp_path / "link") is None


def _junction(link: Path, target: Path) -> None:
    import _winapi  # Windows only

    _winapi.CreateJunction(str(target), str(link))


@only_windows
def test_a_junction_into_a_synced_folder_is_shown_plain_on_windows_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    """Constructed, on Windows: a real junction in `WTF/` (no privilege needed)
    to an absolute folder under a Dropbox folder. Its target is read back
    with the prefix; the survey's link line, the sync hint and `--json`
    carry it plain."""
    elsewhere = tmp_path / "Dropbox" / "Elsewhere"
    elsewhere.mkdir(parents=True)
    _junction(flavor / "WTF" / "Junction", elsewhere)
    out = ok("doctor").stdout
    assert "\\\\?\\" not in out
    assert f"    WTF/Junction -> {elsewhere} (junction)" in out
    assert f"a folder Dropbox appears to sync ({elsewhere})" in out
    data = ok("doctor", "--json").stdout
    assert "\\\\\\\\?\\\\" not in data  # the prefix, as JSON escapes it


@only_windows
def test_an_absolute_and_a_relative_symlink_are_shown_plain_on_windows_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    """Constructed, on Windows: a real absolute symlink and a real relative
    one in `WTF/`. The absolute target is shown without the prefix; the
    relative one exactly as it reads back."""
    outside = tmp_path / "outside"
    outside.mkdir()
    wtf = flavor / "WTF"
    try:
        (wtf / "Absolute").symlink_to(outside, target_is_directory=True)
        (wtf / "Relative").symlink_to("..\\Interface", target_is_directory=True)
    except OSError as exc:  # no symlink privilege on this runner
        pytest.skip(f"cannot create a symlink here: {exc}")
    out = ok("doctor").stdout
    assert "\\\\?\\" not in out
    assert f"    WTF/Absolute -> {outside} (symlink)" in out
    assert "    WTF/Relative -> ..\\Interface (symlink)" in out
