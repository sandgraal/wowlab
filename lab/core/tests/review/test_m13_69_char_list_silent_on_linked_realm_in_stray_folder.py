# Probe from review of m13/69-gaps-not-accounts; reproduces `char list` going silent (exit 0) on a linked realm folder in a folder of WTF/Account/ that the walk does not take as an account
"""M13-69 re-picks `Symlink.entry_id` below a folder of `WTF/Account/` named
in `not_accounts`, so a link `WTF/Account/OLDACCT/Realm` now has no row
where `main` gave it `realm-folder`. `labaddon._place` counts a link
directly in an account-level folder only when its entry id is a realm or
digits row (`_REALM_ENTRIES`), so `survey` drops it from `not_looked_at`.
The walk never follows links, so it cannot know whether that realm holds a
character folder; `main` said so on stderr and exited 1 ("could not look
inside WTF/Account/OLDACCT/Realm: a link, not followed"). The branch says
nothing and exits 0, and `char list --account OLDACCT` exits 2 with "holds
no realm folder with a character folder inside", while the link it never
followed holds one. `labaddon.py` is not in the diff and the PR's
user-facing list does not name this change.

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`. Constructed (L8, boundary): the `OLDACCT` folder, the link and
the realm folder outside the install it points to. Nothing reads or writes
a real install: `WOWLAB_WOW_ROOT` points at the copy and the user data
directory is redirected into `tmp_path`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")

runner = CliRunner()


@pytest.fixture
def account_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR / "WTF" / "Account"


def _linked_realm(folder: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere" / "Realm"
    (elsewhere / "Char" / "SavedVariables").mkdir(parents=True)
    folder.mkdir(exist_ok=True)
    (folder / "Realm").symlink_to(elsewhere, target_is_directory=True)


def test_positive_control_linked_realm_in_an_account_folder_is_named(
    account_dir: Path, tmp_path: Path
) -> None:
    # The same link in the real account folder: named, exit 1 (as on main).
    _linked_realm(account_dir / "90000001#6", tmp_path)
    result = runner.invoke(cli.app, ["char", "list"])
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert "WTF/Account/90000001#6/Realm" in result.stderr


def test_linked_realm_in_a_folder_not_taken_as_an_account_is_still_named(
    account_dir: Path, tmp_path: Path
) -> None:
    _linked_realm(account_dir / "OLDACCT", tmp_path)
    result = runner.invoke(cli.app, ["char", "list"])
    # main: exit 1, "could not look inside WTF/Account/OLDACCT/Realm: a link, not followed"
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert "WTF/Account/OLDACCT/Realm" in result.stderr
    named = runner.invoke(cli.app, ["char", "list", "--account", "OLDACCT"])
    assert named.exit_code == 1, (named.stdout, named.stderr)
    assert "WTF/Account/OLDACCT/Realm" in named.stderr
