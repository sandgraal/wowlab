# Probe from review of m13/09-redaction; reproduces a Windows home folder surviving `doctor --redact` inside an OSError message.
"""On Windows, `str(OSError)` names its file with `repr()`, which doubles
every backslash: `[WinError 5] Access is denied: 'C:\\\\Users\\\\Jaina
Proudmoore\\\\...'` as printed. Neither the known-home pass (it looks for
`C:\\Users\\Jaina Proudmoore` and `C:/Users/Jaina Proudmoore`) nor the
home-shape pattern (one separator between `C:`, `Users` and the name) of
`wowlab_core.redact` matches that spelling, so the user name reaches the
output `bug.yml` asks a player to paste.

`doctor` puts `str(exc)` of an OSError in the report in several places:
`problems` (`<flavor>: cannot read the config files: {exc}`, --json), the
client probe's `error` (`Client: could not check (...)`, text and --json),
`game_data.detail`, and the `RedactedError` of an error that stops it.
Windows is the platform M13-02 makes first-class; ADR-0031 says absolute
home paths never leave the machine unredacted.

The OSError is built here with the exact arguments Windows would give it;
`str(OSError)` is the same function of them on every platform, so the probe
runs anywhere. Positive control: the same path spelled with single
backslashes (as `str(path)` gives it) is redacted.

Constructed (hostile input, L8): the committed capture copied into a fake
home under `tmp_path`. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from the graders are requested by name)
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    idle,  # noqa: F401  (autouse: no client in the process table)
    run,
)
from test_cli_doctor_redact import (
    _plain,
    home,  # noqa: F401
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import cli, process

WINDOWS_HOME = r"C:\Users\Jaina Proudmoore"
CONFIG = WINDOWS_HOME + r"\Games\World of Warcraft\_classic_\WTF\Config.wtf"
EXE = WINDOWS_HOME + r"\Games\World of Warcraft\_classic_\WowClassic.exe"


@pytest.fixture(autouse=True)
def windows_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """This machine's home, as Windows names it, is known to the redactor."""
    monkeypatch.setenv("USERPROFILE", WINDOWS_HOME)


def _doctor(*args: str) -> str:
    result = run("doctor", "--offline", *args)
    return _plain(result.stdout) + _plain(result.stderr)


def _deny(*_: Any, **__: Any) -> Any:
    raise PermissionError(13, "Access is denied", EXE)


def test_constructed_client_probe_oserror_names_the_windows_user(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process, "running_clients", _deny)
    assert "Proudmoore" in _doctor()  # the plant reaches the unredacted output
    assert "Proudmoore" not in _doctor("--redact")
    assert "Proudmoore" not in _doctor("--redact", "--json")


def test_constructed_config_oserror_names_the_windows_user_in_json(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_lay: object) -> list[object]:
        raise PermissionError(13, "Access is denied", CONFIG)

    monkeypatch.setattr(cli, "_sync_cvars", broken)
    assert "Proudmoore" in _doctor("--json")
    assert "Proudmoore" not in _doctor("--redact", "--json")


def test_positive_control_the_same_path_with_single_backslashes_is_redacted(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny_plain(*_: Any, **__: Any) -> Any:
        raise PermissionError(f"Access is denied: {EXE}")

    monkeypatch.setattr(process, "running_clients", deny_plain)
    assert "Proudmoore" in _doctor()
    out = _doctor("--redact")
    assert "Proudmoore" not in out
    assert "<HOME>" in out
