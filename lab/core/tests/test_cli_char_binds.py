"""`wowlab char binds` (M14-06, docs/LAB_PLAN.md §16.2).

The install is the captured tree (`fixtures/macos/`, build 69913) copied into
`tmp_path` with its flavor folder named as its provenance row says
(`_classic_beta_`), as `test_cli_binds_coach.py` does. Its account
`bindings-cache.wtf` has three `bind` lines. The action bars, bindings and
spellbook come from the real schema-3 record (`fixtures/macos-70425/`,
M13-05), copied into a character folder of that tree: a `cross-capture`
combination (two builds, two capture runs), said in the test ids. The tree
also holds a schema-1 `WowLab.lua` (M11-03) in another character folder.

Inputs labelled `constructed` are boundary cases (L8): a character
`bindings-cache.wtf` written by the test. Nothing here reads or writes a
real install: `WOWLAB_WOW_ROOT` points at the copy, and the user data and
user config directories are redirected into `tmp_path`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, keymap

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
SCHEMA_3 = (
    FIXTURES
    / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
CHARACTER = "Labcharb-Labrealmd"
CHAR_DIR = f"WTF/Account/{ACCOUNT}/1/{CHARACTER}"
RECORD = f"{CHAR_DIR}/SavedVariables/WowLab.lua"
ACCOUNT_FILE = f"WTF/Account/{ACCOUNT}/bindings-cache.wtf"
SCHEMA_1_CHARACTER = "1/Labchard-Labrealmg"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    return config


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


@pytest.fixture
def record(flavor: Path) -> Path:
    """The real schema-3 record as CHARACTER's WowLab.lua."""
    target = flavor / RECORD
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCHEMA_3, target)
    return target


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def report(*args: str) -> cli.CharBindsReport:
    out = ok("char", "binds", *args, "--json").stdout
    return cli.CharBindsReport.model_validate_json(out)


def _state(path: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(path.rglob("*")):
        st = p.lstat()
        out[p.relative_to(path).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


def test_char_binds_json_131_keys_3_agree_128_not_in_a_file_0_disagree_11_unreached_cross_capture(
    root: Path, record: Path
) -> None:
    before = _state(root)
    rep = report("--character", CHARACTER)
    assert _state(root) == before  # reads only (L1)
    assert (rep.flavor_folder, rep.account, rep.character, rep.chosen_by) == (
        FLAVOR,
        ACCOUNT,
        f"1/{CHARACTER}",
        "--character",
    )
    assert (rep.record_file, rep.record_schema, rep.record_error) == (RECORD, 3, None)
    assert rep.record_mtime_ns == (root / FLAVOR / RECORD).stat().st_mtime_ns
    assert [(f.scope, f.path, f.bind_lines) for f in rep.files] == [
        ("account", ACCOUNT_FILE, 3),
        ("character", None, 0),
    ]
    assert len(rep.keys) == 131
    assert sum(1 for k in rep.keys if k.agreement == "both") == 3
    assert sum(1 for k in rep.keys if k.agreement == "not in a file") == 128
    assert sum(1 for k in rep.keys if k.disagrees) == 0
    assert [u.slot for u in rep.unreached_slots] == [49, 50, *range(64, 73)]
    assert (rep.commands_left_out, rep.keys_left_out) == (1, 0)
    assert (rep.cannot_place, rep.cannot_compare) == (None, None)
    assert cli.CharBindsReport.model_validate_json(rep.model_dump_json()) == rep


def test_char_binds_text_has_the_table_unreached_slots_and_counts_cross_capture(
    record: Path,
) -> None:
    text = ok("char", "binds", "--character", CHARACTER).stdout
    lines = text.splitlines()
    assert lines[0] == f"Character: 1/{CHARACTER} in account {ACCOUNT}"
    assert lines[1].startswith(f"Record: {RECORD} (schema 3), written ")
    assert lines[2].startswith(f"Account bindings: {ACCOUNT_FILE} (3 bind lines), written ")
    assert lines[3].startswith("Character bindings: no bindings-cache.wtf (a character has")
    assert (
        "Keys: 131 (3 in a file and the record, 0 only in a file, 0 only in the record with a "
        "file line putting the key on another command, 128 only in the record with no file "
        "line for the key, 0 cleared)"
    ) in lines
    assert (
        "  Main-bar keys (ACTIONBUTTON1 to 12) are read at page 1, the page shown with no "
        "form, stance or stealth active; another page they may show is not followed."
    ) in lines
    assert (
        "  1                ACTIONBUTTON1                 1  casts Immolate (Rank 2, spell 707)"
        "  (record only (no file line for this key))"
    ) in lines
    assert (
        "  SHIFT-6          ACTIONPAGE6                   -  shows main-bar page 6"
        "  (record only (no file line for this key))"
    ) in lines
    assert "disagrees" not in text
    assert (
        "  Q                MULTIACTIONBAR1BUTTON1       61  casts Will of the Forsaken "
        "(Racial, spell 7744)  (account file and record agree)"
    ) in lines
    assert (
        f"  Only in the record, no file line for the key: {keymap.NOT_IN_A_FILE_NOTE}."
    ) in lines
    assert "Filled slots no key reaches on the page its bar shows by default: 11" in lines
    assert (
        "  slot  64  runs macro 149 (a macro index [verify]); the record keeps no macro name or "
        "body  (bottom left bar button 4, page 6)"
    ) in lines
    assert (
        "Left out of the bindings record, counted with no list: 1 command not named in plain "
        "upper case (a key bound straight to a spell, macro, item or click may be one), and "
        "0 keys not printable ASCII."
    ) in lines
    assert "cannot place" not in text


def test_char_binds_without_character_takes_the_latest_record(flavor: Path, record: Path) -> None:
    newest = max(p.stat().st_mtime_ns for p in flavor.rglob("WowLab.lua"))
    os.utime(record, ns=(newest + 10**9, newest + 10**9))
    rep = report()
    assert (rep.character, rep.chosen_by, rep.record_schema) == (f"1/{CHARACTER}", "latest", 3)
    assert "(written last; choose another with --character)" in ok("char", "binds").stdout


def test_a_schema_1_record_gives_the_file_rows_cannot_place(root: Path) -> None:
    rep = report("--character", SCHEMA_1_CHARACTER)
    assert (rep.record_schema, rep.record_error) == (1, None)
    assert [(k.key, k.slot, k.does, k.sources) for k in rep.keys] == [
        ("Q", 61, "cannot place: no action-bar record", ["account file"]),
        ("BUTTON3", 62, "cannot place: no action-bar record", ["account file"]),
        ("E", 63, "cannot place: no action-bar record", ["account file"]),
    ]
    assert (rep.unreached_slots, rep.commands_left_out, rep.keys_left_out) == ([], None, None)
    text = ok("char", "binds", "--character", SCHEMA_1_CHARACTER).stdout
    assert (
        "Cannot place keys on action slots or compare the files with the record: schema 1 has "
        "no action-bar or bindings record (" in text
    )
    assert "MULTIACTIONBAR1BUTTON1       61  cannot place: no action-bar record" in text
    assert "Filled slots no key reaches" not in text
    assert "Without a bindings record only the files' lines are listed" in text
    assert "Neither is taken as the truth" not in text


def test_a_character_without_a_record_gives_the_file_rows_exit_0(root: Path) -> None:
    rep = report("--character", CHARACTER)
    assert rep.record_file is None and rep.record_error is not None
    assert f"no WowLab.lua in 1/{CHARACTER}" in rep.record_error
    assert rep.character == f"1/{CHARACTER}"
    assert [k.does for k in rep.keys] == ["cannot place: no action-bar record"] * 3
    text = ok("char", "binds", "--character", CHARACTER).stdout
    assert text.splitlines()[1].startswith(f"Record: none (no WowLab.lua in 1/{CHARACTER}.")
    assert "Cannot place keys" not in text  # the Record line already says why


def test_a_character_folder_that_does_not_exist_is_a_usage_error(root: Path) -> None:
    assert run("char", "binds", "--character", "Nobody-Nowhere").exit_code == 2


def test_a_character_bindings_file_is_read_beside_the_account_one_constructed(
    flavor: Path, record: Path
) -> None:
    (flavor / CHAR_DIR / "bindings-cache.wtf").write_bytes(
        b"bind Q MULTIACTIONBAR1BUTTON1\r\nbind CTRL-F11 TOGGLEBACKPACK\r\n"
        b"bind 1 MULTIACTIONBAR5BUTTON1\r\n"
    )
    rep = report("--character", CHARACTER)
    assert [(f.scope, f.path, f.bind_lines) for f in rep.files] == [
        ("account", ACCOUNT_FILE, 3),
        ("character", f"{CHAR_DIR}/bindings-cache.wtf", 3),
    ]
    q = next(k for k in rep.keys if k.key == "Q")
    assert q.sources == ["account file", "character file", "record"]
    backpack = next(k for k in rep.keys if k.key == "CTRL-F11")
    assert (backpack.sources, backpack.agreement) == (["character file"], "file only")
    text = ok("char", "binds", "--character", CHARACTER).stdout
    assert (
        "  CTRL-F11         TOGGLEBACKPACK                -  a binding command, not an action "
        "slot  (character file only: disagrees with the record)"
    ) in text
    assert "the record has no command on CTRL-F11" in text
    # Key 1 is on ACTIONBUTTON1 in the record and on bar 5 in the file: both rows
    # disagree, and the unconfirmed bar's page note is printed under its row.
    lines = text.splitlines()
    bar5 = lines.index(
        "  1                MULTIACTIONBAR5BUTTON1      145  empty slot (nothing recorded in it)"
        "  (character file only: disagrees with the record)"
    )
    assert lines[bar5 + 1].endswith(
        "bar 5 button 1, page 13 (unconfirmed: no capture has had a key on this bar)"
    )
    assert lines[bar5 + 2].endswith("the record has 1 on ACTIONBUTTON1")
    assert (
        "  1                ACTIONBUTTON1                 1  casts Immolate (Rank 2, spell 707)"
        "  (record only: disagrees with the files)"
    ) in lines
