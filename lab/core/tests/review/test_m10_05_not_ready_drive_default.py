# Probe from review of m10/05-install-discovery; reproduces discover() raising
# on an empty removable drive among the Windows defaults instead of passing it.
"""LAB_PLAN §6.1 amendment 2026-09-22 (1) probes every drive `os.listdrives()`
returns, removable and optical drives included. On Windows, `stat` of any
path on a drive with no medium (an empty DVD drive or card reader) raises
`OSError` with `winerror` 21 (ERROR_NOT_READY), which is not ENOENT. CPython's
own pathlib lists that winerror in `_IGNORED_WINERRORS` ("drive exists but is
not accessible") so `exists()` and `is_dir()` read it as absent.

`install._probe` treats every `OSError` other than `FileNotFoundError` and
`NotADirectoryError` as `unreadable`, and the defaults loop re-raises
anything but `missing`. So an empty D: stops the search before E:, or turns
"no install found" into "D:\\World of Warcraft is not a WoW install". The
amendment's rule (2) is about a default that *holds* a `.build.info` which
cannot be read; a drive with no medium holds nothing.

Constructed (boundary case): the Windows error is simulated by wrapping
`os.stat` / `os.lstat` for paths under one directory, because a not-ready
drive cannot be made in a test. The two positive controls pin what must not
change: without the fault the search passes the empty default and finds the
install, and a present folder whose `.build.info` is refused still raises
(amendment rule 2).
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.install import InstallNotFoundError, NotAnInstallError, discover

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "macos"
BUILD_INFO = (FIXTURES / ".build.info").read_bytes()
FLAVOR_INFO = (FIXTURES / "forever" / ".flavor.info").read_bytes()
ERROR_NOT_READY = 21


def _real_install(parent: Path) -> Path:
    root = parent / "World of Warcraft"
    (root / "_classic_beta_").mkdir(parents=True)
    (root / ".build.info").write_bytes(BUILD_INFO)
    (root / "_classic_beta_" / ".flavor.info").write_bytes(FLAVOR_INFO)
    return root


def _fault_under(
    monkeypatch: pytest.MonkeyPatch, prefix: Path, make: Callable[[str], OSError]
) -> None:
    real_stat, real_lstat = os.stat, os.lstat

    def wrap(real: Callable[..., os.stat_result]) -> Callable[..., os.stat_result]:
        def fake(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
            text = os.fsdecode(path) if not isinstance(path, int) else ""
            if text == str(prefix) or text.startswith(str(prefix) + os.sep):
                raise make(text)
            return real(path, *args, **kwargs)

        return fake

    monkeypatch.setattr(os, "stat", wrap(real_stat))
    monkeypatch.setattr(os, "lstat", wrap(real_lstat))


def _not_ready(path: str) -> OSError:
    # What Windows raises: errno EACCES (CPython maps winerror 19-36 there),
    # winerror 21. POSIX OSError ignores the winerror argument, so set it.
    exc = PermissionError(errno.EACCES, "The device is not ready", path)
    exc.winerror = ERROR_NOT_READY  # type: ignore[attr-defined]
    return exc


def _refused(path: str) -> OSError:
    return PermissionError(errno.EACCES, "Access is denied", path)


def test_positive_control_empty_default_is_passed_over(tmp_path: Path) -> None:
    empty_drive = tmp_path / "D"
    empty_drive.mkdir()
    real = _real_install(tmp_path / "E")

    got = discover(environ={}, defaults=[empty_drive / "World of Warcraft", real])

    assert got.root == real


def test_positive_control_refused_build_info_in_a_present_default_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    present = _real_install(tmp_path / "D")
    real = _real_install(tmp_path / "E")
    _fault_under(monkeypatch, present / ".build.info", _refused)

    with pytest.raises(NotAnInstallError) as caught:
        discover(environ={}, defaults=[present, real])

    assert caught.value.reason == "unreadable"


def test_not_ready_drive_default_is_passed_over_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_ready_drive = tmp_path / "D"
    not_ready_drive.mkdir()
    real = _real_install(tmp_path / "E")
    _fault_under(monkeypatch, not_ready_drive, _not_ready)

    got = discover(environ={}, defaults=[not_ready_drive / "World of Warcraft", real])

    assert got.root == real


def test_not_ready_drive_alone_is_install_not_found_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_ready_drive = tmp_path / "D"
    not_ready_drive.mkdir()
    _fault_under(monkeypatch, not_ready_drive, _not_ready)

    with pytest.raises(InstallNotFoundError):
        discover(environ={}, defaults=[not_ready_drive / "World of Warcraft"])
