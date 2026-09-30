"""`wowlab char list` (M12-09, docs/LAB_PLAN.md §14.4) through Typer's runner.

The install is a committed capture copied into `tmp_path` with its flavor
folder named as its provenance row says (`_classic_beta_`), as
`test_cli_char.py` does: `fixtures/macos/` (M11-03: two characters with a
`WowLab.lua`, one character folder without one, and the `<Realm>/<First>/`
twin that holds only `AddOns.txt`) or `fixtures/macos-70058/` (M11-23: one
character). `WOWLAB_WOW_ROOT` points at the copy and the user data directory
is redirected into `tmp_path`; nothing reads or writes a real install.

Inputs labelled `constructed` (L8) are boundary and hostile cases: a
capture cut short, a second account, extra character folders copied from a
captured one (names that sort differently from their creation order, names
holding a line feed or a bidirectional override), no lab file at all, a
folder replaced by a link or made unlistable (skipped where the platform
cannot), and a second spelling of a real `WowLab.lua` added to the layout's
listing (names a case-insensitive volume cannot hold twice). Characters
outside printable ASCII are spelled with `chr`.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon, snapshot
from wowlab_core.layout import Layout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
CAPTURE_70058 = FIXTURES / "macos-70058"
FLAVOR = "_classic_beta_"  # the provenance rows' flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCOUNT_DIR = f"WTF/Account/{ACCOUNT}"
FIRST = "1/Labchard-Labrealmg"
SECOND = "1/Labcharb-Labrealmf"
TWIN_REALM = "Labrealmb Partb Partc Partd"  # holds the <Realm>/<First>/ twin, only AddOns.txt
NO_ADDON = "Labcharb-Labrealmd"
HEADING = f"Account {ACCOUNT}: 2 character folders with a WowLab.lua"
ROW = re.compile(
    r"^  (?P<character>.+?) +(?:written \d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}|time unknown)"
    r"  (?P<what>.+)$"
)
RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE, Cf
NOT_FOLLOWED = "a link, not followed, as wowlab never follows links"
POSIX_PERMISSIONS = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permissions, and not as root",
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


def _install(capture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(capture / ".build.info", root / ".build.info")
    shutil.copytree(capture / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _install(CAPTURE, tmp_path, monkeypatch)


def _lab_file(flavor: Path, character: str, account_dir: str = ACCOUNT_DIR) -> Path:
    return flavor / account_dir / character / "SavedVariables" / "WowLab.lua"


def _plant(flavor: Path, character: str, account_dir: str = ACCOUNT_DIR) -> Path:
    """A constructed character folder holding a copy of a captured WowLab.lua."""
    target = _lab_file(flavor, character, account_dir)
    target.parent.mkdir(parents=True)
    shutil.copy2(_lab_file(flavor, FIRST), target)
    return target


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _rows(stdout: str) -> list[tuple[str, str]]:
    out = []
    for line in stdout.split("\n"):
        match = ROW.match(line)
        if match:
            out.append((match.group("character"), match.group("what")))
    return out


def _state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


# ─── the captures ────────────────────────────────────────────────────────────


def test_char_list_lists_each_captured_character_once(flavor: Path) -> None:
    result = ok("char", "list")
    lines = result.stdout.split("\n")
    assert lines[0] == HEADING
    assert _rows(result.stdout) == [
        (SECOND, "schema 1, saved by client 1.60.1.70009, spec id 1482"),
        (FIRST, "schema 1, saved by client 1.60.1.70009, spec id 1490"),
    ]
    assert result.stdout.count(SECOND) == 1 and result.stdout.count(FIRST) == 1
    # The twin and the folder without the addon's file are not listed.
    assert TWIN_REALM not in result.stdout and NO_ADDON not in result.stdout
    assert not any(line.lstrip().startswith("Labchard ") for line in lines)
    assert lines[3:6] == [
        cli._CHAR_NOTES[0],
        "The addon records no time; the time shown is the file's modification time: the "
        "client's last save, unless something wrote the file since (a wowlab snap restore, "
        "undo or sv merge, or a copy).",
        "One row per character folder: a renamed or transferred character (and a deleted one "
        "[verify]) keeps its old folder and last save here.",
    ]
    assert result.stderr == ""


def test_char_list_on_the_70058_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(CAPTURE_70058, tmp_path, monkeypatch)
    result = ok("char", "list")
    assert (
        result.stdout.split("\n")[0] == f"Account {ACCOUNT}: 1 character folder with a WowLab.lua"
    )
    assert _rows(result.stdout) == [
        (FIRST, "schema 1, saved by client 1.60.1.70058, spec id 1490"),
    ]


def test_char_list_shows_the_files_modification_time(flavor: Path) -> None:
    stamp = 1_700_000_000_000_000_000
    os.utime(_lab_file(flavor, FIRST), ns=(stamp, stamp))
    (row,) = [ln for ln in ok("char", "list").stdout.split("\n") if FIRST in ln]
    assert f"written {cli._local_time(stamp)}  schema 1" in row


def test_char_list_json_validates(flavor: Path) -> None:
    result = ok("char", "list", "--json")
    report = cli.CharListReport.model_validate_json(result.stdout)
    assert report.flavor_folder == FLAVOR
    assert report.account is None
    assert report.notes == cli._CHAR_LIST_NOTES
    assert report.not_looked_at == []
    assert [e.character for e in report.characters] == [SECOND, FIRST]
    assert report.characters == labaddon.read_all(Layout(flavor))
    assert {e.shape for e in report.characters} == {"numeric_folder"}
    assert '"shape": "numeric_folder"' in result.stdout
    for entry in report.characters:
        assert entry.error is None
        assert entry.record == labaddon.read_char(flavor / entry.file)
        assert entry.mtime_ns == (flavor / entry.file).lstat().st_mtime_ns
    assert all(" " <= ch <= "~" for ch in result.stdout.replace("\n", ""))
    assert result.stderr == ""


def test_char_list_reads_without_writing(flavor: Path) -> None:
    root = flavor.parent
    before = _state(root)
    ok("char", "list")
    ok("char", "list", "--json")
    ok("char", "list", "--account", ACCOUNT)
    assert _state(root) == before


# ─── an unreadable file ──────────────────────────────────────────────────────


def _damage(flavor: Path) -> str:
    target = _lab_file(flavor, FIRST)
    data = target.read_bytes()
    target.write_bytes(data[: len(data) // 2])
    return f"{ACCOUNT_DIR}/{FIRST}/SavedVariables/WowLab.lua"


def test_constructed_damaged_file_is_named_and_the_rest_listed_exit_1(flavor: Path) -> None:
    file = _damage(flavor)
    result = run("char", "list")
    assert result.exit_code == 1
    assert "Traceback" not in result.stdout + result.stderr
    rows = _rows(result.stdout)
    assert [character for character, _ in rows] == [SECOND, FIRST]
    assert rows[0][1] == "schema 1, saved by client 1.60.1.70009, spec id 1482"
    assert rows[1][1].startswith("not read: not SavedVariables data the parser accepts: line ")
    (line,) = result.stderr.split("\n")[:-1]
    assert line.startswith(
        f"wowlab: could not read {file}: not SavedVariables data the parser accepts: line "
    )
    assert result.stdout.split("\n")[0] == HEADING


def test_constructed_damaged_file_json_still_validates_exit_1(flavor: Path) -> None:
    file = _damage(flavor)
    result = run("char", "list", "--json")
    assert result.exit_code == 1
    report = cli.CharListReport.model_validate_json(result.stdout)
    second, first = report.characters
    assert second.character == SECOND and second.record is not None and second.error is None
    assert first.character == FIRST and first.record is None and first.file == file
    assert first.error is not None
    assert first.error.startswith("not SavedVariables data the parser accepts: line ")
    (line,) = result.stderr.split("\n")[:-1]
    assert line == f"wowlab: could not read {file}: {first.error}"


def test_constructed_unknown_schema_is_named_and_the_rest_listed(flavor: Path) -> None:
    _lab_file(flavor, SECOND).write_bytes(b'\r\nWowLabCharDB = {\r\n["schema"] = 3,\r\n}\r\n')
    result = run("char", "list")
    assert result.exit_code == 1
    rows = dict(_rows(result.stdout))
    assert rows[SECOND] == (
        "not read: WowLabCharDB is schema 3, and this reader knows schema 1, 2 only: it was "
        "written by another version of the lab-addon; nothing was read"
    )
    assert rows[FIRST] == "schema 1, saved by client 1.60.1.70009, spec id 1490"
    assert "wowlab: could not read " in result.stderr and SECOND in result.stderr


def test_constructed_every_unreadable_file_is_named(flavor: Path) -> None:
    for character in (FIRST, SECOND):
        _lab_file(flavor, character).write_bytes(b"not = a lab file\n")
    result = run("char", "list")
    assert result.exit_code == 1
    lines = result.stderr.split("\n")[:-1]
    assert len(lines) == 2
    assert SECOND in lines[0] and FIRST in lines[1]
    assert all(line.startswith("wowlab: could not read ") for line in lines)


# ─── order, accounts, nothing to list ────────────────────────────────────────


def test_constructed_order_is_stable(flavor: Path) -> None:
    created = ["1/zeta-A", "Realm Name/Gamma", "1/Alpha-B", "1/beta-C"]
    for character in created:
        _plant(flavor, character)
    expected = ["1/Alpha-B", "1/beta-C", SECOND, FIRST, "1/zeta-A", "Realm Name/Gamma"]
    first = ok("char", "list").stdout
    assert [c for c, _ in _rows(first)] == expected
    assert first.split("\n")[0] == f"Account {ACCOUNT}: 6 character folders with a WowLab.lua"
    for step, character in enumerate(reversed(expected)):
        stamp = 1_000_000_000_000_000_000 + step * 1_000_000_000
        os.utime(_lab_file(flavor, character), ns=(stamp, stamp))
    again = ok("char", "list").stdout
    assert [c for c, _ in _rows(again)] == expected
    assert ok("char", "list").stdout == again
    report = cli.CharListReport.model_validate_json(ok("char", "list", "--json").stdout)
    assert [e.character for e in report.characters] == expected


def test_constructed_every_account_unless_one_is_named(flavor: Path) -> None:
    _plant(flavor, "2/Other-Char", "WTF/Account/90000002#1")
    everyone = ok("char", "list").stdout
    lines = everyone.split("\n")
    assert lines[0] == HEADING
    assert "Account 90000002#1: 1 character folder with a WowLab.lua" in lines
    assert [c for c, _ in _rows(everyone)] == [SECOND, FIRST, "2/Other-Char"]
    only = ok("char", "list", "--account", "90000002#1").stdout
    assert only.split("\n")[0] == "Account 90000002#1: 1 character folder with a WowLab.lua"
    assert [c for c, _ in _rows(only)] == ["2/Other-Char"]
    report = cli.CharListReport.model_validate_json(
        ok("char", "list", "--account", "90000002#1", "--json").stdout
    )
    assert report.account == "90000002#1"
    assert [(e.account, e.character) for e in report.characters] == [("90000002#1", "2/Other-Char")]


def test_constructed_unknown_account_is_a_usage_error(flavor: Path) -> None:
    result = ok("char", "list", "--account", ACCOUNT)
    assert result.stdout.split("\n")[0] == HEADING
    unknown = run("char", "list", "--account", "Nobody")
    assert unknown.exit_code == cli.EXIT_USAGE
    assert unknown.stderr == (
        f"wowlab: no account folder 'Nobody' (accounts wowlab could look inside: {ACCOUNT})\n"
    )
    assert unknown.stdout == ""


def test_constructed_account_is_matched_with_case_folded_as_char_show_does(
    flavor: Path,
) -> None:
    (flavor / "WTF/Account" / ACCOUNT).rename(flavor / "WTF/Account/MixedCase")
    result = ok("char", "list", "--account", "mixedcase")
    assert (
        result.stdout.split("\n")[0] == "Account MixedCase: 2 character folders with a WowLab.lua"
    )
    report = cli.CharListReport.model_validate_json(
        ok("char", "list", "--account", "mixedcase", "--json").stdout
    )
    assert report.account == "MixedCase"


def test_constructed_nothing_to_list(flavor: Path) -> None:
    _lab_file(flavor, FIRST).unlink()
    _lab_file(flavor, SECOND).unlink()
    result = ok("char", "list")
    assert result.stdout == (
        "No character folder in any account has a WowLab.lua (install the lab-addon with "
        "`wowlab addon install lab`, log in on the character, then log out or /reload).\n"
    )
    named = ok("char", "list", "--account", ACCOUNT)
    assert named.stdout.startswith(f"No character folder in account {ACCOUNT} has a WowLab.lua")
    report = cli.CharListReport.model_validate_json(ok("char", "list", "--json").stdout)
    assert report.characters == [] and report.not_looked_at == []


# ─── what wowlab could not look at ───────────────────────────────────────────


def _move_and_link(path: Path, tmp_path: Path, *, is_dir: bool) -> None:
    outside = tmp_path / f"outside-{path.name}"
    shutil.move(path, outside)
    try:
        path.symlink_to(outside, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def test_constructed_linked_character_folder_is_named_exit_1(flavor: Path, tmp_path: Path) -> None:
    _move_and_link(flavor / ACCOUNT_DIR / SECOND, tmp_path, is_dir=True)
    result = run("char", "list")
    assert result.exit_code == 1
    lines = result.stdout.split("\n")
    assert lines[0] == (
        f"Account {ACCOUNT}: 1 character folder with a WowLab.lua, 1 that wowlab could not "
        "look inside"
    )
    assert _rows(result.stdout) == [
        (SECOND, f"not read: could not look inside the character folder ({NOT_FOLLOWED})"),
        (FIRST, "schema 1, saved by client 1.60.1.70009, spec id 1490"),
    ]
    assert f"  {SECOND}  time unknown  not read: " in result.stdout
    assert result.stderr == (
        f"wowlab: could not look inside {ACCOUNT_DIR}/{SECOND} (the character folder): "
        f"{NOT_FOLLOWED}\n"
    )
    report = cli.CharListReport.model_validate_json(run("char", "list", "--json").stdout)
    entry = report.characters[0]
    assert entry.character == SECOND and entry.record is None and entry.mtime_ns is None
    assert entry.place == "character_folder" and entry.error == NOT_FOLLOWED


@POSIX_PERMISSIONS
def test_constructed_unlistable_saved_variables_folder_is_named_once(flavor: Path) -> None:
    folder = _lab_file(flavor, SECOND).parent
    folder.chmod(0)
    try:
        result = run("char", "list")
    finally:
        folder.chmod(0o755)
    assert result.exit_code == 1
    assert dict(_rows(result.stdout))[SECOND] == (
        "not read: could not look inside the character's SavedVariables folder (Permission denied)"
    )
    assert result.stderr == (
        f"wowlab: could not look inside {ACCOUNT_DIR}/{SECOND}/SavedVariables (the "
        "character's SavedVariables folder): Permission denied\n"
    )


def test_constructed_linked_folder_above_the_characters_is_named_exit_1(
    flavor: Path, tmp_path: Path
) -> None:
    other = "WTF/Account/90000002#1"
    _plant(flavor, "2/Other-Char", other)
    _move_and_link(flavor / other / "2", tmp_path, is_dir=True)
    result = run("char", "list")
    assert result.exit_code == 1
    assert [c for c, _ in _rows(result.stdout)] == [SECOND, FIRST]
    assert (
        "wowlab could not look inside 1 place where character folders can be (named on "
        "stderr); any character folder inside it is not listed." in result.stdout.split("\n")
    )
    assert result.stderr == f"wowlab: could not look inside {other}/2: {NOT_FOLLOWED}\n"
    report = cli.CharListReport.model_validate_json(run("char", "list", "--json").stdout)
    assert [(n.path, n.reason, n.account) for n in report.not_looked_at] == [
        (f"{other}/2", NOT_FOLLOWED, "90000002#1")
    ]
    # Only the account it is in: another account is not told about it.
    assert ok("char", "list", "--account", ACCOUNT).stderr == ""


def test_constructed_account_option_naming_a_linked_account_names_the_link(
    flavor: Path, tmp_path: Path
) -> None:
    """`--account` naming an account folder that is a link: the link is named
    (exit 1), not "no account folder" (exit 2); an account that exists
    nowhere is still a usage error."""
    linked = "90000002#1"
    _move_and_link(flavor / "WTF/Account" / ACCOUNT, tmp_path, is_dir=True)
    (flavor / "WTF/Account" / ACCOUNT).rename(flavor / "WTF/Account" / linked)
    shutil.copytree(tmp_path / f"outside-{ACCOUNT}", flavor / "WTF/Account" / ACCOUNT)
    result = run("char", "list", "--account", linked)
    assert result.exit_code == 1
    assert result.stdout == (
        f"No character folder in account {linked} with a WowLab.lua was found, but wowlab could "
        "not look inside 1 place where character folders can be (named on stderr); any "
        "character folder inside it is not listed.\n"
    )
    assert result.stderr == f"wowlab: could not look inside WTF/Account/{linked}: {NOT_FOLLOWED}\n"
    report = cli.CharListReport.model_validate_json(
        run("char", "list", "--account", linked, "--json").stdout
    )
    assert report.account == linked and report.characters == []
    assert [n.path for n in report.not_looked_at] == [f"WTF/Account/{linked}"]
    nowhere = run("char", "list", "--account", "Nobody")
    assert nowhere.exit_code == cli.EXIT_USAGE
    assert nowhere.stderr == (
        f"wowlab: no account folder 'Nobody' (accounts wowlab could look inside: {ACCOUNT})\n"
    )


def test_constructed_account_option_is_folded_for_a_linked_account(
    flavor: Path, tmp_path: Path
) -> None:
    """`--account` is compared with case folded against a linked account as
    against a listed one, and the report spells it as the install does."""
    outside = tmp_path / "outside-account"
    shutil.copytree(flavor / "WTF/Account" / ACCOUNT, outside)
    try:
        (flavor / "WTF/Account/LinkedAcct").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    result = run("char", "list", "--account", "linkedacct")
    assert result.exit_code == 1
    assert (
        result.stderr == f"wowlab: could not look inside WTF/Account/LinkedAcct: {NOT_FOLLOWED}\n"
    )
    report = cli.CharListReport.model_validate_json(
        run("char", "list", "--account", "linkedacct", "--json").stdout
    )
    assert report.account == "LinkedAcct"
    assert [n.path for n in report.not_looked_at] == ["WTF/Account/LinkedAcct"]


def test_constructed_a_mistyped_account_opens_no_file(
    flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deciding whether a missing account is hidden needs the listing only:
    no WowLab.lua is opened before the usage error, nor before the hidden
    account's own answer."""
    outside = tmp_path / "outside-account"
    shutil.copytree(flavor / "WTF/Account" / ACCOUNT, outside)
    try:
        (flavor / "WTF/Account/LinkedAcct").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")

    def never(path: Path, **kwargs: object) -> bytes:
        raise AssertionError(f"read {path}")

    monkeypatch.setattr(snapshot, "read_regular_file", never)
    typo = run("char", "list", "--account", "Nobody")
    assert typo.exit_code == cli.EXIT_USAGE, (typo.stderr, typo.exception)
    hidden = run("char", "list", "--account", "LinkedAcct")
    assert hidden.exit_code == 1
    assert not isinstance(hidden.exception, AssertionError), hidden.exception
    assert "not followed" in hidden.stderr


def test_constructed_hard_linked_copy_has_its_own_row_and_line(flavor: Path) -> None:
    extra = _lab_file(flavor, "1/Linked-Copy")
    extra.parent.mkdir(parents=True)
    try:
        os.link(_lab_file(flavor, FIRST), extra)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot make a hard link here: {exc}")
    result = run("char", "list")
    assert result.exit_code == 1
    twin = f"the same file as {FIRST} (a hard link), read once, on that character's row"
    assert dict(_rows(result.stdout))["1/Linked-Copy"] == twin
    assert "not read" not in dict(_rows(result.stdout))["1/Linked-Copy"]
    assert (
        result.stderr
        == f"wowlab: {ACCOUNT_DIR}/1/Linked-Copy/SavedVariables/WowLab.lua is {twin}\n"
    )


@POSIX_PERMISSIONS
def test_constructed_nothing_listed_because_a_folder_could_not_be_looked_at(
    flavor: Path,
) -> None:
    """No install advice when wowlab could not look: it says what it missed."""
    folder = flavor / ACCOUNT_DIR / "1"
    folder.chmod(0)
    try:
        text = run("char", "list")
        named = run("char", "list", "--account", ACCOUNT)
        as_json = run("char", "list", "--json")
    finally:
        folder.chmod(0o755)
    assert text.exit_code == named.exit_code == as_json.exit_code == 1
    assert text.stdout == (
        "No character folder in any account with a WowLab.lua was found, but wowlab could not "
        "look inside 1 place where character folders can be (named on stderr); any character "
        "folder inside it is not listed.\n"
    )
    assert "install" not in text.stdout + named.stdout
    assert text.stderr == f"wowlab: could not look inside {ACCOUNT_DIR}/1: Permission denied\n"
    assert named.stdout.startswith(f"No character folder in account {ACCOUNT} with a WowLab.lua")
    report = cli.CharListReport.model_validate_json(as_json.stdout)
    assert report.characters == []
    assert [(n.path, n.reason) for n in report.not_looked_at] == [
        (f"{ACCOUNT_DIR}/1", "Permission denied")
    ]


def test_text_keeps_summaries_only_and_json_keeps_records(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Text needs only each file's summary, so it asks `survey` not to keep
    the parsed records (memory stays bounded by the largest single file)."""
    real = labaddon.survey
    asked: list[bool] = []

    def spy(*args: Any, **kwargs: Any) -> labaddon.AllCharacters:
        asked.append(kwargs["keep_records"])
        return real(*args, **kwargs)

    monkeypatch.setattr(labaddon, "survey", spy)
    text = ok("char", "list").stdout
    as_json = cli.CharListReport.model_validate_json(ok("char", "list", "--json").stdout)
    assert asked == [False, True]
    assert "saved by client 1.60.1.70009" in text
    assert all(e.record is not None and e.summary for e in as_json.characters)


@POSIX_PERMISSIONS
def test_constructed_permission_denied_reads_once(flavor: Path) -> None:
    target = _lab_file(flavor, FIRST)
    target.chmod(0)
    try:
        result = run("char", "list")
    finally:
        target.chmod(0o644)
    assert result.exit_code == 1
    assert dict(_rows(result.stdout))[FIRST] == "not read: Permission denied"
    assert result.stderr == (
        f"wowlab: could not read {ACCOUNT_DIR}/{FIRST}/SavedVariables/WowLab.lua: "
        "Permission denied\n"
    )


# ─── terminal-safe text ──────────────────────────────────────────────────────


def _terminal_safe(text: str) -> bool:
    return all(
        unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp")
        for line in text.split("\n")
        for c in line
    )


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows forbids control characters 0-31 in file names"
)
def test_constructed_hostile_folder_names_are_escaped(flavor: Path) -> None:
    names = {"Evil\nwowlab: restored 1 file": "Evil\\x0awowlab: restored 1 file"}
    names[f"Evil{RLO}elif.gnp"] = "Evil\\xe2\\x80\\xaeelif.gnp"
    for name in names:
        _plant(flavor, f"1/{name}")
    damaged = _plant(flavor, f"1/Bad{RLO}Name")
    damaged.write_bytes(b"broken = \n")
    result = run("char", "list")
    assert result.exit_code == 1
    assert _terminal_safe(result.stdout) and _terminal_safe(result.stderr)
    for name, shown in names.items():
        assert name not in result.stdout
        (row,) = [ln for ln in result.stdout.split("\n") if f"1/{shown} " in ln]
        assert row.startswith(f"  1/{shown} ")
    assert not any(ln.startswith("wowlab: restored") for ln in result.stdout.split("\n"))
    (line,) = result.stderr.split("\n")[:-1]
    assert line.startswith(f"wowlab: could not read {ACCOUNT_DIR}/1/Bad\\xe2\\x80\\xaeName/")
    report = cli.CharListReport.model_validate_json(run("char", "list", "--json").stdout)
    labels = {e.label for e in report.characters}
    assert set(names) <= labels and f"Bad{RLO}Name" in labels
    # The columns line up after escaping: every row's time starts at one offset.
    rows = [ln for ln in result.stdout.split("\n") if ln.startswith("  ")]
    assert len(rows) == 5
    assert len({ln.index("  written ", 2) for ln in rows}) == 1


# ─── char show chooses the same file (one rule, M12-09) ─────────────────────


def _listed_beside(monkeypatch: pytest.MonkeyPatch, character: str, spelling: str) -> None:
    """Every Layout lists `spelling` beside `character`'s WowLab.lua, as a
    case-sensitive volume could hold it."""
    real_walk = Layout.wtf_walk
    real_list = Layout.saved_variables

    def add(files: tuple[Any, ...]) -> tuple[Any, ...]:
        extra = [
            f.model_copy(
                update={"path": f.path[: -len("WowLab.lua")] + spelling, "addon": spelling[:-4]}
            )
            for f in files
            if f.path == f"{ACCOUNT_DIR}/{character}/SavedVariables/WowLab.lua"
        ]
        return (*files, *extra)

    def walk(self: Layout) -> Any:
        found = real_walk(self)
        return found.model_copy(update={"saved_variables": add(found.saved_variables)})

    def listing(self: Layout, scope: Any = None) -> tuple[Any, ...]:
        return add(real_list(self, scope))

    monkeypatch.setattr(Layout, "wtf_walk", walk)
    monkeypatch.setattr(Layout, "saved_variables", listing)


@pytest.mark.parametrize("spelling", ["WOWLAB.lua", "WowLab.LUA", "wowlab.lua"])
def test_constructed_char_show_and_char_list_read_the_exact_name(
    flavor: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    newest = 2_000_000_000_000_000_000  # FIRST is the latest, so `char show` alone takes it
    os.utime(_lab_file(flavor, FIRST), ns=(newest, newest))
    _listed_beside(monkeypatch, FIRST, spelling)
    listed = cli.CharListReport.model_validate_json(ok("char", "list", "--json").stdout)
    (entry,) = [e for e in listed.characters if e.character == FIRST]
    assert entry.file.endswith("/SavedVariables/WowLab.lua") and entry.record is not None
    for args in (["--character", FIRST], []):
        shown = cli.CharShowReport.model_validate_json(ok("char", "show", *args, "--json").stdout)
        assert shown.character == FIRST
        assert shown.file == entry.file


def test_constructed_char_show_and_char_list_refuse_the_same_variants(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = _lab_file(flavor, FIRST)
    lab.rename(lab.with_name("wowlab.lua"))
    real_walk = Layout.wtf_walk
    real_list = Layout.saved_variables

    def add(files: tuple[Any, ...]) -> tuple[Any, ...]:
        extra = [
            f.model_copy(update={"path": f.path[: -len("wowlab.lua")] + "WOWLAB.lua"})
            for f in files
            if f.path == f"{ACCOUNT_DIR}/{FIRST}/SavedVariables/wowlab.lua"
        ]
        return (*files, *extra)

    monkeypatch.setattr(
        Layout,
        "wtf_walk",
        lambda self: real_walk(self).model_copy(
            update={"saved_variables": add(real_walk(self).saved_variables)}
        ),
    )
    monkeypatch.setattr(
        Layout, "saved_variables", lambda self, scope=None: add(real_list(self, scope))
    )
    reason = (
        "the character folder holds 2 files named like WowLab.lua (SavedVariables/WOWLAB.lua, "
        "SavedVariables/wowlab.lua, none spelled exactly WowLab.lua); which one the client "
        "reads is not known, so none was read"
    )
    listed = run("char", "list")
    assert listed.exit_code == 1
    assert dict(_rows(listed.stdout))[FIRST] == f"not read: {reason}"
    shown = run("char", "show", "--character", FIRST)
    assert shown.exit_code == 1
    assert shown.stderr == f"wowlab: {FIRST}: {reason}\n"
