"""`dressing` in `wowlab char gear --json` (M15-02, §16.3's amendment of 2026-10-19).

The install is the M15-01 capture (`macos-70450/`, files-only) copied into
`tmp_path` inside the install root borrowed from `macos/`, as its fixture
row says. The room is picked from the version discovery reads in that
root's `.build.info`; the constructed case rewrites that version in the copy
to one no band covers. The text form is graded by the snapshots in
`test_cli_char_gear.py`, whose two `macos-70450` text files were written by
`main`'s code before this field existed. Nothing here reads or writes a real
install, and nothing is fetched.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, dressing, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
DERIVED = json.loads(
    (FIXTURES / "macos-70450" / "derived" / "dressing-room.json").read_text(encoding="utf-8")
)
ADDRESSES = {e["record"].split("/")[-3]: e["address"] for e in DERIVED["examples"]}

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        platformdirs, "user_data_path", lambda *a, **k: tmp_path / "userdata" / "wowlab"
    )
    monkeypatch.setattr(
        platformdirs, "user_config_path", lambda *a, **k: tmp_path / "userconfig" / "wowlab"
    )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    flavor = root / FLAVOR
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70450" / "forever", flavor)
    shutil.copy2(FIXTURES / "macos" / "forever" / ".flavor.info", flavor / ".flavor.info")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _gear(character: str) -> dict[str, Any]:
    result = runner.invoke(cli.app, ["char", "gear", "--character", character, "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    data: dict[str, Any] = json.loads(result.stdout)
    return data


@pytest.mark.parametrize("character", sorted(ADDRESSES))
def test_char_gear_json_carries_the_recorded_address(root: Path, character: str) -> None:
    data = _gear(character)
    assert list(data)[-1] == "dressing"
    assert list(data["dressing"]) == ["url", "site", "note"]
    report = cli.CharGearReport.model_validate(data)
    assert isinstance(report.dressing, dressing.DressingLink)
    assert report.dressing.url == ADDRESSES[character]
    assert report.dressing.site == "Wowhead"


def test_an_unlisted_version_gives_the_item_ids_constructed(root: Path) -> None:
    info = root / ".build.info"
    data = info.read_bytes()
    assert data.count(b"|1.60.1.69913|") == 1
    info.write_bytes(data.replace(b"|1.60.1.69913|", b"|11.2.5.69913|"))
    out = _gear("Sangraal-Sett")
    assert list(out["dressing"]) == ["items", "note"]
    report = cli.CharGearReport.model_validate(out)
    assert isinstance(report.dressing, dressing.DressingItems)
    assert report.dressing.items == [str(s.item_id) for s in report.slots]
    assert "client version 11.2.5.69913" in report.dressing.note
