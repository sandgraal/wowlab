# Probe from review of m16/65-readers-from-root; reproduces sv dump through a shortcut into the install followed by '..' still downgrading to the last-part check past a linked folder
"""Constructed links and outside folders (hostile input); the install is the
captured tree `fixtures/macos/` copied into `tmp_path` (provenance row in
`fixtures/README.md`). Nothing reads or writes a real install.

`_folders_passed_through` takes the spelled path's `..` lexically before it
walks, but the open takes them after the link before them is followed. With
`Short -> <install>/<flavor>/WTF/Account`, the path
`Short/../Account/<acct>/SavedVariables/x.lua` is opened as
`<install>/<flavor>/WTF/Account/<acct>/...`, while the walk sees
`<tmp>/Account/...`, which does not exist, and stops. With the account folder
linked out, no check finds the install, and the read follows the linked
folder with the last-part note. The control: the same spelling with nothing
linked out is refused (exit 3), as the M16-49 probe r3's control is.
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


def _spelled(root: Path, tmp_path: Path) -> str:
    short = tmp_path / "Short-constructed"
    short.symlink_to(root / FLAVOR / "WTF" / "Account", target_is_directory=True)
    return f"{short}/../Account/{ACCOUNT}/{SV}"


def test_control_shortcut_then_dotdot_into_the_install_is_refused(
    root: Path, tmp_path: Path
) -> None:
    done = CliRunner().invoke(cli.app, ["sv", "dump", _spelled(root, tmp_path)])
    assert done.exit_code == cli.EXIT_REFUSED, done.output


def test_shortcut_then_dotdot_with_the_account_linked_out_is_not_downgraded(
    root: Path, tmp_path: Path
) -> None:
    spelled = _spelled(root, tmp_path)
    account = root / FLAVOR / "WTF" / "Account" / ACCOUNT
    outside = tmp_path / "Elsewhere-constructed" / ACCOUNT
    outside.parent.mkdir()
    shutil.move(account, outside)
    account.symlink_to(outside, target_is_directory=True)
    done = CliRunner().invoke(cli.app, ["sv", "dump", spelled])
    assert done.exit_code != 0, done.stderr
    assert "only the file itself was checked" not in done.stderr, done.stderr
