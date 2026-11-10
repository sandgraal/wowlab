# Probe from review of m16/49-readers-from-root; reproduces `sv dump` of an
# in-install path spelled with `..` dropping the from-root walk, following a
# linked folder and saying the file "is not in an install wowlab found".
"""Constructed hostile input (L8): the account folder of the captured install
(`fixtures/macos/`, README rows) is moved out and a link left in its place;
the file behind the link is a constructed marker. The same file asked for by
its plain spelling is refused (positive control); asked for with a `..`
below the install root, it is read through the link."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)
ACCOUNT = "90000001#6"
SV_REL = f"WTF/Account/{ACCOUNT}/SavedVariables/Blizzard_AuctionHouseUI.lua"
MARKER = b'OUTSIDE_MARKER = "behind the link"\n'

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "ud")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([]))
    r = tmp_path / "World of Warcraft"
    r.mkdir()
    shutil.copy2(FIXTURES / ".build.info", r / ".build.info")
    shutil.copytree(FIXTURES / "forever", r / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(r))
    monkeypatch.chdir(tmp_path)
    folder = r / FLAVOR / "WTF" / "Account" / ACCOUNT
    outside = tmp_path / "Elsewhere-constructed" / ACCOUNT
    outside.parent.mkdir()
    shutil.move(folder, outside)
    folder.symlink_to(outside, target_is_directory=True)
    (outside / "SavedVariables" / "Blizzard_AuctionHouseUI.lua").write_bytes(MARKER)
    return r


def test_positive_control_plain_spelling_is_refused(root: Path) -> None:
    done = CliRunner().invoke(cli.app, ["sv", "dump", str(root / FLAVOR / SV_REL)])
    assert done.exit_code not in (0, None), done.output
    assert "OUTSIDE_MARKER" not in done.stdout
    assert "linked folder" in done.stderr


@pytest.mark.parametrize(
    "spelling",
    [
        "absolute-dotdot",  # <root>/<flavor>/WTF/../WTF/Account/<linked>/...
        "relative-dotdot",  # run from the flavor folder: WTF/../WTF/Account/<linked>/...
    ],
)
def test_dotdot_spelling_inside_the_install_is_not_read_through_the_link(
    root: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    if spelling == "absolute-dotdot":
        given = str(root / FLAVOR / "WTF" / ".." / SV_REL)
    else:
        monkeypatch.chdir(root / FLAVOR)
        given = "WTF/../" + SV_REL
    done = CliRunner().invoke(cli.app, ["sv", "dump", given])
    assert "OUTSIDE_MARKER" not in done.stdout, (done.stdout, done.stderr)
