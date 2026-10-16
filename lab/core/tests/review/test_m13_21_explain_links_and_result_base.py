# Probe from review of m13/21-verdict-in-core; reproduces `explain` calling a symlinked folder a restorable file, and the round-2 fallback rewriting `result` in `explain --json` (a non-additive change).
"""Two defects in `wowlab explain` at d0709ff, each with a positive control.

1. `cli._is_folder` never follows a link, so a symlinked folder (the usual
   way an addon author puts a working copy under `Interface/AddOns/`) is not
   a folder, and `verdict_of` answers as for a file: `restorable` True and,
   for an unclassified path, "a whole snapshot restore puts it back like any
   file here". A snapshot captures regular files only and never walks a link
   (`snapshot._walk`, `_is_link_like_dir`), so a restore puts nothing back.

2. M13-21 says the JSON change is additive. On main, a bare relative path
   with no file-map row anywhere reports `result.base == "root"` and
   `result.flavor_folder == null`; the round-2 fallback replaces `result`
   with the in-flavor lookup, so both fields change. The PR's User-facing
   box says "Both are additive".

All inputs are constructed (a copy of the captured macOS tree plus
constructed folders and links under tmp_path).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIX / ".build.info", root / ".build.info")
    shutil.copytree(FIX / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def _explain(path: str) -> dict[str, Any]:
    result = CliRunner().invoke(cli.app, ["explain", path, "--json"])
    assert result.exit_code == 0, result.stdout
    out: dict[str, Any] = json.loads(result.stdout)
    return out


def test_positive_control_a_real_folder_has_no_restorable_answer(flavor: Path) -> None:
    (flavor / "Interface" / "AddOns" / "Real-constructed").mkdir()
    (flavor / "WTF" / "Real-constructed").mkdir()
    assert _explain("Interface/AddOns/Real-constructed")["verdict"]["restorable"] is None
    assert _explain("WTF/Real-constructed")["verdict"]["restorable"] is None


def test_a_symlinked_folder_is_not_called_a_restorable_file(flavor: Path, tmp_path: Path) -> None:
    (tmp_path / "work-a").mkdir()
    (tmp_path / "work-b").mkdir()
    (flavor / "Interface" / "AddOns" / "Linked-constructed").symlink_to(tmp_path / "work-a")
    (flavor / "WTF" / "Linked-constructed").symlink_to(tmp_path / "work-b")
    addon = _explain("Interface/AddOns/Linked-constructed")["verdict"]
    unrowed = _explain("WTF/Linked-constructed")["verdict"]
    assert addon["restorable"] is not True, addon
    assert unrowed["restorable"] is not True, unrowed
    assert "puts it back" not in unrowed["words"], unrowed


def test_positive_control_a_classified_bare_path_reads_in_the_flavor(flavor: Path) -> None:
    result = _explain("WTF/Config.wtf")["result"]
    assert (result["base"], result["flavor_folder"]) == ("flavor", FLAVOR)


def test_an_unclassified_bare_path_keeps_mains_result(flavor: Path) -> None:
    """On origin/main this reports base `root` and no flavor folder."""
    (flavor / "WTF" / "Weird-constructed").mkdir()
    (flavor / "WTF" / "Weird-constructed" / "x.txt").write_bytes(b"x")
    result = _explain("WTF/Weird-constructed/x.txt")["result"]
    assert (result["base"], result["flavor_folder"]) == ("root", None)
