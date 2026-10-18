"""Which folders under `WTF/Account/` are accounts (M13-50).

Found on the owner's install: `WTF/Account/` held the account folder and a
stray `SavedVariables/` folder beside it, and every account-scoped command
without `--account` refused with "2 accounts (<account>, SavedVariables)".
An account folder is one whose name has the `<digits>#<n>` shape, or that
holds a character folder (a realm or digits folder with a folder inside).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
with its flavor folder named as its provenance row says (`_classic_beta_`),
as `test_cli_char.py` does. Every test here is `constructed` (L8): the
stray folder, its files and a second account are added by the test to that
copy. Nothing reads or writes a real install: `WOWLAB_WOW_ROOT` points at the
copy, and the user data and config directories are redirected into
`tmp_path`; no command here reaches the network (`_open_gamedata` raises).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, layout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
STRAY = "SavedVariables"
STRAY_DIR = f"WTF/Account/{STRAY}"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> Iterator[Any]:
        raise AssertionError("no test here reaches the game-data client")

    monkeypatch.setattr(cli, "_open_gamedata", refuse)


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


# Constructed: the files a stray folder beside the account holds here.
STRAY_FILES = {
    "Stray.lua": b"StrayDB = {}\n",
    "SavedVariables.lua": b"x = 1\n",
    "config-cache.wtf": b'SET foo "1"\n',
    "bindings-cache.wtf": b"BINDINGMODE 0\n",
    "SavedVariables/Nested.lua": b"NestedDB = {}\n",
}


@pytest.fixture
def stray(flavor: Path) -> Path:
    """Constructed: a stray `WTF/Account/SavedVariables/` beside the account."""
    folder = flavor / STRAY_DIR
    for name, data in STRAY_FILES.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return folder


def _layout(root: Path) -> layout.Layout:
    inst = install.discover(root)
    return layout.Layout.for_flavor(inst.flavors[0], inst.root)


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


# ─── the rule ────────────────────────────────────────────────────────────────


def test_constructed_stray_folder_is_not_an_account(root: Path, stray: Path) -> None:
    lay = _layout(root)
    assert [a.folder for a in lay.accounts()] == [ACCOUNT]
    inv = lay.inventory()
    assert [a.folder for a in inv.accounts] == [ACCOUNT]
    assert inv.not_accounts == (STRAY_DIR,)
    assert lay.wtf_walk().not_accounts == (STRAY_DIR,)
    assert lay.is_account_folder(ACCOUNT)
    assert not lay.is_account_folder(STRAY)
    assert not lay.is_account_folder("NoSuchFolder")


def test_constructed_files_under_a_stray_folder_are_other_wtf_files(
    root: Path, stray: Path
) -> None:
    lay = _layout(root)
    assert not [sv for sv in lay.saved_variables() if sv.path.startswith(STRAY_DIR + "/")]
    under = {f.path: f for f in lay.wtf_files() if f.path.startswith(STRAY_DIR + "/")}
    assert set(under) == {f"{STRAY_DIR}/{name}" for name in STRAY_FILES}
    assert {(f.scope, f.account, f.realm_folder, f.character_folder) for f in under.values()} == {
        ("other", None, None, None)
    }


@pytest.mark.parametrize(
    "folders",
    [
        ("12345678#1",),  # the name shape alone, no character folder yet
        ("LEGACY", "Realm", "Character"),  # an old-style name holding a character
        ("LEGACY", "1", "First-Second"),  # a digits folder holding a character
    ],
    ids=["constructed-name-shape", "constructed-realm-character", "constructed-digits-character"],
)
def test_constructed_account_folders_by_name_or_character(
    root: Path, flavor: Path, folders: tuple[str, ...]
) -> None:
    (flavor / "WTF/Account").joinpath(*folders).mkdir(parents=True)
    lay = _layout(root)
    assert {a.folder for a in lay.accounts()} == {ACCOUNT, folders[0]}
    assert lay.inventory().not_accounts == ()


@pytest.mark.parametrize(
    "folders",
    [
        ("LEGACY",),  # empty
        ("LEGACY", "Realm"),  # a realm folder with nothing in it
        ("LEGACY", "Realm", "SavedVariables"),  # only a SavedVariables folder below
        ("LEGACY", "SavedVariables", "Inner"),  # not a realm folder
        ("12345678#", "Realm"),  # not the name shape
        ("#1",),
    ],
    ids=[
        "constructed-empty",
        "constructed-empty-realm",
        "constructed-saved-variables-below",
        "constructed-saved-variables-folder",
        "constructed-no-n",
        "constructed-no-digits",
    ],
)
def test_constructed_not_account_folders(
    root: Path, flavor: Path, folders: tuple[str, ...]
) -> None:
    (flavor / "WTF/Account").joinpath(*folders).mkdir(parents=True)
    lay = _layout(root)
    assert [a.folder for a in lay.accounts()] == [ACCOUNT]
    assert lay.inventory().not_accounts == (f"WTF/Account/{folders[0]}",)


# ─── commands without --account ──────────────────────────────────────────────

# Every command the owner's walkthrough of 2026-10-18 named, with the
# arguments it needs here.
ACCOUNT_COMMANDS = [
    ("char", "show"),
    ("binds", "list"),
    ("macros", "list"),
    ("macros", "check"),
    ("binds", "coach"),
    ("looks", "import-char", "mthirteenfifty"),
]
COMMAND_IDS = [f"constructed-{'-'.join(c[:2])}" for c in ACCOUNT_COMMANDS]


@pytest.fixture
def picked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The account folder each `_select_account` call returned."""
    found: list[str] = []
    real = cli._select_account

    def spy(lay: layout.Layout, name: str | None) -> layout.Account:
        acct = real(lay, name)
        found.append(acct.folder)
        return acct

    monkeypatch.setattr(cli, "_select_account", spy)
    return found


@pytest.mark.parametrize("command", ACCOUNT_COMMANDS, ids=COMMAND_IDS)
def test_constructed_one_account_and_a_stray_folder_needs_no_account_option(
    stray: Path, picked: list[str], command: tuple[str, ...]
) -> None:
    result = run(*command)
    output = result.stdout + result.stderr
    assert "--account" not in output, output
    assert picked and set(picked) == {ACCOUNT}, (picked, output)


@pytest.mark.parametrize("command", ACCOUNT_COMMANDS, ids=COMMAND_IDS)
def test_constructed_two_real_accounts_still_refuse_and_name_both(
    flavor: Path, stray: Path, command: tuple[str, ...]
) -> None:
    (flavor / "WTF/Account/12345678#1").mkdir()
    result = run(*command)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr)
    assert f"2 accounts (12345678#1, {ACCOUNT}); choose one with --account" in result.stderr
    assert STRAY not in result.stderr.split("choose one")[0]


def test_constructed_char_list_account_stray_is_refused_with_the_real_accounts(
    stray: Path,
) -> None:
    result = run("char", "list", "--account", STRAY)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr)
    assert f"no account folder {STRAY!r} (accounts wowlab could look inside: {ACCOUNT})" in (
        result.stderr
    )
    assert f"{STRAY_DIR} is not an account folder" in result.stderr


def test_constructed_char_list_without_account_is_unchanged(flavor: Path) -> None:
    before = ok("char", "list").stdout
    for name, data in STRAY_FILES.items():
        path = flavor / STRAY_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    assert ok("char", "list").stdout == before


def test_constructed_profile_preset_skips_the_stray_folder(stray: Path) -> None:
    out = ok("profile", "save", "keys", "--preset", "bindings", "--json").stdout
    report = cli.ProfileReport.model_validate_json(out)
    paths = {e.path for e in report.entries}
    assert not [p for p in paths if p.startswith(f"{FLAVOR}/{STRAY_DIR}/")], paths
    assert not [p for p in report.subtrees if p.startswith(f"{FLAVOR}/{STRAY_DIR}")]


# ─── what the read commands say about the stray folder ───────────────────────


def test_constructed_sv_list_leaves_out_the_stray_folder(stray: Path) -> None:
    report = cli.SvListReport.model_validate_json(ok("sv", "list", "--json").stdout)
    assert report.files
    assert not [f for f in report.files if f.path.startswith(STRAY_DIR + "/")]
    assert STRAY_DIR not in ok("sv", "list").stdout


def test_constructed_tree_names_the_stray_folder(stray: Path) -> None:
    report = cli.TreeReport.model_validate_json(ok("tree", "--json").stdout)
    items = {i.path: i for i in report.items}
    assert items[STRAY_DIR].note == cli._NOT_AN_ACCOUNT
    assert items[f"WTF/Account/{ACCOUNT}"].note == "account folder"
    # The real account's realm and character rows stay (review of the first
    # cut: a loop nested under the stray rows dropped them).
    assert items[f"WTF/Account/{ACCOUNT}/1"].note == "numeric folder (not a realm name)"
    assert items[f"WTF/Account/{ACCOUNT}/1/Labchard-Labrealmg"].note == "character folder"
    assert items[f"WTF/Account/{ACCOUNT}/Labrealmb Partb Partc Partd"].note == "realm folder"
    for name in STRAY_FILES:
        assert items[f"{STRAY_DIR}/{name}"].note is None
    assert STRAY_DIR in ok("tree").stdout


def test_constructed_explain_names_no_account_view_for_a_stray_file(stray: Path) -> None:
    report = cli.ExplainReport.model_validate_json(
        ok("explain", f"{STRAY_DIR}/config-cache.wtf", "--json").stdout
    )
    assert report.views == []
    assert report.views_note is not None
    assert f"{STRAY_DIR} is {cli._NOT_AN_ACCOUNT}" in report.views_note
    text = ok("explain", f"{STRAY_DIR}/config-cache.wtf").stdout
    assert f"views:        {STRAY_DIR} is not an account folder" in text
    assert "--account" not in text
    real = cli.ExplainReport.model_validate_json(
        ok("explain", f"WTF/Account/{ACCOUNT}/config-cache.wtf", "--json").stdout
    )
    assert [v.params.get("account") for v in real.views] == [ACCOUNT]


def test_ui_help_says_the_whole_install() -> None:
    text = " ".join(ok("ui", "--help").stdout.split())
    assert "the whole install's files" in text
    assert "a flavor folder's files" not in text
