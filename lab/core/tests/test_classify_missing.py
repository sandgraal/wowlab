"""A path that does not exist is not a folder (M13-29).

`classify` on a path with nothing on disk, and no `is_dir` from the caller,
used to match rows of either kind, so a missing `WTF/Account/A/unknown.txt`
matched the folder-only `realm-folder` row and `explain` called it a folder.
Now such a path matches file rows and `**` rows only (a row that names a
file by its shape), folder rows only when it is written with a trailing
separator, and with no row it is reported as "not found", at verdict level
`unclassified`.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`
with its flavor folder named as its provenance row says (`_classic_beta_`),
as `test_cli.py` does. `WOWLAB_WOW_ROOT` points at the copy, the user data
directory is redirected into `tmp_path`, and `psutil.process_iter` is an
empty table. Nothing reads or writes a real install.

Every missing path here is `constructed` (L8): a boundary case, a name the
capture does not hold, placed at each depth under `WTF/Account/`.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, filemap, install, layout
from wowlab_core.verdict import verdict_of

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
REALM = "Labrealmb Partb Partc Partd"
FIRST = "Labchard"  # a `<Realm>/<First>/` character folder in the capture
NUMERIC_CHAR = "1/Labcharb-Labrealmd"  # a `<digits>/<First>-<Second>/` one
NOT_FOUND = "not found; nothing is at this path"
AS_FOLDER = " (end it with / to look it up as a folder)"
NOT_FOUND_WORDS = "not found; nothing is at this path, and no file-map row matches it"

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


@pytest.fixture
def flavor(root: Path) -> Path:
    return root / FLAVOR


def ok(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, (args, result.stdout, result.stderr, result.exception)
    return result


def explain_json(path: str) -> cli.ExplainReport:
    return cli.ExplainReport.model_validate_json(ok("explain", path, "--json").stdout)


A = f"WTF/Account/{ACCOUNT}"
# (path relative to the flavor folder, the row it gets or None for "not
# found", whether the reason offers the folder lookup). Every path is
# missing from the capture; each depth under `WTF/Account/` is covered.
CASES: list[tuple[str, str | None, bool]] = [
    # depth 1: an account folder
    ("WTF/Account/NoSuchAccount", None, True),
    ("WTF/Account/NoSuchAccount/", "account-folder", False),
    # depth 2: inside an account, the case the domain review of #195 found
    (f"{A}/unknown.txt", None, True),
    (f"{A}/NoSuchRealm", None, True),
    (f"{A}/NoSuchRealm/", "realm-folder", False),
    (f"{A}/12345", None, True),
    (f"{A}/12345/", "numeric-folder", False),
    ("WTF/Account/NoSuchAccount/SavedVariables.lua", "account-blizzard-savedvariables", False),
    ("WTF/Account/NoSuchAccount/macros-cache.txt", "account-macros-cache", False),
    # depth 3: inside a realm or numeric folder, or SavedVariables/
    (f"{A}/{REALM}/NoSuchChar", None, True),
    (f"{A}/{REALM}/NoSuchChar/", "character-folder", False),
    (f"{A}/1/NoSuch-Char", None, True),
    (f"{A}/1/NoSuch-Char/", "second-name-character-folder", False),
    (f"{A}/1/SavedVariables", None, False),
    (f"{A}/SavedVariables/NoSuchAddon.lua", "account-savedvariables", False),
    (f"{A}/SavedVariables/NoSuchAddon.lua.bak", "savedvariables-backup", False),
    (f"{A}/SavedVariables/unknown.txt", None, False),
    # depth 4: inside a character folder
    (f"{A}/{REALM}/{FIRST}/unknown.txt", None, False),
    (f"{A}/{REALM}/NoSuchChar/AddOns.txt", "character-addons-txt", False),
    (f"{A}/{NUMERIC_CHAR}/unknown.txt", None, False),
    (f"{A}/1/NoSuch-Char/layout-local.txt", "character-layout-local", False),
    (f"{A}/{REALM}/NoSuchChar/SavedVariables", None, True),
    (f"{A}/{REALM}/NoSuchChar/SavedVariables/", "savedvariables-folder", False),
    # depth 5: a character's SavedVariables/
    (f"{A}/{REALM}/NoSuchChar/SavedVariables/NoSuchAddon.lua", "character-savedvariables", False),
    (f"{A}/{REALM}/{FIRST}/SavedVariables/unknown.txt", None, False),
]


@pytest.mark.parametrize(
    ("path", "entry_id", "as_folder"), CASES, ids=[f"constructed-{c[0]}" for c in CASES]
)
def test_a_missing_path_gets_its_row_or_not_found(
    flavor: Path, path: str, entry_id: str | None, as_folder: bool
) -> None:
    assert not os.path.lexists(flavor / path.rstrip("/"))
    for typed in (path, f"{FLAVOR}/{path}", str(flavor / path) + ("/" if path[-1] == "/" else "")):
        report = explain_json(typed)
        text = ok("explain", typed).stdout
        assert report.stat is None
        if entry_id is None:
            assert isinstance(report.result, layout.Unclassified), typed
            assert report.result.reason == NOT_FOUND + (AS_FOLDER if as_folder else ""), typed
            assert report.verdict.level == "unclassified"
            assert report.verdict.words.startswith(NOT_FOUND_WORDS)
            assert text.startswith(f"{typed}: no file-map row")
            assert f": {report.result.reason}\n" in text
        else:
            assert isinstance(report.result, layout.Classified), typed
            assert report.result.entry.id == entry_id
            assert f"  file-map row: {entry_id} " in text


def _folders_under_accounts(flavor: Path) -> list[Path]:
    accounts = flavor / "WTF" / "Account"
    found = [accounts]
    for dirpath, dirnames, _ in os.walk(accounts):
        found.extend(Path(dirpath) / d for d in sorted(dirnames))
    return found


MISSING_NAMES = ["zz-missing", "zz-missing.txt", "zz-missing.lua", "12345", "SavedVariables"]


def test_no_missing_path_under_accounts_matches_a_folder_row_constructed(flavor: Path) -> None:
    """Every folder of the capture under `WTF/Account/`, at every depth, with
    names it does not hold: never a folder row, never level `folder`; a
    row only when it names a file of that shape, else "not found"."""
    folders = _folders_under_accounts(flavor)
    assert len({len(f.relative_to(flavor).parts) for f in folders}) >= 5  # WTF/Account down to SV
    checked = 0
    for folder in folders:
        for name in MISSING_NAMES:
            if os.path.lexists(folder / name):
                continue
            rel = (folder / name).relative_to(flavor).as_posix()
            report = explain_json(rel)
            checked += 1
            assert report.verdict.level != "folder", rel
            if isinstance(report.result, layout.Classified):
                entry = report.result.entry
                assert entry.matches(tuple(rel.split("/")), "file"), (rel, entry.id)
                assert entry.edit != "—", (rel, entry.id)
            else:
                assert report.result.reason.startswith(NOT_FOUND), rel
                assert report.verdict.words.startswith(NOT_FOUND_WORDS), rel
    assert checked > 50


def test_an_existing_path_is_never_reported_as_not_found(flavor: Path) -> None:
    """Every path in the capture classifies as an `lstat` from the caller
    would say, and none of them is "not found"."""
    inst = install.discover(flavor.parent)
    for p in sorted(flavor.rglob("*")):
        rel = p.relative_to(flavor).as_posix()
        found = layout.classify(f"{FLAVOR}/{rel}", inst)
        told = layout.classify(f"{FLAVOR}/{rel}", inst, is_dir=p.is_dir())
        assert found == told, rel
        if isinstance(found, layout.Unclassified):
            assert found.reason == "no file-map row matches", rel


def test_a_file_at_the_install_root_only_keeps_its_words_constructed(root: Path) -> None:
    """A relative path that exists at the install root but not in the
    flavor is not "not found" (the flavor fallback reads the missing one)."""
    (root / "unknown-constructed.txt").write_bytes(b"")
    report = explain_json("unknown-constructed.txt")
    assert isinstance(report.result, layout.Unclassified)
    assert report.result.reason == "no file-map row matches"
    assert not report.verdict.words.startswith("not found")


def test_a_path_missing_at_the_root_but_present_in_the_flavor_constructed(flavor: Path) -> None:
    """Typed relative, a flavor file with no row is looked up at the install
    root first, where nothing is: the printed reason is the flavor's."""
    (flavor / "WTF" / "Weird-constructed.txt").write_bytes(b"")
    report = explain_json("WTF/Weird-constructed.txt")
    assert isinstance(report.result, layout.Unclassified)
    assert report.result.reason == "no file-map row matches"
    text = ok("explain", "WTF/Weird-constructed.txt").stdout
    assert text.startswith("WTF/Weird-constructed.txt: no file-map row: no file-map row matches\n")


def test_layout_classify_of_a_missing_path_constructed(flavor: Path) -> None:
    lay = layout.Layout.for_flavor(install.discover(flavor.parent).flavors[0], flavor.parent)
    missing = lay.classify(f"WTF/Account/{ACCOUNT}/unknown.txt")
    assert isinstance(missing, layout.Unclassified)
    assert missing.reason == NOT_FOUND + AS_FOLDER
    slash = lay.classify(f"WTF/Account/{ACCOUNT}/unknown.txt/")
    assert isinstance(slash, layout.Classified) and slash.entry.id == "realm-folder"
    # The caller's word is taken, and nothing is probed on disk for it.
    told = lay.classify(f"WTF/Account/{ACCOUNT}/unknown.txt", is_dir=True)
    assert isinstance(told, layout.Classified) and told.entry.id == "realm-folder"
    told_file = lay.classify(f"WTF/Account/{ACCOUNT}/unknown.txt", is_dir=False)
    assert isinstance(told_file, layout.Unclassified)
    assert told_file.reason == "no file-map row matches"
    # Under a file: "not a directory" is absent too.
    under_file = lay.classify("WTF/Config.wtf/unknown.txt")
    assert isinstance(under_file, layout.Unclassified) and under_file.reason == NOT_FOUND


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_an_unreadable_folder_is_not_not_found_constructed(flavor: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads past permission bits")
    lay = layout.Layout.for_flavor(install.discover(flavor.parent).flavors[0], flavor.parent)
    char = flavor / "WTF" / "Account" / ACCOUNT / REALM / FIRST
    char.chmod(0)
    try:
        found = lay.classify(f"WTF/Account/{ACCOUNT}/{REALM}/{FIRST}/unknown.txt")
    finally:
        char.chmod(0o755)
    assert isinstance(found, layout.Unclassified)
    assert found.reason == "no file-map row matches"


def test_verdict_words_for_a_missing_path() -> None:
    gate_path = verdict_of(None, "flavor", "WTF/unknown.txt", missing=True)
    assert gate_path.level == "unclassified" and gate_path.label == "unknown"
    assert gate_path.words.startswith(NOT_FOUND_WORDS)
    assert gate_path.restorable is True
    elsewhere = verdict_of(None, "flavor", "Cache/unknown.txt", missing=True)
    assert elsewhere.words == NOT_FOUND_WORDS and elsewhere.restorable is False
    folder = verdict_of(None, "flavor", "WTF/unknown/", folder=True, missing=True)
    assert folder.words == NOT_FOUND_WORDS and folder.restorable is None
    # A missing path with a row is judged by its row.
    entry = filemap.load().entry("account-savedvariables")
    assert verdict_of(entry, "flavor", "WTF/x.lua", missing=True) == verdict_of(
        entry, "flavor", "WTF/x.lua"
    )
