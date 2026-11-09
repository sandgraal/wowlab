"""`wowlab addons check` and `sv list --orphans` (M16-34, docs/LAB_PLAN.md
§18.1 item 8).

Graded on the captured tree (`fixtures/macos/`, rows in `fixtures/README.md`,
L8) copied into `tmp_path` as an install: its two DBM addons (both TOCs
declare `## RequiredDeps: DBM-Core`, which is not installed, and an
interface the 1.60.1 client does not have) and its four account
SavedVariables addons that no installed TOC declares (DBM-Party-Vanilla,
DBM-StatusBarTimers, RareScanner with its `.bak`, Syndicator). What the
capture does not hold (an AddOns.txt that lists a DBM addon, an installed
dependency, a TOC that declares a file, a flavor with no version) is added
to the copy, and those test ids say `constructed`. Nothing reads or writes
a real install, the user data directory is redirected, the process table is
a fake with no client, and opening game data fails the test (no network).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import addoncheck, cli, install, layout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
SV_DIR = f"WTF/Account/{ACCOUNT}/SavedVariables"
TWIN = f"WTF/Account/{ACCOUNT}/Labrealmb Partb Partc Partd/Labchard"
ORPHANS = [
    f"{SV_DIR}/DBM-Party-Vanilla.lua",
    f"{SV_DIR}/DBM-StatusBarTimers.lua",
    f"{SV_DIR}/RareScanner.lua",
    f"{SV_DIR}/RareScanner.lua.bak",
    f"{SV_DIR}/Syndicator.lua",
]
LAB_RECORDS = [
    f"WTF/Account/{ACCOUNT}/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua",
    f"WTF/Account/{ACCOUNT}/1/Labchard-Labrealmg/SavedVariables/WowLab.lua",
    f"{SV_DIR}/WowLab.lua",
]

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))

    def no_network() -> None:
        raise AssertionError("game data was opened: these commands are offline")

    monkeypatch.setattr(cli, "_open_gamedata", no_network)


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


def ok(*args: str) -> str:
    result = runner.invoke(cli.app, list(args), terminal_width=80)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert result.stderr == ""
    return result.stdout


def _check() -> cli.AddonsCheckReport:
    return cli.AddonsCheckReport.model_validate_json(ok("addons", "check", "--json"))


def _orphans(*args: str) -> cli.SvListReport:
    return cli.SvListReport.model_validate_json(ok("sv", "list", "--orphans", "--json", *args))


def _state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


# ─── addons check ────────────────────────────────────────────────────────────


def test_addons_check_on_the_two_dbm_addons(root: Path) -> None:
    report = _check()
    check = report.check
    assert report.flavor_folder == FLAVOR
    assert check.client_version == "1.60.1.69913"
    assert check.client_interface == 16001, "by the patch-number rule"
    assert [a.name for a in check.addons] == ["DBM-Brawlers", "DBM-Challenges"]
    brawlers, challenges = check.addons
    for addon in (brawlers, challenges):
        (toc,) = addon.tocs
        assert toc.interface_status == "differs"
        assert [(d.name, d.status) for d in toc.dependencies] == [("DBM-Core", "missing")]
        assert addon.missing_dependencies == ("DBM-Core",)
        assert toc.load_on_demand is True
        assert addon.characters == (), "the one AddOns.txt lists neither"
    assert brawlers.tocs[0].interface_versions == (120100,)
    assert challenges.tocs[0].interface_versions == (50504, 120100)
    assert brawlers.tocs[0].version is None
    assert challenges.tocs[0].version == "r264"
    (listed,) = check.addons_txt
    assert (listed.path, listed.listed, listed.error) == (f"{TWIN}/AddOns.txt", 2, None)
    assert report.notes == [
        cli._CHECK_OUT_OF_DATE,
        cli._CHECK_DEFAULT,
        cli._CHECK_BLIZZARD,
        cli._CHECK_DEPS,
    ]


GOLDEN_CHECK = """\
2 addons: 2 missing a required addon, 2 out of date; this client's interface is 16001.

DBM-Brawlers  "<DBM Mod> Brawlers"
  interface   120100: this client is 16001, so it is out of date
  needs       DBM-Core (not installed)
  loads       on demand: only when another addon or the client asks for it
  version     not given
  characters  not listed in the one AddOns.txt found

DBM-Challenges  "<DBM Mod> Challenges"
  interface   50504, 120100: this client is 16001, so it is out of date
  needs       DBM-Core (not installed)
  loads       on demand: only when another addon or the client asks for it
  version     r264
  characters  not listed in the one AddOns.txt found

4 notes: --notes shows them
"""


def test_addons_check_text_answers_first_and_ends_with_the_footer(root: Path) -> None:
    assert ok("addons", "check") == GOLDEN_CHECK


def test_addons_check_notes_replace_the_footer(root: Path) -> None:
    out = ok("addons", "check", "--notes")
    assert out.startswith(GOLDEN_CHECK.removesuffix("4 notes: --notes shows them\n"))
    assert "--notes shows" not in out
    assert out.count("\n  - ") == 4
    assert "(not yet confirmed)" in out and "[verify]" not in out


def test_addons_check_names_the_characters_constructed(flavor: Path) -> None:
    other = flavor / "WTF" / "Account" / ACCOUNT / "1" / "Labchard-Labrealmg" / "AddOns.txt"
    other.write_bytes(b"DBM-Brawlers: enabled\r\ndbm-challenges: disabled\r\n")
    check = _check().check
    brawlers, challenges = check.addons
    assert [(c.realm_folder, c.character_folder, c.state) for c in brawlers.characters] == [
        ("1", "Labchard-Labrealmg", "enabled")
    ]
    assert [(c.state, c.enabled) for c in challenges.characters] == [("disabled", False)]
    out = ok("addons", "check")
    assert "  characters  enabled for 1/Labchard-Labrealmg\n" in out
    assert "  characters  disabled for 1/Labchard-Labrealmg\n" in out


def test_an_installed_dependency_and_a_matching_interface_constructed(flavor: Path) -> None:
    core = flavor / "Interface" / "AddOns" / "DBM-Core"
    core.mkdir()
    (core / "DBM-Core.toc").write_bytes(
        b"## Interface: 120100, 16001\r\n## Dependencies: Blizzard_Nope\r\n## Version: 1\r\n"
    )
    check = _check().check
    by_name = {a.name: a for a in check.addons}
    assert by_name["DBM-Brawlers"].missing_dependencies == ()
    assert by_name["DBM-Brawlers"].tocs[0].dependencies[0].status == "installed"
    (toc,) = by_name["DBM-Core"].tocs
    assert toc.interface_status == "matches"
    assert [(d.name, d.status) for d in toc.dependencies] == [("Blizzard_Nope", "blizzard")]
    assert toc.load_on_demand is False
    out = ok("addons", "check")
    assert out.startswith("3 addons: 0 missing a required addon, 2 out of date;")
    assert "  interface   120100, 16001: matches this client\n" in out


def test_a_flavor_with_no_version_judges_no_interface_constructed(flavor: Path) -> None:
    lay = layout.Layout(flavor)
    check = addoncheck.check_addons(lay, None)
    assert check.client_interface is None
    assert {t.interface_status for a in check.addons for t in a.tocs} == {"unknown"}
    assert cli._check_caveat(check) is not None


# ─── sv list --orphans ───────────────────────────────────────────────────────


def test_orphans_are_the_four_addons_saved_data(root: Path) -> None:
    report = _orphans()
    assert report.orphans is True
    assert [f.path for f in report.files] == ORPHANS
    assert {f.addon for f in report.files} == {
        "DBM-Party-Vanilla",
        "DBM-StatusBarTimers",
        "RareScanner",
        "Syndicator",
    }
    assert report.notes[0] == cli._lab_records_caveat(len(LAB_RECORDS))
    plain = cli.SvListReport.model_validate_json(ok("sv", "list", "--json"))
    assert plain.orphans is False
    sizes = {f.path: f.size for f in plain.files}
    assert [f.size for f in report.files] == [sizes[p] for p in ORPHANS]


def test_orphans_text_answers_first(root: Path, flavor: Path) -> None:
    out = ok("sv", "list", "--orphans")
    total = sum((flavor / p).stat().st_size for p in ORPHANS)
    lines = out.splitlines()
    first_row = next(i for i, ln in enumerate(lines) if ln.startswith("account "))
    answer = " ".join(ln.strip() for ln in lines[:first_row])
    assert answer == (
        f"5 SavedVariables files of 4 addons, {total:,} bytes in all, are declared by no "
        "installed addon. If the addon is installed again it reads this file again, so "
        "deleting it loses those settings."
    ), "the answer says what deleting one costs, not only behind --notes"
    rows = lines[first_row : first_row + 5]
    assert [ln.rsplit("  ", 1)[-1] for ln in rows] == [
        *ORPHANS[:3],
        f"{ORPHANS[3]} (previous write, .bak)",
        ORPHANS[4],
    ]
    assert "WowLab" not in "\n".join(rows)
    assert lines[first_row + 5].startswith(
        "Not listed: 3 WowLab.lua files, the lab-addon's records"
    )
    assert lines[-1] == "3 notes: --notes shows them"
    noted = ok("sv", "list", "--orphans", "--notes")
    assert "Notes:" in noted and "--notes shows" not in noted


def test_orphans_of_one_character_constructed(root: Path) -> None:
    report = _orphans("--character", "Labchard-Labrealmg")
    assert report.files == []
    assert report.notes[0] == cli._lab_records_caveat(1)
    assert ok("sv", "list", "--orphans", "--character", "Labchard-Labrealmg").startswith(
        "No SavedVariables file is left without an installed addon that declares it.\n"
    )


def test_a_toc_that_declares_the_file_ends_the_orphan_constructed(flavor: Path) -> None:
    addons = flavor / "Interface" / "AddOns"
    (addons / "Syndicator").mkdir()
    (addons / "Syndicator" / "Syndicator.toc").write_bytes(
        b"## Interface: 16001\n## SavedVariables: SYNDICATOR_CONFIG\n"
    )
    (addons / "RareScanner").mkdir()
    (addons / "RareScanner" / "RareScanner.toc").write_bytes(
        b"## Interface: 16001\n## SavedVariablesPerCharacter: RareScannerCharDB\n"
    )
    report = _orphans()
    assert f"{SV_DIR}/Syndicator.lua" not in [f.path for f in report.files]
    # RareScanner's TOC declares only per-character data: its account file stays.
    assert f"{SV_DIR}/RareScanner.lua" in [f.path for f in report.files]


def test_an_installed_lab_addon_leaves_no_caveat_constructed(flavor: Path) -> None:
    lab = flavor / "Interface" / "AddOns" / "WowLab"
    lab.mkdir()
    (lab / "WowLab.toc").write_bytes(
        b"## Interface: 16001\n## SavedVariables: WowLabDB\n"
        b"## SavedVariablesPerCharacter: WowLabCharDB\n"
    )
    report = _orphans()
    assert [f.path for f in report.files] == ORPHANS
    assert report.notes[0] == cli._ORPHAN_WHAT, "no caveat"


def test_orphans_library_never_lists_blizzard_files(flavor: Path) -> None:
    lay = layout.Layout(flavor)
    found = addoncheck.orphan_saved_variables(lay.saved_variables(), lay.addons())
    assert not [f for f in found.files if f.blizzard or (f.addon or "").startswith("Blizzard_")]
    assert [f.path for f in found.lab_addon_records] == LAB_RECORDS
    assert found.not_judged == ()


# ─── reads only ──────────────────────────────────────────────────────────────


def test_both_commands_leave_the_install_untouched(root: Path) -> None:
    before = _state(root)
    for args in (
        ("addons", "check"),
        ("addons", "check", "--json"),
        ("addons", "check", "--notes"),
        ("sv", "list", "--orphans"),
        ("sv", "list", "--orphans", "--json"),
        ("sv", "list", "--orphans", "--notes"),
    ):
        ok(*args)
    assert _state(root) == before


# ─── round 1 of the reviews ──────────────────────────────────────────────────


def _two_tocs(flavor: Path, first: bytes, second: bytes) -> None:
    folder = flavor / "Interface" / "AddOns" / "Twin"
    folder.mkdir()
    (folder / "Twin.toc").write_bytes(first)
    (folder / "Twin_Vanilla.toc").write_bytes(second)


def test_several_tocs_one_matching_is_not_out_of_date_constructed(flavor: Path) -> None:
    """The client loads one TOC by game type: one that matches is enough,
    and only its dependencies count."""
    _two_tocs(
        flavor,
        b"## Interface: 120100\r\n## RequiredDeps: NotHere\r\n",
        b"## Interface: 16001\r\n",
    )
    twin = {a.name: a for a in _check().check.addons}["Twin"]
    assert twin.selection == "depends_on_game_type"
    assert twin.counted_tocs == ("Twin_Vanilla.toc",)
    assert (twin.out_of_date, twin.no_toc_for_client) == (False, False)
    assert twin.missing_dependencies == (), "the other game type's dependency does not count"
    out = ok("addons", "check")
    assert out.startswith("3 addons: 2 missing a required addon, 2 out of date;")
    assert "    interface   120100: for another game type\n" in out
    assert "    interface   16001: matches this client\n" in out
    # Only the two single-TOC DBM addons are out of date, not Twin.
    assert sum(1 for a in _check().check.addons if a.out_of_date) == 2


def test_several_tocs_one_matching_and_no_other_out_of_date_constructed(flavor: Path) -> None:
    for name in ("DBM-Brawlers", "DBM-Challenges"):
        shutil.rmtree(flavor / "Interface" / "AddOns" / name)
    _two_tocs(flavor, b"## Interface: 120100\n", b"## Interface: 16001\n")
    assert ok("addons", "check").startswith("1 addon: 0 missing a required addon, 0 out of date;")


def test_several_tocs_none_matching_have_their_own_heading_constructed(flavor: Path) -> None:
    _two_tocs(
        flavor,
        b"## Interface: 120100\n## RequiredDeps: NotHere\n",
        b"## Interface: 50504\n",
    )
    twin = {a.name: a for a in _check().check.addons}["Twin"]
    assert (twin.out_of_date, twin.no_toc_for_client) == (False, True)
    assert twin.counted_tocs == ("Twin.toc", "Twin_Vanilla.toc")
    assert twin.missing_dependencies == ("NotHere",)
    out = ok("addons", "check")
    assert out.startswith(
        "3 addons: 3 missing a required addon, 2 out of date, 1 with several TOCs, none for "
        "this client's interface;"
    )
    assert "    interface   120100: not this client's 16001\n" in out


def test_a_dependency_with_no_loadable_toc_constructed(flavor: Path) -> None:
    core = flavor / "Interface" / "AddOns" / "DBM-Core"
    (core / "DBM-Core").mkdir(parents=True)  # unzipped one folder too deep
    (core / "DBM-Core" / "DBM-Core.toc").write_bytes(b"## Interface: 16001\n")
    brawlers = {a.name: a for a in _check().check.addons}["DBM-Brawlers"]
    assert [d.status for d in brawlers.tocs[0].dependencies] == ["installed_no_toc"]
    assert "DBM-Core (installed, but has no TOC the client loads)" in ok("addons", "check")


def test_saved_data_of_a_folder_with_no_toc_is_not_an_orphan_constructed(flavor: Path) -> None:
    deep = flavor / "Interface" / "AddOns" / "Syndicator" / "Syndicator"
    deep.mkdir(parents=True)
    (deep / "Syndicator.toc").write_bytes(b"## SavedVariables: SYNDICATOR_CONFIG\n")
    lay = layout.Layout(flavor)
    found = addoncheck.orphan_saved_variables(lay.saved_variables(), lay.addons())
    assert found.no_toc == ("Syndicator",)
    assert f"{SV_DIR}/Syndicator.lua" not in [f.path for f in found.files]
    report = _orphans()
    assert f"{SV_DIR}/Syndicator.lua" not in [f.path for f in report.files]
    caveat = cli._no_toc_caveat(["Syndicator"])
    assert caveat == (
        "Not listed: the saved data of Syndicator, whose folder has no TOC named after it "
        "(often an addon unzipped one folder too deep); `wowlab addons check` shows it."
    )
    # The lab-addon caveat comes first; this one leads the standing notes.
    assert report.notes[:2] == [cli._lab_records_caveat(3), caveat]
    assert "unzipped one folder too deep" in ok("sv", "list", "--orphans", "--notes")


def test_orphan_addons_are_counted_by_folded_name_constructed(flavor: Path) -> None:
    sv = flavor / SV_DIR
    (sv / "syndicator.lua.bak").write_bytes((sv / "Syndicator.lua").read_bytes())
    out = ok("sv", "list", "--orphans")
    assert out.startswith("6 SavedVariables files of 4 addons,")
