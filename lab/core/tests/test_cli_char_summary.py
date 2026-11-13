"""`wowlab char list --summary` (M17-01, docs/LAB_PLAN.md §19 CW-1, §14.4):
the account hub's table, in text and as JSON.

The install is two committed captures side by side, each copied into
`tmp_path` with its flavor folder named as its provenance row says:
`fixtures/macos/` as `_classic_beta_` (two characters with a schema-1
`WowLab.lua`, one character folder without one, and the `<Realm>/<First>/`
twin) and `fixtures/macos-70888/` as `_forever_` (the launch client: one
character with a schema-4 record). Their `.build.info` files share a header,
so the install's is the first capture's with the second's data row added:
that combination is `constructed` (L8, a boundary input: an install with
two flavors), and so is every test labelled so (a file that cannot be read,
game tables placed in the cache). `fixtures/macos-70058/` (professions
switched off in the game) gives a record that leaves a section out.
`WOWLAB_WOW_ROOT` points at the copy and the user data directory is
redirected into `tmp_path`; nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gamedata, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLASSIC = FIXTURES / "macos"
LAUNCH = FIXTURES / "macos-70888"
EARLY = FIXTURES / "macos-70058"
WAGO = FIXTURES / "wago"
# The provenance rows' flavor folders; tests may name them (L6).
BETA = "_classic_beta_"
FOREVER = "_forever_"
ACCOUNT = "90000001#6"
LAUNCH_ACCOUNT = "LABACCOUNTA"
FIRST = "1/Labchard-Labrealmg"
SECOND = "1/Labcharb-Labrealmf"
BARE = "1/Labcharb-Labrealmd"
TWIN = "Labrealmb Partb Partc Partd/Labchard"
WARLOCK = "1/Sandgraal-Rizzen"
BETA_VERSION = "1.60.1.69913"
LAUNCH_VERSION = "1.60.1.70888"
FIX = "wowlab addon install lab"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    return redirected


def two_flavors(root: Path) -> Path:
    """The two captures side by side under `root` (constructed: see the
    module docstring)."""
    root.mkdir(parents=True)
    head, *rows = CLASSIC.joinpath(".build.info").read_bytes().splitlines(keepends=True)
    launch_head, launch_row = LAUNCH.joinpath(".build.info").read_bytes().splitlines(keepends=True)
    assert launch_head == head
    root.joinpath(".build.info").write_bytes(b"".join([head, *rows, launch_row]))
    shutil.copytree(CLASSIC / "forever", root / BETA)
    shutil.copytree(LAUNCH / "forever", root / FOREVER)
    return root


@pytest.fixture
def both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = two_flavors(tmp_path / "World of Warcraft")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def summary(*args: str) -> tuple[int, dict[str, Any]]:
    done = run("char", "list", "--summary", "--json", *args)
    return done.exit_code, json.loads(done.stdout)


def rows_by_path(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{r['realm_folder']}/{r['label']}": r for r in report["rows"]}


# ─── JSON ────────────────────────────────────────────────────────────────────


def test_one_row_per_character_folder_across_every_flavor_constructed(both: Path) -> None:
    code, report = summary()
    assert code == 0
    assert report["flavor_folder"] is None
    assert [(f["folder"], f["version"]) for f in report["flavors"]] == [
        (BETA, BETA_VERSION),
        (FOREVER, LAUNCH_VERSION),
    ]
    rows = rows_by_path(report)
    # The <Realm>/<First> twin is not a character folder: the lab-addon never
    # writes there, so the hub has no row for it.
    assert set(rows) == {FIRST, SECOND, BARE, WARLOCK}
    assert TWIN not in rows
    assert {p: r["flavor"] for p, r in rows.items()} == {
        FIRST: BETA,
        SECOND: BETA,
        BARE: BETA,
        WARLOCK: FOREVER,
    }
    assert rows[WARLOCK]["account"] == LAUNCH_ACCOUNT
    assert rows[FIRST]["version"] == BETA_VERSION and rows[WARLOCK]["version"] == LAUNCH_VERSION
    assert report["not_looked_at"] == []
    # No record is carried: the hub needs the values, not the file.
    assert all("record" not in r for r in report["rows"])


def test_a_schema_4_record_gives_every_value_constructed(both: Path) -> None:
    """The launch client's record (schema 4, `identity` present): the
    values as the file holds them."""
    row = rows_by_path(summary()[1])[WARLOCK]
    assert row["state"] == "recorded" and row["record_schema"] == 4
    assert row["client_build"] == LAUNCH_VERSION and row["older_client"] is False
    assert (row["level"], row["level_absent"]) == (13, None)
    assert (row["class_id"], row["class_file"], row["class_absent"]) == (9, "WARLOCK", None)
    assert (row["race_id"], row["race_absent"]) == (5, None)
    assert (row["spec_id"], row["spec_absent"]) == (1490, None)
    # `gear.average.equipped`, as recorded.
    assert (row["item_level"], row["item_level_absent"]) == (8.1875, None)
    assert [(p["skill_line"], p["rank"], p["max_rank"]) for p in row["professions"]] == [
        (197, 21, 75),
        (393, 90, 150),
        (129, 37, 75),
        (356, 1, 75),
        (185, 17, 75),
    ]
    assert row["professions_absent"] is None
    # No game table is cached: no name, the ids stand.
    assert (row["class_name"], row["race_name"], row["spec_name"]) == (None, None, None)
    assert all(p["name"] is None for p in row["professions"])
    assert row["mtime_ns"] is not None and row["file"].endswith("SavedVariables/WowLab.lua")


def test_a_schema_1_record_leaves_class_and_race_out_with_a_reason_constructed(
    both: Path,
) -> None:
    rows = rows_by_path(summary()[1])
    first, second = rows[FIRST], rows[SECOND]
    assert first["record_schema"] == second["record_schema"] == 1
    assert (first["level"], second["level"]) == (13, 10)
    assert (first["spec_id"], second["spec_id"]) == (1490, 1482)
    assert (first["item_level"], second["item_level"]) == (3.3125, 2.375)
    for row in (first, second):
        for name in ("class_id", "class_file", "race_id"):
            assert row[name] is None, name
        assert row["class_absent"] == row["race_absent"]
        assert "predates the identity section" in row["class_absent"]
    assert [p["skill_line"] for p in first["professions"]] == [182, 393, 129, 185]


def test_a_folder_with_no_record_says_how_to_get_one_constructed(both: Path) -> None:
    row = rows_by_path(summary()[1])[BARE]
    assert row["state"] == "no_lab_record"
    assert row["fix_command"] == FIX
    assert FIX in row["advice"] and "log out" in row["advice"]
    assert row["mtime_ns"] is None and row["error"] is None
    values = (
        "level",
        "class_id",
        "race_id",
        "spec_id",
        "item_level",
        "professions",
        "level_absent",
        "class_absent",
        "professions_absent",
    )
    assert all(row[v] is None for v in values)


def test_a_section_switched_off_leaves_its_value_out_with_the_addons_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 70058 capture: the player switched professions off in the game,
    so the record holds the section as absent with its reason."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(EARLY / ".build.info", root / ".build.info")
    shutil.copytree(EARLY / "forever", root / BETA)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    code, report = summary()
    assert code == 0
    (row,) = [r for r in report["rows"] if r["state"] == "recorded"]
    assert row["professions"] is None
    assert row["professions_absent"]
    assert row["level"] is not None and row["item_level"] is not None


def test_names_come_from_tables_already_cached_constructed(both: Path, user_data: Path) -> None:
    """Spec and profession names for the beta flavor's version, from the
    recorded wago exports placed in the cache; nothing is fetched."""
    folder = user_data / "gamedata" / "tables" / BETA_VERSION
    assert gamedata.default_cache_dir() == user_data / "gamedata"
    folder.mkdir(parents=True)
    shutil.copy2(WAGO / "SkillLine.1.60.1.70058.subset.csv", folder / "SkillLine.csv")
    shutil.copy2(
        WAGO / "ChrSpecialization.1.60.1.70058.subset.csv", folder / "ChrSpecialization.csv"
    )
    rows = rows_by_path(summary()[1])
    first = rows[FIRST]
    assert first["spec_name"]
    assert {p["skill_line"]: p["name"] for p in first["professions"]} == {
        182: "Herbalism",
        393: "Skinning",
        129: "First Aid",
        185: "Cooking",
    }
    # The launch flavor's version has no tables cached: ids only.
    assert rows[WARLOCK]["spec_name"] is None
    assert all(p["name"] is None for p in rows[WARLOCK]["professions"])


def test_a_file_that_cannot_be_read_is_a_row_and_exits_1_constructed(both: Path) -> None:
    target = both / BETA / "WTF" / "Account" / ACCOUNT / SECOND / "SavedVariables" / "WowLab.lua"
    target.write_bytes(b"WowLabCharDB = {\n")
    code, report = summary()
    assert code == 1
    rows = rows_by_path(report)
    assert rows[SECOND]["state"] == "not_read"
    assert rows[SECOND]["error"]
    assert str(both) not in rows[SECOND]["error"]
    # The others are still there.
    assert rows[FIRST]["state"] == rows[WARLOCK]["state"] == "recorded"


def test_flavor_narrows_to_one_flavor_constructed(both: Path) -> None:
    code, report = summary("--flavor", FOREVER)
    assert code == 0
    assert report["flavor_folder"] == FOREVER
    assert list(rows_by_path(report)) == [WARLOCK]


# ─── text ────────────────────────────────────────────────────────────────────


def test_the_text_form_lists_each_character_constructed(both: Path) -> None:
    done = runner.invoke(cli.app, ["char", "list", "--summary"], terminal_width=200)
    assert done.exit_code == 0, done.output
    lines = done.stdout.splitlines()
    assert lines[0] == f"Flavor {BETA} ({BETA_VERSION})"
    assert f"Flavor {FOREVER} ({LAUNCH_VERSION})" in lines
    at = lines.index(f"  {WARLOCK}")
    assert lines[at + 1] == ("    level 13, class WARLOCK, race id 5, spec id 1490, item level 8.2")
    assert lines[at + 2] == (
        "    professions: skill line 197 (21 of 75), skill line 393 (90 of 150), skill line "
        "129 (37 of 75), skill line 356 (1 of 75), skill line 185 (17 of 75)"
    )
    assert lines[at + 3].startswith("    last logout or /reload: ")
    at = lines.index(f"  {FIRST}")
    assert lines[at + 1] == (
        "    level 13, class not recorded, race not recorded, spec id 1490, item level 3.3"
    )
    at = lines.index(f"  {BARE}")
    assert lines[at + 1] == (
        f"    no lab record: run `{FIX}`, then log in on this character and log out once (or "
        "/reload)"
    )
    assert TWIN not in done.stdout
    # The standing notes behind --notes, one footer line without it.
    assert lines[-1] == "5 notes: --notes shows them"
    noted = runner.invoke(cli.app, ["char", "list", "--summary", "--notes"], terminal_width=200)
    assert "Notes:" in noted.stdout and "--notes shows" not in noted.stdout
    assert "(not yet confirmed)" in noted.stdout and "[verify]" not in noted.stdout


def test_the_plain_list_is_unchanged_by_the_new_options_constructed(both: Path) -> None:
    plain = run("char", "list")
    noted = run("char", "list", "--notes")
    assert plain.stdout == noted.stdout
    assert "level 13" not in plain.stdout


def test_the_hub_reads_nothing_into_the_install_constructed(both: Path) -> None:
    """L1: the summary run leaves the install as it was."""

    def tree() -> dict[str, tuple[int, int]]:
        out = {}
        for path in sorted(both.rglob("*")):
            info = path.lstat()
            out[str(path.relative_to(both))] = (info.st_size, info.st_mtime_ns)
        return out

    before = tree()
    assert summary()[0] == 0
    assert run("char", "list", "--summary").exit_code == 0
    assert tree() == before


def test_an_empty_result_says_how_to_get_a_record_constructed(both: Path) -> None:
    """No character folder in the flavor: the plain list's advice, in the
    summary's words too (code review of M17-01)."""
    shutil.rmtree(both / FOREVER / "WTF" / "Account" / LAUNCH_ACCOUNT / "1")
    done = run("char", "list", "--summary", "--flavor", FOREVER)
    assert done.exit_code == 0, done.output
    assert done.stdout.splitlines()[0] == (
        "No character folder was found in any account (install the lab-addon with "
        "`wowlab addon install lab`, log in on the character, then log out or /reload)."
    )


def test_a_flavor_without_the_account_is_headed_so_constructed(both: Path) -> None:
    done = runner.invoke(
        cli.app, ["char", "list", "--summary", "--account", ACCOUNT], terminal_width=200
    )
    assert done.exit_code == 0, done.output
    assert f"Flavor {FOREVER} ({LAUNCH_VERSION}): no account {ACCOUNT}" in done.stdout
    assert "no character folder" not in done.stdout


def test_the_notes_help_says_what_it_does_on_char_list() -> None:
    done = runner.invoke(cli.app, ["char", "list", "--help"], terminal_width=200)
    words = " ".join(done.stdout.split())
    assert "With --summary, also print the standing notes" in words
    assert "The plain list always prints its notes." in words


def _as_schema_5(data: bytes) -> bytes:
    """A real schema-4 record as the schema-5 addon (M16-45) would write it:
    `schema` 5, `announced`, and `clicks_left_out` on bindings. Constructed
    (L8) until the owner's schema-5 capture lands (docs/handoffs/M16-45.md)."""
    for old, new in (
        (b'["schema"] = 4,', b'["schema"] = 5,\n["announced"] = 5,'),
        (b'["commands_left_out"] = 0,', b'["commands_left_out"] = 0,\n["clicks_left_out"] = 0,'),
    ):
        assert data.count(old) == 1, old
        data = data.replace(old, new)
    return data


def test_a_schema_5_record_gives_every_value_constructed(both: Path) -> None:
    """The launch record rewritten as schema 5 (M16-45): the hub reads it
    as it reads schema 4."""
    target = both / FOREVER / "WTF" / "Account" / LAUNCH_ACCOUNT / WARLOCK
    target = target / "SavedVariables" / "WowLab.lua"
    target.write_bytes(_as_schema_5(target.read_bytes()))
    code, report = summary()
    assert code == 0
    row = rows_by_path(report)[WARLOCK]
    assert row["state"] == "recorded" and row["record_schema"] == 5
    assert (row["level"], row["class_id"], row["race_id"], row["spec_id"]) == (13, 9, 5, 1490)
    assert row["item_level"] == 8.1875
    assert [p["skill_line"] for p in row["professions"]] == [197, 393, 129, 356, 185]
