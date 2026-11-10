# Probe from review of m16/65-readers-from-root; reproduces char gear following a linked WTF folder out of the install when a realm and character folder are named WTF/Account
"""Constructed folder names, links and outside folders (hostile input); the
install is the captured tree `fixtures/macos/` copied into `tmp_path`
(provenance row in `fixtures/README.md`). Nothing reads or writes a real
install.

`char gear` (and `char show`'s character read) knows the install (`lay.install_root`) but calls
`labaddon.read_char(path)` without it, so `_install_root_of` guesses the root
from the last `WTF/Account` pair in the path. A realm folder named `WTF`
holding a character folder named `Account` makes that last pair lie inside
the account folder, so the walk starts at `<flavor>/WTF/Account`, which is
opened by path: the flavor's `WTF` swapped for a link out of the install is
followed, and what is behind it is shown. The positive control reads the
same path with the root given and is refused.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon, snapshot

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
CHAR = "Labchard-Labrealmg"
REALM = "1"

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
    acct = install_root / FLAVOR / "WTF" / "Account" / ACCOUNT
    # Constructed: a realm folder named WTF holding a character folder named Account.
    (acct / REALM).rename(acct / "WTF")
    (acct / "WTF" / CHAR).rename(acct / "WTF" / "Account")
    monkeypatch.setenv(install.ENV_ROOT, str(install_root))
    monkeypatch.chdir(tmp_path)
    return install_root


def _char_file(root: Path) -> Path:
    return (
        (root / FLAVOR / "WTF" / "Account" / ACCOUNT / "WTF" / "Account")
        / "SavedVariables"
        / "WowLab.lua"
    )


def _link_wtf_out(root: Path, tmp_path: Path) -> Path:
    wtf = root / FLAVOR / "WTF"
    outside = tmp_path / "Elsewhere-constructed" / "WTF"
    outside.parent.mkdir()
    shutil.copytree(wtf, outside)
    shutil.rmtree(wtf)
    wtf.symlink_to(outside, target_is_directory=True)
    return wtf


def test_positive_control_the_root_given_refuses_the_linked_wtf(root: Path, tmp_path: Path) -> None:
    path = _char_file(root)
    assert labaddon.read_char(path, root=root) is not None
    link = _link_wtf_out(root, tmp_path)
    with pytest.raises(snapshot.LinkedPathError) as caught:
        labaddon.read_char(path, root=root)
    assert caught.value.folder == link


def test_read_char_without_root_follows_the_linked_wtf(root: Path, tmp_path: Path) -> None:
    path = _char_file(root)
    _link_wtf_out(root, tmp_path)
    with pytest.raises(snapshot.LinkedPathError):
        labaddon.read_char(path)


def test_char_gear_follows_the_linked_wtf(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = labaddon.read_char
    swapped_in: list[Path] = []

    def swapped(path: Path, **kwargs: Any) -> Any:
        if not swapped_in:
            swapped_in.append(_link_wtf_out(root, tmp_path))
        return real(path, **kwargs)

    monkeypatch.setattr(labaddon, "read_char", swapped)
    done = CliRunner().invoke(
        cli.app, ["char", "gear", "--account", ACCOUNT, "--character", "WTF/Account"]
    )
    assert swapped_in, done.output
    assert done.exit_code not in (0, None), done.output
    assert "linked folder" in done.stderr, done.stderr
