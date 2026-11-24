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

import os
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
    user-data folder, and `folder / "CurseForge"` the CurseForge app's, so
    `wowlab addons list` never reads the machine's own WowUp or CurseForge
    list (M22-16, M22-25). A test that moves the home folder with
    `redirect_user_dirs` needs no more; one that leaves the home as it is
    calls this."""
    from wowlab_core import addonsource

    monkeypatch.setattr(addonsource, "wowup_folder", lambda platform=None: folder)
    monkeypatch.setattr(
        addonsource, "curseforge_folder", lambda platform=None: folder / "CurseForge"
    )


# ─── the addon managers' own folders (M22-25) ────────────────────────────────

# `wowlab addons list` reads WowUp's and the CurseForge app's records from
# their user-data folders. The root `conftest.py` keeps every test out of the
# real ones, whatever the test itself does: `keep_manager_folders_out`
# (autouse) answers each folder rule with a folder that does not exist
# whenever the rule would name the real folder of the user running the tests,
# and `audit_manager_opens` (an audit hook) records any open of a path inside
# one, which fails the test. Only an `@live` test, run by hand, is exempt.

_REAL_MANAGER_FOLDERS: list[str] = []
MANAGER_OPENS: list[str] = []
_MANAGER_RULES = ("wowup_folder", "curseforge_folder")


def note_real_manager_folders() -> tuple[str, ...]:
    """The real WowUp and CurseForge folders, as this process found them
    before any test ran (the root `conftest.py` calls this at start). Empty
    where `wowlab_core` is not installed (the hook tests' Python 3.9)."""
    if not _REAL_MANAGER_FOLDERS:
        try:
            from wowlab_core import addonsource
        except ImportError:
            return ()
        for name in _MANAGER_RULES:
            found = getattr(addonsource, name)()
            for text in (str(found), os.path.realpath(found)):
                if text not in _REAL_MANAGER_FOLDERS:
                    _REAL_MANAGER_FOLDERS.append(text)
    return tuple(_REAL_MANAGER_FOLDERS)


def audit_manager_opens(event: str, args: tuple[object, ...]) -> None:
    """An audit hook: record an open of any path inside a real manager folder."""
    if event != "open" or not args or not _REAL_MANAGER_FOLDERS:
        return
    path = args[0]
    if not isinstance(path, (str, bytes, os.PathLike)):
        return  # a descriptor, or nothing
    text = os.fsdecode(path)
    for real in _REAL_MANAGER_FOLDERS:
        if text == real or text.startswith(real + os.sep):
            MANAGER_OPENS.append(text)
            return


def keep_manager_folders_out(monkeypatch: pytest.MonkeyPatch, blocked: Path) -> None:
    """Wrap each manager folder rule so that where it would name the real
    folder it names `blocked / <rule>` instead (a folder that does not
    exist: the list is "missing"). A test that moved the home folder still
    gets its own folder, as the rule gives it."""
    try:
        from wowlab_core import addonsource
    except ImportError:
        return
    real = set(_REAL_MANAGER_FOLDERS)
    for name in _MANAGER_RULES:
        rule = getattr(addonsource, name)

        def folder(platform: str | None = None, _rule: object = rule, _name: str = name) -> Path:
            found: Path = _rule(platform)  # type: ignore[operator]
            if str(found) in real or os.path.realpath(found) in real:
                return blocked / _name
            return found

        folder.__wrapped__ = rule  # type: ignore[attr-defined]
        monkeypatch.setattr(addonsource, name, folder)
