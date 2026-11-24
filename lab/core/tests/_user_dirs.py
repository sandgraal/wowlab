"""Point every per-user folder a test touches at a stand-in (M18-14).

Tests that must not reach the real user data directory (the snapshot store,
the looks store, the game-data cache) redirect it through environment
variables. Which variable counts depends on the platform: macOS follows
`HOME`, Linux `XDG_DATA_HOME` then `HOME`, but on Windows `platformdirs` asks
the system for the known folder and ignores `HOME`, `USERPROFILE`, `APPDATA`
and `LOCALAPPDATA` alike. Only its own override variables,
`WIN_PD_OVERRIDE_APPDATA` and `WIN_PD_OVERRIDE_LOCAL_APPDATA`, move it. A
fixture that set only the plain variables sent every snapshot into the
runner's real `%LOCALAPPDATA%\\wowlab`, where parallel tests taking
different snapshots at the same fixed `now` collided
(`SnapshotExistsError`, `lab (windows)` on #583).

`redirect_user_dirs` sets the plain variables and the overrides together, so
a fixture cannot set one without the other. The root `conftest.py` checks, for
every test, that a plain variable pointing into the test's temporary folder
has its override pointing there too, and that the user data directory then
resolves inside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# platformdirs' own override variable for each Windows folder variable.
WINDOWS_OVERRIDES: dict[str, str] = {
    "APPDATA": "WIN_PD_OVERRIDE_APPDATA",
    "LOCALAPPDATA": "WIN_PD_OVERRIDE_LOCAL_APPDATA",
}

_FOLDER_VARIABLES = (
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
)


def redirect_user_dirs(
    monkeypatch: pytest.MonkeyPatch, home: Path, *, separate: bool = True
) -> None:
    """Make `home` the user's home and keep every per-user folder inside it.

    With `separate` (the default) each folder variable gets its own
    subfolder named after it in lower case (`home / "localappdata"`); without
    it, every variable points at `home` itself.
    """
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    for name in _FOLDER_VARIABLES:
        monkeypatch.setenv(name, str(home / name.lower() if separate else home))
    for plain, override in WINDOWS_OVERRIDES.items():
        monkeypatch.setenv(override, str(home / plain.lower() if separate else home))


def no_wowup_list(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    """Make `folder` (inside the test's `tmp_path`, and empty) WowUp's
    user-data folder, so `wowlab addons list` never reads the machine's own
    WowUp list (M22-16). A test that moves the home folder with
    `redirect_user_dirs` needs no more; one that leaves the home as it is
    calls this."""
    from wowlab_core import addonsource

    monkeypatch.setattr(addonsource, "wowup_folder", lambda platform=None: folder)
