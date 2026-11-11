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
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
EMPTY = "_forever_"  # constructed: a second flavor with no character
ACCOUNT = "90000001#6"
FIRST = "1/Labchard-Labrealmg"

runner = CliRunner()


@pytest.fixture(autouse=True)
def two_flavors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
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
    assert f"Flavor {FLAVOR}" in lines
    assert f"Flavor {EMPTY}: no character folder" in lines
    at = lines.index(f"Flavor {FLAVOR}")
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
    assert flavors[EMPTY] == {"folder": EMPTY, "version": None, "characters": 0}
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
    assert [f["folder"] for f in data["flavors"]] == [FLAVOR]
    assert run("char", "list", "--account", "nope").exit_code == 2


# ─── the other char commands ─────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["show", "gear", "binds", "macros"])
def test_char_reads_use_the_only_flavor_with_characters_constructed(command: str) -> None:
    result = run("char", command, "--character", FIRST, "--json")
    assert result.exit_code in (0, 1), (result.stdout, result.stderr)
    assert f"using {FLAVOR}, the only flavor with characters" in result.stderr
    assert result.stderr.count("the only flavor with characters") == 1


def test_char_show_says_which_flavor_constructed() -> None:
    result = run("char", "show", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["flavor_folder"] == FLAVOR
    assert f"using {FLAVOR}, the only flavor with characters" in result.stderr


def test_char_show_named_flavor_is_not_announced_constructed() -> None:
    result = run("char", "show", "--json", "--flavor", FLAVOR)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "the only flavor with characters" not in result.stderr


def test_char_show_with_characters_in_both_still_asks_constructed(two_flavors: Path) -> None:
    shutil.rmtree(two_flavors / EMPTY)
    shutil.copytree(two_flavors / FLAVOR, two_flavors / EMPTY)
    result = run("char", "show")
    assert result.exit_code == 2
    assert "choose one with --flavor" in result.stderr


def test_char_show_named_empty_flavor_is_used_constructed() -> None:
    result = run("char", "show", "--flavor", EMPTY)
    assert result.exit_code != 0
    assert "the only flavor with characters" not in result.stderr


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
    assert "the only flavor with characters" not in result.stderr
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
    assert "the only flavor with characters" not in result.stderr


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
