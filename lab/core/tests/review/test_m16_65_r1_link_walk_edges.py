# Probe from review of m16/65-readers-from-root fix round 1 (f7006a8); exercises `_link_out_of_install` with chains after an absolute target, relative targets with `..`, the 40-link cap and a loop, each ending in a linked account folder
"""Constructed links and outside folders (hostile input); the install is the
captured tree `fixtures/macos/` copied into `tmp_path` (provenance row in
`fixtures/README.md`). Nothing reads or writes a real install.

Every case leaves the account folder linked out of the install and asks
`sv dump` for a file behind it by a path that enters the install through a
link. None may be read with only its last part checked.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
SV = "SavedVariables/Blizzard_AuctionHouseUI.lua"
LAST_PART = "only the file itself was checked"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "ud")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([]))
    install_root = tmp_path / "World of Warcraft"
    install_root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install_root / ".build.info")
    shutil.copytree(CAPTURE / "forever", install_root / FLAVOR)
    account = install_root / FLAVOR / "WTF" / "Account" / ACCOUNT
    outside = tmp_path / "Elsewhere-constructed" / ACCOUNT
    outside.parent.mkdir()
    shutil.move(account, outside)
    account.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv(install.ENV_ROOT, str(install_root))
    monkeypatch.chdir(tmp_path)
    return install_root


def _dump(given: Path) -> tuple[int, str]:
    done = CliRunner().invoke(cli.app, ["sv", "dump", str(given)])
    return done.exit_code, done.stderr


def test_constructed_chain_after_an_absolute_target(root: Path, tmp_path: Path) -> None:
    second = tmp_path / "Second-constructed"
    second.symlink_to(root / FLAVOR, target_is_directory=True)
    first = tmp_path / "First-constructed"
    first.symlink_to(second, target_is_directory=True)
    code, err = _dump(first / "WTF" / "Account" / ACCOUNT / SV)
    assert code != 0 and LAST_PART not in err, err


def test_constructed_relative_target_with_dotdot(root: Path, tmp_path: Path) -> None:
    (tmp_path / "Deep-constructed" / "x").mkdir(parents=True)
    rel = tmp_path / "Deep-constructed" / "x" / "Rel"
    rel.symlink_to(Path("..") / ".." / root.name / FLAVOR / "WTF", target_is_directory=True)
    code, err = _dump(rel / "Account" / ACCOUNT / SV)
    assert code != 0 and LAST_PART not in err, err


def test_constructed_dotdot_after_a_relative_link(root: Path, tmp_path: Path) -> None:
    rel = tmp_path / "Rel2-constructed"
    rel.symlink_to(Path(root.name) / FLAVOR / "WTF" / "Account", target_is_directory=True)
    code, err = _dump(rel / ".." / "Account" / ACCOUNT / SV)
    assert code != 0 and LAST_PART not in err, err


@pytest.mark.parametrize("outside_links", [38, 39, 40, 45])
def test_constructed_long_chain_before_the_install(
    root: Path, tmp_path: Path, outside_links: int
) -> None:
    chain = tmp_path / "Chain-constructed"
    chain.mkdir()
    previous = root / FLAVOR
    for i in range(outside_links):
        link = chain / f"l{i}"
        link.symlink_to(previous, target_is_directory=True)
        previous = link
    given = previous / "WTF" / "Account" / ACCOUNT / SV
    code, err = _dump(given)
    assert code != 0, (outside_links, err)


def test_constructed_loop_inside_the_install(root: Path, tmp_path: Path) -> None:
    loop = root / FLAVOR / "WTF" / "Loop-constructed"
    loop.symlink_to(loop, target_is_directory=True)
    shortcut = tmp_path / "Sc-constructed"
    shortcut.symlink_to(root / FLAVOR / "WTF", target_is_directory=True)
    code, err = _dump(shortcut / "Loop-constructed" / "x" / SV)
    assert code != 0, err
