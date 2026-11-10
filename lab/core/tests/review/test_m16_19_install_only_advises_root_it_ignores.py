# Probe from review of m16/19-errors-in-words; reproduces `sv dump --install-only --root <a real install>` refused with advice to "name the install folder ... with --root", the option it was given and cannot use.
"""M16-19 makes every no-install line end with "name the install folder
(the one that holds .build.info) with --root, or set WOWLAB_WOW_ROOT to
it". Under `sv dump --install-only` the install is the one wowlab finds by
itself, without `--root` (`WOWLAB_WOW_ROOT` or the default places), and a
`--root` is only checked against it. A player who passes `--root` naming a
real install, with no `WOWLAB_WOW_ROOT`, is refused and told to do what
they did; only setting the variable helps. The line may still name
`--root` (the ticket asks every no-install error to), but must not offer it
as the way out.

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`; the defaults are constructed (none, labelled). Nothing touches
a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
FILE = "WTF/Account/90000001#6/SavedVariables/Blizzard_AuctionHouseUI.lua"
ADVICE = "name the install folder (the one that holds .build.info) with --root"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "userdata")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.setattr(install, "default_roots", lambda **k: ())  # constructed: no defaults
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    return root


def test_positive_control_variable_set_reads_the_file(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    result = CliRunner().invoke(
        cli.app, ["sv", "dump", "--install-only", "--root", str(root), FILE]
    )
    assert result.exit_code == 0, result.stderr


def test_install_only_with_root_given_does_not_advise_root(root: Path) -> None:
    result = CliRunner().invoke(
        cli.app, ["sv", "dump", "--install-only", "--root", str(root), FILE]
    )
    assert result.exit_code == cli.EXIT_REFUSED
    assert "WOWLAB_WOW_ROOT" in result.stderr
    # Today the line ends "...with --root, or set WOWLAB_WOW_ROOT to it".
    assert ADVICE not in result.stderr, result.stderr
