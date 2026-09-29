"""`wowlab char show` (M11-04, docs/LAB_PLAN.md §13.1) through Typer's runner.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
with its flavor folder named as its provenance row says (`_classic_beta_`),
as `test_cli.py` does; it holds the M11-03 lab-addon capture (the account
`WowLab.lua` and two characters'). Nothing here reads or writes a real
install: `WOWLAB_WOW_ROOT` points at the copy and the user data directory is
redirected into `tmp_path`. Inputs labelled `constructed` are boundary cases
(L8): a schema-2 file, a character with no `WowLab.lua`, a missing account
file.
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

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCOUNT_DIR = f"WTF/Account/{ACCOUNT}"
FIRST = "1/Labchard-Labrealmg"
SECOND = "1/Labcharb-Labrealmf"
NO_ADDON = "Labcharb-Labrealmd"  # a character folder without the lab-addon's file

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def flavor(root: Path) -> Path:
    return root / FLAVOR


def _lab_file(flavor: Path, character: str) -> Path:
    return flavor / ACCOUNT_DIR / character / "SavedVariables" / "WowLab.lua"


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


@pytest.mark.parametrize("character", [FIRST, SECOND])
def test_char_show_renders_each_capture(flavor: Path, character: str) -> None:
    out = ok("char", "show", "--character", character).stdout
    assert f"Character: {character} in account {ACCOUNT}\n" in out
    assert f"File: {ACCOUNT_DIR}/{character}/SavedVariables/WowLab.lua" in out
    assert "(the file's modification time)" in out
    assert "Client: version 1.60.1, build 70009, interface 16001" in out
    assert "Legacy candidates: present, nothing spent, 0 points available" in out
    assert "Customization: absent (no barber-shop visit recorded with the addon enabled)" in out
    assert "Account WowLabDB: schema 1, nothing else" in out
    assert "sheet" not in out.casefold()


def test_char_show_takes_the_folder_name_alone(flavor: Path) -> None:
    out = ok("char", "show", "--character", "Labchard-Labrealmg").stdout
    assert f"Character: {FIRST} in account {ACCOUNT}\n" in out
    assert "Probe: loads 4" in out


@pytest.mark.parametrize("character", [FIRST, SECOND])
def test_char_show_json_validates(flavor: Path, character: str) -> None:
    result = ok("char", "show", "--character", character, "--json")
    report = cli.CharShowReport.model_validate_json(result.stdout)
    assert report.character == character
    assert report.account == ACCOUNT
    assert report.chosen_by == "--character"
    assert report.flavor_folder == FLAVOR
    assert report.record.schema_ == 1
    assert report.account_record is not None and report.account_record.schema_ == 1
    assert report.account_file == f"{ACCOUNT_DIR}/SavedVariables/WowLab.lua"
    assert report.unknown_keys == []
    assert report.skip_known == [] and report.skip_ignored == []
    assert report.customization_loads_ago is None
    original = labaddon.read_char(_lab_file(flavor, character))
    assert report.record == original


def test_char_show_without_character_picks_the_latest_capture(flavor: Path) -> None:
    os.utime(_lab_file(flavor, FIRST), ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    os.utime(_lab_file(flavor, SECOND), ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    out = ok("char", "show").stdout
    assert f"Character: {SECOND} in account {ACCOUNT} (the WowLab.lua written last" in out
    report = cli.CharShowReport.model_validate_json(ok("char", "show", "--json").stdout)
    assert report.character == SECOND and report.chosen_by == "latest"
    os.utime(_lab_file(flavor, FIRST), ns=(3_000_000_000_000_000_000, 3_000_000_000_000_000_000))
    assert f"Character: {FIRST} in account" in ok("char", "show").stdout


def test_char_show_reads_without_writing(root: Path) -> None:
    before = _state(root)
    ok("char", "show", "--character", FIRST)
    ok("char", "show", "--character", SECOND, "--json")
    ok("char", "show")
    assert _state(root) == before


def test_constructed_character_without_lab_file_exits_1(flavor: Path) -> None:
    result = run("char", "show", "--character", NO_ADDON)
    assert result.exit_code == 1
    assert "has not written for this character" in result.stderr


def test_constructed_unknown_character_is_a_usage_error(flavor: Path) -> None:
    result = run("char", "show", "--character", "Nobody")
    assert result.exit_code == 2
    assert "no character folder 'Nobody'" in result.stderr


def test_constructed_schema_2_capture_is_refused(flavor: Path) -> None:
    _lab_file(flavor, FIRST).write_bytes(b'\r\nWowLabCharDB = {\r\n["schema"] = 2,\r\n}\r\n')
    result = run("char", "show", "--character", FIRST)
    assert result.exit_code == 1
    assert "WowLabCharDB is schema 2, and this reader knows schema 1 only" in result.stderr
    assert result.stdout == ""


def test_constructed_tampered_text_is_refused_without_echo(flavor: Path) -> None:
    target = _lab_file(flavor, FIRST)
    data = target.read_bytes().replace(
        b'["schema"] = 1,', b'["schema"] = 1,\r\n["note"] = "CANARY\x1b[31m",', 1
    )
    target.write_bytes(data)
    result = run("char", "show", "--character", FIRST)
    assert result.exit_code == 1
    assert "the unknown key note holds text" in result.stderr
    assert "CANARY" not in result.stderr + result.stdout


def test_constructed_no_account_file_is_reported(flavor: Path) -> None:
    (flavor / ACCOUNT_DIR / "SavedVariables" / "WowLab.lua").unlink()
    out = ok("char", "show", "--character", FIRST).stdout
    assert "Account WowLabDB: no account WowLab.lua" in out
    report = cli.CharShowReport.model_validate_json(
        ok("char", "show", "--character", FIRST, "--json").stdout
    )
    assert report.account_record is None and report.account_file is None


def test_constructed_no_lab_file_anywhere_exits_1(flavor: Path) -> None:
    _lab_file(flavor, FIRST).unlink()
    _lab_file(flavor, SECOND).unlink()
    result = run("char", "show")
    assert result.exit_code == 1
    assert "no character in account" in result.stderr
