"""Where a player's install and user data folder actually live (M13-02).

The `lab (windows)` and `lab (macos)` CI jobs run the whole Lab suite; this
module adds the install locations the rest of the suite does not stand in:

- a root whose path holds spaces and parentheses, as
  `C:\\Program Files (x86)\\World of Warcraft` does, named by
  `WOWLAB_WOW_ROOT`, with a user data folder that has a space in it too;
- a root deep enough that every file path in it is past Windows' classic
  260-character `MAX_PATH`;
- on Windows, a file the client holds open without read sharing while a
  snapshot is taken.

Each case runs the owner's loop through the CLI: `doctor`, `snap create`,
an edit, `snap restore --yes`, `undo --yes`.

The install is the real capture (`fixtures/macos/`, README row) copied under
`tmp_path`; only its location is constructed, so the test ids say so (L8).
The user data directory is redirected into `tmp_path`, the process table is
a fake one without a client in it, and no command here asks wago.tools
anything (`doctor --offline`; the game-data opener is replaced by one that
fails). Nothing reads or writes a real install.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, guard, install
from wowlab_core.snapshot import Manifest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SYNDICATOR = "WTF/Account/90000001#6/SavedVariables/Syndicator.lua"
OLD = b'"show_tooltips_on_shift"] = false'
NEW = b'"show_tooltips_on_shift"] = true'
MAX_PATH = 260  # Windows' classic path limit, in UTF-16 units, terminator included

runner = CliRunner()


class _Idle:
    """A process with no client in it (the `psutil.Process` surface read)."""

    pid = 4242

    def name(self) -> str:
        return "python3"

    def exe(self) -> str:
        return "/elsewhere/python3"

    def cmdline(self) -> list[str]:
        return [self.exe()]

    def status(self) -> str:
        return "running"


@pytest.fixture(autouse=True)
def _offline_and_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([_Idle()]))

    def no_network() -> Iterator[Any]:
        raise AssertionError("no command in this module may ask wago.tools anything")

    monkeypatch.setattr(cli, "_open_gamedata", no_network)


def _user_data(monkeypatch: pytest.MonkeyPatch, where: Path) -> Path:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: where)
    return where


def _install_at(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real capture as an install at `root`, named by `WOWLAB_WOW_ROOT`."""
    root.mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    return root


def ok(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _round_trip(root: Path) -> None:
    """doctor, snapshot, edit, restore, undo: every byte back where it was."""
    flavor = root / FLAVOR
    doctor = ok("doctor", "--offline").stdout
    assert str(root) in doctor, "doctor names the install the variable points at"
    assert f"Flavor {FLAVOR}" in doctor

    manifest = Manifest.model_validate_json(ok("snap", "create", "-m", "base", "--json").stdout)
    assert manifest.entry(f"{FLAVOR}/{SYNDICATOR}") is not None

    target = flavor / SYNDICATOR
    original = target.read_bytes()
    assert OLD in original
    edited = original.replace(OLD, NEW)
    target.write_bytes(edited)

    assert "Restored 1 file(s)" in ok("snap", "restore", manifest.id, "--yes").stdout
    assert target.read_bytes() == original
    (record,) = guard.history()
    assert record.state == "committed"
    assert [p.path for p in record.paths] == [SYNDICATOR]

    ok("undo", "--yes")
    assert target.read_bytes() == edited
    assert len(guard.history()) == 2


def test_constructed_location_root_with_spaces_and_parentheses_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`WOWLAB_WOW_ROOT` names `…/Program Files (x86)/World of Warcraft`, and
    the user data folder sits under a name with a space in it."""
    _user_data(monkeypatch, tmp_path / "App Data" / "wowlab")
    root = _install_at(tmp_path / "Program Files (x86)" / "World of Warcraft", monkeypatch)
    monkeypatch.chdir(tmp_path)
    _round_trip(root)


def test_constructed_location_root_past_max_path_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every file under the root is past `MAX_PATH`, and so is the root
    itself; each component stays short. On Windows this holds where long
    paths are enabled for the process (`LongPathsEnabled`, and Python's
    `longPathAware` manifest); the CI job records which."""
    deep = tmp_path.joinpath(*(f"games-library-{n:02d}-" + "x" * 40 for n in range(5)))
    root = deep / "World of Warcraft"
    assert len(str(root)) > MAX_PATH
    _user_data(monkeypatch, tmp_path / "data" / "wowlab")
    _install_at(root, monkeypatch)
    monkeypatch.chdir(tmp_path)
    _round_trip(root)


@pytest.mark.skipif(sys.platform != "win32", reason="share modes are a Windows facility")
def test_constructed_windows_snapshot_of_a_file_held_without_read_sharing_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file another process holds open with no read sharing cannot be read
    at all on Windows. `snap create` says which file and exits 1: no
    traceback, no manifest (a partial snapshot is never written), nothing in
    the install changed. The next `snap create`, after the file is let go,
    works."""
    import _winapi  # Windows only; CPython's own CreateFile wrapper

    user_data = _user_data(monkeypatch, tmp_path / "data" / "wowlab")
    root = _install_at(tmp_path / "World of Warcraft", monkeypatch)
    monkeypatch.chdir(tmp_path)
    held = root / FLAVOR / SYNDICATOR
    before = held.read_bytes()

    handle = _winapi.CreateFile(
        str(held),
        _winapi.GENERIC_READ,
        0,  # no FILE_SHARE_READ / WRITE / DELETE: what an exclusive holder asks for
        0,
        _winapi.OPEN_EXISTING,
        0,
        0,
    )
    try:
        result = runner.invoke(cli.app, ["snap", "create", "-m", "held"])
        assert result.exit_code == 1, (result.stdout, result.stderr, result.exception)
        assert isinstance(result.exception, SystemExit), "a clean error, not a traceback"
        assert "Syndicator.lua" in result.stderr, "the error names the file"
        manifests = user_data / "store" / "manifests"
        assert not manifests.is_dir() or not any(manifests.iterdir()), "no partial snapshot"
    finally:
        _winapi.CloseHandle(handle)

    assert held.read_bytes() == before
    ok("snap", "create", "-m", "after")
