"""`wowlab looks` through Typer's runner (M11-06, docs/LAB_PLAN.md §13.2, §6.11).

The tables are the wago.tools recordings for 1.60.1.70009 under
`fixtures/wago/` (README rows), served through `GameData` by a `Source` that
reads those files, so nothing here reaches the network (ADR-0012). The user
data directory is redirected into `tmp_path`, and no test finds a real
install: `WOWLAB_WOW_ROOT` is unset and the platform defaults are replaced by
empty folders in `tmp_path`, except where the captured tree
(`fixtures/macos/`) is copied into `tmp_path` to show the build coming from
discovery.

Inputs labelled `constructed` (L8): a requirement row given an achievement
unlock (the recorded build has none), a choice id the build lacks, a saved
look edited to name another build, damaged and hostile look files, a looks
directory inside an install.
"""

from __future__ import annotations

import csv
import errno
import gzip
import io
import json
import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import platformdirs
import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from wowlab_core import cli, install, lookstore
from wowlab_core.gamedata import GameData, TableNotPublished
from wowlab_core.looks import Customizations, FindingKind
from wowlab_core.lookstore import LookStore, SavedLook

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WAGO = FIXTURES / "wago"
CAPTURE = FIXTURES / "macos"
BUILD = "1.60.1.70009"  # the recorded tables; tests may name builds, the library may not (L6)
CAPTURED_VERSION = "1.60.1.69913"  # the captured .build.info's version
FLAVOR = "_classic_beta_"  # the capture's flavor folder, as its provenance row says

# Real ids in the recorded build (see test_looks.py).
HUMAN, NIGHT_ELF = 1, 4
WARRIOR, DRUID = 1, 11
HUMAN_BODY_0_SKIN, HUMAN_BODY_0_FACE, HUMAN_BODY_1_SKIN = 9, 10, 14
PLAIN_SKIN, OTHER_SKIN = 1, 2  # choices of option 9; requirement 141 limits nothing
DK_SKIN = 13  # option 9; requirement 53: death knight only, and Face must be 20, 22 or 31
FACE_ALLOWED, FACE_OTHER = 20, 21
BEAR_FORM = 901  # druid form option on model 189
IMP_STYLE = 1528  # warlock Imp "Style" (model 148): three choices, each warlock only
TYRANT_STYLE = 8668  # warlock Tyrant "Style" (model 198): one choice
EXPORTED_ONLY = (
    f"Checked against build {BUILD}'s exported tables only: not what a server allows, not "
    "hotfixes the server sends (Cache/ADB), and not what this account has unlocked."
)
HOTFIX_HINT = (
    f"is not in build {BUILD}'s tables: check the id with `wowlab looks options`; an id read "
    "from the game may come from a hotfix the exported tables lack"
)

runner = CliRunner()


# ─── the recorded tables ─────────────────────────────────────────────────────


def _recorded(name: str) -> bytes:
    plain = WAGO / f"{name}.{BUILD}.csv"
    if plain.exists():
        return plain.read_bytes()
    return gzip.decompress((WAGO / f"{name}.{BUILD}.csv.gz").read_bytes())


def _with_achievement(body: bytes, req_id: str, achievement: str) -> bytes:
    """Constructed: one ChrCustomizationReq row given an achievement unlock."""
    rows = list(csv.reader(io.StringIO(body.decode("utf-8"), newline="")))
    header = rows[0]
    for row in rows[1:]:
        if row[header.index("ID")] == req_id:
            row[header.index("ReqAchievementID")] = achievement
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue().encode("utf-8")


class _Source:
    """Serves the recorded 70009 tables; any other build is not published."""

    name = "fixtures"

    def __init__(self, overrides: dict[str, bytes] | None = None) -> None:
        self.asked: list[tuple[str, str]] = []
        self.overrides = overrides or {}

    def fetch_builds(self, dest: BinaryIO) -> str:
        dest.write(gzip.decompress((WAGO / "builds.2026-09-28.json.gz").read_bytes()))
        return "fixture:builds.2026-09-28.json.gz"

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        self.asked.append((table, build))
        if build != BUILD:
            raise TableNotPublished(table, build)
        dest.write(self.overrides.get(table) or _recorded(table))
        return f"fixture:{table}.{build}.csv"


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture(autouse=True)
def no_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery finds nothing: no env root, and defaults that hold no install."""
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


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> Customizations:
    data = GameData(_Source(), cache_dir=tmp_path_factory.mktemp("gamedata"))
    return Customizations.from_gamedata(data, BUILD)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The captured tree as an install, named by `WOWLAB_WOW_ROOT`."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    return root


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _json[M: BaseModel](model: type[M], *args: str) -> M:
    return model.model_validate_json(ok(*args, "--json").stdout)


def _tree(path: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(path).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(path.rglob("*"))
    }


def _save(name: str, *choices: str, extra: tuple[str, ...] = ()) -> Any:
    args = ["looks", "save", name, "--race", "human", "--sex", "0", "--build", BUILD]
    for c in choices:
        args += ["--choice", c]
    return run(*args, *extra)


# ─── races ───────────────────────────────────────────────────────────────────


def test_races_lists_what_the_tables_flag_playable() -> None:
    report = _json(cli.LooksRacesReport, "looks", "races", "--build", BUILD)
    assert report.build == BUILD
    assert [r.id for r in report.races] == [1, 2, 3, 4, 5, 6, 7, 8, 95, 96]
    assert all(r.flagged_playable for r in report.races)
    assert cli._PLAYABLE_NOTE in report.notes
    human = report.races[0]
    assert (human.name, [b.body_type for b in human.body_types]) == ("Human", [0, 1])

    text = ok("looks", "races", "--build", BUILD).stdout
    assert f"Races flagged playable in build {BUILD}'s ChrRaces:" in text
    assert "Human  [Alliance]  body types 0 (model 1), 1 (model 2)" in text
    assert "not a claim about what a server lets anyone create" in text
    assert (
        "Body types are numbered as the tables number them (ChrRaceXChrModel.Sex 0 and 1); "
        "the game's own screens may number them differently [verify]."
    ) in text


def test_races_takes_the_build_from_discovery(root: Path, source: _Source) -> None:
    """The captured install is on 69913, whose tables were never recorded: the
    command asks for that build and says it is not published (L6)."""
    result = run("looks", "races")
    assert result.exit_code == 1
    assert CAPTURED_VERSION in result.stderr
    assert {build for _, build in source.asked} == {CAPTURED_VERSION}
    assert _json(cli.LooksRacesReport, "looks", "races", "--build", BUILD).build == BUILD


def test_races_without_an_install_uses_the_one_cached_build() -> None:
    first = run("looks", "races")
    assert first.exit_code == 1
    assert "pass --build" in first.stderr
    ok("looks", "races", "--build", BUILD)  # caches the tables
    report = _json(cli.LooksRacesReport, "looks", "races")
    assert report.build == BUILD
    assert any("the one build whose customization tables are cached" in n for n in report.notes)


def test_a_malformed_build_is_a_usage_error() -> None:
    result = run("looks", "races", "--build", "70009")
    assert result.exit_code == 2
    assert "full build string" in result.stderr


# ─── options ─────────────────────────────────────────────────────────────────


def test_options_follow_options_for(model: Customizations) -> None:
    report = _json(
        cli.LooksOptionsReport, "looks", "options", "human", "--sex", "0", "--build", BUILD
    )
    assert report.race.id == HUMAN and report.class_id is None
    [body] = report.body_types
    assert body.body_type == 0
    ids = [o.id for o in body.options]
    assert ids == [o.id for o in model.options_for(HUMAN, 0)]
    assert HUMAN_BODY_0_SKIN in ids and HUMAN_BODY_1_SKIN not in ids
    skin = next(o for o in body.options if o.id == HUMAN_BODY_0_SKIN)
    assert [c.id for c in skin.choices] == [c.id for c in model.options[HUMAN_BODY_0_SKIN].choices]
    dk = next(c for c in skin.choices if c.id == DK_SKIN)
    assert dk.refusals == []
    kinds = {f.kind for f in dk.notes}
    assert FindingKind.CLASS_RESTRICTED in kinds  # no class given: noted, not refused
    assert FindingKind.UNDECIDED_DEPENDENCY in kinds  # Face is not set
    assert (
        f"choice 13 of option 'Skin Color' (9): no class in build {BUILD} can use this: its "
        "ClassMask 0x20 allows only class id 6, which the build's ChrClasses does not have; "
        "a look with any class of this build is refused"
    ) in [f.message for f in dk.notes]
    plain = next(c for c in skin.choices if c.id == PLAIN_SKIN)
    assert (plain.refusals, plain.notes) == ([], [])


def test_options_with_a_class_show_the_refusal(model: Customizations) -> None:
    report = _json(
        cli.LooksOptionsReport,
        "looks", "options", "1", "--sex", "male", "--class", "warrior", "--build", BUILD,
    )  # fmt: skip
    assert (report.class_id, report.class_name) == (WARRIOR, "Warrior")
    [body] = report.body_types
    assert [o.id for o in body.options] == [o.id for o in model.options_for(HUMAN, 0, WARRIOR)]
    skin = next(o for o in body.options if o.id == HUMAN_BODY_0_SKIN)
    dk = next(c for c in skin.choices if c.id == DK_SKIN)
    assert [f.message for f in dk.refusals] == [
        "choice 13 of option 'Skin Color' (9): requirement 53 excludes class 1 (it allows "
        f"only class id 6, which build {BUILD}'s ChrClasses does not have)"
    ]

    text = ok(
        "looks", "options", "human", "--sex", "0", "--class", "1", "--build", BUILD
    ).stdout  # fmt: skip
    assert f"Human (1), Warrior (1), body type 0 (model 1), build {BUILD}:" in text
    assert f"option {HUMAN_BODY_0_SKIN}  Skin Color" in text
    assert "refused: " in text and "excludes class 1" in text


def test_options_lift_what_every_choice_shares() -> None:
    """The Imp's Style: the form-or-pet note is on the option itself, and the
    warlock-only note every choice carries is shown once as "every choice"."""
    report = _json(
        cli.LooksOptionsReport, "looks", "options", "human", "--sex", "0", "--build", BUILD
    )
    options = {o.id: o for o in report.body_types[0].options}
    imp = options[IMP_STYLE]
    assert len(imp.choices) == 3
    assert [(f.kind, f.message, f.choice_id) for f in imp.notes] == [
        (
            FindingKind.FORM_OR_PET_OPTION,
            "option 'Style' (1528) (Imp) is on model 148, which no race and body type uses: "
            "a form, pet or mount option, checked by its requirements only [verify]",
            None,
        ),
        (
            FindingKind.CLASS_RESTRICTED,
            "every choice: only for Warlock (9) (no class given)",
            None,
        ),
    ]
    assert all(c.notes == [] and c.refusals == [] for c in imp.choices)
    tyrant = options[TYRANT_STYLE]  # one choice: its option finding is still lifted
    assert len(tyrant.choices) == 1
    assert [f.kind for f in tyrant.notes] == [FindingKind.FORM_OR_PET_OPTION]
    assert all(f.kind is not FindingKind.FORM_OR_PET_OPTION for f in tyrant.choices[0].notes)

    text = ok("looks", "options", "human", "--sex", "0", "--build", BUILD).stdout
    assert "note: every choice: only for Warlock (9) (no class given)" in text


def test_options_without_sex_cover_every_body_type() -> None:
    report = _json(cli.LooksOptionsReport, "looks", "options", "Human", "--build", BUILD)
    assert [b.body_type for b in report.body_types] == [0, 1]
    assert any("No --class" in n for n in report.notes)


def test_options_list_forms_for_the_class_that_has_them() -> None:
    druid = _json(
        cli.LooksOptionsReport,
        "looks", "options", "night elf", "--sex", "0", "--class", "druid", "--build", BUILD,
    )  # fmt: skip
    bear = [o for o in druid.body_types[0].options if o.id == BEAR_FORM]
    assert bear and bear[0].form_or_pet
    assert any(f.kind is FindingKind.FORM_OR_PET_OPTION for f in bear[0].notes)
    warrior = _json(
        cli.LooksOptionsReport,
        "looks", "options", "4", "--sex", "0", "--class", str(WARRIOR), "--build", BUILD,
    )  # fmt: skip
    assert BEAR_FORM not in {o.id for o in warrior.body_types[0].options}
    assert druid.class_id == DRUID


@pytest.mark.parametrize(
    ("args", "words"),
    [
        (
            ("skyborne",),  # two ChrRaces rows share the file string and the models
            "race 'skyborne' matches High Order Skyborne (95, Alliance), Windshaper Skyborne "
            "(96, Horde): one race's two faction rows, sharing models; give the id "
            "(race-masked choices are checked against it)",
        ),
        (("no-such-race",), "no race 'no-such-race'"),
        (("999999",), "no race 999999"),
        (("human", "--sex", "7"), "has no body type 7"),
        (("human", "--sex", "tall"), "--sex takes 0, 1, male or female"),
        (("human", "--class", "bard"), "no class 'bard'"),
    ],
)
def test_options_usage_errors(args: tuple[str, ...], words: str) -> None:
    result = run("looks", "options", *args, "--build", BUILD)
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert words in result.stderr


def test_options_say_when_the_playable_flag_chose_the_race() -> None:
    """ChrRaces has two rows named Human; only race 1 is flagged playable."""
    report = _json(
        cli.LooksOptionsReport, "looks", "options", "human", "--sex", "0", "--build", BUILD
    )
    remark = "'human' also names Human (33), not flagged playable; using Human (1). Give 33 for that row."
    assert report.race.id == HUMAN and remark in report.notes
    assert remark in ok("looks", "options", "human", "--sex", "0", "--build", BUILD).stdout
    by_id = _json(cli.LooksOptionsReport, "looks", "options", "1", "--sex", "0", "--build", BUILD)
    assert not any("also names" in n for n in by_id.notes)
    saved = _save("mine", f"9={PLAIN_SKIN}", extra=("--json",))
    assert remark in cli.LookReport.model_validate_json(saved.stdout).remarks


def test_options_show_needs_unlock_constructed(source: _Source, model: Customizations) -> None:
    """Constructed: requirement 141 (Skin Color's plain choices) given an
    achievement; the recorded build has no unlock rows."""
    source.overrides["ChrCustomizationReq"] = _with_achievement(
        _recorded("ChrCustomizationReq"), "141", "12345"
    )
    report = _json(
        cli.LooksOptionsReport, "looks", "options", "human", "--sex", "0", "--build", BUILD
    )
    skin = next(o for o in report.body_types[0].options if o.id == HUMAN_BODY_0_SKIN)
    plain = next(c for c in skin.choices if c.id == PLAIN_SKIN)
    assert plain.refusals == []
    assert [f.message.split(": ")[-1] for f in plain.notes] == ["needs achievement 12345"]
    text = ok("looks", "options", "human", "--sex", "0", "--build", BUILD).stdout
    assert "note: " in text and "needs achievement 12345" in text


# ─── save ────────────────────────────────────────────────────────────────────


def test_save_writes_json_under_the_user_data_directory(user_data: Path) -> None:
    result = _save("mine", f"9={PLAIN_SKIN}", f"10={FACE_ALLOWED}", extra=("--json",))
    assert result.exit_code == 0, result.stderr
    report = cli.LookReport.model_validate_json(result.stdout)
    path = user_data / "looks" / "mine.json"
    assert report.path == str(path) and path.is_file()
    assert (report.saved_build, report.build, report.refused) == (BUILD, BUILD, False)
    assert [(c.option_name, c.choice_id) for c in report.choices] == [
        ("Skin Color", PLAIN_SKIN),
        ("Face", FACE_ALLOWED),
    ]
    saved = SavedLook.model_validate_json(path.read_bytes())
    assert saved.saved_build == BUILD
    assert (saved.look.race_id, saved.look.body_type, saved.look.class_id) == (HUMAN, 0, None)
    assert saved.look.choices == {HUMAN_BODY_0_SKIN: PLAIN_SKIN, HUMAN_BODY_0_FACE: FACE_ALLOWED}
    assert sorted(p.name for p in path.parent.iterdir()) == ["mine.json"]  # no temp file left


def test_save_text_output() -> None:
    result = _save("mine", f"9={DK_SKIN}", f"10={FACE_ALLOWED}")
    assert result.exit_code == 0, result.stderr
    assert f"Saved look mine (checked against build {BUILD})" in result.stdout
    assert "race:      Human (1), body type 0" in result.stdout
    assert "class:     not given" in result.stdout
    assert f"Skin Color (9) = {DK_SKIN}" in result.stdout
    assert "verdict:   not refused" in result.stdout
    assert (
        f"note: choice 13 of option 'Skin Color' (9): no class in build {BUILD} can use this"
    ) in result.stdout
    assert EXPORTED_ONLY in result.stdout


def test_save_refused_by_the_tables_writes_nothing(user_data: Path) -> None:
    """Death knight skin on a warrior with Face set to a choice it does not allow."""
    result = _save(
        "bad",
        f"9={DK_SKIN}",
        f"10={FACE_OTHER}",
        extra=("--class", "warrior", "--json"),
    )
    assert result.exit_code == 1
    report = cli.LookReport.model_validate_json(result.stdout)
    assert report.refused and report.path is None
    assert {f.kind for f in report.refusals} == {
        FindingKind.CLASS_EXCLUDED,
        FindingKind.MISSING_DEPENDENCY,
    }
    assert "nothing was saved" in result.stderr
    assert not (user_data / "looks" / "bad.json").exists()
    text = _save("bad", f"9={DK_SKIN}", extra=("--class", "1"))
    assert text.exit_code == 1
    assert "Not saved: the tables refuse look bad" in text.stdout
    assert "refused: " in text.stdout


def test_save_keeps_an_unknown_choice_as_a_note_constructed() -> None:
    """Constructed: a choice id 1.60.1.70009 does not have (a hotfix, maybe)."""
    result = _save("hotfix", "9=999999", extra=("--json",))
    assert result.exit_code == 0, result.stderr
    report = cli.LookReport.model_validate_json(result.stdout)
    assert not report.refused
    assert report.choices[0].choice_name is None
    assert [f.message for f in report.notes] == [
        f"choice 999999 of option 'Skin Color' (9) {HOTFIX_HINT}"
    ]
    shown = _json(cli.LookReport, "looks", "show", "hotfix", "--build", BUILD)
    assert [f.message for f in shown.notes] == [
        f"choice 999999 is unknown to build {BUILD} (possibly a hotfix)"
    ]
    option = _save("hotfix2", "77777=1", extra=("--json",))
    assert option.exit_code == 0, option.stderr
    assert [f.message for f in cli.LookReport.model_validate_json(option.stdout).notes] == [
        f"option 77777 {HOTFIX_HINT}"
    ]


def test_save_needs_unlock_is_a_note_constructed(source: _Source) -> None:
    source.overrides["ChrCustomizationReq"] = _with_achievement(
        _recorded("ChrCustomizationReq"), "141", "12345"
    )
    result = _save("unlock", f"9={PLAIN_SKIN}")
    assert result.exit_code == 0, result.stderr
    assert "note: " in result.stdout and "needs achievement 12345" in result.stdout


def test_save_refuses_a_taken_name_unless_replace(user_data: Path) -> None:
    assert _save("mine", f"9={PLAIN_SKIN}").exit_code == 0
    again = _save("MINE", f"9={OTHER_SKIN}")  # same look on a case-insensitive volume
    assert again.exit_code == 1
    assert "already saved" in again.stderr and "--replace" in again.stderr
    path = user_data / "looks" / "mine.json"
    assert SavedLook.model_validate_json(path.read_bytes()).look.choices == {9: PLAIN_SKIN}
    assert _save("mine", f"9={OTHER_SKIN}", extra=("--replace",)).exit_code == 0
    assert SavedLook.model_validate_json(path.read_bytes()).look.choices == {9: OTHER_SKIN}
    assert sorted(p.name for p in path.parent.iterdir()) == ["mine.json"]


@pytest.mark.parametrize("name", ["../escape", ".hidden", "a/b", "x" * 65, "", "café"])
def test_save_refuses_a_name_that_is_not_a_plain_file_name_constructed(
    name: str, user_data: Path, tmp_path: Path
) -> None:
    result = _save(name, f"9={PLAIN_SKIN}")
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert "is not a look name" in result.stderr
    assert not (user_data / "looks").exists()
    assert not (tmp_path / "escape.json").exists()


@pytest.mark.parametrize(
    ("choice", "words"),
    [("9", "OPTION=CHOICE"), ("9=x", "OPTION=CHOICE"), ("=1", "OPTION=CHOICE")],
)
def test_save_choice_usage_errors(choice: str, words: str) -> None:
    result = _save("mine", choice)
    assert result.exit_code == 2
    assert words in result.stderr


def test_save_same_option_twice_is_a_usage_error() -> None:
    result = _save("mine", "9=1", "9=2")
    assert result.exit_code == 2
    assert "given twice" in result.stderr


def test_save_never_touches_the_install(root: Path, user_data: Path) -> None:
    before = _tree(root)
    assert _save("mine", f"9={PLAIN_SKIN}").exit_code == 0
    assert _tree(root) == before
    assert (user_data / "looks" / "mine.json").is_file()


# ─── show ────────────────────────────────────────────────────────────────────


def test_show_with_nothing_saved_needs_no_tables(source: _Source, user_data: Path) -> None:
    report = _json(cli.LooksListReport, "looks", "show")
    assert (report.looks, report.damaged, report.build) == ([], [], None)
    assert report.directory == str(user_data / "looks")
    assert source.asked == []
    assert "No saved looks" in ok("looks", "show").stdout


def test_show_one_look() -> None:
    assert _save("mine", f"9={DK_SKIN}", extra=("--class", "warrior")).exit_code == 1
    assert _save("mine", f"9={PLAIN_SKIN}", extra=("--class", "warrior")).exit_code == 0
    report = _json(cli.LookReport, "looks", "show", "mine")  # no install: the saved build
    assert (report.name, report.build, report.saved_build) == ("mine", BUILD, BUILD)
    assert (report.class_id, report.class_name) == (WARRIOR, "Warrior")
    assert any("the build the look was saved against" in r for r in report.remarks)
    assert EXPORTED_ONLY in report.remarks
    text = ok("looks", "show", "mine").stdout
    assert f"Look mine (checked against build {BUILD})" in text
    assert "class:     Warrior (1)" in text


def test_show_lists_every_look_with_its_verdict() -> None:
    assert _save("alpha", f"9={PLAIN_SKIN}").exit_code == 0
    assert _save("beta", f"9={DK_SKIN}").exit_code == 0
    report = _json(cli.LooksListReport, "looks", "show", "--build", BUILD)
    assert report.build == BUILD
    assert [(s.name, s.refused, s.choices) for s in report.looks] == [
        ("alpha", False, 1),
        ("beta", False, 1),
    ]
    assert report.looks[1].notes >= 2  # class restricted, undecided dependency
    assert EXPORTED_ONLY in report.remarks
    text = ok("looks", "show").stdout
    assert "alpha  Human (1), body type 0, 1 choice(s): not refused, 0 note(s)" in text


def test_show_checks_a_look_saved_on_another_build_constructed(user_data: Path) -> None:
    """Constructed: a saved look edited to name a build that was never recorded."""
    assert _save("mine", f"9={PLAIN_SKIN}").exit_code == 0
    path = user_data / "looks" / "mine.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["saved_build"] = "1.60.1.1"
    path.write_text(json.dumps(data), encoding="utf-8")
    report = _json(cli.LookReport, "looks", "show", "mine", "--build", BUILD)
    assert report.saved_build == "1.60.1.1" and report.build == BUILD
    assert f"Saved against build 1.60.1.1; checked here against build {BUILD}." in report.remarks
    unpublished = run("looks", "show", "mine")  # no install: the saved build, not published
    assert unpublished.exit_code == 1
    assert "1.60.1.1" in unpublished.stderr


def test_show_names_a_damaged_file_and_exits_1_constructed(user_data: Path) -> None:
    assert _save("good", f"9={PLAIN_SKIN}").exit_code == 0
    (user_data / "looks" / "broken.json").write_text("{not json", encoding="utf-8")
    (user_data / "looks" / "other.json").write_text(
        (user_data / "looks" / "good.json").read_text(encoding="utf-8"), encoding="utf-8"
    )  # holds a look named "good"
    result = run("looks", "show", "--json", "--build", BUILD)
    assert result.exit_code == 1
    report = cli.LooksListReport.model_validate_json(result.stdout)
    assert [s.name for s in report.looks] == ["good"]
    assert [d.file for d in report.damaged] == ["broken.json", "other.json"]
    assert "not a saved look" in report.damaged[0].error
    assert "holds a look named 'good'" in report.damaged[1].error
    assert "damaged look file broken.json" in result.stderr
    one = run("looks", "show", "broken", "--build", BUILD)
    assert one.exit_code == 1 and "not a saved look" in one.stderr


def test_show_a_missing_look() -> None:
    result = run("looks", "show", "nobody")
    assert result.exit_code == 1
    assert "no saved look 'nobody'" in result.stderr
    assert run("looks", "show", "../x").exit_code == 2


# ─── compare ─────────────────────────────────────────────────────────────────


def test_compare_two_looks() -> None:
    assert _save("a", f"9={PLAIN_SKIN}", f"10={FACE_ALLOWED}").exit_code == 0
    assert _save("b", f"9={OTHER_SKIN}", f"10={FACE_ALLOWED}", "999999=999998").exit_code == 0
    report = _json(cli.LooksCompareReport, "looks", "compare", "a", "b")
    assert report.build == BUILD
    assert (report.same_race, report.same_body_type, report.same_class) == (True, True, True)
    assert [(s.option_id, s.choice_id) for s in report.same] == [(10, FACE_ALLOWED)]
    diff = {d.option_id: d for d in report.different}
    assert sorted(diff) == [9, 999999]
    assert diff[9].a is not None and diff[9].a.choice_id == PLAIN_SKIN
    assert diff[9].b is not None and diff[9].b.choice_id == OTHER_SKIN
    assert diff[999999].a is None and diff[999999].option_name is None
    assert not report.a.refused and not report.b.refused
    assert any("option 999999 is unknown to build" in f.message for f in report.b.notes)

    text = ok("looks", "compare", "a", "b").stdout
    assert f"Looks a | b, checked against build {BUILD}" in text
    assert f"Skin Color (9): {PLAIN_SKIN} | {OTHER_SKIN}" in text
    assert "option 999999: (not set in this look) | 999998 (unknown to this build)" in text
    assert report.remarks.count(EXPORTED_ONLY) == 1
    assert text.count(EXPORTED_ONLY) == 1
    assert "same choice on 1 option(s)" in text


def test_compare_a_missing_look() -> None:
    assert _save("a", f"9={PLAIN_SKIN}").exit_code == 0
    result = run("looks", "compare", "a", "nobody")
    assert result.exit_code == 1
    assert "no saved look 'nobody'" in result.stderr


# ─── the store ───────────────────────────────────────────────────────────────


def _saved(name: str = "mine") -> SavedLook:
    return SavedLook(
        saved_build=BUILD,
        look=cli.looks.Look(name=name, race_id=HUMAN, body_type=0, choices={9: 1}),
    )


def test_store_refuses_a_directory_inside_an_install_constructed(tmp_path: Path) -> None:
    install_root = tmp_path / "game"
    install_root.mkdir()
    (install_root / ".build.info").write_bytes(b"")
    store = LookStore(install_root / "somewhere" / "looks")
    with pytest.raises(lookstore.LookLocationError, match="inside a game install"):
        store.save(_saved())
    assert not (install_root / "somewhere").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_store_refuses_a_fifo_without_blocking_constructed(tmp_path: Path) -> None:
    store = LookStore(tmp_path / "looks")
    store.root.mkdir()
    os.mkfifo(store.root / "pipe.json")
    with pytest.raises(lookstore.LookStoreError, match="not a regular file"):
        store.load("pipe")
    looks_found, damaged = store.listing()
    assert looks_found == [] and [d.file for d in damaged] == ["pipe.json"]


def test_store_refuses_an_oversized_file_constructed(tmp_path: Path) -> None:
    store = LookStore(tmp_path / "looks")
    store.root.mkdir()
    (store.root / "big.json").write_bytes(b" " * (lookstore.MAX_LOOK_BYTES + 1))
    with pytest.raises(lookstore.LookStoreError, match="limit for a look"):
        store.load("big")


def test_store_falls_back_without_hard_links_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: a filesystem that refuses hard links (EPERM, as exFAT does)."""

    def no_links(self: Path, target: Path) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(Path, "hardlink_to", no_links)
    store = LookStore(tmp_path / "looks")
    path = store.save(_saved())
    assert store.load("mine") == _saved()
    with pytest.raises(lookstore.LookExistsError):
        store.save(_saved())
    assert sorted(p.name for p in store.root.iterdir()) == [path.name]


def test_store_round_trips_a_look(tmp_path: Path) -> None:
    store = LookStore(tmp_path / "looks")
    path = store.save(_saved())
    assert path == store.root / "mine.json"
    assert store.load("mine") == _saved()
    assert store.locate("MINE") == path
    with pytest.raises(lookstore.LookNotFoundError):
        store.load("other")
    if sys.platform != "win32":
        assert not any(p.name.startswith(".") for p in store.root.iterdir())
