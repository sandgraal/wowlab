# Probe from review of m12/09-char-list; reproduces `char list` giving install advice and exiting 0 when the flavor folder cannot be listed, so WTF/ was never looked at
"""Round 2 of the M12-09 review.

The conductor's ruling for this ticket, in the branch's §14.4 amendment:
whatever wowlab could not look at is named and makes `char list` exit 1,
and "with nothing listed and something not looked inside, the text says so
instead of giving install advice".

`Layout.wtf_walk()` finds `WTF/` by listing the flavor folder. When that
listing fails (a flavor folder that can be entered but not read: mode
0o111), the layout records a walk error whose path is the flavor folder
itself, the empty string. `labaddon._place` only considers paths whose
first part is `WTF`, so it drops that error; `survey` returns no
characters and nothing in `not_looked_at`, and `char list` prints "No
character folder in any account has a WowLab.lua (install the lab-addon
...)" and exits 0, in text and in `--json`. Install discovery still works
there (it opens `.flavor.info` by name), so the command gets this far.

Positive control: the same mode on `WTF/` itself is named ("could not look
inside WTF") and exits 1 today.

Constructed (L8): the `macos` capture copied into `tmp_path` with one
folder's mode changed and restored. POSIX only, not as root. Nothing reads
or writes a real install.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permissions, and not as root",
)

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


def _entered_not_listed(folder: Path) -> tuple[Any, Any]:
    folder.chmod(0o111)
    try:
        return run("char", "list"), run("char", "list", "--json")
    finally:
        folder.chmod(0o755)


def test_constructed_positive_control_wtf_entered_not_listed_is_named(flavor: Path) -> None:
    text, as_json = _entered_not_listed(flavor / "WTF")
    assert text.exit_code == 1 and as_json.exit_code == 1, text.output
    assert "could not look inside WTF" in text.stderr
    assert "install the lab-addon" not in text.stdout


def test_constructed_flavor_folder_entered_not_listed_is_named_exit_1(flavor: Path) -> None:
    text, as_json = _entered_not_listed(flavor)
    assert "install the lab-addon" not in text.stdout, (text.exit_code, text.stdout)
    assert text.exit_code == 1, (text.stdout, text.stderr)
    assert as_json.exit_code == 1, as_json.stdout
    assert cli.CharListReport.model_validate_json(as_json.stdout).not_looked_at
