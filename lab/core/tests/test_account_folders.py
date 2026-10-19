"""Which folders under `WTF/Account/` are accounts (M13-50).

Found on the owner's install (2026-10-17): `WTF/Account/` held the account
folder and, beside it, a `SavedVariables/` folder, and every account-scoped
command without `--account` refused with "2 accounts (<account>,
SavedVariables)". That folder is the client's own: a read-only listing of
the owner's Forever beta install (2026-10-18, not captured) shows the client
writing Blizzard SavedVariables there (`Blizzard_GamepadSmartNavigation.lua`,
`Blizzard_PTRFeedbackGlue.lua`, `Blizzard_SavedSets.lua`,
`Blizzard_SettingsDefinitions_Shared.lua`, each with a `.lua.bak`) that
belong to no account; when it writes them and whether it reads them back
are **[verify]**. An account folder is one whose name has the `<digits>#<n>`
shape, or that holds a character folder (a realm or digits folder with a
folder inside).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
with its flavor folder named as its provenance row says (`_classic_beta_`),
as `test_cli_char.py` does. Every test here is `constructed` (L8): the
client's folder (in the shape the owner's listing shows, with contents in
the client's SavedVariables layout), the edge-case files beside it, an
old-style folder and a second account are added by the test to that copy.
Nothing reads or writes a real install: `WOWLAB_WOW_ROOT` points at the copy,
and the user data and config directories are redirected into `tmp_path`; no
command here reaches the network (`_open_gamedata` raises).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, doctorredact, install, layout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
CLIENT = "SavedVariables"
CLIENT_DIR = f"WTF/Account/{CLIENT}"
OLD_STYLE = "LEGACYACCT"  # an old-style account name with no character folder yet
OLD_DIR = f"WTF/Account/{OLD_STYLE}"

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


# Constructed, in the shape the owner's listing shows: Blizzard SavedVariables
# directly inside, each with a `.lua.bak`, in the client's layout.
OBSERVED = {
    f"{name}{suffix}": f"{var} = {{}}\n".encode()
    for name, var in (
        ("Blizzard_GamepadSmartNavigation.lua", "GamepadSmartNavigationSaved"),
        ("Blizzard_PTRFeedbackGlue.lua", "PTRFeedbackGlueSaved"),
        ("Blizzard_SavedSets.lua", "SavedSetsSaved"),
        ("Blizzard_SettingsDefinitions_Shared.lua", "NewSettingsSeen"),
    )
    for suffix in ("", ".bak")
}
# Constructed edge cases, not seen there: files whose names have account rows.
EDGE = {
    "SavedVariables.lua": b"x = 1\n",
    "config-cache.wtf": b'SET foo "1"\n',
    "bindings-cache.wtf": b"BINDINGMODE 0\n",
    "SavedVariables/Nested.lua": b"NestedDB = {}\n",
}
CLIENT_FILES = {**OBSERVED, **EDGE}


def _write(folder: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


@pytest.fixture
def client(flavor: Path) -> Path:
    """Constructed: the client's `WTF/Account/SavedVariables/` beside the account."""
    _write(flavor / CLIENT_DIR, CLIENT_FILES)
    return flavor / CLIENT_DIR


@pytest.fixture
def old_style(flavor: Path) -> Path:
    """Constructed: an old-style account folder with no character folder yet."""
    _write(flavor / OLD_DIR, {"config-cache.wtf": b'SET foo "1"\n'})
    return flavor / OLD_DIR


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


def test_constructed_client_folder_is_not_an_account(root: Path, client: Path) -> None:
    lay = _layout(root)
    assert [a.folder for a in lay.accounts()] == [ACCOUNT]
    inv = lay.inventory()
    assert [a.folder for a in inv.accounts] == [ACCOUNT]
    assert inv.not_accounts == (CLIENT_DIR,)
    assert lay.wtf_walk().not_accounts == (CLIENT_DIR,)
    assert lay.is_account_folder(ACCOUNT)
    assert not lay.is_account_folder(CLIENT)
    assert not lay.is_account_folder("NoSuchFolder")


def test_constructed_client_files_belong_to_no_account(root: Path, client: Path) -> None:
    lay = _layout(root)
    assert not [sv for sv in lay.saved_variables() if sv.path.startswith(CLIENT_DIR + "/")]
    under = {f.path: f for f in lay.wtf_files() if f.path.startswith(CLIENT_DIR + "/")}
    assert set(under) == {f"{CLIENT_DIR}/{name}" for name in CLIENT_FILES}
    assert {(f.scope, f.account, f.realm_folder, f.character_folder) for f in under.values()} == {
        ("other", None, None, None)
    }
    ids = {name: under[f"{CLIENT_DIR}/{name}"].entry_id for name in CLIENT_FILES}
    for name in OBSERVED:
        backup = name.endswith(".bak")
        assert ids[name] == (
            "client-savedvariables-backup" if backup else "client-savedvariables"
        ), name
    # Edge cases: no account row applies below a folder that is no account.
    assert ids["SavedVariables.lua"] == "client-savedvariables"
    assert ids["config-cache.wtf"] is None
    assert ids["bindings-cache.wtf"] is None
    assert ids["SavedVariables/Nested.lua"] is None


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
def test_constructed_one_account_and_the_client_folder_need_no_account_option(
    client: Path, old_style: Path, picked: list[str], command: tuple[str, ...]
) -> None:
    result = run(*command)
    output = result.stdout + result.stderr
    assert "--account" not in output, output
    assert picked and set(picked) == {ACCOUNT}, (picked, output)


@pytest.mark.parametrize("command", ACCOUNT_COMMANDS, ids=COMMAND_IDS)
def test_constructed_two_real_accounts_still_refuse_and_name_both(
    flavor: Path, client: Path, command: tuple[str, ...]
) -> None:
    (flavor / "WTF/Account/12345678#1").mkdir()
    result = run(*command)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr)
    assert f"2 accounts (12345678#1, {ACCOUNT}); choose one with --account" in result.stderr
    assert CLIENT not in result.stderr.split("choose one")[0]


def test_constructed_char_list_account_client_folder_is_refused_with_the_real_accounts(
    client: Path,
) -> None:
    result = run("char", "list", "--account", CLIENT)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr)
    assert f"no account folder {CLIENT!r} (accounts wowlab could look inside: {ACCOUNT})" in (
        result.stderr
    )
    assert f"{CLIENT_DIR} is {layout.CLIENT_SAVED_VARIABLES}" in " ".join(result.stderr.split())


def test_constructed_char_list_account_old_style_folder_says_why(old_style: Path) -> None:
    result = run("char", "list", "--account", OLD_STYLE)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr)
    assert f"{OLD_DIR} is {layout.NOT_TAKEN_AS_ACCOUNT}" in " ".join(result.stderr.split())


def test_constructed_char_list_without_account_is_unchanged(flavor: Path) -> None:
    before = ok("char", "list").stdout
    _write(flavor / CLIENT_DIR, CLIENT_FILES)
    assert ok("char", "list").stdout == before


def test_constructed_profile_preset_skips_the_client_folder(client: Path) -> None:
    out = ok("profile", "save", "keys", "--preset", "bindings", "--json").stdout
    report = cli.ProfileReport.model_validate_json(out)
    paths = {e.path for e in report.entries}
    assert f"{FLAVOR}/WTF/Account/{ACCOUNT}/bindings-cache.wtf" in paths
    assert not [p for p in paths if p.startswith(f"{FLAVOR}/{CLIENT_DIR}/")], paths
    assert not [p for p in report.subtrees if p.startswith(f"{FLAVOR}/{CLIENT_DIR}")]


# ─── what the read commands say about the client's folder ────────────────────

SV_FOOTER = (
    f"{CLIENT_DIR}/ holds {len(CLIENT_FILES)} files that belong to no account; "
    f"`wowlab tree {CLIENT_DIR}` lists them."
)


def test_constructed_sv_list_says_what_it_leaves_out(client: Path) -> None:
    report = cli.SvListReport.model_validate_json(ok("sv", "list", "--json").stdout)
    assert report.files
    assert not [f for f in report.files if f.path.startswith(CLIENT_DIR + "/")]
    assert report.notes == [cli._SV_TIMING, SV_FOOTER]
    text = ok("sv", "list").stdout
    assert text.splitlines()[-1] == SV_FOOTER
    assert f"{CLIENT_DIR}/Blizzard" not in text
    # With --account the list is one account's, and the line is left out.
    narrowed = cli.SvListReport.model_validate_json(
        ok("sv", "list", "--account", ACCOUNT, "--json").stdout
    )
    assert narrowed.notes == [cli._SV_TIMING]


def test_constructed_sv_list_without_the_folder_has_no_footer(root: Path) -> None:
    report = cli.SvListReport.model_validate_json(ok("sv", "list", "--json").stdout)
    assert report.notes == [cli._SV_TIMING]


def test_constructed_tree_names_the_client_folder(client: Path, old_style: Path) -> None:
    report = cli.TreeReport.model_validate_json(ok("tree", "--explain", "--json").stdout)
    items = {i.path: i for i in report.items}
    real = items[f"WTF/Account/{ACCOUNT}"]
    assert real.note == "account folder"
    assert real.explained is not None and real.explained.entry_id == "account-folder"
    # The real account's realm and character rows stay (review of the first
    # cut: a loop nested under the not-account rows dropped them).
    assert items[f"WTF/Account/{ACCOUNT}/1"].note == "numeric folder (not a realm name)"
    assert items[f"WTF/Account/{ACCOUNT}/1/Labchard-Labrealmg"].note == "character folder"
    assert items[f"WTF/Account/{ACCOUNT}/Labrealmb Partb Partc Partd"].note == "realm folder"

    folder = items[CLIENT_DIR]
    assert folder.note == layout.CLIENT_SAVED_VARIABLES
    assert folder.explained is not None
    assert folder.explained.entry_id == "client-savedvariables-folder"
    explained = {
        name: (e.entry_id if (e := items[f"{CLIENT_DIR}/{name}"].explained) else None)
        for name in CLIENT_FILES
    }
    assert explained["Blizzard_SavedSets.lua"] == "client-savedvariables"
    assert explained["Blizzard_SavedSets.lua.bak"] == "client-savedvariables-backup"
    assert explained["config-cache.wtf"] is None
    assert explained["SavedVariables/Nested.lua"] is None

    old = items[OLD_DIR]
    assert old.note == layout.NOT_TAKEN_AS_ACCOUNT
    assert old.explained is None
    assert items[f"{OLD_DIR}/config-cache.wtf"].explained is None
    text = ok("tree").stdout
    assert CLIENT_DIR in text and OLD_DIR in text


def test_constructed_explain_the_client_folder_and_its_files(client: Path) -> None:
    folder = cli.ExplainReport.model_validate_json(ok("explain", f"{CLIENT_DIR}/", "--json").stdout)
    assert isinstance(folder.result, layout.Classified)
    assert folder.result.entry.id == "client-savedvariables-folder"
    assert "Not an account folder" in ok("explain", f"{CLIENT_DIR}/").stdout

    blizzard = cli.ExplainReport.model_validate_json(
        ok("explain", f"{CLIENT_DIR}/Blizzard_SavedSets.lua", "--json").stdout
    )
    assert isinstance(blizzard.result, layout.Classified)
    assert blizzard.result.entry.id == "client-savedvariables"
    assert [v.command for v in blizzard.views] == [["sv", "dump"]]

    # An edge case: a name with an account row, in a folder that is no account.
    cache = cli.ExplainReport.model_validate_json(
        ok("explain", f"{CLIENT_DIR}/config-cache.wtf", "--json").stdout
    )
    assert isinstance(cache.result, layout.Unclassified)
    assert cache.result.reason == f"{CLIENT_DIR} is {layout.CLIENT_SAVED_VARIABLES}"
    assert cache.verdict.level == "unclassified"
    assert cache.views == []
    text = ok("explain", f"{CLIENT_DIR}/config-cache.wtf").stdout
    assert f"{CLIENT_DIR} is {layout.CLIENT_SAVED_VARIABLES}" in " ".join(text.split())
    assert "--account" not in text
    assert "Account-scoped" not in text

    real = cli.ExplainReport.model_validate_json(
        ok("explain", f"WTF/Account/{ACCOUNT}/config-cache.wtf", "--json").stdout
    )
    assert isinstance(real.result, layout.Classified)
    assert real.result.entry.id == "account-config-cache"
    assert [v.params.get("account") for v in real.views] == [ACCOUNT]


def test_constructed_explain_an_old_style_folder(old_style: Path) -> None:
    report = cli.ExplainReport.model_validate_json(ok("explain", f"{OLD_DIR}/", "--json").stdout)
    assert isinstance(report.result, layout.Unclassified)
    assert report.result.reason == f"{OLD_DIR} is {layout.NOT_TAKEN_AS_ACCOUNT}"
    text = ok("explain", f"{OLD_DIR}/").stdout
    assert "One folder per game account" not in text
    # A folder that is not there is not judged: the account row stays.
    missing = cli.ExplainReport.model_validate_json(
        ok("explain", "WTF/Account/NoSuchAccount/", "--json").stdout
    )
    assert isinstance(missing.result, layout.Classified)
    assert missing.result.entry.id == "account-folder"


def test_constructed_redaction_keeps_old_style_names_and_leaves_the_client_folder(
    root: Path, client: Path, old_style: Path
) -> None:
    found = doctorredact.identities(root)
    assert OLD_STYLE in found.accounts
    assert ACCOUNT in found.accounts
    assert not [a for a in found.accounts if a.casefold() == CLIENT.casefold()]


def test_ui_help_says_the_whole_install() -> None:
    text = " ".join(ok("ui", "--help").stdout.split())
    assert "the whole install's files" in text
    assert "a flavor folder's files" not in text
