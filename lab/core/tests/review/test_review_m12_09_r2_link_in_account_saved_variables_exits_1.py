# Probe from review of m12/09-char-list; reproduces `char list` exiting 1 and saying "a character folder behind one is not listed" for a symlinked or FIFO file directly in the account's own SavedVariables/ folder
"""Round 2 of the M12-09 review.

The §14.4 amendment on this branch says what `char list` names and exits 1
for: a character's `WowLab.lua` it cannot read (or that is a link, a FIFO or
a folder), a character folder or its `SavedVariables/` that is a link or
cannot be listed, and "a link or unlistable folder above the character
folders (`WTF/`, `WTF/Account/`, an account folder, a realm or digits
folder; a linked name in an account folder only where the file map would
take it for one, so a linked `config-cache.wtf` hides nothing)". The
docstring of `labaddon._place` says the rest is None: "another addon's
file, a character's other files, the account's own SavedVariables/".

`_place` checks for the account's own `SavedVariables/` only at its own
depth (`len(below) == 2`). One level down, a path such as
`WTF/Account/<A>/SavedVariables/WeakAuras.lua` has three parts below
`Account/`, so it is taken for a character folder in a realm folder named
`SavedVariables`; no such realm is in the layout's typed lists, so `survey`
puts it in `not_looked_at`. `char list` then exits 1 and prints "wowlab
could not look inside 1 folder under WTF/ (named on stderr): a character
folder behind one is not listed". Neither is true: the entry is another
addon's file, and no character folder can be behind it.

The trigger is ordinary: players link account-wide SavedVariables files
(WeakAuras, for example) to share them between accounts or installs. The
same link inside a character's `SavedVariables/` is correctly passed over
(positive control 1), and a link that can hide characters, a linked
digits folder in the account folder, is still named with exit 1
(positive control 2).

Constructed (L8): the `macos` capture copied into `tmp_path`, with a link
or FIFO added; the link points at a file outside the flavor folder. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon
from wowlab_core.layout import Layout

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)
ACCOUNT_DIR = "WTF/Account/90000001#6"
FIRST = "1/Labchard-Labrealmg"
SECOND = "1/Labcharb-Labrealmf"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def _outside(tmp_path: Path) -> Path:
    target = tmp_path / "shared" / "WeakAuras.lua"
    target.parent.mkdir()
    target.write_bytes(b"WeakAurasSaved = {}\n")
    return target


def _symlink(link: Path, target: Path, *, is_dir: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def _survey(flavor: Path) -> labaddon.AllCharacters:
    return labaddon.survey(Layout(flavor, install_root=flavor.parent))


def _assert_both_listed_exit_0(flavor: Path) -> None:
    found = _survey(flavor)
    assert [n.path for n in found.not_looked_at] == [], found.not_looked_at
    assert {e.character for e in found.characters} == {FIRST, SECOND}
    text = run("char", "list")
    assert text.exit_code == 0, (text.stdout, text.stderr)
    assert "could not look inside" not in text.stdout + text.stderr, text.output


def test_constructed_linked_addon_file_in_the_account_saved_variables_is_not_named(
    flavor: Path, tmp_path: Path
) -> None:
    _symlink(flavor / ACCOUNT_DIR / "SavedVariables" / "WeakAuras.lua", _outside(tmp_path))
    _assert_both_listed_exit_0(flavor)


def test_constructed_fifo_in_the_account_saved_variables_is_not_named(flavor: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("no os.mkfifo on this platform")
    os.mkfifo(flavor / ACCOUNT_DIR / "SavedVariables" / "Other.lua")
    _assert_both_listed_exit_0(flavor)


def test_constructed_positive_control_same_link_in_a_character_saved_variables(
    flavor: Path, tmp_path: Path
) -> None:
    _symlink(flavor / ACCOUNT_DIR / FIRST / "SavedVariables" / "WeakAuras.lua", _outside(tmp_path))
    _assert_both_listed_exit_0(flavor)


def test_constructed_positive_control_a_linked_digits_folder_is_named(
    flavor: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-digits"
    shutil.copytree(flavor / ACCOUNT_DIR / "1", outside)
    _symlink(flavor / ACCOUNT_DIR / "2", outside, is_dir=True)
    found = _survey(flavor)
    assert [n.path for n in found.not_looked_at] == [f"{ACCOUNT_DIR}/2"]
    text = run("char", "list")
    assert text.exit_code == 1
    assert f"{ACCOUNT_DIR}/2" in text.stderr
