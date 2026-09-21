# Probe from review of m10/09-process-detection; reproduces: psutil's public exe() calls cmdline() itself when the native exe is denied, so the module's "never cmdline() on Windows" and "only an OS-reported path clears a process" do not hold
"""`psutil.Process.exe()` is not a thin wrapper. When the platform
implementation raises AccessDenied or returns "", it calls `self.cmdline()`
and returns argv[0] if that is an absolute, existing, executable file
(psutil/__init__.py, `guess_it`). The fakes in `test_process.py` cannot show
this because they do not model psutil; these probes drive a real
`psutil.Process` (this test process) with only the platform layer stubbed.

Two consequences at 7393d47:

1. With `sys.platform == "win32"`, `process.py` never calls `cmdline()`
   itself, but its `exe()` call still does, exactly when the target denies
   the native query. On Windows that is the PEB read the module docstring
   says is never made (L7, ADR-0023).
2. A path the module labels `exe_source="exe"` can be a self-chosen argv[0],
   and it clears a process whose name and native exe are both denied.

Constructed inputs. The stubs reach into `psutil.Process._proc`, which is
private; that is deliberate, it is the seam where the behaviour lives.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator

import psutil
import pytest

from wowlab_core import process
from wowlab_core.process import ClientState, client_state, running_clients


def _denied(self: object) -> str:
    raise psutil.AccessDenied(os.getpid())


@pytest.fixture
def me() -> psutil.Process:
    return psutil.Process(os.getpid())


@pytest.fixture
def cmdline_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """Counts calls to the public `psutil.Process.cmdline`, whoever makes them."""
    calls: list[int] = []

    def recording(self: psutil.Process) -> list[str]:
        calls.append(self.pid)
        return [sys.executable, "-m", "probe"]

    monkeypatch.setattr(psutil.Process, "cmdline", recording)
    yield calls


# ─── positive controls: these pass at 7393d47 ────────────────────────────────


def test_control_readable_native_exe_means_no_cmdline_call_on_windows(
    me: psutil.Process, cmdline_calls: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(type(me._proc), "name", lambda self: "probe")
    monkeypatch.setattr(type(me._proc), "exe", lambda self: sys.executable)
    running_clients(process_iter=lambda: [me])
    assert cmdline_calls == []


def test_control_everything_denied_is_unknown(
    me: psutil.Process, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(type(me._proc), "name", _denied)
    monkeypatch.setattr(type(me._proc), "exe", _denied)
    monkeypatch.setattr(psutil.Process, "cmdline", _denied)
    assert client_state(process_iter=lambda: [me]) is ClientState.UNKNOWN


# ─── the defects ─────────────────────────────────────────────────────────────


def test_no_cmdline_call_reaches_psutil_on_windows_when_the_native_exe_is_denied(
    me: psutil.Process, cmdline_calls: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(type(me._proc), "name", lambda self: "probe")
    monkeypatch.setattr(type(me._proc), "exe", _denied)
    running_clients(process_iter=lambda: [me])
    assert cmdline_calls == [], "psutil.Process.exe() fell back to cmdline() on win32"


def test_psutils_argv0_guess_does_not_clear_a_process_with_name_and_exe_denied(
    me: psutil.Process, cmdline_calls: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(type(me._proc), "name", _denied)
    monkeypatch.setattr(type(me._proc), "exe", _denied)
    assert client_state(process_iter=lambda: [me]) is ClientState.UNKNOWN
