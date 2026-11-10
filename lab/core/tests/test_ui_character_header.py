"""The Character tab's header in player terms (M16-31, §16.3).

The install is the owner's schema-4 capture (`macos-70450/`, files-only, two
characters with an `identity` record), classified inside the install root
borrowed from `macos/` as its fixture row says, copied into `tmp_path` under
the stand-in home folder of `test_ui_explorer.py`. The page is driven under
Node on the harness of `test_ui_character.py`, fed what the real CLI printed
through the real server.

The header shows the level and the class from the record, and the spec and
race (and class) names from the game-data tables already in wowlab's cache
for the installed client's version, else the ids. The tables placed in the
cache are the recorded wago.tools exports under `fixtures/wago/` (their
provenance rows), copied under the installed version's name: a test-made
cache, since no table was recorded for this capture's build (labelled
`constructed` where it matters). Nothing is fetched: no source exists in
these runs. The picker's tie-break by schema adds a real schema-1 record
from `macos/` beside the two schema-4 ones (`constructed`: the three files
never shared an account).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
import test_ui_explorer as first_stage
from test_ui_character import ACCOUNT, _drive, _report, _scenario, _set_report
from test_ui_explorer import FLAVOR, _stdout

from wowlab_core import cli, gamedata, gear, install, labaddon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WAGO = FIXTURES / "wago"
MAGE = "1/Dangling-Participle"
WARLOCK = "1/Sangraal-Sett"
SCHEMA_1 = "1/Labchard-Labrealmg"  # sorts between the two by path
SAME_NS = 1_760_000_000_000_000_000
TABLES = {
    "ChrSpecialization": "ChrSpecialization.1.60.1.70058.subset.csv",
    "ChrRaces": "ChrRaces.1.60.1.70009.csv",
    "ChrClasses": "ChrClasses.1.60.1.70009.csv",
}

home = first_stage.home
server = first_stage.server


def _lab_file(root: Path, character: str) -> Path:
    return root / FLAVOR / "WTF" / "Account" / ACCOUNT / character / "SavedVariables" / "WowLab.lua"


@pytest.fixture
def install70450(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = home / "Games" / "World of Warcraft"
    root.mkdir(parents=True)
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70450" / "forever", root / FLAVOR)
    shutil.copy2(FIXTURES / "macos" / "forever" / ".flavor.info", root / FLAVOR / ".flavor.info")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    # The two characters at different times: the mage written last.
    os.utime(_lab_file(root, MAGE), ns=(SAME_NS, SAME_NS))
    os.utime(_lab_file(root, WARLOCK), ns=(SAME_NS - 10**9, SAME_NS - 10**9))
    return root


def _cache_tables(build: str) -> Path:
    """The recorded exports, placed in the cache under `build` (constructed)."""
    folder = platformdirs.user_data_path("wowlab") / "gamedata" / "tables" / build
    folder.mkdir(parents=True)
    for name, fixture in TABLES.items():
        shutil.copy2(WAGO / fixture, folder / f"{name}.csv")
    return folder


def _facts(view: dict[str, Any]) -> dict[str, str]:
    facts = view["headerFacts"]
    return dict(zip(facts[::2], facts[1::2], strict=True))


def _gear(server: Any, character: str) -> dict[str, Any]:
    params = {"character": character, "account": ACCOUNT, "flavor": FLAVOR}
    return cli.CharGearReport.model_validate_json(
        _stdout(server, ["char", "gear"], params)
    ).model_dump(mode="json")


# ─── the report ──────────────────────────────────────────────────────────────


def test_gear_json_without_cached_tables_names_nothing_constructed(
    server: Any, install70450: Path
) -> None:
    report = _gear(server, MAGE)
    assert report["level"] == 10
    assert report["identity"]["class_id"] == 8 and report["identity"]["race_id"] == 2
    assert isinstance(report["names"]["build"], str)
    assert {k: v for k, v in report["names"].items() if k != "build"} == {
        "spec": None,
        "race": None,
        "class_name": None,
    }


@pytest.mark.parametrize(
    ("character", "level", "spec", "race", "klass"),
    [(MAGE, 10, "Mage", "Orc", "Mage"), (WARLOCK, 15, "Warlock", "Undead", "Warlock")],
)
def test_gear_json_names_from_the_cached_tables_constructed(
    server: Any, install70450: Path, character: str, level: int, spec: str, race: str, klass: str
) -> None:
    build = _gear(server, character)["names"]["build"]
    _cache_tables(build)
    report = _gear(server, character)
    assert report["level"] == level
    assert report["names"] == {"build": build, "spec": spec, "race": race, "class_name": klass}


def test_a_table_without_the_row_or_unreadable_falls_back_constructed(
    server: Any, install70450: Path
) -> None:
    build = _gear(server, MAGE)["names"]["build"]
    folder = _cache_tables(build)
    # The spec subset holds 1482 and 1490; take the mage's row out.
    spec = folder / "ChrSpecialization.csv"
    lines = spec.read_text(encoding="utf-8").splitlines(keepends=True)
    spec.write_text("".join(line for line in lines if ",1482," not in line), encoding="utf-8")
    # A race table that is not UTF-8.
    (folder / "ChrRaces.csv").write_bytes(b"ID,Name_lang\n2,\xff\xfe\n")
    names = _gear(server, MAGE)["names"]
    assert names == {"build": build, "spec": None, "race": None, "class_name": "Mage"}


def test_names_read_no_table_for_a_malformed_version_constructed(tmp_path: Path) -> None:
    spec = gear.GearSpec(id=1482, index=1, api="x", absent=None)
    identity = labaddon.CharIdentity(race_id=2, sex=1, sex_raw=3, class_id=8, class_file="MAGE")
    names = cli._char_names("../../etc", spec, identity)
    assert names == cli.CharNames(build="../../etc", spec=None, race=None, class_name=None)
    assert cli._char_names(None, spec, identity).build is None


def test_the_female_name_when_the_table_has_one_else_the_name_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "wowlab")
    folder = _cache_tables("1.60.1.1")
    (folder / "ChrRaces.csv").write_text(
        "ID,Name_lang,Name_female_lang\n2,Orc,Orcess\n", encoding="utf-8"
    )
    spec = gear.GearSpec(id=1482, index=1, api="x", absent=None)
    female = labaddon.CharIdentity(race_id=2, sex=1, sex_raw=3, class_id=8, class_file="MAGE")
    male = labaddon.CharIdentity(race_id=2, sex=0, sex_raw=2, class_id=8, class_file="MAGE")
    # ChrClasses' female column is empty for every class: the name stands.
    assert cli._char_names("1.60.1.1", spec, female) == cli.CharNames(
        build="1.60.1.1", spec="Mage", race="Orcess", class_name="Mage"
    )
    assert cli._char_names("1.60.1.1", spec, male).race == "Orc"


# ─── the page ────────────────────────────────────────────────────────────────


def _header(server: Any, tmp_path: Path, mutate: Any = None) -> dict[str, Any]:
    scenario = _scenario(server, [{"char_tab": True}], [MAGE])
    if mutate is not None:
        report = _report(scenario, MAGE)
        mutate(report)
        _set_report(scenario, MAGE, report)
    view: dict[str, Any] = _drive(tmp_path / "node", scenario)["steps"][0]
    return view


def test_the_header_names_class_race_and_spec_from_the_cache_constructed(
    server: Any, install70450: Path, tmp_path: Path
) -> None:
    _cache_tables(_gear(server, MAGE)["names"]["build"])
    view = _header(server, tmp_path)
    assert view["headerFacts"][:14] == [
        "Level", "10",
        "Class", "Mage",
        "Race", "Orc",
        "Spec", "Mage",
        "Average item level, equipped", "2.4",
        "Average item level, overall", "2.4",
        "Character folder", MAGE,
    ]  # fmt: skip
    titles = dict(zip(view["headerFacts"][::2], view["headerTitles"], strict=True))
    assert titles["Class"] == "class id 8"
    assert titles["Race"] == "race id 2"
    # Forever has one specialization per class, so the names match; the
    # title says what the row is.
    assert (
        titles["Spec"] == "spec id 1482; the client's specialization, not the talent points spent"
    )
    # The exact figure the client returned, behind the rounded one.
    assert titles["Average item level, equipped"] == "rounded to one decimal; exact: 2.375"
    assert titles["Average item level, overall"] == "rounded to one decimal; exact: 2.375"
    assert titles["Level"] == ""


def test_without_the_cache_the_header_shows_the_ids_constructed(
    server: Any, install70450: Path, tmp_path: Path
) -> None:
    facts = _facts(_header(server, tmp_path))
    assert facts["Level"] == "10"
    # No class name cached: the class file the client gave.
    assert facts["Class"] == "MAGE"
    assert facts["Race"] == "race id 2"
    assert facts["Spec"] == "spec id 1482"


def test_absent_values_say_why_constructed(server: Any, install70450: Path, tmp_path: Path) -> None:
    def strip(report: dict[str, Any]) -> None:
        report["level"] = None
        report["level_absent"] = "the talent record is absent (switched off by the owner)"
        report["identity"] = dict(
            report["identity"],
            race_id=None,
            race_id_absent="identity switched off",
            class_id=None,
            class_id_absent="identity switched off",
        )
        report["spec"] = dict(report["spec"], id=None, absent="not in the file")
        report["average"] = dict(report["average"], equipped=9, overall="x")

    facts = _facts(_header(server, tmp_path, strip))
    assert facts["Level"] == "not recorded: the talent record is absent (switched off by the owner)"
    assert facts["Class"] == "not recorded: identity switched off"
    # The same reason is said once.
    assert facts["Race"] == "not recorded: as for Class"
    assert facts["Spec"] == "not recorded: not in the file"
    assert facts["Average item level, equipped"] == "9.0"
    assert facts["Average item level, overall"] == "not returned"


def test_a_cached_name_is_drawn_as_text_constructed(
    server: Any, install70450: Path, tmp_path: Path
) -> None:
    hostile = "<img src=x onerror=alert(1)>" + chr(0x202E) + "x"

    def name(report: dict[str, Any]) -> None:
        report["names"] = dict(report["names"], race=hostile)

    facts = _facts(_header(server, tmp_path, name))
    assert facts["Race"].startswith("<img src=x onerror=alert(1)>")
    assert chr(0x202E) not in facts["Race"]


def test_the_picker_breaks_a_time_tie_by_the_newest_schema_constructed(
    server: Any, install70450: Path, tmp_path: Path
) -> None:
    older = _lab_file(install70450, SCHEMA_1)
    older.parent.mkdir(parents=True)
    shutil.copy2(FIXTURES / "macos" / "forever" / "WTF" / "Account" / ACCOUNT / SCHEMA_1
                 / "SavedVariables" / "WowLab.lua", older)  # fmt: skip
    for character in (MAGE, WARLOCK, SCHEMA_1):
        os.utime(_lab_file(install70450, character), ns=(SAME_NS, SAME_NS))
    scenario = _scenario(server, [{"char_tab": True}], [MAGE])
    listed = [c for c in _list(scenario) if c["no_lab_record"] is None]
    assert sorted((c["label"], c["record_schema"]) for c in listed) == [
        ("Dangling-Participle", 4),
        ("Labchard-Labrealmg", 1),
        ("Sangraal-Sett", 4),
    ]
    view = _drive(tmp_path / "node", scenario)["steps"][0]
    labels = [o["text"].split(",")[0] for o in view["options"][:3]]
    # Same time: schema 4 before schema 1, then by folder.
    assert labels == ["Dangling-Participle", "Sangraal-Sett", "Labchard-Labrealmg"]


def _list(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    listed: list[dict[str, Any]] = json.loads(scenario["runs"][0]["stdout"])["characters"]
    return listed


def test_cached_rows_reads_only_what_is_cached_constructed(tmp_path: Path) -> None:
    """The cache-only reader neither fetches nor creates anything (L1)."""
    cache = tmp_path / "gamedata"
    assert gamedata.cached_rows("ChrRaces", "1.60.1.1", cache_dir=cache) is None
    assert not cache.exists()
    with pytest.raises(ValueError):
        gamedata.cached_rows("ChrRaces", "../x", cache_dir=cache)
    folder = cache / "tables" / "1.60.1.1"
    folder.mkdir(parents=True)
    shutil.copy2(WAGO / TABLES["ChrClasses"], folder / "ChrClasses.csv")
    rows = gamedata.cached_rows("ChrClasses", "1.60.1.1", cache_dir=cache)
    assert rows is not None
    assert [r["Name_lang"] for r in rows if r["ID"] in ("8", "9")] == ["Mage", "Warlock"]
    assert sorted(p.name for p in folder.iterdir()) == ["ChrClasses.csv"]


def test_char_gear_text_has_the_headers_lines_constructed(
    server: Any, install70450: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`char gear`'s text says what the page's header says, with the same
    fallbacks: names once the tables are cached, ids before."""
    from typer.testing import CliRunner

    runner = CliRunner()
    args = ["char", "gear", "--character", MAGE, "--account", ACCOUNT, "--flavor", FLAVOR]
    before = runner.invoke(cli.app, args)
    assert before.exit_code == 0, before.output
    lines = before.stdout.splitlines()
    assert "Level: 10" in lines
    assert "Class: MAGE (class id 8)" in lines
    assert "Race: race id 2" in lines
    assert any(line.startswith("Spec: spec id 1482 (index ") for line in lines)
    _cache_tables(_gear(server, MAGE)["names"]["build"])
    after = runner.invoke(cli.app, args).stdout.splitlines()
    assert "Class: Mage (class id 8)" in after
    assert "Race: Orc (race id 2)" in after
    assert any(line.startswith("Spec: Mage (spec id 1482, index ") for line in after)


def test_level_absent_says_why_constructed() -> None:
    """`level_absent_of` on a real schema-4 record with its level taken out."""
    path = (
        FIXTURES / "macos-70450" / "forever" / "WTF" / "Account" / ACCOUNT / MAGE
        / "SavedVariables" / "WowLab.lua"
    )  # fmt: skip
    record = labaddon.read_char(path)
    assert labaddon.level_absent_of(record) is None
    no_talents = record.model_copy(update={"talents": None})
    assert labaddon.level_absent_of(no_talents) == f"the talent record is {labaddon.NOT_IN_FILE}"
    assert record.talents is not None
    no_legacy = record.model_copy(
        update={"talents": record.talents.model_copy(update={"legacy": None})}
    )
    assert labaddon.level_absent_of(no_legacy) == (
        f"the legacy talent record is {labaddon.NOT_IN_FILE}"
    )
