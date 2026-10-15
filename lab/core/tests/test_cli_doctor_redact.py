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
a client whose executable lives under the home folder, and the login and
host names the process would see. Each test first
shows that the plant reaches the unredacted output, so its absence from the
redacted output proves something.
"""

from __future__ import annotations

import getpass
import json
import logging
import shutil
import socket
import types
import typing
import warnings
from enum import Enum
from pathlib import Path, PurePath
from typing import Any

import platformdirs
import pytest
from pydantic import BaseModel
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

from wowlab_core import cli, doctorredact, firstrun, guard, install, redact, snapshot

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
LOGIN = "jprdmr"
HOST = "stormwind-pc.example.lan"

FORBIDDEN = (
    LOGIN,
    "stormwind",
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
    # The login and host names this machine would give, planted too.
    for variable in ("USER", "USERNAME", "LOGNAME"):
        monkeypatch.setenv(variable, LOGIN)
    monkeypatch.setattr(getpass, "getuser", lambda: LOGIN)
    monkeypatch.setattr(socket, "gethostname", lambda: HOST)
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
        f"Labcharb-Labrealmd and Labrealmb Partb Partc Partd, {LOGIN} on stormwind-pc"
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


def test_constructed_windows_oserror_in_the_report_is_redacted(
    planted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # str(OSError) writes its file name with repr(): every backslash doubled.
    error = PermissionError(13, "Access is denied", WINDOWS_HOME + r"\WTF\Config.wtf")
    assert "\\\\Users\\\\" in str(error)

    def broken(_lay: object) -> list[object]:
        raise error

    monkeypatch.setattr(cli, "_sync_cvars", broken)
    assert "Proudmoore" in _text("--json")
    for args in (("--redact",), ("--redact", "--json")):
        assert _leaks(_text(*args)) == [], args


def test_constructed_log_tracebacks_and_warnings_are_redacted(
    planted: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = cli._store_report

    def noisy_store_report() -> Any:
        try:
            raise OSError(f"cannot open {home}/{PLANTED_CHARACTER}")
        except OSError:
            logging.getLogger("wowlab_core.snapshot").warning(
                "store trouble", exc_info=True, stack_info=True
            )
        warnings.warn(f"{PLANTED_CHARACTER} under {home}", UserWarning, stacklevel=1)
        return real()

    monkeypatch.setattr(cli, "_store_report", noisy_store_report)
    with warnings.catch_warnings(record=True) as shown:
        warnings.simplefilter("always")
        plain = run("doctor", "--offline")
    assert PLANTED_CHARACTER in plain.stderr  # the traceback reaches stderr unredacted
    assert any(PLANTED_CHARACTER in str(w.message) for w in shown)
    with warnings.catch_warnings(record=True) as shown:
        warnings.simplefilter("always")
        result = run("doctor", "--offline", "--redact")
    assert _leaks(result.stderr) == []
    assert "store trouble" in result.stderr
    assert "Traceback" in result.stderr
    assert [str(w.message) for w in shown if PLANTED_CHARACTER in str(w.message)] == []


# ─── the first run is skipped under --redact (conductor decision, M13-09) ────

FIRST_RUN_TEXT = ("First run:", "first snapshot", "wowlab's own store", "`wowlab undo`")


def _no_first_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal that could be asked, and a first run that fails loudly if
    any part of it is reached."""

    def reached(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the first run was reached under --redact")

    monkeypatch.setattr(cli, "_interactive", lambda: True)
    for name in ("is_first_run", "intro_lines", "scan_flavor", "run"):
        monkeypatch.setattr(firstrun, name, reached)
    monkeypatch.setattr(cli.typer, "confirm", reached)
    monkeypatch.setattr(cli.typer, "prompt", reached)


def test_a_fresh_store_is_a_first_run_without_redact(root: Path) -> None:
    # The fixture install with an empty store: without --redact, `doctor`
    # runs the first run, so its absence below proves something.
    assert "First run:" in _text()
    _, report = _json()
    assert report.first_run is not None


def test_redact_on_a_fresh_store_skips_the_first_run(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_first_run(monkeypatch)
    text = _text("--redact")
    for phrase in FIRST_RUN_TEXT:
        assert phrase not in text, phrase
    assert f"WTF/Account/{redact.ACCOUNT}/config-cache.wtf" in text  # the report itself
    raw, report = _json("--redact")
    assert report.first_run is None
    assert json.loads(raw)["first_run"] is None
    assert not snapshot.SnapshotStore().path.exists(), "no snapshot, not even the store"


def test_redact_name_alone_skips_the_first_run(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_first_run(monkeypatch)
    _, report = _json("--redact-name", NAMED_GUILD)
    assert report.first_run is None
    assert not snapshot.SnapshotStore().path.exists()


@pytest.mark.parametrize(
    "extra", [("--redact", "--yes"), ("--redact", "-y"), ("--redact", "--flavor", FLAVOR),
              ("--redact-name", NAMED_GUILD, "--yes")]
)  # fmt: skip
def test_redact_with_yes_or_flavor_is_a_usage_error(
    root: Path, monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...]
) -> None:
    _no_first_run(monkeypatch)
    result = run("doctor", "--offline", *extra)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr)
    assert "--redact skips the first run" in _plain(result.stderr)
    assert result.stdout == ""
    assert not snapshot.SnapshotStore().path.exists()


def test_constructed_a_first_run_report_is_dropped_by_the_field_table(root: Path) -> None:
    # Fail closed: even a report that carries a first run loses it whole.
    _, report = _json("--yes")
    assert report.first_run is not None and report.first_run.snapshot is not None
    out = doctorredact.redact_model(report, redact.Redactor(), doctorredact.DOCTOR_FIELDS)
    assert out.first_run is None


def _string_fields(model: type[BaseModel], prefix: str = "") -> dict[str, bool]:
    """Every field of `model`, recursively, that can hold text: its dotted
    path, and whether it can hold only values the code names (Literal, enum)."""
    out: dict[str, bool] = {}
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        if doctorredact.DOCTOR_FIELDS.get(path) == doctorredact.DROP:
            # Dropped whole (None in the redacted report): what is inside
            # it needs no treatment, but the field must accept None.
            assert type(None) in typing.get_args(info.annotation), f"{path}: cannot be None"
            out[path] = False
            continue
        _annotation(info.annotation, path, out)
    return out


def _annotation(kind: Any, path: str, out: dict[str, bool]) -> None:
    origin = typing.get_origin(kind)
    if isinstance(kind, type) and issubclass(kind, BaseModel):
        out.update(_string_fields(kind, path + "."))
    elif origin is typing.Literal:
        out[path] = True  # only the values the annotation names
    elif origin in (list, tuple, set, frozenset, typing.Union, types.UnionType, dict):
        for arg in typing.get_args(kind):
            if arg is not Ellipsis:
                _annotation(arg, path, out)
    elif isinstance(kind, type) and issubclass(kind, Enum):
        out[path] = True
    elif isinstance(kind, type) and issubclass(kind, str | PurePath):
        out[path] = False
    elif kind in (int, float, bool, type(None)):
        pass
    else:
        raise AssertionError(f"{path}: a field type this walk does not know: {kind!r}")


def test_every_text_field_of_the_doctor_report_has_a_declared_treatment() -> None:
    """Fail closed: a field added to DoctorReport (M13-04's first run, ...)
    must be declared printed as-is, redacted, or replaced whole."""
    fields = _string_fields(cli.DoctorReport)
    assert set(fields) == set(doctorredact.DOCTOR_FIELDS), set(fields) ^ set(
        doctorredact.DOCTOR_FIELDS
    )
    for path, closed in fields.items():
        treatment = doctorredact.DOCTOR_FIELDS[path]
        assert treatment in (
            doctorredact.AS_IS,
            doctorredact.REDACT,
            doctorredact.DROP,
            *redact.PLACEHOLDERS,
        ), path
        if treatment == doctorredact.AS_IS:
            assert closed, f"{path}: only a Literal or enum field may be printed as it is"


class _Free(BaseModel):
    counts: dict[str, int]
    note: str
    listed: str


def test_constructed_dict_keys_are_redacted_and_an_unlisted_field_is_replaced_whole() -> None:
    replace = redact.Redactor(characters=[PLANTED_CHARACTER])
    out = doctorredact.redact_model(
        _Free(counts={PLANTED_CHARACTER: 1}, note=PLANTED_CHARACTER, listed=PLANTED_CHARACTER),
        replace,
        {"counts": doctorredact.REDACT, "listed": doctorredact.REDACT},
    )
    assert out.counts == {redact.CHARACTER: 1}
    assert out.note == redact.TEXT
    assert out.listed == redact.CHARACTER
