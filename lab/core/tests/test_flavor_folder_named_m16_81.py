"""A flavor folder that becomes a link mid-read is named by its own name,
never "." (M16-81, from the M16-65 security review, #429).

The install is the captured tree (`fixtures/macos/`, rows in
`fixtures/README.md`, L8) copied into `tmp_path`; the link and the outside
folder are constructed (hostile input; ids and names say so). Nothing here
touches a real install.
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

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
CHAR_A = "Labchard-Labrealmg"  # M11-03's capture
CHAR_REL = f"WTF/Account/90000001#6/1/{CHAR_A}/SavedVariables/WowLab.lua"

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
    """Constructed: `folder` moved out of the install, a link left in its
    place. Returns the link."""
    outside = tmp_path / "Elsewhere-constructed" / folder.name
    outside.parent.mkdir(exist_ok=True)
    shutil.move(folder, outside)
    folder.symlink_to(outside, target_is_directory=True)
    return folder


def test_constructed_char_show_names_a_flavor_folder_linked_mid_read(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flavor folder is swapped for a link after the listing, before the
    file is read: the refusal names the flavor folder, not "."."""
    flavor = root / FLAVOR
    real = labaddon.read_char

    def swapped(path: Path, **kwargs: Any) -> Any:
        if not flavor.is_symlink():
            _link_out(flavor, tmp_path)
        return real(path, **kwargs)

    monkeypatch.setattr(labaddon, "read_char", swapped)
    done = CliRunner().invoke(cli.app, ["char", "show", "--character", CHAR_A])
    assert done.exit_code not in (0, None), done.output
    assert done.stderr == (
        f"wowlab: {CHAR_REL} is reached through a linked folder: {FLAVOR} became a link "
        "while wowlab read it; run the command again\n"
    ), done.stderr
    assert done.stdout == ""


def test_the_flavor_folder_itself_is_named_by_its_name(tmp_path: Path) -> None:
    """Both refusals that name the folder relative to the flavor folder."""
    flavor = tmp_path / "Flavor-constructed"
    path = flavor / "WTF" / "Config.wtf"
    through = snapshot.LinkedPathError(path, flavor, final=False, within=flavor)
    assert str(through) == (
        "WTF/Config.wtf is reached through a linked folder, Flavor-constructed; wowlab does "
        "not read through links, so it reads this only when the real folder is there"
    )
    changed = snapshot.LinkedPathError(path, flavor, final=False).changed_while_read(flavor)
    assert str(changed) == (
        "WTF/Config.wtf is reached through a linked folder: Flavor-constructed became a link "
        "while wowlab read it; run the command again"
    )
    assert changed.folder == flavor
    # A folder below the flavor folder keeps its relative name.
    below = snapshot.LinkedPathError(path, flavor / "WTF", final=False).changed_while_read(flavor)
    assert str(below).startswith("WTF/Config.wtf is reached through a linked folder: WTF became")
