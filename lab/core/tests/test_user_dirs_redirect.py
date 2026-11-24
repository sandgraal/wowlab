"""The shared stand-in home keeps the user data directory in tmp_path on
Windows too (M18-14).

On Windows platformdirs ignores `HOME`, `USERPROFILE`, `APPDATA` and
`LOCALAPPDATA`; only `WIN_PD_OVERRIDE_APPDATA` and
`WIN_PD_OVERRIDE_LOCAL_APPDATA` move it. No one working on this repository has
Windows, so these tests run platformdirs' own Windows class on any machine,
with the system's answer replaced by a made-up runner folder, and show that
the fixture moves the store and that the root conftest's check catches a
fixture that forgets to. Windows CI is the end-to-end proof, while it runs:
the Windows suite is paused since 2026-11-24 (ADR-0031 amendment of that
date), so until it resumes these tests are the only check.
"""

from __future__ import annotations

import importlib.util
import ntpath
import os
from pathlib import Path

import platformdirs
import platformdirs.windows
import pytest
import test_ui_explorer as first_stage
from _user_dirs import WINDOWS_OVERRIDES, redirect_user_dirs

from wowlab_core import snapshot

ROOT = Path(__file__).resolve().parents[3]
RUNNER_LOCAL = "C:\\Users\\runneradmin\\AppData\\Local"
RUNNER_ROAMING = "C:\\Users\\runneradmin\\AppData\\Roaming"

home = first_stage.home


def _root_conftest() -> object:
    spec = importlib.util.spec_from_file_location(
        "wowlab_root_conftest_m18_14", ROOT / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER_FOLDERS = {"CSIDL_LOCAL_APPDATA": RUNNER_LOCAL, "CSIDL_APPDATA": RUNNER_ROAMING}


def _override_variable(csidl_name: str) -> str:
    """platformdirs' documented override variable for a `CSIDL_*` folder."""
    return "WIN_PD_OVERRIDE_" + csidl_name.removeprefix("CSIDL_")


def _runner_win_folder(csidl_name: str) -> str:
    """`platformdirs.windows.get_win_folder` as it answers on a Windows
    runner (constructed): the override variable when it holds a fully
    qualified path, otherwise the system's known folder, whatever `APPDATA`
    or `LOCALAPPDATA` say. A drive path is fully qualified, and so, here, is
    a POSIX `tmp_path`."""
    override = os.environ.get(_override_variable(csidl_name), "").strip()
    if override and (ntpath.isabs(override) or Path(override).is_absolute()):
        return override
    return RUNNER_FOLDERS[csidl_name]


@pytest.fixture
def windows_platformdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """platformdirs as it behaves on a Windows runner (constructed).

    Only public names are replaced: the module-level `get_win_folder`, which
    every `Windows` directory property calls, and `PlatformDirs`. The
    private resolver behind `get_win_folder` changes between platformdirs
    releases (`_resolve_win_folder` went in 4.14.8, #697);
    `test_the_stand_in_matches_platformdirs_override_constructed` holds the
    stand-in to the real function's override rule.
    """
    monkeypatch.setattr(platformdirs.windows, "get_win_folder", _runner_win_folder)
    monkeypatch.setattr(platformdirs, "PlatformDirs", platformdirs.windows.Windows)


def test_the_stand_in_matches_platformdirs_override_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real `get_win_folder` honours the override variable the stand-in
    reads, named as `WINDOWS_OVERRIDES` names it, ahead of the plain
    variable. A drive path is fully qualified on every platform."""
    assert {_override_variable(c) for c in RUNNER_FOLDERS} == set(WINDOWS_OVERRIDES.values())
    for csidl_name in RUNNER_FOLDERS:
        override = "C:\\Stand-in\\" + csidl_name
        plain = csidl_name.removeprefix("CSIDL_").replace("_", "")
        monkeypatch.setenv(plain, "C:\\Plain\\" + csidl_name)
        monkeypatch.setenv(_override_variable(csidl_name), f"  {override} ")
        assert platformdirs.windows.get_win_folder(csidl_name) == override
        assert _runner_win_folder(csidl_name) == override


def test_the_stand_in_sets_both_windows_overrides_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_user_dirs(monkeypatch, tmp_path)
    for plain, override in WINDOWS_OVERRIDES.items():
        assert os.environ[override] == os.environ[plain] == str(tmp_path / plain.lower())
    redirect_user_dirs(monkeypatch, tmp_path, separate=False)
    for plain, override in WINDOWS_OVERRIDES.items():
        assert os.environ[override] == os.environ[plain] == str(tmp_path)


def test_the_overrides_match_the_root_conftest_constructed() -> None:
    assert _root_conftest().WINDOWS_OVERRIDES == WINDOWS_OVERRIDES  # type: ignore[attr-defined]


def test_windows_ignores_localappdata_without_the_override_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_platformdirs: None
) -> None:
    """Positive control: the plain variables alone leave the real store."""
    for plain in WINDOWS_OVERRIDES:
        monkeypatch.setenv(plain, str(tmp_path / plain.lower()))
        monkeypatch.delenv(WINDOWS_OVERRIDES[plain], raising=False)
    assert str(snapshot.user_data_dir()).startswith(RUNNER_LOCAL)
    leaks = _root_conftest().user_store_leaks(  # type: ignore[attr-defined]
        os.environ, tmp_path, snapshot.user_data_dir(), windows=True
    )
    assert len(leaks) == 3, leaks


def test_the_shared_home_keeps_the_store_in_tmp_path_on_windows_constructed(
    windows_platformdirs: None, home: Path, tmp_path: Path
) -> None:
    """The `home` fixture of test_ui_explorer, which test_ui_kept and the
    review probes use, moves the Windows store into tmp_path."""
    data = snapshot.user_data_dir()
    assert data == home / "localappdata" / "wowlab" / "wowlab"
    assert tmp_path in data.parents
    leaks = _root_conftest().user_store_leaks(  # type: ignore[attr-defined]
        os.environ, tmp_path, data, windows=True
    )
    assert leaks == []


def test_the_check_is_quiet_when_nothing_points_into_tmp_path_constructed(
    tmp_path: Path,
) -> None:
    conftest = _root_conftest()
    elsewhere = tmp_path.parent / "not-this-test"
    environ = {"LOCALAPPDATA": str(elsewhere), "HOME": str(elsewhere)}
    for windows in (True, False):
        assert (
            conftest.user_store_leaks(environ, tmp_path, Path(RUNNER_LOCAL), windows=windows) == []
        )  # type: ignore[attr-defined]


def test_the_check_names_a_posix_store_outside_tmp_path_constructed(tmp_path: Path) -> None:
    environ = {"HOME": str(tmp_path)}
    leaks = _root_conftest().user_store_leaks(  # type: ignore[attr-defined]
        environ, tmp_path, tmp_path.parent / "elsewhere", windows=False
    )
    assert len(leaks) == 1 and "outside tmp_path" in leaks[0]
