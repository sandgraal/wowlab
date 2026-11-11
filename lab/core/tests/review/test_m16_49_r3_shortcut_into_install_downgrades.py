# Probe from review of m16/49-readers-from-root (fix round 2, 1a161be); reproduces `sv dump` through a link that points into a folder of the install downgrading to the last-part check once a folder on that link's target is linked out
"""Constructed links and outside folders (hostile input); the install is the
captured tree `fixtures/macos/` copied into `tmp_path` (provenance row in
`fixtures/README.md`). Nothing reads or writes a real install.

A player's shortcut `MySV -> <install>/<flavor>/WTF/Account/<acct>` is
refused (control). When the account folder inside the install becomes a link
out, no ancestor of the spelled path is the install root, and the real path is
outside, so `_really_inside` says "outside" and the read follows the linked
folder with the last-part note.
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


def _shortcut(root: Path, tmp_path: Path) -> Path:
    shortcut = tmp_path / "MySV-constructed"
    shortcut.symlink_to(root / FLAVOR / "WTF" / "Account" / ACCOUNT, target_is_directory=True)
    return shortcut


def test_control_shortcut_into_the_install_is_refused(root: Path, tmp_path: Path) -> None:
    done = CliRunner().invoke(cli.app, ["sv", "dump", str(_shortcut(root, tmp_path) / SV)])
    assert done.exit_code == cli.EXIT_REFUSED, done.output


def test_shortcut_with_its_target_folder_linked_out_is_not_downgraded(
    root: Path, tmp_path: Path
) -> None:
    shortcut = _shortcut(root, tmp_path)
    account = root / FLAVOR / "WTF" / "Account" / ACCOUNT
    outside = tmp_path / "Elsewhere-constructed" / ACCOUNT
    outside.parent.mkdir()
    shutil.move(account, outside)
    account.symlink_to(outside, target_is_directory=True)
    done = CliRunner().invoke(cli.app, ["sv", "dump", str(shortcut / SV)])
    assert done.exit_code != 0, done.stderr
    assert "only the file itself was checked" not in done.stderr, done.stderr
