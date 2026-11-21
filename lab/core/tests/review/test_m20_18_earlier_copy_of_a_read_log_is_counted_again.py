# Probe from review of m20/18-world-pvp; reproduces a copy of a log taken before its last read (same first line, shorter, another path) being read as a new log, so its encounters are counted twice
"""CONSTRUCTED INPUT (the crew book and log builders of `test_crew_world`).

BACKLOG M20-18: "each log is read once, told apart by its path, size and
first line, and reading is resumable by offset so a growing log is not
counted twice". The first line carries the session's start time to the
millisecond, so a file at another path with the same first line is the
same log; when it is shorter than what was read, it is a part already
read, not a new log. `crewdb._resume` takes an unknown path with a known
first line as "moved" only when it is at least as long, and otherwise as
"new", read from byte 0. The positive control is a copy of the whole log,
which is found as "moved" and adds nothing.
"""

# ruff: noqa: F811 - `make` is test_crewdb's fixture, imported and requested by name

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import cli, crewdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _crew_build import ACCOUNT, FLAVOR
from _world_lines import FOE_D, you_hit
from test_crew_world import (
    LOG_NAME,
    _book,
    _fight,
    _json,
    _scan,
    _write_log,
    owner,  # noqa: F401 - the fixture
)
from test_crewdb import make  # noqa: F401 - the fixture


def _scan_report(env: Any, *args: str) -> crewdb.WorldOutcome:
    return cli.CrewScanLogReport.model_validate(_json(_scan(env, *args, "--json"))).scan


@pytest.mark.parametrize("cut", [False, True], ids=["control-whole-copy", "earlier-copy"])
def test_a_copy_of_a_read_log_adds_no_encounter(
    make: Any, owner: Any, tmp_path: Path, cut: bool
) -> None:
    env = make(_book())
    early = _fight()
    path = _write_log(env, early)
    archive = tmp_path / "archive" / LOG_NAME
    archive.parent.mkdir()
    archive.write_bytes(early)  # the player copies the log away mid-session
    with path.open("ab") as f:  # the client goes on writing it
        f.write((you_hit(FOE_D, 2000) + "\r\n").encode())
    if not cut:
        archive.write_bytes(path.read_bytes())
    _scan_report(env)
    held = len(crewdb.world_encounters(FLAVOR, ACCOUNT))
    assert held == 2
    _scan_report(env, str(archive))
    assert len(crewdb.world_encounters(FLAVOR, ACCOUNT)) == held
