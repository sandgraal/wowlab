# Probe from review of m13/45-why-not-a-flavor; reproduces `explain _retail_` (relative) beside a real flavor saying neither link nor not taken
"""M13-45, fifth bullet: `explain <linked flavor>` and `explain
<linked>/WTF/Config.wtf` print the link verdict's words first, then the
not-taken line. On the ticket's own acceptance install (a symlinked flavor
folder with a real one beside it), the relative spelling the ticket uses is
judged inside the one real flavor (`judged = in_flavor`, M13-29), so the
verdict is "wowlab does not know what this file is", `verdict.link` is
false, `not_taken` is absent, and the text is main's "no file-map row:
no file-map row matches". Only the absolute spelling (the one
`test_not_taken.py` covers) gets the M13-45 words.

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`. Constructed (L8, boundary): the `_retail_` symlink beside it,
pointing at a second copy outside the install. Nothing reads or writes a
real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

CAPTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (L6: tests may name it)
LINKED = "_retail_"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import platformdirs

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "ud" / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)  # outside the install: a relative PATH is read in it


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    target = tmp_path / "elsewhere" / "retail"
    shutil.copytree(CAPTURE / "forever", target)
    try:
        (root / LINKED).symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    return root


def _explain(path: str, root: Path) -> tuple[str, dict[str, Any]]:
    text = CliRunner().invoke(cli.app, ["explain", path, "--root", str(root)])
    data = CliRunner().invoke(cli.app, ["explain", path, "--root", str(root), "--json"])
    assert text.exit_code == 0 and data.exit_code == 0, (text.output, data.output)
    return text.stdout, json.loads(data.stdout)


@pytest.mark.parametrize("below", ["", "/WTF/Config.wtf"], ids=["folder", "file-inside"])
def test_positive_control_absolute_spelling_says_link_and_not_taken(
    tmp_path: Path, below: str
) -> None:
    root = _install(tmp_path)
    text, data = _explain(f"{root / LINKED}{below}", root)
    assert data["verdict"]["link"] is True
    assert data["not_taken"]["name"] == LINKED
    assert f"  {LINKED}: {data['not_taken']['note']}" in text.splitlines()


@pytest.mark.parametrize("below", ["", "/WTF/Config.wtf"], ids=["folder", "file-inside"])
def test_relative_spelling_says_link_and_not_taken(tmp_path: Path, below: str) -> None:
    root = _install(tmp_path)
    path = f"{LINKED}{below}"
    text, data = _explain(path, root)
    assert data["result"]["base"] == "root"  # found at the install root
    assert data["verdict"]["link"] is True, data["verdict"]
    assert data.get("not_taken", {}).get("name") == LINKED, data.get("not_taken")
    assert text.splitlines()[:2] == [
        f"{path}: {data['verdict']['words']}",
        f"  {LINKED}: {data['not_taken']['note']}",
    ]
