# Probe from review of m16/49-readers-from-root; reproduces two SavedVariables reads that still follow a linked folder out of the install after M16-49
"""Constructed links and outside folders (hostile input); the install is the
captured tree `fixtures/macos/` copied into `tmp_path` (provenance row in
`fixtures/README.md`). Nothing reads or writes a real install.

1. `char show` reads `WowLab.lua` through `labaddon.read_char`, which still
   checks only the file itself: the account folder swapped for a link between
   the listing and the read is followed, and what is behind the link is shown.
   The same final-component-only read is used by `sv merge` (`cli._read_sv`),
   the char list (`labaddon._Reader.read`, the WTF counts) and the undo
   detail (`cli._disk_now`, `cli._on_disk`).
2. `sv dump` of a path inside the install spelled with a `..` falls back to
   the final-component check, follows a linked folder, and its note says the
   file "is not in an install wowlab found", which is not so.
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

from wowlab_core import cli, install, labaddon

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
SV_REL = f"WTF/Account/{ACCOUNT}/SavedVariables/Blizzard_AuctionHouseUI.lua"
MARKER = b"-- OUTSIDE-THE-INSTALL-constructed\n"

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
    """Move `folder` out of the install, mark every `.lua` below it, and leave
    a link in its place."""
    outside = tmp_path / "Elsewhere-constructed" / folder.name
    outside.parent.mkdir(exist_ok=True)
    shutil.move(folder, outside)
    for lua in outside.rglob("*.lua"):
        lua.write_bytes(MARKER + lua.read_bytes())
    folder.symlink_to(outside, target_is_directory=True)
    return outside


def test_char_show_refuses_an_account_folder_swapped_for_a_link(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = root / FLAVOR / "WTF" / "Account" / ACCOUNT
    real = labaddon.read_char
    seen: list[bytes] = []

    def swapped(path: Path, **kwargs: Any) -> Any:
        if not account.is_symlink():
            _link_out(account, tmp_path)
        seen.append(Path(path).read_bytes()[: len(MARKER)])  # what the read will see
        return real(path, **kwargs)

    monkeypatch.setattr(labaddon, "read_char", swapped)
    done = CliRunner().invoke(cli.app, ["char", "show", "--json"])
    assert seen == [MARKER], seen  # the swap happened: the file is outside now
    # Fixed: refused, the linked folder named, nothing from outside shown.
    assert "linked folder" in done.output, done.output


def test_sv_dump_with_dotdot_inside_the_install_is_not_read_through_a_link(
    root: Path, tmp_path: Path
) -> None:
    _link_out(root / FLAVOR / "WTF" / "Account" / ACCOUNT, tmp_path)
    given = root / FLAVOR / "WTF" / ".." / SV_REL
    done = CliRunner().invoke(cli.app, ["sv", "dump", str(given)])
    if done.exit_code == 0:
        # Read anyway: the note must not say the file is outside every install.
        assert "not in an install wowlab found" not in done.stderr, done.stderr
