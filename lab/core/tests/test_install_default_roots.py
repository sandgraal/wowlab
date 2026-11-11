"""install: the wider default search and the error that lists it (M16-112).

LAB_PLAN §6.1, amendment of 2026-11-11. Every test is a constructed folder
layout under `tmp_path` (L8 boundary: folder layouts, no game data); the
one `.build.info` an install needs is the real capture from the fixture
corpus, copied in. The platform, the home directory, the `/Volumes` folder
and the drives are injected: no test reads a real default location, the
real home, or the real environment variable.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import cli, install
from wowlab_core.install import (
    InstallNotFoundError,
    NotAnInstallError,
    default_roots,
    discover,
    name_the_install_lines,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "macos"
WOW = "World of Warcraft"


def _install(parent: Path) -> Path:
    root = parent / WOW
    root.mkdir(parents=True)
    shutil.copy2(FIXTURES / ".build.info", root / ".build.info")
    return root


def _mac(home: str | Path, volumes: Path) -> tuple[PurePosixPath, ...]:
    got = default_roots("darwin", home=home, volumes=volumes)
    assert all(isinstance(p, PurePosixPath) for p in got)
    return got  # type: ignore[return-value]


def _p(*parts: str | Path) -> PurePosixPath:
    return PurePosixPath(*(str(p) for p in parts), WOW)


# ─── order ───────────────────────────────────────────────────────────────────


def test_macos_order_constructed(tmp_path: Path) -> None:
    volumes = tmp_path / "Volumes"
    for name in ("Zeta", "Alpha", "Games Disk"):
        (volumes / name).mkdir(parents=True)

    got = _mac("/Users/someone", volumes)

    assert got == (
        _p("/Applications"),
        _p("/Applications/Games"),
        _p("/Games"),
        _p("/Users/someone/Applications"),
        _p("/Users/someone/Games"),
        _p("/Users/Shared"),
        _p(volumes, "Alpha"),
        _p(volumes, "Alpha", "Applications"),
        _p(volumes, "Alpha", "Games"),
        _p(volumes, "Games Disk"),
        _p(volumes, "Games Disk", "Applications"),
        _p(volumes, "Games Disk", "Games"),
        _p(volumes, "Zeta"),
        _p(volumes, "Zeta", "Applications"),
        _p(volumes, "Zeta", "Games"),
    )


def test_macos_without_a_volumes_folder_ends_at_users_shared_constructed(tmp_path: Path) -> None:
    got = _mac("/Users/someone", tmp_path / "absent")
    assert got[-1] == _p("/Users/Shared")
    assert len(got) == 6


def test_windows_order_adds_games_after_the_three_on_each_drive() -> None:
    got = default_roots("win32", drives=["C:\\", "D:\\"], environ={"SystemDrive": "D:"})

    assert got == tuple(
        PureWindowsPath(p)
        for p in (
            "D:\\World of Warcraft",
            "D:\\Program Files (x86)\\World of Warcraft",
            "D:\\Program Files\\World of Warcraft",
            "D:\\Games\\World of Warcraft",
            "C:\\World of Warcraft",
            "C:\\Program Files (x86)\\World of Warcraft",
            "C:\\Program Files\\World of Warcraft",
            "C:\\Games\\World of Warcraft",
        )
    )


@pytest.mark.parametrize("platform", ["linux", "freebsd14"])
def test_other_platforms_still_search_nothing(platform: str, tmp_path: Path) -> None:
    assert default_roots(platform, home=tmp_path, volumes=tmp_path) == ()


# ─── /Volumes ────────────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink")
def test_volumes_link_entry_is_skipped_constructed(tmp_path: Path) -> None:
    """The boot volume's link back to `/` is never searched."""
    volumes = tmp_path / "Volumes"
    (volumes / "Data").mkdir(parents=True)
    (volumes / "Macintosh HD").symlink_to("/")
    (volumes / "Linked Dir").symlink_to(volumes / "Data")

    got = _mac("/Users/someone", volumes)

    assert [p for p in got if str(p).startswith(str(volumes))] == [
        _p(volumes, "Data"),
        _p(volumes, "Data", "Applications"),
        _p(volumes, "Data", "Games"),
    ]


def test_volumes_entry_that_is_not_a_folder_is_skipped_constructed(tmp_path: Path) -> None:
    volumes = tmp_path / "Volumes"
    (volumes / "Data").mkdir(parents=True)
    (volumes / "a-file").write_bytes(b"")

    got = _mac("/Users/someone", volumes)

    assert _p(volumes, "a-file") not in got
    assert _p(volumes, "Data") in got


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permission bits and a non-root user",
)
def test_volumes_entry_that_cannot_be_listed_is_skipped_without_error_constructed(
    tmp_path: Path,
) -> None:
    volumes = tmp_path / "Volumes"
    (volumes / "Data").mkdir(parents=True)
    locked = volumes / "Locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        got = _mac("/Users/someone", volumes)
    finally:
        locked.chmod(stat.S_IRWXU)

    assert _p(volumes, "Locked") not in got
    assert _p(volumes, "Data") in got


def test_volumes_entry_whose_listing_is_refused_is_skipped_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same refusal, injected, so it runs as root and on every OS."""
    volumes = tmp_path / "Volumes"
    (volumes / "Data").mkdir(parents=True)
    (volumes / "Locked").mkdir()
    real_scandir = os.scandir

    def refusing(path: object = ".") -> object:
        if Path(str(path)).name == "Locked":
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)  # type: ignore[call-overload]

    monkeypatch.setattr(install.os, "scandir", refusing)
    got = _mac("/Users/someone", volumes)

    assert _p(volumes, "Locked") not in got
    assert _p(volumes, "Data") in got


def test_unreadable_volumes_folder_gives_no_volumes_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volumes = tmp_path / "Volumes"
    (volumes / "Data").mkdir(parents=True)

    def refusing(path: object = ".") -> object:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(install.os, "scandir", refusing)
    assert len(_mac("/Users/someone", volumes)) == 6


# ─── no home ─────────────────────────────────────────────────────────────────


def test_no_home_skips_the_home_entries_constructed(tmp_path: Path) -> None:
    got = _mac("", tmp_path / "absent")

    assert got == (
        _p("/Applications"),
        _p("/Applications/Games"),
        _p("/Games"),
        _p("/Users/Shared"),
    )


def test_home_that_cannot_be_found_skips_the_home_entries_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(install.Path, "home", staticmethod(no_home))
    got = default_roots("darwin", volumes=tmp_path / "absent")

    assert got == (
        _p("/Applications"),
        _p("/Applications/Games"),
        _p("/Games"),
        _p("/Users/Shared"),
    )


def test_home_found_by_default_is_searched_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install.Path, "home", staticmethod(lambda: tmp_path / "me"))
    got = default_roots("darwin", volumes=tmp_path / "absent")

    assert got[3:5] == (_p(tmp_path / "me", "Applications"), _p(tmp_path / "me", "Games"))


# ─── two installs: the first in the order wins ──────────────────────────────


def _under(paths: tuple[PurePosixPath, ...], base: Path) -> list[Path]:
    """The defaults inside `base` only, so no real location is ever looked at."""
    return [Path(str(p)) for p in paths if str(p).startswith(str(base))]


@pytest.mark.skipif(sys.platform == "win32", reason="macOS paths built as POSIX paths")
def test_two_installs_the_first_in_order_wins_constructed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    volumes = tmp_path / "Volumes"
    in_home = _install(home / "Games")
    _install(volumes / "Alpha" / "Applications")
    defaults = _under(_mac(home, volumes), tmp_path)

    assert discover(environ={}, defaults=defaults).root == in_home


@pytest.mark.skipif(sys.platform == "win32", reason="macOS paths built as POSIX paths")
def test_two_volume_installs_the_first_by_name_wins_constructed(tmp_path: Path) -> None:
    volumes = tmp_path / "Volumes"
    _install(volumes / "Zeta")
    first = _install(volumes / "Alpha" / "Games")
    defaults = _under(_mac("", volumes), tmp_path)

    assert discover(environ={}, defaults=defaults).root == first


# ─── unchanged: explicit root and the variable are final ────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="macOS paths built as POSIX paths")
def test_explicit_root_and_variable_stay_final_constructed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _install(home / "Games")
    defaults = _under(_mac(home, tmp_path / "absent"), tmp_path)
    not_an_install = tmp_path / "elsewhere"
    not_an_install.mkdir()

    with pytest.raises(NotAnInstallError):
        discover(not_an_install, environ={}, defaults=defaults)
    with pytest.raises(NotAnInstallError):
        discover(environ={install.ENV_ROOT: str(not_an_install)}, defaults=defaults)


# ─── the error text ──────────────────────────────────────────────────────────


def test_not_found_notes_list_every_location_in_order_then_the_two_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absent = tmp_path / "absent" / WOW
    a_file = tmp_path / "file" / WOW
    a_file.parent.mkdir()
    a_file.write_bytes(b"")  # constructed: not a directory
    empty = tmp_path / "empty" / WOW
    empty.mkdir(parents=True)
    monkeypatch.setattr(install.sys, "platform", "darwin")

    with pytest.raises(InstallNotFoundError) as caught:
        discover(environ={}, defaults=[absent, a_file, empty])

    assert caught.value.checked == (absent, a_file, empty)
    assert caught.value.__notes__ == [
        f"searched, no install there: {absent}",
        f"could not check: {a_file} (not a directory)",
        f"searched, no install there: {empty}",
        "to name the install, use the folder that holds .build.info (not a flavor folder "
        "named like _name_ inside it), for one command or for every command:",
        '  --root "<path>"',
        '  export WOWLAB_WOW_ROOT="<path>"',
    ]


def test_the_variable_line_is_set_on_windows_and_export_elsewhere() -> None:
    assert name_the_install_lines("win32")[-1] == "  set WOWLAB_WOW_ROOT=<path>"
    assert name_the_install_lines("darwin")[-1] == '  export WOWLAB_WOW_ROOT="<path>"'
    assert name_the_install_lines("linux")[-1] == '  export WOWLAB_WOW_ROOT="<path>"'
    error = InstallNotFoundError([], platform="win32")
    assert error.__notes__[-2:] == ['  --root "<path>"', "  set WOWLAB_WOW_ROOT=<path>"]


def test_cli_prints_the_error_then_every_location_then_the_commands_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the player sees: one error line, then one line per location in
    the order checked, then the lines to copy."""
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.setattr(install.sys, "platform", "darwin")
    first = tmp_path / "a" / WOW
    second = tmp_path / "b" / WOW
    monkeypatch.setattr(install, "default_roots", lambda **k: (first, second))

    result = CliRunner().invoke(cli.app, ["install", "show"])

    assert result.exit_code == cli.EXIT_ERROR
    assert result.stderr.splitlines() == [
        "wowlab: no WoW install found where the game installs by default (2 locations "
        "checked, listed below); name the install folder (the one that holds .build.info) "
        "with --root, or set WOWLAB_WOW_ROOT to it; wowlab doctor says how",
        f"searched, no install there: {first}",
        f"searched, no install there: {second}",
        "to name the install, use the folder that holds .build.info (not a flavor folder "
        "named like _name_ inside it), for one command or for every command:",
        '  --root "<path>"',
        '  export WOWLAB_WOW_ROOT="<path>"',
    ]


def test_a_reworded_not_found_keeps_its_notes(tmp_path: Path) -> None:
    """Where the CLI words the error into its own (`sv dump --install-only`,
    `look check`), the locations and the commands still follow it."""
    error = InstallNotFoundError([tmp_path / WOW], platform="darwin")
    kept = cli._keep_notes(cli.CliError("refused: ..."), error)
    assert kept.__notes__ == error.__notes__
    with pytest.raises(typer.Exit):
        cli._fail("x", 1, kept)
