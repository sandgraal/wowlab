# Probe from review of m10/09-process-detection; reproduces: the cmdline shadow deletes an injected process object's own instance attribute `cmdline`
"""`_os_reported_exe` sets `proc.cmdline` and then `delattr`s it. For a real
`psutil.Process` the method lives on the class, so deleting the instance
attribute restores it. For an injected `ProcessLike` whose methods are
instance attributes (`types.SimpleNamespace`, `unittest.mock.MagicMock`: the
obvious ways to build the fake process table that `process_iter` exists for,
and that M10-11T will build), the delete removes the fake's own `cmdline`.
The next line of `_read_path` then calls `proc.cmdline()` and an
AttributeError escapes `running_clients`; the caller's object is left
mutated. Expected: whatever was on the instance before is put back.

Constructed inputs: an injected fake process table, as the ticket specifies.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from wowlab_core import process
from wowlab_core.process import running_clients


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "Games" / "World of Warcraft"


@pytest.fixture(autouse=True)
def cmdline_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The argv[0] fallback is off on win32; these probes are about it."""
    monkeypatch.setattr(process, "_cmdline_is_a_listing_call", lambda: True)


class _ClassFake:
    """Methods on the class, like psutil.Process."""

    pid = 7

    def __init__(self, argv0: str) -> None:
        self._argv0 = argv0

    def name(self) -> str:
        return "client"

    def exe(self) -> str:
        return ""

    def cmdline(self) -> list[str]:
        return [self._argv0]

    def status(self) -> str:
        return "running"


# ─── positive control: passes at fb19863 ─────────────────────────────────────


def test_control_class_based_fake_matches_by_argv0(root: Path) -> None:
    fake = _ClassFake(str(root / "_retail_" / "client"))
    [found] = running_clients([root], process_iter=lambda: [fake])
    assert (found.matched_by, found.exe_source) == ("path", "cmdline")


# ─── the defect ──────────────────────────────────────────────────────────────


def test_simplenamespace_fake_keeps_its_cmdline(root: Path) -> None:
    argv0 = str(root / "_retail_" / "client")
    fake = SimpleNamespace(
        pid=7,
        name=lambda: "client",
        exe=lambda: "",
        cmdline=lambda: [argv0],
        status=lambda: "running",
    )
    [found] = running_clients([root], process_iter=lambda: [fake])
    assert (found.matched_by, found.exe_source) == ("path", "cmdline")
    assert fake.cmdline() == [argv0], "the caller's object was left mutated"


def test_magicmock_fake_keeps_its_cmdline(root: Path) -> None:
    argv0 = str(root / "_retail_" / "client")
    fake = mock.MagicMock()
    fake.pid = 8
    fake.name.return_value = "client"
    fake.exe.return_value = ""
    fake.cmdline.return_value = [argv0]
    fake.status.return_value = "running"
    [found] = running_clients([root], process_iter=lambda: [fake])
    assert (found.matched_by, found.exe_source) == ("path", "cmdline")
    assert fake.cmdline() == [argv0]
