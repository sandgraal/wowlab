"""`--json` errors, and one error base (M16-22).

- wowlab's own errors derive from `WowlabError`, which carries the exit
  code, and `_handled` catches the base.
- Under `--json`, an error is also an `ErrorReport {kind, message,
  exit_code}` on stdout. The stderr line and the exit code are the same as
  without `--json`, and a run that already printed its JSON (a partial
  report) prints no second document.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`
with its flavor folder named as its provenance row says; the user data
directory is redirected into `tmp_path` and the process probe sees no
client. The SavedVariables file holding a function and the store inside
the install are constructed, and labelled as such. Nothing reads or writes
a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import (
    addoninstall,
    cli,
    gamedata,
    install,
    labaddon,
    lookstore,
    profiles,
    snapshot,
    wtfconfig,
)
from wowlab_core.errors import WowlabError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT_SV = "WTF/Account/90000001#6/SavedVariables"

runner = CliRunner()


class _NoClient:
    pid = 101

    def __init__(self, exe: str) -> None:
        self._exe = exe

    def name(self) -> str:
        return "python3"

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    user = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: user)
    process = _NoClient(str(tmp_path / "elsewhere" / "python3"))
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([process]))
    monkeypatch.chdir(tmp_path)
    return user


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    return root


@pytest.fixture
def no_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No `WOWLAB_WOW_ROOT`, and one default that holds no install."""
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    empty = tmp_path / "defaults" / "World of Warcraft"
    empty.mkdir(parents=True)
    monkeypatch.setattr(install, "default_roots", lambda **k: (empty,))
    return empty


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def _error_line(stderr: str) -> str:
    lines = [line for line in stderr.splitlines() if line.startswith("wowlab: ")]
    assert len(lines) == 1, stderr
    return lines[0]


def _both(*args: str) -> tuple[Any, Any]:
    """The same command as text and as `--json`, after the subcommand's
    words (`args[0]` is the command path, as a tuple)."""
    command, rest = args[0], args[1:]
    return run(*command, *rest), run(*command, "--json", *rest)


def _check(text: Any, as_json: Any, kind: str, code: int) -> cli.ErrorReport:
    """The `--json` run printed one `ErrorReport` and the text run printed
    nothing on stdout; both end with the same stderr line and exit code."""
    assert text.exit_code == as_json.exit_code == code
    assert text.stdout == ""
    assert _error_line(as_json.stderr) == _error_line(text.stderr)
    report = cli.ErrorReport.model_validate_json(as_json.stdout)
    assert report.kind == kind
    assert report.exit_code == code
    assert f"wowlab: {report.message}" == _error_line(as_json.stderr)
    return report


# ─── the base ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        cli.CliError,
        install.InstallError,
        install.InstallNotFoundError,
        snapshot.SnapshotError,
        snapshot.StoreFullError,
        profiles.ProfileError,
        addoninstall.AddonError,
        labaddon.LabAddonError,
        gamedata.GameDataError,
        lookstore.LookStoreError,
        wtfconfig.WtfLimitError,
    ],
)
def test_module_errors_derive_from_the_one_base(error: type[Exception]) -> None:
    assert issubclass(error, WowlabError)
    assert issubclass(error, cli._HANDLED_ERRORS)


def test_the_two_value_errors_stay_value_errors() -> None:
    # Code that catches them as ValueError still does.
    assert issubclass(labaddon.LabAddonError, ValueError)
    assert issubclass(wtfconfig.WtfLimitError, ValueError)


def test_the_base_carries_the_exit_code() -> None:
    assert WowlabError.exit_code == cli.EXIT_ERROR
    assert install.InstallNotFoundError.exit_code == cli.EXIT_ERROR
    usage = cli.CliError("x", cli.EXIT_USAGE)
    assert usage.exit_code == usage.code == cli.EXIT_USAGE
    assert cli._error_words(usage, json_out=True) == ("x", cli.EXIT_USAGE)


def test_a_new_module_error_is_handled_by_its_base_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An error class no list names (constructed) is reported as one line
    with its own exit code, and as an `ErrorReport` under `--json`."""

    class NewModuleError(WowlabError):
        exit_code = 4

    @cli._handled
    def command(json_out: bool = False) -> None:
        raise NewModuleError("a new module refused")

    with pytest.raises(typer.Exit) as exited:
        command(json_out=True)
    assert exited.value.exit_code == 4
    out, err = capsys.readouterr()
    assert err == "wowlab: a new module refused\n"
    report = cli.ErrorReport.model_validate_json(out)
    assert report == cli.ErrorReport(
        kind="NewModuleError", message="a new module refused", exit_code=4
    )


def test_an_error_after_the_report_prints_no_second_document_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that printed its JSON and then failed (constructed) keeps one
    JSON document on stdout: the error is on stderr only."""

    @cli._handled
    def command(json_out: bool = False) -> None:
        cli._emit(cli.ErrorReport(kind="Earlier", message="the run's own report", exit_code=0))
        raise cli.CliError("then it failed")

    with pytest.raises(typer.Exit) as exited:
        command(json_out=True)
    assert exited.value.exit_code == cli.EXIT_ERROR
    out, err = capsys.readouterr()
    assert cli.ErrorReport.model_validate_json(out).kind == "Earlier"
    assert err == "wowlab: then it failed\n"


def test_the_kind_skips_private_classes() -> None:
    class _Private(install.InstallNotFoundError):
        pass

    assert cli._error_kind(_Private([], [], [])) == "InstallNotFoundError"
    assert cli._error_kind(FileNotFoundError("x")) == "FileNotFoundError"


# ─── three error kinds, end to end ───────────────────────────────────────────


def test_no_install_is_an_error_report(no_install: Path) -> None:
    report = _check(*_both(("cvar", "list")), kind="InstallNotFoundError", code=cli.EXIT_ERROR)
    assert report.message.startswith("no WoW install found where the game installs by default")
    assert "WOWLAB_WOW_ROOT" in report.message


def test_a_file_the_parser_refuses_is_an_error_report_constructed(root: Path) -> None:
    bad = root / FLAVOR / ACCOUNT_SV / "Constructed.lua"
    bad.write_bytes(b"Constructed = function() end\n")  # constructed: L3 refuses it
    text, as_json = _both(("sv", "dump"), str(bad))
    assert text.exit_code == as_json.exit_code == cli.EXIT_ERROR
    assert text.stdout == ""
    report = cli.ErrorReport.model_validate_json(as_json.stdout)
    assert report.kind == "LuaDataError"
    assert report.exit_code == cli.EXIT_ERROR
    # `--json` keeps the parser's own words on stderr (M16-15), and the
    # report holds that same line.
    assert f"wowlab: {report.message}" == _error_line(as_json.stderr)
    assert report.message.startswith("not SavedVariables data the parser accepts: ")
    assert _error_line(text.stderr).startswith("wowlab: not SavedVariables data wowlab can read")


def test_a_write_gate_refusal_is_an_error_report_with_exit_3_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inside = root / FLAVOR / "WTF" / "store"  # constructed: a store inside the install
    monkeypatch.setattr(snapshot, "default_store_path", lambda: inside)
    report = _check(*_both(("snap", "create")), kind="GuardError", code=cli.EXIT_REFUSED)
    assert report.message.startswith("refused by the write gate: ")
    assert not inside.exists()


def test_a_missing_file_is_an_error_report(root: Path, tmp_path: Path) -> None:
    gone = tmp_path / "elsewhere" / "Nope.lua"
    report = _check(*_both(("sv", "dump"), str(gone)), kind="CliError", code=cli.EXIT_ERROR)
    assert report.message.startswith(f"no SavedVariables file at {gone}")


def test_a_partial_report_is_not_replaced(root: Path) -> None:
    """`explain` of a path that matches nothing exits 1 with its own report
    (M16-18): stdout holds that report alone, which the UI's explain pane
    draws."""
    result = run("explain", "--json", str(root / FLAVOR / "Nothing" / "here.txt"))
    assert result.exit_code == cli.EXIT_ERROR
    report = cli.ExplainReport.model_validate_json(result.stdout)
    assert report.result is not None


def test_a_follow_runs_error_report_is_one_line(no_install: Path) -> None:
    """`log tail --follow --json` prints one JSON value per line: its error
    report is one line too, and the run is otherwise as without `--follow`."""
    follow = run("log", "tail", "--follow", "--json")
    once = run("log", "tail", "--json")
    assert follow.exit_code == once.exit_code == cli.EXIT_ERROR
    assert follow.stdout.count("\n") == 1, follow.stdout
    assert cli.ErrorReport.model_validate_json(follow.stdout) == (
        cli.ErrorReport.model_validate_json(once.stdout)
    )
    assert _error_line(follow.stderr) == _error_line(once.stderr)
