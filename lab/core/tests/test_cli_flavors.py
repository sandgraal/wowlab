"""Several flavors, characters in one (M16-114, docs/LAB_PLAN.md §16 CLI
conventions, amendment of 2026-11-11), through Typer's runner.

The install is the committed capture `fixtures/macos/` (M11-03) copied into
`tmp_path` with its flavor folder named as its provenance row says
(`_classic_beta_`), as `test_cli_char_list.py` does. Every test then adds a
second flavor folder, `_forever_`, holding only a `.flavor.info` with a
product no `.build.info` row names: a `constructed` boundary input (L8),
never a capture. `WOWLAB_WOW_ROOT` points at the copy, `WOWLAB_FLAVOR` is
cleared unless a test sets it, and the user data directory is redirected
into `tmp_path`; nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
EMPTY = "_forever_"  # constructed: a second flavor with no character
ACCOUNT = "90000001#6"
FIRST = "1/Labchard-Labrealmg"

# The capture's version, from its .build.info row (what discovery reads).
VERSION = "1.60.1.69913"
ONLY = "the only flavor with character folders"
USING = f"wowlab: using {FLAVOR} ({VERSION}), {ONLY} (--flavor picks another)"
OTHER_ACCOUNT = "90000002#1"  # constructed

runner = CliRunner()


@pytest.fixture(autouse=True)
def two_flavors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    # No client in the process table: the write gate's client check reads it,
    # and a Windows runner's table cannot always be read ("unknown", exit 3).
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    other = root / EMPTY
    other.mkdir()
    (other / ".flavor.info").write_bytes(b"Product Flavor!STRING:0\nwow_constructed\n")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    monkeypatch.chdir(tmp_path)
    return root


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def test_discovery_finds_both_flavors_constructed() -> None:
    data = json.loads(run("install", "show", "--json").stdout)
    assert sorted(f["folder"] for f in data["flavors"]) == sorted([FLAVOR, EMPTY])


# ─── char list ───────────────────────────────────────────────────────────────


def test_char_list_labels_rows_by_flavor_constructed() -> None:
    result = run("char", "list")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    lines = result.stdout.splitlines()
    heading = f"Flavor {FLAVOR} ({VERSION})"
    assert heading in lines
    assert f"Flavor {EMPTY}: no character folder" in lines
    at = lines.index(heading)
    assert lines[at + 1].startswith(f"Account {ACCOUNT}:")
    assert any(FIRST in line for line in lines[at + 1 :])


def test_char_list_json_carries_flavor_constructed() -> None:
    result = run("char", "list", "--json", "--brief")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data["flavor_folder"] is None
    assert data["installed_version"] is None
    assert data["characters"]
    assert {c["flavor"] for c in data["characters"]} == {FLAVOR}
    flavors = {f["folder"]: f for f in data["flavors"]}
    assert set(flavors) == {FLAVOR, EMPTY}
    # No --account: `account_found` is null on every entry (M16-116).
    assert flavors[EMPTY] == {
        "folder": EMPTY,
        "version": None,
        "characters": 0,
        "account_found": None,
    }
    assert flavors[FLAVOR]["account_found"] is None
    assert flavors[FLAVOR]["characters"] > 0
    assert flavors[FLAVOR]["version"]


def test_char_list_with_flavor_lists_that_one_constructed() -> None:
    result = run("char", "list", "--json", "--brief", "--flavor", FLAVOR)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data["flavor_folder"] == FLAVOR
    assert [f["folder"] for f in data["flavors"]] == [FLAVOR]
    assert {c["flavor"] for c in data["characters"]} == {FLAVOR}
    text = run("char", "list", "--flavor", FLAVOR).stdout
    assert "Flavor " not in text


def test_char_list_account_in_one_flavor_constructed() -> None:
    result = run("char", "list", "--account", ACCOUNT, "--json", "--brief")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data["account"] == ACCOUNT
    # The flavor without the account stays listed, marked (M16-116).
    assert [(f["folder"], f["account_found"]) for f in data["flavors"]] == [
        (FLAVOR, True),
        (EMPTY, False),
    ]
    assert run("char", "list", "--account", "nope").exit_code == 2


# ─── the other char commands ─────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["show", "gear", "binds", "macros"])
def test_char_reads_use_the_only_flavor_with_characters_constructed(command: str) -> None:
    result = run("char", command, "--character", FIRST, "--json")
    assert result.exit_code in (0, 1), (result.stdout, result.stderr)
    assert USING in result.stderr
    assert result.stderr.count(ONLY) == 1


def test_char_show_says_which_flavor_constructed() -> None:
    result = run("char", "show", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["flavor_folder"] == FLAVOR
    assert USING in result.stderr


def test_char_show_named_flavor_is_not_announced_constructed() -> None:
    result = run("char", "show", "--json", "--flavor", FLAVOR)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert ONLY not in result.stderr


def test_char_show_with_characters_in_both_still_asks_constructed(two_flavors: Path) -> None:
    shutil.rmtree(two_flavors / EMPTY)
    shutil.copytree(two_flavors / FLAVOR, two_flavors / EMPTY)
    result = run("char", "show")
    assert result.exit_code == 2
    assert "choose one with --flavor" in result.stderr


def test_char_show_named_empty_flavor_is_used_constructed() -> None:
    result = run("char", "show", "--flavor", EMPTY)
    assert result.exit_code != 0
    assert ONLY not in result.stderr


# ─── writes never pick ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        ("addon", "install", "lab"),
        ("addon", "install", "lab", "--dry-run"),
        ("addon", "remove", "lab"),
    ],
)
def test_write_without_flavor_lists_command_lines_constructed(args: tuple[str, ...]) -> None:
    result = run(*args)
    assert result.exit_code == 2, (result.stdout, result.stderr)
    for folder in (FLAVOR, EMPTY):
        assert f"`wowlab {' '.join(args)} --flavor {folder}`" in result.stderr
    assert ONLY not in result.stderr
    assert install.ENV_FLAVOR in result.stderr


def test_command_lines_keep_what_was_typed_constructed(two_flavors: Path) -> None:
    result = run("addon", "install", "lab", "--root", str(two_flavors), "--json")
    assert result.exit_code == 2
    message = json.loads(result.stdout)["message"]
    assert f"--root {two_flavors} --json --flavor {FLAVOR}" in message.replace("'", "")


def test_read_command_without_flavor_lists_command_lines_constructed() -> None:
    result = run("cvar", "list")
    assert result.exit_code == 2
    assert f"`wowlab cvar list --flavor {FLAVOR}`" in result.stderr


# ─── WOWLAB_FLAVOR ───────────────────────────────────────────────────────────


def test_env_flavor_is_used_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install.ENV_FLAVOR, FLAVOR)
    assert run("cvar", "list").exit_code == 0
    data = json.loads(run("char", "list", "--json", "--brief").stdout)
    assert data["flavor_folder"] == FLAVOR
    result = run("char", "show", "--json")
    assert result.exit_code == 0
    assert ONLY not in result.stderr


def test_env_flavor_reaches_writes_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install.ENV_FLAVOR, EMPTY)
    result = run("addon", "remove", "lab", "--dry-run", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["flavor_path"].endswith(EMPTY)


def test_flavor_option_wins_over_env_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install.ENV_FLAVOR, EMPTY)
    data = json.loads(run("char", "list", "--json", "--brief", "--flavor", FLAVOR).stdout)
    assert data["flavor_folder"] == FLAVOR
    assert data["characters"]
    result = run("addon", "remove", "lab", "--dry-run", "--json", "--flavor", FLAVOR)
    assert json.loads(result.stdout)["flavor_path"].endswith(FLAVOR)


def test_bad_env_flavor_errors_and_lists_flavors_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(install.ENV_FLAVOR, "_nope_")
    for args in (("cvar", "list"), ("char", "list"), ("char", "show")):
        result = run(*args)
        assert result.exit_code == 2, args
        assert "'_nope_'" in result.stderr and install.ENV_FLAVOR in result.stderr
        assert FLAVOR in result.stderr and EMPTY in result.stderr


def test_blank_env_flavor_is_unset_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install.ENV_FLAVOR, "  ")
    data = json.loads(run("char", "list", "--json", "--brief").stdout)
    assert data["flavor_folder"] is None


# ─── what counts as the only flavor with characters ──────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="a symlink needs a privilege on Windows")
def test_a_flavor_wowlab_cannot_look_inside_is_not_passed_over_constructed(
    two_flavors: Path, tmp_path: Path
) -> None:
    """The second flavor's WTF/Account is a link, never followed: it may hold
    characters, so nothing is picked and the several-flavors error stands."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (two_flavors / EMPTY / "WTF").mkdir()
    (two_flavors / EMPTY / "WTF" / "Account").symlink_to(elsewhere, target_is_directory=True)
    result = run("char", "show")
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert ONLY not in result.stderr
    assert "choose one with --flavor" in result.stderr
    listed = run("char", "list", "--json", "--brief")
    data = json.loads(listed.stdout)
    assert {n["flavor"] for n in data["not_looked_at"]} == {EMPTY}


def _other_account(root: Path) -> None:
    """The second flavor gets a copy of the capture's WTF/ under another
    account folder name (constructed)."""
    shutil.copytree(root / FLAVOR / "WTF", root / EMPTY / "WTF")
    account = root / EMPTY / "WTF" / "Account"
    (account / ACCOUNT).rename(account / OTHER_ACCOUNT)


def test_account_picks_the_only_flavor_with_that_accounts_characters_constructed(
    two_flavors: Path,
) -> None:
    _other_account(two_flavors)
    assert run("char", "show").exit_code == 2  # characters in both
    result = run("char", "show", "--account", ACCOUNT, "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert USING in result.stderr
    assert json.loads(result.stdout)["flavor_folder"] == FLAVOR
    other = run("char", "show", "--account", OTHER_ACCOUNT, "--json")
    assert other.exit_code == 0, (other.stdout, other.stderr)
    assert json.loads(other.stdout)["flavor_folder"] == EMPTY
    assert f"wowlab: using {EMPTY}, {ONLY}" in other.stderr


def test_char_list_names_a_flavor_without_the_account_constructed(two_flavors: Path) -> None:
    _other_account(two_flavors)
    text = run("char", "list", "--account", OTHER_ACCOUNT).stdout.splitlines()
    assert f"Flavor {FLAVOR} ({VERSION}): no account {OTHER_ACCOUNT}" in text
    assert f"Flavor {EMPTY}" in text


def test_char_list_json_names_a_flavor_without_the_account_constructed(
    two_flavors: Path,
) -> None:
    """M16-116: the flavor the text calls "no account X" stays in `flavors[]`
    with no characters and `account_found: false`; the text is as before."""
    _other_account(two_flavors)
    text = run("char", "list", "--account", OTHER_ACCOUNT)
    assert text.exit_code == 0, (text.stdout, text.stderr)
    lines = text.stdout.splitlines()
    assert f"Flavor {FLAVOR} ({VERSION}): no account {OTHER_ACCOUNT}" in lines
    assert f"Flavor {EMPTY}" in lines
    result = run("char", "list", "--account", OTHER_ACCOUNT, "--json", "--brief")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data["account"] == OTHER_ACCOUNT
    assert {c["flavor"] for c in data["characters"]} == {EMPTY}
    flavors = data["flavors"]
    order = [f["folder"] for f in json.loads(run("install", "show", "--json").stdout)["flavors"]]
    assert [f["folder"] for f in flavors] == order  # discovery order, both kept
    by_folder = {f["folder"]: f for f in flavors}
    assert by_folder[FLAVOR] == {
        "folder": FLAVOR,
        "version": VERSION,
        "characters": 0,
        "account_found": False,
    }
    assert by_folder[EMPTY]["account_found"] is True
    assert by_folder[EMPTY]["characters"] > 0
    # The report reads back into its model.
    report = cli.CharListReport.model_validate_json(result.stdout)
    assert {f.folder: f.account_found for f in report.flavors} == {FLAVOR: False, EMPTY: True}


def test_char_list_account_behind_an_unfollowed_link_is_not_found_constructed(
    two_flavors: Path, tmp_path: Path
) -> None:
    """M16-116: when the second flavor's whole `WTF/Account/` is a link
    wowlab does not follow, the --account folder may be behind it: that
    flavor is `account_found: null` (not looked at, named in
    `not_looked_at`), never true or false. The first flavor, read, stays
    true."""
    outside = tmp_path / "outside-accounts"
    shutil.copytree(two_flavors / FLAVOR / "WTF" / "Account", outside)
    (two_flavors / EMPTY / "WTF").mkdir()
    try:
        (two_flavors / EMPTY / "WTF" / "Account").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    result = run("char", "list", "--account", ACCOUNT, "--json", "--brief")
    assert result.exit_code == 1, (result.stdout, result.stderr)  # a place not looked inside
    data = json.loads(result.stdout)
    assert {n["flavor"] for n in data["not_looked_at"]} == {EMPTY}
    by_folder = {f["folder"]: f for f in data["flavors"]}
    assert by_folder[EMPTY] == {
        "folder": EMPTY,
        "version": None,
        "characters": 0,
        "account_found": None,
    }
    assert by_folder[FLAVOR]["account_found"] is True
    assert by_folder[FLAVOR]["characters"] > 0


def test_char_list_one_flavor_account_found_constructed() -> None:
    """With --flavor and --account the one flavor listed has the account;
    one without it is an error, as before (M16-116)."""
    result = run("char", "list", "--flavor", FLAVOR, "--account", ACCOUNT, "--json", "--brief")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert [(f["folder"], f["account_found"]) for f in json.loads(result.stdout)["flavors"]] == [
        (FLAVOR, True)
    ]


def test_char_gear_names_its_flavor_constructed() -> None:
    result = run("char", "gear", "--character", FIRST, "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["flavor_folder"] == FLAVOR
    assert USING in result.stderr


def test_suggested_lines_go_before_a_double_dash_constructed() -> None:
    result = run("cvar", "get", "--", "x")
    assert result.exit_code == 2
    assert f"`wowlab cvar get --flavor {FLAVOR} -- x`" in result.stderr
