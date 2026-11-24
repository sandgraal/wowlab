# Probe from review of m21/03-links; reproduces `char link list` failing (exit 1) when no game folder is found, since round 1's "gone" check catches only CliError.
"""Before round 1, `char link list` without `--character` did not read the
game folder at all. Round 1 looks for it to list addresses kept for a
character folder that is gone, and means to say nothing about them when no
game folder is found (`except CliError: inst = None`). But `_discover`
calls `install.discover`, which raises `install.InstallNotFoundError` (a
`WowlabError`, not a `CliError`), so the whole listing fails.

Constructed (L8): no `WOWLAB_WOW_ROOT`, and the default search locations
replaced by none, so nothing on this machine is looked at. The positive
control is the same listing with the capture copy named as the root.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FLAVOR = "_forever_"  # macos-70888's provenance row; tests may name it (L6)
FT = "https://forevertwinking.com/armory/13-sandgraal-rizzen"


@pytest.fixture
def saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(
        platformdirs, "user_config_path", lambda *a, **k: tmp_path / "userconfig" / "wowlab"
    )
    wow = tmp_path / "World of Warcraft"
    wow.mkdir()
    shutil.copy2(FIXTURES / "macos-70888" / ".build.info", wow / ".build.info")
    shutil.copytree(FIXTURES / "macos-70888" / "forever", wow / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(wow))
    monkeypatch.chdir(tmp_path)
    args = ["char", "link", "set", FT, "--character", "Sandgraal-Rizzen", "--yes"]
    done = CliRunner().invoke(cli.app, args)
    assert done.exit_code == 0, done.output
    return wow


def test_list_with_the_game_folder_found(saved: Path) -> None:
    out = CliRunner().invoke(cli.app, ["char", "link", "list", "--json"])
    assert out.exit_code == 0, out.output
    report = json.loads(out.stdout)
    assert [r["url"] for r in report["links"]] == [FT] and report["gone"] == []


def test_list_with_no_game_folder_found_still_lists(
    saved: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(install.ENV_ROOT)
    monkeypatch.setattr(install, "default_roots", lambda *a, **k: iter(()))
    with pytest.raises(install.InstallNotFoundError):
        install.discover(None)  # the precondition: nothing is found
    out = CliRunner().invoke(cli.app, ["char", "link", "list", "--json"])
    assert out.exit_code == 0, (out.output, out.stderr)
    report = json.loads(out.stdout)
    assert [r["url"] for r in report["links"]] == [FT] and report["gone"] == []
