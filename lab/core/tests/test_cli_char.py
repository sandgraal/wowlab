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

import json
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
    assert (
        f"Character: {SECOND} in account {ACCOUNT} (the WowLab.lua with the newest modification "
        "time; a wowlab restore also sets it; choose another with --character)\n"
    ) in out
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
    assert (
        f"no WowLab.lua in 1/{NO_ADDON}. Character folders in account {ACCOUNT} that have one: "
        f"{SECOND}, {FIRST}."
        in " ".join(result.stderr.split())
        or f"no WowLab.lua in 1/{NO_ADDON}. Character folders in account {ACCOUNT} that have "
        f"one: {FIRST}, {SECOND}."
        in " ".join(result.stderr.split())
    )
    assert (
        "If none is listed: install the lab-addon (wowlab addon install lab), log in on the "
        "character, then log out or /reload." in " ".join(result.stderr.split())
    )


def test_constructed_first_name_alone_matches_the_realm_name_twin(flavor: Path) -> None:
    # `Labchard` alone names the `<Realm>/<First>/` twin folder, which holds no
    # WowLab.lua; the message lists the folders that do.
    result = run("char", "show", "--character", "Labchard")
    assert result.exit_code == 1
    message = " ".join(result.stderr.split())
    assert "no WowLab.lua in Labrealmb Partb Partc Partd/Labchard." in message
    assert FIRST in message and SECOND in message
    assert "has not written" not in message


def test_constructed_no_lab_file_lists_none(flavor: Path) -> None:
    _lab_file(flavor, FIRST).unlink()
    _lab_file(flavor, SECOND).unlink()
    result = run("char", "show", "--character", "Labchard")
    assert result.exit_code == 1
    assert "that have one: none. If none is listed:" in " ".join(result.stderr.split())


def test_constructed_tie_on_modification_time_is_mentioned(flavor: Path) -> None:
    stamp = 1_500_000_000_000_000_000
    os.utime(_lab_file(flavor, FIRST), ns=(stamp, stamp))
    os.utime(_lab_file(flavor, SECOND), ns=(stamp, stamp))
    out = ok("char", "show").stdout
    first_line = out.splitlines()[0]
    assert "; tied with 1/" in first_line
    assert "on that time, taken by path order; choose another with --character)" in first_line


def test_constructed_huge_integer_exits_1_without_traceback(flavor: Path) -> None:
    target = _lab_file(flavor, FIRST)
    data = target.read_bytes()
    target.write_bytes(data.replace(b'["loads"] = 4,', b'["loads"] = 0x' + b"F" * 4000 + b","))
    result = run("char", "show", "--character", FIRST)
    assert result.exit_code == 1
    assert "probe.loads" in result.stderr
    assert "Traceback" not in result.stderr + result.stdout
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_constructed_unknown_character_is_a_usage_error(flavor: Path) -> None:
    result = run("char", "show", "--character", "Nobody")
    assert result.exit_code == 2
    assert "no character folder 'Nobody'" in result.stderr


def test_constructed_schema_3_capture_is_refused(flavor: Path) -> None:
    # Schema 2 is known since M11-29, so the first unknown schema is 3.
    _lab_file(flavor, FIRST).write_bytes(b'\r\nWowLabCharDB = {\r\n["schema"] = 3,\r\n}\r\n')
    result = run("char", "show", "--character", FIRST)
    assert result.exit_code == 1
    assert "WowLabCharDB is schema 3, and this reader knows schema 1, 2 only" in result.stderr
    assert result.stdout == ""


def test_constructed_schema_2_capture_is_shown(flavor: Path) -> None:
    """M11-29: the real capture with only its schema number changed to 2 reads
    and prints as schema 1 did (schema 2 adds keys on customization only)."""
    target = _lab_file(flavor, FIRST)
    before = run("char", "show", "--character", FIRST)
    assert before.exit_code == 0
    data = target.read_bytes()
    assert data.count(b'["schema"] = 1,') == 1
    stat = target.stat()
    target.write_bytes(data.replace(b'["schema"] = 1,', b'["schema"] = 2,'))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # the time is printed
    after = run("char", "show", "--character", FIRST)
    assert after.exit_code == 0, after.stderr
    assert after.stdout == before.stdout
    shown = run("char", "show", "--character", FIRST, "--json")
    assert shown.exit_code == 0
    assert json.loads(shown.stdout)["record"]["schema"] == 2


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


def test_constructed_long_non_ascii_reason_reads_escaped_and_flagged(flavor: Path) -> None:
    # M11-27 (c): a long reason with a bidi override, a newline escape and an
    # invalid UTF-8 byte (a long Lua error the addon stored) no longer refuses
    # the file; the terminal and the JSON get printable ASCII only.
    target = _lab_file(flavor, FIRST)
    reason = b"Core.lua:12: CANARY\\n\xe2\x80\xae\xff" + b"x" * 3000
    data = target.read_bytes()
    old = b'["absent"] = "no barber-shop visit recorded with the addon enabled",'
    assert data.count(old) == 1
    target.write_bytes(data.replace(old, b'["absent"] = "' + reason + b'",'))
    out = ok("char", "show", "--character", FIRST).stdout
    assert all(" " <= ch <= "~" for ch in out.replace("\n", ""))
    assert "Customization: absent (Core.lua:12: CANARY\\x0a\\xe2\\x80\\xae\\xff" in out
    assert (
        "[the reason is 3024 bytes in the file; shown with each byte outside printable ASCII "
        "written as \\xHH and each backslash as \\\\, and cut to at most 1024 characters; "
        "the file is unchanged]"
    ) in " ".join(out.split())
    assert "Gear (slots 1 to 19" in out and "Professions (by skill line)" in out
    result = ok("char", "show", "--character", FIRST, "--json")
    assert all(" " <= ch <= "~" for ch in result.stdout.replace("\n", ""))
    report = cli.CharShowReport.model_validate_json(result.stdout)
    assert report.record == labaddon.read_char(target)
    section = report.record.customization
    assert isinstance(section, labaddon.AbsentSection)
    assert section.absent_clipped == labaddon.ClippedReason(
        original_length=3024, escaped=True, truncated=True
    )
