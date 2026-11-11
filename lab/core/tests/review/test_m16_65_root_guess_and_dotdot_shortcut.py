# Probe from review of m16/65-readers-from-root (cf7448a); reproduces (1) `labaddon.read_char` guessing the install root from a character folder named like `WTF/Account` and reading through a linked flavor folder, and (2) `sv dump` through a shortcut spelled with `..` reading through a linked account folder with the last-part note
"""Constructed folders and links (hostile input); the install is the captured
tree `fixtures/macos/` copied into `tmp_path` (provenance row in
`fixtures/README.md`). Nothing reads or writes a real install.

(1) `_install_root_of` takes the *last* `WTF/Account` pair in the path. A
realm folder named `WTF` holding a character folder named `Account` (any
case) puts a later pair in the path, so the walk starts at
`<flavor>/WTF/Account` and never looks at the flavor folder: a linked flavor
folder is followed. Every CLI caller (`char show`, `char gear`, `looks
import`, coach, `char history`) calls `read_char` without `root=`.

(2) `_folders_passed_through`, `_install_holding` and the spelled-parents
check all take a `..` lexically, but a POSIX open takes it after the link
before it is followed. `Sc/../SavedVariables/F.lua`, with `Sc` a shortcut
to `<acct>/SavedVariables` and `<acct>` linked out, satisfies none of the
three checks and is read with only its last part checked.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon, snapshot

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCT_REL = f"WTF/Account/{ACCOUNT}"
CHAR_REL = f"{ACCT_REL}/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
SV_NAME = "Blizzard_AuctionHouseUI.lua"

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
    monkeypatch.setenv(install.ENV_ROOT, str(install_root))
    monkeypatch.chdir(tmp_path)
    return install_root


def _link_out(folder: Path, tmp_path: Path) -> Path:
    outside = tmp_path / "Elsewhere-constructed" / folder.name
    outside.parent.mkdir(exist_ok=True)
    shutil.move(folder, outside)
    folder.symlink_to(outside, target_is_directory=True)
    return folder


def _crafted_char(root: Path) -> Path:
    """Constructed: realm folder `WTF`, character folder `account`."""
    rel = f"{ACCT_REL}/WTF/account/SavedVariables/WowLab.lua"
    dest = root / FLAVOR / rel
    dest.parent.mkdir(parents=True)
    shutil.copy2(CAPTURE / "forever" / CHAR_REL, dest)
    return dest


def test_control_plain_names_refuse_a_linked_flavor_folder(root: Path, tmp_path: Path) -> None:
    _link_out(root / FLAVOR, tmp_path)
    with pytest.raises(snapshot.LinkedPathError):
        labaddon.read_char(root / FLAVOR / CHAR_REL)


def test_constructed_crafted_names_still_refuse_a_linked_flavor_folder(
    root: Path, tmp_path: Path
) -> None:
    path = _crafted_char(root)
    _link_out(root / FLAVOR, tmp_path)
    with pytest.raises(snapshot.LinkedPathError):
        labaddon.read_char(path)


def _dotdot_shortcut(root: Path, tmp_path: Path) -> Path:
    shortcut = tmp_path / "Sc-constructed"
    shortcut.symlink_to(root / FLAVOR / ACCT_REL / "SavedVariables", target_is_directory=True)
    return shortcut / ".." / "SavedVariables" / SV_NAME


def test_control_dotdot_shortcut_into_the_install_is_refused(root: Path, tmp_path: Path) -> None:
    done = CliRunner().invoke(cli.app, ["sv", "dump", str(_dotdot_shortcut(root, tmp_path))])
    assert done.exit_code == cli.EXIT_REFUSED, done.output


def test_constructed_dotdot_shortcut_with_account_linked_out_is_not_downgraded(
    root: Path, tmp_path: Path
) -> None:
    given = _dotdot_shortcut(root, tmp_path)
    _link_out(root / FLAVOR / ACCT_REL, tmp_path)
    done = CliRunner().invoke(cli.app, ["sv", "dump", str(given)])
    assert done.exit_code != 0, done.stdout[:200] + done.stderr
    assert "only the file itself was checked" not in done.stderr, done.stderr
