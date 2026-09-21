# Probe from review of m10/09-process-detection; reproduces: a readable, process-chosen argv[0] turns an UNKNOWN process into "not reported"
"""`process.py` says of the cmdline fallback: "It is only ever used to add a
match." It is also used to settle a process as not-a-client. A process whose
name and exe() are denied is UNKNOWN (guard refuses) until its argv[0] happens
to be any absolute path outside the install, at which point it disappears from
the result and `client_state` says NOT_RUNNING. argv[0] is whatever the process
(or its parent) chose; it is not evidence that the process is not the client
(docs/LAB_PLAN.md §6.7: access-denied is reported as unknown, fail closed).

Constructed inputs: an injected fake process table, as the ticket specifies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import psutil
import pytest

from wowlab_core.process import ClientState, client_state, running_clients

DENIED = object()


class _Fake:
    def __init__(self, pid: int, *, name: Any, exe: Any, cmdline: Any) -> None:
        self.pid = pid
        self._values = {"name": name, "exe": exe, "cmdline": cmdline, "status": "running"}

    def _answer(self, attr: str) -> Any:
        value = self._values[attr]
        if value is DENIED:
            raise psutil.AccessDenied(self.pid)
        return value

    def name(self) -> str:
        return str(self._answer("name"))

    def exe(self) -> str:
        return str(self._answer("exe"))

    def cmdline(self) -> list[str]:
        return list(self._answer("cmdline"))

    def status(self) -> str:
        return str(self._answer("status"))


def _table(*procs: _Fake) -> Callable[[], Iterable[Any]]:
    return lambda: iter(procs)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "Games" / "World of Warcraft"


@pytest.fixture
def unrelated_argv0(tmp_path: Path) -> str:
    return str(tmp_path / "usr" / "bin" / "innocent")


# ─── positive controls: these pass on the branch as reviewed ─────────────────


def test_control_nothing_readable_is_unknown(root: Path) -> None:
    procs = _table(_Fake(1, name=DENIED, exe=DENIED, cmdline=DENIED))
    assert client_state([root], process_iter=procs) is ClientState.UNKNOWN


def test_control_truncated_client_name_with_nothing_else_is_unknown(root: Path) -> None:
    procs = _table(_Fake(1, name="World of Warcraf", exe=DENIED, cmdline=DENIED))
    assert client_state([root], process_iter=procs) is ClientState.UNKNOWN


# ─── the defect ──────────────────────────────────────────────────────────────


def test_argv0_does_not_clear_a_process_whose_name_and_exe_are_denied(
    root: Path, unrelated_argv0: str
) -> None:
    procs = _table(_Fake(1, name=DENIED, exe=DENIED, cmdline=[unrelated_argv0]))
    [found] = running_clients([root], process_iter=procs)
    assert found.state is ClientState.UNKNOWN
    assert client_state([root], process_iter=procs) is ClientState.UNKNOWN


def test_argv0_does_not_clear_a_truncated_client_name(root: Path, unrelated_argv0: str) -> None:
    procs = _table(_Fake(1, name="World of Warcraf", exe=DENIED, cmdline=[unrelated_argv0]))
    [found] = running_clients([root], process_iter=procs)
    assert found.state is ClientState.UNKNOWN
    assert client_state([root], process_iter=procs) is ClientState.UNKNOWN
