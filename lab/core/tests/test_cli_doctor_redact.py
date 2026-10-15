"""`wowlab doctor --redact` (M13-09, docs/LAB_PLAN.md §15.2, ADR-0031).

The install is the committed capture (`fixtures/macos/`) copied into a fake
home folder inside `tmp_path`, `<tmp>/Users/Jaina Proudmoore/Games/World of
Warcraft` (a macOS-shaped home with a space in it), and the user data
directory sits under that home as it does on a real machine. `HOME`,
`USERPROFILE` and `Path.home()` all name the fake home. Nothing reads or
writes a real install; game data is replayed (ADR-0012).

Every test here is `constructed` (L8, hostile input): identity strings are
planted where `doctor` prints them, a second account folder with a realm
and a character folder, its `config-cache.wtf` whose sync CVar values carry
a BattleTag, GUIDs, an email address, guild names and home paths in macOS,
Windows and Linux forms, `realmName` and `accountName` in `Config.wtf`, a
journal label naming a guild, a damaged manifest named after a character,
and a client whose executable lives under the home folder. Each test first
shows that the plant reaches the unredacted output, so its absence from the
redacted output proves something.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from test_cli import (
    ACCOUNT,
    CAPTURE,
    FLAVOR,
    FakeProcess,
    _plain,
    _use_table,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    run,
)

from wowlab_core import cli, guard, install, redact, snapshot

PLANTED_ACCOUNT = "PROUDMOORE1"
PLANTED_REALM = "Area 52"
PLANTED_CHARACTER = "Thrallina"
HARVESTED_REALM = "Kel'Thuzad"  # only in Config.wtf's realmName
EMAIL = "jaina.proudmoore@example.com"  # Config.wtf's accountName, and a CVar value
BATTLETAG = "Proudfoot#24680"
PLAYER_GUID = "Player-1403-0A1B2C3D"
GUILD_GUID = "Guild-1403-00ABCDEF"
LABEL_GUILD = "Order of the Lantern"  # only in the journal label
NAMED_GUILD = "Knights of Dalaran"  # only in a CVar value; needs --redact-name
WINDOWS_HOME = r"C:\Users\Jaina Proudmoore\Documents\World of Warcraft"
LINUX_HOME = "/home/jaina/Games/wow"
# The fixture's own (pseudonymous) names: an account, a realm-name folder, and
# `<digits>/<First>-<Second>` character folders.
FIXTURE_NAMES = ("90000001#6", "Labrealmb Partb Partc Partd", "Labcharb", "Labrealmd")

REDACTED_ROOT = str(Path(redact.HOME) / "Games" / "World of Warcraft")

# What must never appear in redacted output, compared with case folded.
FORBIDDEN = (
    "Jaina",
    "Proudmoore",
    PLANTED_ACCOUNT,
    PLANTED_REALM,
    "Area52",
    "area-52",
    PLANTED_CHARACTER,
    "Thuzad",
    "example.com",
    "Proudfoot",
    "24680",
    PLAYER_GUID,
    GUILD_GUID,
    "0A1B2C3D",
    LABEL_GUILD,
    NAMED_GUILD,
    "/home/",
    "\\Users\\",
    "/Users/",
    *FIXTURE_NAMES,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "Users" / "Jaina Proudmoore"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(autouse=True)
def user_data(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user data directory under the fake home, as on a real machine."""
    data = home / "Library" / "Application Support" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    return data


@pytest.fixture
def root(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = home / "Games" / "World of Warcraft"
    root.mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def planted(root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every identity string, planted where `doctor` prints it."""
    wtf = root / FLAVOR / "WTF"
    with (wtf / "Config.wtf").open("ab") as f:
        f.write(f'SET realmName "{HARVESTED_REALM}"\nSET accountName "{EMAIL}"\n'.encode())
    account = wtf / "Account" / PLANTED_ACCOUNT
    (account / PLANTED_REALM / PLANTED_CHARACTER).mkdir(parents=True)
    (account / PLANTED_REALM / PLANTED_CHARACTER / "AddOns.txt").write_bytes(b"")
    names = (
        f"{PLANTED_CHARACTER}-Area52 of {PLANTED_REALM} (area-52), thrallina, {HARVESTED_REALM} "
        f"{BATTLETAG} {PLAYER_GUID} {GUILD_GUID} {EMAIL} {NAMED_GUILD} "
        "Labcharb-Labrealmd and Labrealmb Partb Partc Partd"
    )
    paths = f"{WINDOWS_HOME} {LINUX_HOME}"
    (account / "config-cache.wtf").write_bytes(
        f'SET synchronizeMacros "{names}"\n'
        f'SET synchronizeBindings "{paths}"\n'
        f'SET synchronizeConfig "{root}/WTF"\n'.encode()
    )

    record = guard.HistoryRecord(
        id="20261014T120000Z-0001",
        created_at="2026-10-14T12:00:00Z",
        label=f"profile apply {LABEL_GUILD} raid ({PLANTED_CHARACTER})",
        snapshot_id="20261014T115959Z-0001",
        flavor_path=str(root / FLAVOR),
        state="committed",
        rolled_back=False,
        paths=(),
        created_dirs=(),
        temps_removed=(),
        temps_left=(),
    )
    monkeypatch.setattr(guard, "history", lambda **k: (record,))

    manifests = snapshot.SnapshotStore().manifests_dir
    assert manifests.is_relative_to(user_data)
    manifests.mkdir(parents=True)
    (manifests / f"{PLANTED_CHARACTER}-Area52.json").write_bytes(b"not a manifest")

    exe = root / FLAVOR / "World of Warcraft Classic.app" / "Contents" / "MacOS" / "WoW"
    _use_table(monkeypatch, FakeProcess(4242, "World of Warcraft", str(exe)))
    return root


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (k, *_strings(v))]
    return []


def _leaks(text: str) -> list[str]:
    folded = text.casefold()
    return [word for word in FORBIDDEN if word.casefold() in folded]


def _text(*args: str) -> str:
    result = run("doctor", "--offline", *args)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return _plain(result.stdout) + _plain(result.stderr)


def _json(*args: str) -> tuple[str, cli.DoctorReport]:
    result = run("doctor", "--offline", "--json", *args)
    report = cli.DoctorReport.model_validate_json(result.stdout)
    return result.stdout, report


def test_constructed_plants_reach_the_unredacted_doctor_output(planted: Path, home: Path) -> None:
    text = _text()
    raw, _ = _json()
    values = "\n".join(_strings(json.loads(raw)))
    # Each plant is printed somewhere without --redact, so its absence below means something.
    for word in FORBIDDEN:
        assert word.casefold() in (text + values).casefold(), word
    assert str(home) in text
    assert str(home) in values
    # The routes, one by one: a path, the account field, a sync value, the
    # label, a damaged manifest, a client executable.
    assert f"WTF/Account/{PLANTED_ACCOUNT}/config-cache.wtf" in text
    assert f"damaged: {PLANTED_CHARACTER}-Area52.json" in text
    assert f'"profile apply {LABEL_GUILD} raid ({PLANTED_CHARACTER})"' in text
    assert BATTLETAG in text
    assert str(planted / FLAVOR / "World of Warcraft Classic.app") in values


def test_constructed_redacted_text_names_nobody(planted: Path, home: Path) -> None:
    text = _text("--redact", "--redact-name", NAMED_GUILD)
    assert _leaks(text) == []
    assert str(home) not in text
    for placeholder in (redact.HOME, redact.ACCOUNT, redact.CHARACTER, redact.REALM):
        assert placeholder in text, placeholder
    for placeholder in (redact.BATTLETAG, redact.GUID, redact.EMAIL, redact.NAME, redact.LABEL):
        assert placeholder in text, placeholder
    # What the report is for survives: the layout of the paths, the version, the state.
    assert f"WTF/Account/{redact.ACCOUNT}/config-cache.wtf" in text
    assert f"Install: {REDACTED_ROOT} (found by" in text
    assert "Flavor _classic_beta_: product wow_classic_beta" in text
    assert "Client: running" in text


def test_constructed_redacted_json_names_nobody_and_validates(planted: Path, home: Path) -> None:
    raw, report = _json("--redact", "--redact-name", NAMED_GUILD)
    values = "\n".join(_strings(json.loads(raw)))
    assert _leaks(raw) == []
    assert _leaks(values) == []
    assert str(home) not in values
    assert report.install_root == REDACTED_ROOT
    assert report.store.last_transaction is not None
    assert report.store.last_transaction.label == redact.LABEL
    accounts = {s.account for f in report.flavors for s in f.sync_cvars}
    assert accounts == {redact.ACCOUNT, None}
    assert report.client is not None
    assert [str(p.exe).startswith(redact.HOME) for p in report.client.processes] == [True]


def test_constructed_a_guild_name_outside_a_label_needs_redact_name(planted: Path) -> None:
    # Nothing in an install names a guild as such; the label goes whole, and
    # any other place needs the player to name it.
    text = _text("--redact")
    assert _leaks(text) == [NAMED_GUILD]
    assert LABEL_GUILD.casefold() not in text.casefold()


def test_constructed_windows_home_of_this_machine_is_redacted_in_either_separator(
    planted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Jaina Proudmoore")
    text = _text("--redact", "--redact-name", NAMED_GUILD)
    assert _leaks(text) == []


def test_constructed_redact_short_name_is_a_usage_error(planted: Path) -> None:
    result = run("doctor", "--offline", "--redact-name", "x")
    assert result.exit_code == cli.EXIT_USAGE
    assert "two or more characters" in result.stderr


def test_constructed_an_error_that_stops_the_report_is_redacted(
    planted: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> Any:
        raise OSError(f"cannot read {home}/x for {PLANTED_CHARACTER} of {BATTLETAG}")

    monkeypatch.setattr(cli, "_store_report", broken)
    plain = run("doctor", "--offline")
    assert PLANTED_CHARACTER in plain.stderr  # without --redact, the message is as raised
    result = run("doctor", "--offline", "--redact")
    assert result.exit_code == cli.EXIT_ERROR
    assert _leaks(result.stdout + result.stderr) == []
    assert f"cannot read {redact.HOME}/x for {redact.CHARACTER}" in result.stderr


def test_constructed_library_log_lines_are_redacted(
    planted: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = cli._store_report

    def logging_store_report() -> Any:
        logging.getLogger("wowlab_core.snapshot").warning(
            "skipped %s under %s", PLANTED_CHARACTER, home
        )
        return real()

    monkeypatch.setattr(cli, "_store_report", logging_store_report)
    assert PLANTED_CHARACTER in run("doctor", "--offline").stderr
    result = run("doctor", "--offline", "--redact")
    assert _leaks(result.stderr) == []
    assert f"skipped {redact.CHARACTER} under {redact.HOME}" in result.stderr


def test_constructed_no_install_redacts_the_searched_locations(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = home / "Applications" / "World of Warcraft"
    monkeypatch.setenv(install.ENV_ROOT, str(missing))
    plain = run("doctor", "--offline")
    assert "Proudmoore" in plain.stdout + plain.stderr
    result = run("doctor", "--offline", "--redact")
    assert result.exit_code == cli.EXIT_ERROR
    assert _leaks(result.stdout + result.stderr) == []


def test_doctor_redact_keeps_an_account_folder_that_is_not_planted_out(root: Path) -> None:
    # The fixture install alone: its account folder is redacted too.
    text = _text("--redact")
    assert ACCOUNT not in text
    assert f"WTF/Account/{redact.ACCOUNT}/config-cache.wtf" in text
