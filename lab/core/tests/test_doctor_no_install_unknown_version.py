"""`doctor` does not offer a lab-addon install into a flavor whose version is
unknown (M16-123).

That install is refused (the TOC's `## Interface:` cannot be filled), so the
next step says the lab-addon cannot be installed there until the game
version is known, and points at the flavor's own lines above it rather than
repeating why the version is unknown.

The install is the one `test_doctor_version_words.py` builds: the captured
tree (`fixtures/macos/`) with, constructed (L8, boundary), a flavor folder
whose product no `.build.info` row names, one whose `.flavor.info` names no
product, and one whose row has a blank version. The user data directory is
redirected and the process table holds no client. Nothing reads or writes a
real install.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from test_doctor_version_words import (
    BLANK_VERSION,
    FLAVOR,
    NO_PRODUCT,
    NO_ROW,
    _doctor,
    _install,
    _isolated,  # noqa: F401 (the autouse fixture: no client, the user data directory redirected)
)

from wowlab_core import cli


def _steps(text: str) -> list[str]:
    return text.split("Next steps:\n", 1)[1].splitlines()


def _command(root: Path, folder: str) -> str:
    """The install command as doctor quotes it for this machine's shell."""
    argv = ["wowlab", "addon", "install", "lab", "--root", str(root), "--flavor", folder]
    return cli._powershell_join(argv) if os.name == "nt" else shlex.join(argv)


def _install_line(root: Path, folder: str) -> str:
    command = _command(root, folder)
    return f"    Lab-addon: not installed. Close the game, then run `{command}`."


def test_no_addon_install_for_a_flavor_with_no_row_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    steps = _steps(_doctor(root))

    at = steps.index(f"  {NO_ROW}:")
    assert steps[at + 1] == (
        "    Lab-addon: not installed. It cannot be installed in this flavor until its game "
        f"version is known; the lines under Flavor {NO_ROW} above say why it is not."
    )
    assert not any("`wowlab addon install" in line and NO_ROW in line for line in steps)
    # The flavor whose version is known keeps its line as before.
    assert steps[steps.index(f"  {FLAVOR}:") + 1] == _install_line(root, FLAVOR)


def test_each_way_a_version_is_unknown_gets_no_install_line_constructed(tmp_path: Path) -> None:
    """No product, and a row with a blank version, as well as no row."""
    root = _install(tmp_path, blank=True, blank_version=True)
    text = _doctor(root)
    steps = _steps(text)

    for folder in (NO_ROW, NO_PRODUCT, BLANK_VERSION):
        assert steps[steps.index(f"  {folder}:") + 1] == (
            "    Lab-addon: not installed. It cannot be installed in this flavor until its "
            f"game version is known; the lines under Flavor {folder} above say why it is not."
        )
        # The lines it points at are there.
        assert f"Flavor {folder}: product " in text
    assert steps[steps.index(f"  {FLAVOR}:") + 1] == _install_line(root, FLAVOR)
    assert sum("`wowlab addon install" in line for line in steps) == 1


def test_json_names_no_command_for_that_flavor_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    data = json.loads(_doctor(root, "--json"))

    lab = {s["flavor_folder"]: s for s in data["next_steps"] if s["item"] == "lab_addon"}
    assert lab[NO_ROW]["state"] == "missing"
    assert lab[NO_ROW]["command"] is None
    assert lab[NO_ROW]["fix"] == cli._no_install_until_version(NO_ROW)
    assert lab[FLAVOR]["state"] == "missing"
    assert lab[FLAVOR]["command"] == _command(root, FLAVOR)
