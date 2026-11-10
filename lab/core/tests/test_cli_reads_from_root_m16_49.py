"""The CLI callers open WTF and SavedVariables files from the install root
(M16-49): a file reached through a linked folder is refused with the folder
named, and `sv dump` of a file outside any install says it checked only the
file itself.

The install is the captured tree (`fixtures/macos/`, rows in
`fixtures/README.md`, L8) copied into `tmp_path`; the links and the outside
folders are constructed (hostile input, ids and names say so). Nothing here
touches a real install.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import addoncheck, cli, install, snapshot, wtfconfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
SV_REL = f"WTF/Account/{ACCOUNT}/SavedVariables/Blizzard_AuctionHouseUI.lua"
CONFIG = "WTF/Config.wtf"

needs_symlink = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)


class _Proc:
    def __init__(self, pid: int, name: str, exe: str) -> None:
        self.pid, self._name, self._exe = pid, name, exe

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    other = _Proc(101, "python3", str(tmp_path / "elsewhere" / "python3"))
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([other]))
    install_root = tmp_path / "World of Warcraft"
    install_root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install_root / ".build.info")
    shutil.copytree(CAPTURE / "forever", install_root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(install_root))
    monkeypatch.chdir(tmp_path)
    return install_root


def _dump(*args: str) -> Any:
    return CliRunner().invoke(cli.app, ["sv", "dump", *args])


def _link_account_folder(root: Path, tmp_path: Path) -> Path:
    """The account folder moved out of the install and a link left in its
    place (constructed): the files below it are reached through the link."""
    folder = root / FLAVOR / "WTF" / "Account" / ACCOUNT
    outside = tmp_path / "Elsewhere-constructed" / ACCOUNT
    outside.parent.mkdir()
    shutil.move(folder, outside)
    folder.symlink_to(outside, target_is_directory=True)
    return folder


def test_sv_dump_of_a_file_in_the_install_reads_it_with_no_note(root: Path) -> None:
    done = _dump(str(root / FLAVOR / SV_REL))
    assert done.exit_code == 0, done.output
    assert done.stdout
    assert "only the file itself was checked" not in done.stderr


def test_sv_dump_relative_to_the_flavor_folder_reads_it_with_no_note(root: Path) -> None:
    done = _dump(SV_REL, "--root", str(root))
    assert done.exit_code == 0, done.output
    assert "only the file itself was checked" not in done.stderr


@needs_symlink
def test_constructed_sv_dump_through_a_linked_folder_is_refused_with_the_folder_named(
    root: Path, tmp_path: Path
) -> None:
    folder = _link_account_folder(root, tmp_path)
    done = _dump(str(root / FLAVOR / SV_REL))
    assert done.exit_code not in (0, None), done.output
    assert done.exception is None or isinstance(done.exception, SystemExit), done.exception
    assert done.stdout == ""
    first = done.stderr.strip().splitlines()[0]
    assert first.startswith("wowlab: "), first
    assert "linked folder" in first, first
    assert str(folder) in first, first


def test_constructed_sv_dump_outside_any_install_says_it_checked_the_file_only(
    root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "Loose-constructed" / "Blizzard_AuctionHouseUI.lua"
    outside.parent.mkdir()
    shutil.copy2(root / FLAVOR / SV_REL, outside)
    done = _dump(str(outside))
    assert done.exit_code == 0, done.output
    assert done.stdout
    assert "only the file itself was checked for a link" in done.stderr, done.stderr


def test_constructed_sv_dump_outside_any_install_with_no_install_found(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No install found at all: the file is read as before, with the note."""
    monkeypatch.setenv(install.ENV_ROOT, str(tmp_path / "no-install-here"))
    outside = tmp_path / "Loose-constructed" / "Blizzard_AuctionHouseUI.lua"
    outside.parent.mkdir()
    shutil.copy2(root / FLAVOR / SV_REL, outside)
    done = _dump(str(outside))
    assert done.exit_code == 0, done.output
    assert "only the file itself was checked for a link" in done.stderr, done.stderr


@needs_symlink
def test_constructed_sv_dump_install_only_reads_the_real_path_from_the_root(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--install-only` reads the real path it checked, from the install's
    real path: no note, and the walk is given that root."""
    from wowlab_core import luadata

    seen: list[tuple[Path, Any]] = []
    real_read = luadata.read

    def spy(path: Path, **kwargs: Any) -> Any:
        seen.append((Path(path), kwargs.get("root")))
        return real_read(path, **kwargs)

    monkeypatch.setattr(luadata, "read", spy)
    done = _dump("--install-only", str(root / FLAVOR / SV_REL))
    assert done.exit_code == 0, done.output
    assert seen == [(Path(os.path.realpath(root / FLAVOR / SV_REL)), Path(os.path.realpath(root)))]
    assert "only the file itself was checked" not in done.stderr


@needs_symlink
def test_constructed_cvar_list_refuses_a_config_swapped_behind_a_linked_folder(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `WTF` folder becomes a link between the listing and the read: the
    reader, given the install root, refuses it and names the folder."""
    real = wtfconfig.read_config
    wtf = root / FLAVOR / "WTF"

    def swapped(target: Path, **kwargs: Any) -> Any:
        if not wtf.is_symlink():
            outside = tmp_path / "Elsewhere-constructed" / "WTF"
            outside.parent.mkdir()
            shutil.move(wtf, outside)
            wtf.symlink_to(outside, target_is_directory=True)
        return real(target, **kwargs)

    monkeypatch.setattr(wtfconfig, "read_config", swapped)
    done = CliRunner().invoke(cli.app, ["cvar", "list"])
    assert done.exit_code not in (0, None), done.output
    assert done.exception is None or isinstance(done.exception, SystemExit), done.exception
    assert done.stdout == ""
    first = done.stderr.strip().splitlines()[0]
    assert "linked folder" in first and str(wtf) in first, first


def test_constructed_linked_path_error_in_addons_check_words(tmp_path: Path) -> None:
    folder = tmp_path / "Install" / "_f_" / "WTF"
    path = folder / "Config.wtf"
    through = snapshot.LinkedPathError(path, folder, final=False)
    assert addoncheck._unsafe_words(through) == (
        "is reached through a linked folder, WTF, not followed"
    )
    final = snapshot.LinkedPathError(path, path, final=True)
    assert addoncheck._unsafe_words(final) == "is a link, not followed"
    assert str(tmp_path) not in addoncheck._unsafe_words(through)


def test_read_regular_file_refuses_root_with_expect_or_dir_fd(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    with pytest.raises(ValueError):
        snapshot.read_regular_file(path, root=tmp_path, expect=path.stat())
    with pytest.raises(ValueError):
        snapshot.read_regular_file(path, root=tmp_path, dir_fd=0)
    assert snapshot.read_regular_file(path, root=tmp_path) == b"x"


def test_constructed_sv_dump_of_an_install_file_spelled_with_dotdot_is_refused(
    root: Path,
) -> None:
    """A path inside the found install, spelled with `..`, is refused by the
    read from the root, never read with only its last part checked."""
    done = _dump(str(root / FLAVOR / "WTF" / ".." / SV_REL))
    assert done.exit_code not in (0, None), done.output
    assert done.stdout == ""
    assert "'..'" in done.stderr, done.stderr
    assert "only the file itself was checked" not in done.stderr


def test_constructed_sv_dump_climbing_out_of_the_install_reads_with_the_note(
    root: Path, tmp_path: Path
) -> None:
    """`<install>/../Loose/...` is outside the install once `..` is taken:
    read as a loose file, with the note."""
    outside = tmp_path / "Loose-constructed" / "Blizzard_AuctionHouseUI.lua"
    outside.parent.mkdir()
    shutil.copy2(root / FLAVOR / SV_REL, outside)
    done = _dump(str(root / ".." / "Loose-constructed" / outside.name))
    assert done.exit_code == 0, done.output
    assert "only the file itself was checked for a link" in done.stderr, done.stderr


@needs_symlink
def test_constructed_sv_dump_through_a_link_to_the_install_is_refused_not_downgraded(
    root: Path, tmp_path: Path
) -> None:
    """The install named through a link to it is another spelling of the
    same install: refused, never read with only the last part checked."""
    alias = tmp_path / "Games-constructed"
    alias.symlink_to(root, target_is_directory=True)
    done = _dump(str(alias / FLAVOR / SV_REL))
    assert done.exit_code == cli.EXIT_REFUSED, done.output
    assert done.stdout == ""
    assert "not spelled under" in done.stderr, done.stderr
    assert "only the file itself was checked" not in done.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="case folding is graded on POSIX here")
def test_constructed_sv_dump_of_the_install_in_another_case_is_never_downgraded(
    root: Path,
) -> None:
    """On a case-insensitive volume (macOS) the swapped-case spelling names
    the install and is refused; on a case-sensitive one it names nothing."""
    variant = root.parent / root.name.swapcase()
    done = _dump(str(variant / FLAVOR / SV_REL))
    assert done.exit_code not in (0, None), done.output
    assert "only the file itself was checked" not in done.stderr
