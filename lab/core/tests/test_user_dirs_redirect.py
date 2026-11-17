"""The shared stand-in home keeps the user data directory in tmp_path on
Windows too (M18-14).

On Windows platformdirs ignores `HOME`, `USERPROFILE`, `APPDATA` and
`LOCALAPPDATA`; only `WIN_PD_OVERRIDE_APPDATA` and
`WIN_PD_OVERRIDE_LOCAL_APPDATA` move it. No one working on this repository has
Windows, so these tests run platformdirs' own Windows class on any machine,
with the system's answer replaced by a made-up runner folder, and show that
the fixture moves the store and that the root conftest's check catches a
fixture that forgets to. Windows CI is the end-to-end proof.
"""

from __future__ import annotations

import importlib.util
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


@pytest.fixture
def windows_platformdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """platformdirs as it behaves on a Windows runner (constructed).

    The system's known-folder lookup (`_resolve_win_folder`, platformdirs
    4.x) answers the runner's real folders whatever the environment says,
    and a POSIX `tmp_path` counts as a fully qualified override path.
    """
    real = {"CSIDL_LOCAL_APPDATA": RUNNER_LOCAL, "CSIDL_APPDATA": RUNNER_ROAMING}
    monkeypatch.setattr(platformdirs.windows, "_resolve_win_folder", real.__getitem__)
    monkeypatch.setattr(platformdirs.windows, "_is_fully_qualified", os.path.isabs)
    monkeypatch.setattr(platformdirs, "PlatformDirs", platformdirs.windows.Windows)


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
