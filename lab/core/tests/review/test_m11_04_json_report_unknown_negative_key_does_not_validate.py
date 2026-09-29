# Probe from review of m11/04-labaddon-reader; reproduces `wowlab char show --json` emitting a report its own model refuses when an unknown key holds a table with a negative number key
"""M11-04 acceptance: "`--json` validates". An unknown key is kept when its
value holds numbers, booleans and tables of them (§13.1 amendment of
2026-09-29), and `_check_unknown` accepts any integer key inside such a
table, `-1` included. JSON has only string keys, so the report carries
`"-1"`, and reading it back runs `_check_unknown` on the string `"-1"`, which
`_NESTED_NAME` (`[A-Za-z0-9_]{1,64}`) refuses. The same file therefore reads
and prints, but its `--json` output does not validate as `CharShowReport`.

Constructed (L8, hostile/boundary): the captured tree (`fixtures/macos/`)
copied into `tmp_path` as `test_cli_char.py` does, with one unknown key
added to the first character's `WowLab.lua` at the byte level. Nothing reads
or writes a real install. Positive control: the same key with a positive
number key validates.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
CHARACTER = "1/Labchard-Labrealmg"
LAB = f"WTF/Account/90000001#6/{CHARACTER}/SavedVariables/WowLab.lua"

runner = CliRunner()


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        platformdirs, "user_data_path", lambda *a, **k: tmp_path / "userdata" / "wowlab"
    )
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def _report_with_key(flavor: Path, key: bytes) -> str:
    target = flavor / LAB
    data = target.read_bytes()
    marker = b'["schema"] = 1,'
    assert data.count(marker) == 1
    extra = b'["schema"] = 1,\r\n["future"] = {\r\n[' + key + b"] = 5,\r\n},"
    target.write_bytes(data.replace(marker, extra))
    result = runner.invoke(cli.app, ["char", "show", "--character", CHARACTER, "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    return result.stdout


def test_positive_control_positive_number_key_validates(flavor: Path) -> None:
    report = cli.CharShowReport.model_validate_json(_report_with_key(flavor, b"2"))
    assert report.unknown_keys == ["future"]


def test_constructed_negative_number_key_report_validates(flavor: Path) -> None:
    report = cli.CharShowReport.model_validate_json(_report_with_key(flavor, b"-1"))
    assert report.unknown_keys == ["future"]
