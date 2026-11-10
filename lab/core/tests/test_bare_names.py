"""Bare names in `explain` and `sv dump`; `explain` exits 1 on not-found (M16-18).

A name that is not on disk as typed is resolved by its last parts, among the
real files under the flavor folder and the file-map rows. The candidates are
listed, and three or fewer are each explained. `sv dump` takes a file as
`sv list` lists it: an addon name, a file name, or a path relative to its
account folder, and asks for `--account` or `--character` when several
files have it. `explain` of a path that matches nothing exits 1.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`
with its flavor folder named as its provenance row says (`_classic_beta_`),
as `test_cli.py` does. `WOWLAB_WOW_ROOT` points at the copy, the user data
directory is redirected into `tmp_path`, and `psutil.process_iter` is an
empty table. Nothing reads or writes a real install.

Inputs labelled `constructed` (L8) are names the capture does not hold, a
fourth `WowLab.lua` copied into a constructed character folder, and a path
outside the install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, filemap, install, layout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
A = f"WTF/Account/{ACCOUNT}"
NOT_FOUND = "not found; nothing is at this path"
CHAR_F = "1/Labcharb-Labrealmf"
CHAR_G = "1/Labchard-Labrealmg"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture(autouse=True)
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no client in it."""
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def run(*args: str, code: int) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == code, (args, result.stdout, result.stderr, result.exception)
    return result


# ─── explain ────────────────────────────────────────────────────────────────


def test_the_readme_lead_question_works_as_typed(root: Path) -> None:
    text = run("explain", "config-cache.wtf", code=0).stdout
    assert "on disk by this name (2):" in text
    # Two candidates: each is explained in full, from its own row.
    assert f"{A}/config-cache.wtf (in flavor folder {FLAVOR})" in text
    assert "file-map row: account-config-cache" in text
    assert "file-map row: character-wtf-config" in text
    assert text.count("  edit:         ") == 2


def test_candidates_are_in_the_json_shallowest_first(root: Path) -> None:
    report = json.loads(run("explain", "config-cache.wtf", "--json", code=0).stdout)
    assert report["absent"] is True
    assert [(c["path"], c["entry_id"]) for c in report["candidates"]] == [
        (f"{A}/config-cache.wtf", "account-config-cache"),
        (f"{A}/1/Labcharb-Labrealmd/config-cache.wtf", "character-wtf-config"),
    ]
    assert {c["flavor_folder"] for c in report["candidates"]} == {FLAVOR}


def test_a_partial_path_matches_by_its_last_parts(root: Path) -> None:
    report = json.loads(run("explain", "SavedVariables/WowLab.lua", "--json", code=0).stdout)
    assert [c["path"] for c in report["candidates"]] == [
        f"{A}/SavedVariables/WowLab.lua",
        f"{A}/{CHAR_F}/SavedVariables/WowLab.lua",
        f"{A}/{CHAR_G}/SavedVariables/WowLab.lua",
    ]


def test_the_name_is_matched_whatever_its_case(root: Path) -> None:
    report = json.loads(run("explain", "config.WTF", "--json", code=0).stdout)
    assert [c["path"] for c in report["candidates"]] == ["WTF/Config.wtf"]


def test_three_candidates_are_each_explained(root: Path) -> None:
    text = run("explain", "WowLab.lua", code=0).stdout
    assert "on disk by this name (3):" in text
    assert text.count("  what:         ") == 3


def test_more_than_three_are_listed_not_explained_constructed(root: Path) -> None:
    extra = root / FLAVOR / A / "1" / "Constructed-Labrealmz" / "SavedVariables"
    extra.mkdir(parents=True)
    shutil.copy2(root / FLAVOR / A / "SavedVariables" / "WowLab.lua", extra / "WowLab.lua")
    text = run("explain", "WowLab.lua", code=0).stdout
    assert "on disk by this name (4):" in text
    assert "  what:         " not in text
    assert "explain one of them by the path shown" in text


def test_a_row_that_names_it_with_no_file_is_listed_and_explained(root: Path) -> None:
    # The capture has an account `bindings-cache.wtf` but no character one:
    # the character row names it too, with nothing on disk.
    report = json.loads(run("explain", "bindings-cache.wtf", "--json", code=0).stdout)
    assert [(c["path"], c["entry_id"]) for c in report["candidates"]] == [
        (f"{A}/bindings-cache.wtf", "account-bindings-cache"),
        (None, "character-wtf-config"),
    ]
    text = run("explain", "bindings-cache.wtf", code=0).stdout
    assert "file-map rows for a file by this name (1):" in text
    assert "(file-map row character-wtf-config; no file for this row was found)" in text


def test_a_name_that_matches_nothing_exits_1_constructed(root: Path) -> None:
    result = run("explain", "no-such-file-constructed.txt", code=1)
    first, second = result.stdout.splitlines()[:2]
    assert first == f"no-such-file-constructed.txt: {NOT_FOUND}"
    # The reason no longer says that no row matches a name like this.
    assert second == "  no file-map row matches this whole path"
    report = json.loads(run("explain", "no-such-file-constructed.txt", "--json", code=1).stdout)
    assert report["absent"] is True and "candidates" not in report


def test_a_missing_absolute_path_exits_1_and_is_not_searched(root: Path) -> None:
    path = str(root / FLAVOR / "config-cache.wtf")
    report = json.loads(run("explain", path, "--json", code=1).stdout)
    assert report["absent"] is True and "candidates" not in report


def test_a_path_outside_the_install_exits_1_constructed(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere-constructed.txt"
    outside.write_text("constructed\n")
    result = run("explain", str(outside), code=1)
    assert "not inside the install root" in result.stdout


def test_a_missing_path_a_row_describes_still_exits_0(root: Path) -> None:
    report = json.loads(
        run("explain", "WTF/Account/NoSuchAccount/macros-cache.txt", "--json", code=0).stdout
    )
    assert report["result"]["status"] == "classified" and report["absent"] is True


def test_an_existing_file_is_unchanged(root: Path) -> None:
    report = json.loads(run("explain", "WTF/Config.wtf", "--json", code=0).stdout)
    assert report["result"]["entry"]["id"] == "config-wtf"
    assert "candidates" not in report


def test_with_several_flavors_a_listed_path_explains_as_typed_constructed(root: Path) -> None:
    # A second flavor folder, a copy of the capture (constructed).
    shutil.copytree(CAPTURE / "forever", root / "_constructed_")
    text = run("explain", "config-cache.wtf", code=0).stdout
    assert "on disk by this name (4):" in text
    shown = [line.strip() for line in text.splitlines() if line.startswith("    ") and "/" in line]
    assert f"{FLAVOR}/{A}/config-cache.wtf" in shown
    assert f"_constructed_/{A}/config-cache.wtf" in shown
    for path in shown:
        typed = run("explain", path, "--json", code=0).stdout
        assert json.loads(typed)["result"]["status"] == "classified", path


# ─── the library parts ──────────────────────────────────────────────────────


def test_filemap_named_needs_a_spelled_out_last_segment() -> None:
    fm = filemap.load()
    assert [e.id for e in fm.named(["config-cache.wtf"])] == [
        "account-config-cache",
        "character-wtf-config",
    ]
    # `*.lua` names no file: a bare name never matches it.
    assert fm.named(["WowLab.lua"]) == ()
    assert fm.named([]) == ()
    # The parts before the name match the pattern's segments in place.
    assert [e.id for e in fm.named(["WTF", "Config.wtf"])] == ["config-wtf"]
    assert fm.named(["Interface", "Config.wtf"]) == ()


def test_named_paths_reads_only_and_never_follows_a_link_constructed(
    root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-constructed"
    outside.mkdir()
    (outside / "config-cache.wtf").write_text("constructed\n")
    try:
        (root / FLAVOR / "Linked-constructed").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    found = layout.named_paths(root / FLAVOR, ["config-cache.wtf"])
    assert found == [f"{A}/config-cache.wtf", f"{A}/1/Labcharb-Labrealmd/config-cache.wtf"]


# ─── sv dump ────────────────────────────────────────────────────────────────

DBM_FIRST = 'DBT_AllPersistentOptions["Default"]'


def test_the_readme_sv_dump_example_works_as_typed(root: Path) -> None:
    text = run("sv", "dump", "SavedVariables/DBM-StatusBarTimers.lua", code=0).stdout
    assert text.startswith(DBM_FIRST)


@pytest.mark.parametrize(
    "name", ["DBM-StatusBarTimers", "DBM-StatusBarTimers.lua", "dbm-statusbartimers"]
)
def test_sv_dump_takes_an_addon_or_file_name(root: Path, name: str) -> None:
    assert run("sv", "dump", name, code=0).stdout.startswith(DBM_FIRST)


def test_sv_dump_json_names_the_file_it_found(root: Path) -> None:
    out = run("sv", "dump", "DBM-StatusBarTimers", "--json", code=0).stdout
    assert f"{A}/SavedVariables/DBM-StatusBarTimers.lua" in json.loads(out)["file"].replace(
        "\\", "/"
    )


def test_sv_dump_of_an_ambiguous_name_asks_for_account_or_character(root: Path) -> None:
    result = run("sv", "dump", "WowLab", code=2)
    assert "names 3 SavedVariables files" in result.stderr
    assert "--account or --character" in result.stderr
    assert f"{A}/{CHAR_F}/SavedVariables/WowLab.lua" in result.stderr


def test_sv_dump_picks_by_character_or_by_path_in_the_account(root: Path) -> None:
    by_char = run("sv", "dump", "WowLab", "--character", "Labcharb-Labrealmf", code=0).stdout
    by_path = run("sv", "dump", f"{CHAR_F}/SavedVariables/WowLab.lua", code=0).stdout
    direct = run("sv", "dump", f"{A}/{CHAR_F}/SavedVariables/WowLab.lua", code=0).stdout
    assert by_char == by_path == direct


def test_sv_dump_with_account_alone_takes_the_account_wide_file(root: Path) -> None:
    by_account = run("sv", "dump", "WowLab", "--account", ACCOUNT, code=0).stdout
    direct = run("sv", "dump", f"{A}/SavedVariables/WowLab.lua", code=0).stdout
    assert by_account == direct


def test_sv_dump_says_when_account_and_character_are_not_used(root: Path) -> None:
    result = run(
        "sv", "dump", f"{A}/SavedVariables/WowLab.lua", "--character", "Labcharb-Labrealmf", code=0
    )
    assert "choose among names only" in result.stderr
    plain = run("sv", "dump", f"{A}/SavedVariables/WowLab.lua", code=0)
    assert "choose among names only" not in plain.stderr
    assert result.stdout == plain.stdout


def test_sv_dump_of_a_name_nothing_has_exits_1_constructed(root: Path) -> None:
    result = run("sv", "dump", "NoSuchAddon-constructed", code=1)
    assert "no SavedVariables file 'NoSuchAddon-constructed'" in result.stderr
    assert "wowlab sv list" in result.stderr
    assert "Errno" not in result.stderr
