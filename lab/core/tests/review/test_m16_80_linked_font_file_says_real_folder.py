# Probe from review of m16/80-link-notes; reproduces a linked font file whose `tree` note says "real folder"
"""`tree`'s link note picks "real folder" or "real file" from the file map
(`cli._link_stands_for`, M16-80). Under `Fonts/` (and `Screenshots/`,
`Interface/AddOns/<addon>/`) one row covers files and folders alike, so a
link that stands in place of a font file, the file the `fonts` row names
(`FRIZQT__.TTF`), is told "it reads this only when the real folder is
there". The ticket asks for the word chosen by kind; a font file is a file.

Positive control: a linked SavedVariables file in the same tree says "real
file", as the branch's own test pins.

Constructed (L8, boundary): the install is the captured tree
(`fixtures/macos/`) copied into `tmp_path`, plus a constructed `Fonts/`
folder holding one symlink to a constructed file outside the install.
Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wowlab_core import cli

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SYNDICATOR = "WTF/Account/90000001#6/SavedVariables/Syndicator.lua"
FILE_END = "so it reads this only when the real file is there"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import platformdirs

    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    install = tmp_path / "wow"
    install.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install / ".build.info")
    shutil.copytree(CAPTURE / "forever", install / FLAVOR)
    monkeypatch.chdir(tmp_path)
    return install


def _notes(root: Path) -> dict[str, Any]:
    result = CliRunner().invoke(cli.app, ["tree", "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output
    return {i["path"]: i["note"] for i in json.loads(result.stdout)["items"]}


def _link_to_a_file(link: Path, tmp_path: Path, name: str) -> None:
    target = tmp_path / "shared" / name
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"constructed\n")
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists():
        link.unlink()
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def test_control_a_linked_saved_variables_file_says_real_file_constructed(
    root: Path, tmp_path: Path
) -> None:
    _link_to_a_file(root / FLAVOR / SYNDICATOR, tmp_path, "Syndicator.lua")
    assert _notes(root)[SYNDICATOR].endswith(FILE_END)


def test_a_linked_font_file_says_real_file_constructed(root: Path, tmp_path: Path) -> None:
    _link_to_a_file(root / FLAVOR / "Fonts" / "FRIZQT__.TTF", tmp_path, "FRIZQT__.TTF")
    assert _notes(root)["Fonts/FRIZQT__.TTF"].endswith(FILE_END)
