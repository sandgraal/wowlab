# Probe from review of m16/49-readers-from-root (fix round 1, 1ae5704); reproduces `sv dump` through an alias of the install downgrading to the last-part check when a folder inside the install is linked out
"""Constructed links and outside folders (hostile input); the install is the
captured tree `fixtures/macos/` copied into `tmp_path` (provenance row in
`fixtures/README.md`). Nothing reads or writes a real install.

`_really_inside` resolves the whole path, so a link *inside* the install that
points out of it makes the real path "outside", and the read falls back to
checking the last part only. That read goes through the install (by its
alias) and follows the linked folder, with the note saying the file is not in
an install wowlab found. The positive control (no inner link) is refused with
"not spelled under", as fix round 1 intends.
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
SV_REL = f"WTF/Account/{ACCOUNT}/SavedVariables/Blizzard_AuctionHouseUI.lua"

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


def _alias(root: Path, tmp_path: Path) -> Path:
    alias = tmp_path / "Games-constructed"
    alias.symlink_to(root, target_is_directory=True)
    return alias


def test_control_alias_without_an_inner_link_is_refused(root: Path, tmp_path: Path) -> None:
    done = CliRunner().invoke(
        cli.app, ["sv", "dump", str(_alias(root, tmp_path) / FLAVOR / SV_REL)]
    )
    assert done.exit_code == cli.EXIT_REFUSED, done.output


def test_alias_with_an_account_folder_linked_out_is_not_downgraded(
    root: Path, tmp_path: Path
) -> None:
    account = root / FLAVOR / "WTF" / "Account" / ACCOUNT
    outside = tmp_path / "Elsewhere-constructed" / ACCOUNT
    outside.parent.mkdir()
    shutil.move(account, outside)
    account.symlink_to(outside, target_is_directory=True)
    done = CliRunner().invoke(
        cli.app, ["sv", "dump", str(_alias(root, tmp_path) / FLAVOR / SV_REL)]
    )
    # Fixed: refused like the control, never read through the linked folder.
    assert done.exit_code != 0, done.stderr
    assert "only the file itself was checked" not in done.stderr, done.stderr
