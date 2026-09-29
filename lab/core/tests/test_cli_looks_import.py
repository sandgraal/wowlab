"""`wowlab looks import-char` and the saved look's origin (M11-23,
docs/LAB_PLAN.md §13.1, §13.2) through Typer's runner and the reader.

The character with a customization record is the M11-23 capture
(`fixtures/macos-70058/`, build 1.60.1.70058): one barber-shop visit on
Forever, recorded at "open". The characters without one are the M11-03
capture (`fixtures/macos/`, build 70009), whose `customization` is absent
with the addon's reason. Each tree is copied into `tmp_path` with its flavor
folder named as its provenance row says (`_classic_beta_`), and `--root`
names the copy; no test finds a real install, and the user data directory is
redirected into `tmp_path`.

The looks are checked against the recorded 1.60.1.70009 tables
(`fixtures/wago/`), served by `test_cli_looks._Source`, so nothing reaches
the network (ADR-0012). The capture's own build has no recorded tables.

Inputs labelled `constructed` (L8) are boundary cases: a copy of the real
capture with one value changed (race, sex, a model id, a missing choice id,
a doubled option, the section removed), a saved look file written by hand
(an imported look with an id the build lacks, a file from before `origin`).
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from test_cli_looks import BUILD, EXPORTED_ONLY, HOTFIX_HINT, _Source
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon, lookstore
from wowlab_core.gamedata import GameData
from wowlab_core.looks import Look
from wowlab_core.lookstore import LookStore, SavedLook

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE_70058 = FIXTURES / "macos-70058"  # M11-23: a customization record
CAPTURE_70009 = FIXTURES / "macos"  # M11-03: no barber-shop visit
FLAVOR = "_classic_beta_"  # the provenance rows' flavor folder; tests may name it (L6)
CHAR = "WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
REAL = CAPTURE_70058 / "forever" / CHAR
NO_VISIT_A = CAPTURE_70009 / "forever" / CHAR
NO_VISIT_B = (
    CAPTURE_70009
    / "forever"
    / "WTF/Account/90000001#6/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua"
)
CLIENT_BUILD = "1.60.1.70058"
NO_VISIT_REASON = "no barber-shop visit recorded with the addon enabled"
AS_OF = "last barber-shop visit with the addon enabled"

# What the M11-23 record holds (read off the fixture, not the code).
UNDEAD = 5
RECORDED = {58: 918, 59: 923, 60: 941, 61: 962, 563: 6287, 62: 980, 534: 5330}
UNDEAD_BODY_0_MODEL = 9  # ChrRaceXChrModel: race 5, Sex 0 -> ChrModel 9 (70009)

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture(autouse=True)
def no_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    empty = tmp_path / "defaults" / "World of Warcraft"
    empty.mkdir(parents=True)
    monkeypatch.setattr(install, "default_roots", lambda **k: (empty,))


@pytest.fixture(autouse=True)
def source(monkeypatch: pytest.MonkeyPatch, user_data: Path) -> _Source:
    served = _Source()

    @contextmanager
    def fake() -> Iterator[GameData]:
        yield GameData(served, cache_dir=user_data / "gamedata")

    monkeypatch.setattr(cli, "_open_gamedata", fake)
    return served


def _install(tmp_path: Path, capture: Path, name: str) -> Path:
    root = tmp_path / name / "World of Warcraft"
    root.mkdir(parents=True)
    shutil.copy2(capture / ".build.info", root / ".build.info")
    shutil.copytree(capture / "forever", root / FLAVOR)
    return root


@pytest.fixture
def visited(tmp_path: Path) -> Path:
    """The M11-23 capture as an install."""
    return _install(tmp_path, CAPTURE_70058, "visited")


@pytest.fixture
def unvisited(tmp_path: Path) -> Path:
    """The M11-03 capture as an install (no customization record)."""
    return _install(tmp_path, CAPTURE_70009, "unvisited")


def _constructed(root: Path, old: bytes, new: bytes) -> None:
    """Constructed: the installed copy of the real capture with one change."""
    path = root / FLAVOR / CHAR
    data = path.read_bytes()
    assert data.count(old) == 1, old
    path.write_bytes(data.replace(old, new))


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _import(root: Path, name: str, *extra: str) -> Any:
    return run("looks", "import-char", name, "--root", str(root), "--build", BUILD, *extra)


def _report(root: Path, name: str, *extra: str) -> cli.LooksImportReport:
    result = _import(root, name, "--json", *extra)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return cli.LooksImportReport.model_validate_json(result.stdout)


# ─── the reader ──────────────────────────────────────────────────────────────


def test_the_real_record_reads_as_look_material() -> None:
    got = labaddon.customization_import(labaddon.read_char(REAL))
    assert got.race_id == UNDEAD
    assert got.body_type == 0
    assert got.chr_model_id is None  # the capture has no chr_model_id
    assert got.choices == RECORDED
    assert list(got.choices) == [58, 59, 60, 61, 563, 62, 534]  # the record's order
    assert got.without_choice == []
    assert got.recorded_at == "open"
    assert got.as_of == AS_OF
    assert got.carried is False
    assert got.loads_ago == 0  # recorded_load 8, probe.loads 8
    assert got.client_build == CLIENT_BUILD


@pytest.mark.parametrize("path", [NO_VISIT_A, NO_VISIT_B], ids=["first", "second"])
def test_no_visit_gives_the_addons_reason(path: Path) -> None:
    with pytest.raises(labaddon.NoCustomizationError) as caught:
        labaddon.customization_import(labaddon.read_char(path))
    assert caught.value.reason == NO_VISIT_REASON
    assert NO_VISIT_REASON in str(caught.value)


def _real_char() -> dict[str, Any]:
    from wowlab_core import luadata

    value = luadata.parse(REAL.read_bytes()).to_python()["WowLabCharDB"]
    assert isinstance(value, dict)
    return value


def _customization(**changes: Any) -> dict[str, Any]:
    """Constructed: the real record with fields changed (None deletes one)."""
    char = _real_char()
    record = dict(char["customization"])
    for key, value in changes.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    char["customization"] = record
    return char


def test_a_section_missing_from_the_file_is_said_so_constructed() -> None:
    char = _real_char()
    del char["customization"]
    with pytest.raises(labaddon.NoCustomizationError) as caught:
        labaddon.customization_import(labaddon.load_char(char))
    assert caught.value.reason is None
    assert labaddon.NOT_IN_FILE in str(caught.value)


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"race_id": None}, "names no race (race_id)"),
        ({"sex": None}, "names no body type (sex)"),
        ({"sex": 2}, "sex is 2, not 0 or 1"),
    ],
    ids=["no-race", "no-sex", "sex-2"],
)
def test_a_record_without_race_or_body_type_is_not_imported_constructed(
    changes: dict[str, Any], words: str
) -> None:
    with pytest.raises(labaddon.NoCustomizationError, match=re.escape(words)):
        labaddon.customization_import(labaddon.load_char(_customization(**changes)))


def test_an_option_without_a_choice_id_is_left_out_constructed() -> None:
    char = _customization()
    choices = [dict(c) for c in char["customization"]["choices"]]
    del choices[1]["choice"]  # option 59
    char["customization"]["choices"] = choices
    got = labaddon.customization_import(labaddon.load_char(char))
    assert got.without_choice == [59]
    assert 59 not in got.choices
    assert len(got.choices) == 6


def test_an_option_listed_twice_is_refused_constructed() -> None:
    char = _customization()
    choices = char["customization"]["choices"]
    char["customization"]["choices"] = [*choices, dict(choices[0])]
    with pytest.raises(labaddon.NoCustomizationError, match="option 58 twice"):
        labaddon.customization_import(labaddon.load_char(char))


# ─── import-char on the real capture ─────────────────────────────────────────


def test_import_char_saves_the_real_record_as_an_imported_look(
    visited: Path, user_data: Path
) -> None:
    report = _report(visited, "undead")
    assert report.saved is True
    assert report.character == "1/Labchard-Labrealmg"
    assert report.chosen_by == "latest"
    assert report.file == CHAR
    assert report.as_of_words == "as of the last barber-shop open"
    assert report.record.recorded_at == "open"
    look = report.look
    assert look.origin == "imported"
    assert look.build == BUILD
    assert (look.race_id, look.race_name, look.body_type) == (UNDEAD, "Undead", 0)
    assert look.class_id is None
    assert {c.option_id: c.choice_id for c in look.choices} == RECORDED
    assert all(c.option_name is not None and c.choice_name is not None for c in look.choices)
    assert look.refused is False
    assert look.refusals == []
    assert "unknown_to_build" not in {f.kind for f in look.notes}  # all 7 ids are in 70009
    # the file under the user data directory, never in the install
    path = user_data / "looks" / "undead.json"
    assert look.path == str(path)
    saved = SavedLook.model_validate_json(path.read_bytes())
    assert saved.origin == "imported"
    assert saved.saved_build == BUILD
    assert saved.look == Look(name="undead", race_id=UNDEAD, body_type=0, choices=RECORDED)


def test_import_char_remarks_say_when_the_record_was_made(visited: Path) -> None:
    remarks = _report(visited, "undead").look.remarks
    assert remarks[0] == (
        "Customization as of the last barber-shop open (recorded_at: open; the addon's as_of: \""
        f'{AS_OF}"), in the session that saved this file. The character may look different since.'
    )
    assert cli._OPEN_REMARK in remarks
    assert labaddon.PAID_CHANGE_NOTE in remarks
    assert cli._MODEL_ONLY_REMARK in remarks
    assert cli._NO_CLASS_REMARK in remarks
    assert (
        "The record names no model (chr_model_id): the body type is its sex value 0, read as "
        "the tables' body type (ChrRaceXChrModel.Sex) [verify]."
    ) in remarks
    assert (
        f"Recorded by client build {CLIENT_BUILD}; checked against build {BUILD}'s tables: an "
        "id they lack may be newer than those tables, or a hotfix."
    ) in remarks
    assert EXPORTED_ONLY in remarks
    assert cli._IMPORTED_REMARK not in remarks  # that one is for `show`


def test_import_char_text_never_calls_the_look_current(visited: Path) -> None:
    text = ok("looks", "import-char", "undead", "--root", str(visited), "--build", BUILD).stdout
    assert "Character: 1/Labchard-Labrealmg in account 90000001#6 (the WowLab.lua" in text
    assert f"File: {CHAR}" in text
    assert "Imported look undead (checked against build 1.60.1.70009)" in text
    assert "race:      Undead (5), body type 0" in text
    assert "as of the last barber-shop open" in text
    assert "recorded_at: open" in text
    assert f'as_of: "{AS_OF}"' in text
    assert "current" not in text.casefold()


def test_import_char_with_character_and_class(visited: Path) -> None:
    report = _report(visited, "undead", "--character", "Labchard-Labrealmg", "--class", "8")
    assert report.chosen_by == "--character"
    assert report.look.class_id == 8
    assert cli._NO_CLASS_REMARK not in report.look.remarks
    assert report.look.refused is False


def test_import_char_refuses_a_taken_name_unless_replace(visited: Path) -> None:
    assert _import(visited, "undead").exit_code == 0
    again = _import(visited, "undead")
    assert again.exit_code == 1
    assert "undead" in again.stderr
    assert _import(visited, "undead", "--replace").exit_code == 0


def test_import_char_refuses_a_bad_name(visited: Path) -> None:
    result = _import(visited, "../x")
    assert result.exit_code == 2
    assert "is not a look name" in result.stderr


def test_show_and_compare_of_an_imported_look(visited: Path) -> None:
    assert _import(visited, "undead").exit_code == 0
    shown = cli.LookReport.model_validate_json(
        ok("looks", "show", "undead", "--build", BUILD, "--json").stdout
    )
    assert shown.origin == "imported"
    assert cli._IMPORTED_REMARK in shown.remarks
    assert shown.refused is False
    text = ok("looks", "show", "undead", "--build", BUILD).stdout
    assert cli._IMPORTED_REMARK in text
    assert "current" not in text.casefold()
    assert _import(visited, "undead2").exit_code == 0
    compared = cli.LooksCompareReport.model_validate_json(
        ok("looks", "compare", "undead", "undead2", "--build", BUILD, "--json").stdout
    )
    assert len(compared.same) == len(RECORDED)
    assert compared.different == []


# ─── the no-visit case ───────────────────────────────────────────────────────


@pytest.mark.parametrize("character", ["Labchard-Labrealmg", "Labcharb-Labrealmf"])
@pytest.mark.parametrize("json_out", [False, True], ids=["text", "json"])
def test_no_visit_gives_the_addons_reason_and_saves_nothing(
    unvisited: Path, user_data: Path, character: str, json_out: bool
) -> None:
    extra = ("--character", character, *(("--json",) if json_out else ()))
    result = _import(unvisited, "nope", *extra)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert f"1/{character}: nothing to import" in result.stderr
    assert f"customization is absent, with the addon's reason: {NO_VISIT_REASON}" in result.stderr
    assert "nothing was saved" in result.stderr
    assert not (user_data / "looks").exists()


def test_no_visit_asks_no_tables(unvisited: Path, source: _Source) -> None:
    assert _import(unvisited, "nope").exit_code == 1
    assert source.asked == []


# ─── constructed variants of the real capture ────────────────────────────────


def test_a_record_the_tables_refuse_is_not_saved_constructed(
    visited: Path, user_data: Path
) -> None:
    """Constructed: race 1 (Human) with the Undead choices."""
    _constructed(visited, b'["race_id"] = 5,', b'["race_id"] = 1,')
    result = _import(visited, "human", "--json")
    assert result.exit_code == 1
    report = cli.LooksImportReport.model_validate_json(result.stdout)
    assert report.saved is False
    assert report.look.refused is True
    assert report.look.path is None
    assert "nothing was saved" in result.stderr
    assert not (user_data / "looks" / "human.json").exists()
    text = _import(visited, "human")
    assert text.exit_code == 1
    assert "Not saved: the tables refuse look human" in text.stdout


def test_a_model_the_tables_disagree_with_is_remarked_constructed(visited: Path) -> None:
    """Constructed: the record given `chr_model_id` 10 (race 5's body type 1 model)."""
    _constructed(visited, b'["sex"] = 0,', b'["sex"] = 0,\r\n["chr_model_id"] = 10,')
    report = _report(visited, "undead")
    assert report.record.chr_model_id == 10
    assert (
        f"The barber shop was showing model 10; build {BUILD}'s tables give race 5 body type 0 "
        f"model {UNDEAD_BODY_0_MODEL}."
    ) in report.look.remarks
    assert not any("names no model" in r for r in report.look.remarks)


def test_a_model_the_tables_agree_with_needs_no_remark_constructed(visited: Path) -> None:
    """Constructed: the record given `chr_model_id` 9, the model the tables give."""
    _constructed(visited, b'["sex"] = 0,', b'["sex"] = 0,\r\n["chr_model_id"] = 9,')
    remarks = _report(visited, "undead").look.remarks
    assert not any("model" in r and "barber shop was showing" in r for r in remarks)
    assert not any("names no model" in r for r in remarks)


def test_sex_other_than_0_or_1_is_not_imported_constructed(visited: Path) -> None:
    _constructed(visited, b'["sex"] = 0,', b'["sex"] = 3,')
    result = _import(visited, "odd")
    assert result.exit_code == 1
    assert "sex is 3, not 0 or 1" in result.stderr


# ─── origin decides the unknown-id wording ───────────────────────────────────


def _write_look(user_data: Path, name: str, body: dict[str, Any]) -> None:
    folder = user_data / "looks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


UNKNOWN_CHOICE_LOOK = {"name": "x", "race_id": 1, "body_type": 0, "choices": {"9": 999999}}


def test_an_imported_id_the_build_lacks_is_possibly_a_hotfix_constructed(
    user_data: Path,
) -> None:
    """Constructed: an imported look file holding a choice id 70009 lacks."""
    _write_look(
        user_data,
        "x",
        {"format": 1, "saved_build": BUILD, "origin": "imported", "look": UNKNOWN_CHOICE_LOOK},
    )
    shown = cli.LookReport.model_validate_json(
        ok("looks", "show", "x", "--build", BUILD, "--json").stdout
    )
    assert [f.message for f in shown.notes] == [
        f"choice 999999 is unknown to build {BUILD} (possibly a hotfix)"
    ]


def test_a_typed_id_the_build_lacks_stays_a_thing_to_check(user_data: Path) -> None:
    result = run(
        "looks", "save", "t", "--race", "1", "--sex", "0", "--build", BUILD, "--choice", "9=999999"
    )
    assert result.exit_code == 0, result.stderr
    expected = [f"choice 999999 of option 'Skin Color' (9) {HOTFIX_HINT}"]
    shown = cli.LookReport.model_validate_json(
        ok("looks", "show", "t", "--build", BUILD, "--json").stdout
    )
    assert shown.origin == "typed"
    assert [f.message for f in shown.notes] == expected
    assert cli._IMPORTED_REMARK not in shown.remarks
    compared = cli.LooksCompareReport.model_validate_json(
        ok("looks", "compare", "t", "t", "--build", BUILD, "--json").stdout
    )
    assert [f.message for f in compared.a.notes] == expected
    assert SavedLook.model_validate_json((user_data / "looks" / "t.json").read_bytes()).origin == (
        "typed"
    )


def test_a_look_file_from_before_origin_reads_as_typed_constructed(user_data: Path) -> None:
    """Constructed: a look file as M11-06 wrote it, with no `origin` key."""
    _write_look(
        user_data,
        "old",
        {"format": 1, "saved_build": BUILD, "look": {**UNKNOWN_CHOICE_LOOK, "name": "old"}},
    )
    found, damaged = LookStore().listing()
    assert damaged == []
    (saved,) = found
    assert saved.origin == "typed"
    shown = cli.LookReport.model_validate_json(
        ok("looks", "show", "old", "--build", BUILD, "--json").stdout
    )
    assert shown.origin == "typed"
    assert [f.message for f in shown.notes] == [
        f"choice 999999 of option 'Skin Color' (9) {HOTFIX_HINT}"
    ]


def test_origin_takes_only_the_two_values_constructed() -> None:
    with pytest.raises(ValueError, match="origin"):
        SavedLook.model_validate(
            {"format": 1, "saved_build": BUILD, "origin": "guessed", "look": UNKNOWN_CHOICE_LOOK}
        )
    assert lookstore.LOOKS_FORMAT == 1
