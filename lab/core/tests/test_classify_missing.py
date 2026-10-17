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
SECOND = "_constructed_"  # a second flavor folder, a copy of the capture (constructed)
NOT_FOUND = "not found; nothing is at this path"
NO_ROW_LIKE = "no file-map row matches a name like this"
NOT_FOUND_REASON = f"{NOT_FOUND}, and {NO_ROW_LIKE}"
AS_FOLDER = "end it with / to see what a folder by this name would be (/ works on Windows too)"
NOT_FOUND_WORDS = NOT_FOUND_REASON
NO_ROW = "no file-map row matches"

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


def explain_json(path: str, *args: str) -> cli.ExplainReport:
    return cli.ExplainReport.model_validate_json(ok("explain", path, *args, "--json").stdout)


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
        assert report.absent is True
        if entry_id is None:
            assert isinstance(report.result, layout.Unclassified), typed
            tail = f"{NO_ROW_LIKE}; {AS_FOLDER}" if as_folder else NO_ROW_LIKE
            assert report.result.reason == f"{NOT_FOUND}, and {tail}", typed
            assert report.verdict.level == "unclassified"
            assert report.verdict.words.startswith(NOT_FOUND_WORDS)
            # What is (not) there leads; what the map says follows.
            first, second = text.splitlines()[:2]
            assert first.startswith(typed) and first.endswith(f": {NOT_FOUND}"), text
            assert second == f"  {tail}", text
        else:
            assert isinstance(report.result, layout.Classified), typed
            assert report.result.entry.id == entry_id
            assert f"  file-map row: {entry_id} " in text
            noun = "folder" if path.endswith("/") else "file"
            assert f"  on disk:      nothing at this path; this is what a {noun} here would be" in (
                text
            )
            # No view or view hint: each would fail on a path with nothing on it.
            assert report.views == []
            assert report.views_note == "nothing is at this path, so no wowlab command can show it"
            assert all(h.argv[0] == "snap" for h in report.hints), report.hints


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
            assert report.absent is True, rel
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
    assert missing.reason == f"{NOT_FOUND_REASON}; {AS_FOLDER}"
    slash = lay.classify(f"WTF/Account/{ACCOUNT}/unknown.txt/")
    assert isinstance(slash, layout.Classified) and slash.entry.id == "realm-folder"
    # The caller's word is taken, and nothing is probed on disk for it.
    told = lay.classify(f"WTF/Account/{ACCOUNT}/unknown.txt", is_dir=True)
    assert isinstance(told, layout.Classified) and told.entry.id == "realm-folder"
    told_file = lay.classify(f"WTF/Account/{ACCOUNT}/unknown.txt", is_dir=False)
    assert isinstance(told_file, layout.Unclassified)
    assert told_file.reason == NO_ROW
    # Under a file: "not a directory" is absent too.
    under_file = lay.classify("WTF/Config.wtf/unknown.txt")
    assert isinstance(under_file, layout.Unclassified)
    assert under_file.reason == NOT_FOUND_REASON
    assert layout.absent(flavor / "WTF" / "Config.wtf" / "unknown.txt", flavor.parent)
    assert not layout.absent(flavor / "WTF" / "Config.wtf", flavor.parent)
    assert not layout.absent(flavor.parent.parent / "elsewhere.txt", flavor.parent)


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
    assert gate_path.words == (
        f"{NOT_FOUND_WORDS}; `snap restore --paths` naming it puts back a snapshot's copy, "
        "or removes a file created here after that snapshot"
    )
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


# ─── several flavors (review of #242) ────────────────────────────────────────


@pytest.fixture
def two(root: Path) -> Path:
    """A second flavor folder, a copy of the capture, and a file only it holds."""
    shutil.copytree(CAPTURE / "forever", root / SECOND)
    (root / SECOND / "WTF" / "OnlySecond-constructed.txt").write_bytes(b"")
    assert {f.folder for f in install.discover(root).flavors} == {FLAVOR, SECOND}
    return root


def test_with_several_flavors_an_existing_path_keeps_mains_words(two: Path) -> None:
    """No --flavor: the path is read at the install root, where nothing is,
    but it is in a flavor folder, so it is not "not found"."""
    text = ok("explain", "WTF/Config.wtf").stdout
    assert text.startswith(
        "WTF/Config.wtf: no file-map row: no file-map row matches\n"
        "  (with several flavors, pass --flavor to read PATH inside one of them)\n"
    ), text
    report = explain_json("WTF/Config.wtf")
    assert isinstance(report.result, layout.Unclassified)
    assert report.result.reason == NO_ROW
    assert report.absent is False
    assert not report.verdict.words.startswith("not found")


def test_a_path_in_another_flavor_only_is_not_not_found_constructed(two: Path) -> None:
    for args in (["--flavor", FLAVOR], []):
        report = explain_json("WTF/OnlySecond-constructed.txt", *args)
        assert report.absent is False, args
        assert isinstance(report.result, layout.Unclassified)
        assert report.result.reason == NO_ROW, args
        text = ok("explain", "WTF/OnlySecond-constructed.txt", *args).stdout
        assert NOT_FOUND not in text, text


def test_with_several_flavors_a_path_missing_everywhere_is_not_found_constructed(
    two: Path,
) -> None:
    report = explain_json("WTF/zz-missing-constructed.txt")
    assert report.absent is True
    assert isinstance(report.result, layout.Unclassified)
    assert report.result.reason.startswith(NOT_FOUND_REASON)
    assert report.verdict.words.startswith(NOT_FOUND_WORDS)
    text = ok("explain", "WTF/zz-missing-constructed.txt").stdout
    assert text.startswith(f"WTF/zz-missing-constructed.txt: {NOT_FOUND}\n"), text


# ─── links (review of #242) ──────────────────────────────────────────────────


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot make a symlink here: {exc}")


@pytest.mark.parametrize(
    "dangling", [False, True], ids=["constructed-link", "constructed-dangling"]
)
def test_a_path_behind_a_link_is_never_not_found(
    tmp_path: Path, flavor: Path, dangling: bool
) -> None:
    """Nothing is followed through a link, so what is behind it is unknown:
    the words stay main's, never "not found" or the folder lookup hint."""
    outside = tmp_path / "outside"
    if not dangling:
        outside.mkdir()
    account = flavor / "WTF" / "Account" / ACCOUNT
    _link(account / "LinkDir", outside)
    for rel in (
        f"WTF/Account/{ACCOUNT}/LinkDir/inner/missing.txt",
        f"WTF/Account/{ACCOUNT}/LinkDir/missing.txt",
    ):
        lay = layout.Layout.for_flavor(install.discover(flavor.parent).flavors[0], flavor.parent)
        found = lay.classify(rel)
        assert not (
            isinstance(found, layout.Unclassified) and found.reason.startswith(NOT_FOUND)
        ), (rel, found)
        assert not layout.absent(flavor / rel, flavor.parent), rel
        report = explain_json(rel)
        assert report.absent is False, rel
        assert not report.verdict.words.startswith("not found"), rel
        text = ok("explain", rel).stdout
        assert NOT_FOUND not in text and "end it with /" not in text, text
    assert not outside.exists() or not any(outside.iterdir())  # L1: nothing made behind it


# ─── what a missing path a row describes would be (review of #242) ───────────


def test_a_missing_savedvariables_file_says_when_the_client_writes_it_constructed(
    flavor: Path,
) -> None:
    text = ok("explain", f"WTF/Account/{ACCOUNT}/SavedVariables/NoSuchAddon.lua").stdout
    assert (
        "  on disk:      nothing at this path; this is what a file here would be; "
        "the client writes it at logout or /reload\n"
    ) in text


def test_a_missing_path_a_any_depth_row_names_is_a_file_or_folder_constructed(
    flavor: Path,
) -> None:
    report = explain_json("Interface/AddOns/NoSuchAddon-constructed")
    assert isinstance(report.result, layout.Classified)
    text = ok("explain", "Interface/AddOns/NoSuchAddon-constructed").stdout
    assert "this is what a file or folder here would be" in text, text


def test_the_first_snapshot_hint_for_a_missing_path_constructed(flavor: Path) -> None:
    report = explain_json(f"WTF/Account/{ACCOUNT}/zz-missing-constructed.txt")
    assert report.absent is True
    does = [h.does for h in report.hints if h.argv[:2] == ["snap", "create"]]
    assert does == [
        "take a snapshot, so a file created here later can be removed with "
        "`snap restore --paths` naming it"
    ], report.hints
    present = explain_json("WTF/Config.wtf")
    assert [h.does for h in present.hints if h.argv[:2] == ["snap", "create"]] == [
        "take a snapshot, so a later change to this file can be put back"
    ]
