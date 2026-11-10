# Probe from review of m16/65-readers-from-root fix round 1 (f7006a8); shows the CLI telling the player a file "changed while wowlab read it; run the command again" when a folder of the install is a link that stays one (no race), so running again gives the same refusal
"""Constructed links and outside folders (hostile input); the install is the
captured tree `fixtures/macos/` copied into `tmp_path` (provenance row in
`fixtures/README.md`). Nothing reads or writes a real install.

`_listed` turns every `LinkedPathError` into the changed-while-read wording,
on the claim that the listing follows no link. When the listing does pass
through a linked folder that stays linked, that advice is wrong.
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
COMMANDS = [["char", "show"], ["cvar", "list"], ["binds", "list"], ["char", "list"]]
FOLDERS = ["flavor", "wtf", "account"]

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


def _link_out(folder: Path, tmp_path: Path) -> None:
    outside = tmp_path / "Elsewhere-constructed" / folder.name
    outside.parent.mkdir(exist_ok=True)
    shutil.move(folder, outside)
    folder.symlink_to(outside, target_is_directory=True)


def _folder(root: Path, which: str) -> Path:
    if which == "flavor":
        return root / FLAVOR
    if which == "wtf":
        return root / FLAVOR / "WTF"
    return root / FLAVOR / "WTF" / "Account" / ACCOUNT


@pytest.mark.parametrize("which", FOLDERS)
@pytest.mark.parametrize("command", COMMANDS, ids=[" ".join(c) for c in COMMANDS])
def test_constructed_standing_link_is_not_called_a_change(
    root: Path, tmp_path: Path, which: str, command: list[str]
) -> None:
    _link_out(_folder(root, which), tmp_path)
    CliRunner().invoke(cli.app, command)
    again = CliRunner().invoke(cli.app, command)
    said = again.stderr + again.stdout
    assert "changed while wowlab read it" not in said, (again.exit_code, said)
