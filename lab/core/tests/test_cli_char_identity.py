"""`identity` in `wowlab char show` and `wowlab char gear` (M15-01).

The install is the M13-05 capture (`macos-70425/`, schema 3) copied into
`tmp_path`, inside the install root borrowed from `macos/` as its fixture
row says. The schema-4 file is that real record with its `schema` line
changed to 4 and an `identity` table added in the shape
`lab/addon/WowLab/Identity.lua` writes, as text (labelled `constructed`,
L8): no real schema-4 capture exists until the owner runs
`docs/handoffs/M15-01.md`. Nothing here reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
CHARACTER = "1/Labchard-Labrealme"
LAB_FILE = f"WTF/Account/90000001#6/{CHARACTER}/SavedVariables/WowLab.lua"
IDENTITY_TEXT = (
    b'["identity"] = {\r\n["race_id"] = 5,\r\n["sex_raw"] = 3,\r\n["class_id"] = 9,\r\n'
    b'["class_file"] = "WARLOCK",\r\n["events_unregistered"] = {\r\n},\r\n},\r\n'
)
SHOW_FIELDS = [
    "flavor_folder",
    "account",
    "character",
    "chosen_by",
    "file",
    "mtime_ns",
    "account_file",
    "account_record",
    "record",
    "identity",
    "skip_known",
    "skip_ignored",
    "customization_loads_ago",
    "unknown_keys",
    "notes",
]

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
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    flavor = root / FLAVOR
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70425" / "forever", flavor)
    shutil.copy2(FIXTURES / "macos" / "forever" / ".flavor.info", flavor / ".flavor.info")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return flavor


def _make_schema_4(flavor: Path, identity: bytes = IDENTITY_TEXT) -> None:
    path = flavor / LAB_FILE
    data = path.read_bytes()
    assert data.count(b'["schema"] = 3,\r\n') == 1
    path.write_bytes(data.replace(b'["schema"] = 3,\r\n', b'["schema"] = 4,\r\n' + identity))


def ok(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def test_char_show_json_gains_exactly_identity(flavor: Path) -> None:
    data = json.loads(ok("char", "show", "--json").stdout)
    assert list(data) == SHOW_FIELDS
    report = cli.CharShowReport.model_validate(data)
    assert report.identity.class_file is None
    assert report.identity.class_file_absent is not None
    assert report.identity.class_file_absent.startswith("the record is schema 3;")


def test_char_show_text_of_schema_3_has_no_identity_line(flavor: Path) -> None:
    out = ok("char", "show").stdout
    assert "Identity" not in out


def test_constructed_schema_4_char_show(flavor: Path) -> None:
    _make_schema_4(flavor)
    before = ok("char", "show").stdout
    assert before.count("Identity:") == 1
    line = next(x for x in before.splitlines() if x.startswith("Identity:"))
    assert line == (
        "Identity: class WARLOCK (class id 9), race id 5, body type 1 (Enum.UnitSex, from "
        "UnitSex 3)"
    )
    report = cli.CharShowReport.model_validate_json(ok("char", "show", "--json").stdout)
    assert isinstance(report.record, labaddon.CharDBV4)
    assert report.identity == labaddon.CharIdentity(
        race_id=5, sex=1, sex_raw=3, class_id=9, class_file="WARLOCK"
    )
    assert report.unknown_keys == []


def test_constructed_schema_4_char_show_text_is_schema_3_plus_one_line(flavor: Path) -> None:
    as_3 = ok("char", "show").stdout.splitlines()
    _make_schema_4(flavor)
    as_4 = ok("char", "show").stdout.splitlines()
    # The edit changes the file's time, so the "Written:" line is set aside;
    # every other line is the same except the one `Identity:` line.
    written = [i for i, x in enumerate(as_3) if x.startswith("Written:")]
    assert len(written) == 1
    as_4[written[0]] = as_3[written[0]]
    extra = [x for x in as_4 if x not in as_3]
    assert extra == [
        "Identity: class WARLOCK (class id 9), race id 5, body type 1 (Enum.UnitSex, from "
        "UnitSex 3)"
    ]
    as_4.remove(extra[0])
    assert as_4 == as_3


def test_constructed_schema_4_char_gear_json(flavor: Path) -> None:
    _make_schema_4(flavor)
    data = json.loads(ok("char", "gear", "--json").stdout)
    assert data["identity"] == {
        "race_id": 5,
        "race_id_absent": None,
        "sex": 1,
        "sex_absent": None,
        "sex_raw": 3,
        "sex_raw_absent": None,
        "class_id": 9,
        "class_id_absent": None,
        "class_file": "WARLOCK",
        "class_file_absent": None,
    }
    cli.CharGearReport.model_validate(data)


def test_constructed_schema_4_hostile_identity_still_shows(flavor: Path) -> None:
    """A non-numeric race id, a class id out of range and no class file: the
    commands still run, each value absent with a reason."""
    _make_schema_4(
        flavor,
        b'["identity"] = {\r\n["race_id"] = "five",\r\n["sex_raw"] = 1,\r\n'
        b'["class_id"] = 4294967296,\r\n},\r\n',
    )
    out = ok("char", "show").stdout
    line = next(x for x in out.splitlines() if x.startswith("Identity:"))
    assert line == (
        "Identity: class absent (class_id is not a whole number from 1 to 2147483647; set aside "
        "by the reader; the record holds neither class_file nor class_file_absent), race id "
        "absent (race_id is not a whole number from 1 to 2147483647; set aside by the reader), "
        "body type absent (UnitSex returned 1)"
    )
    assert "five" not in out
    gear_data = json.loads(ok("char", "gear", "--json").stdout)
    assert gear_data["identity"]["sex_raw"] == 1 and gear_data["identity"]["sex"] is None
