# Probe from review of m20/19-auto-combat-log; reproduces `crew scan-log` with no PATH failing on every run at one unreadable older log, so no newer log is ever read
"""CONSTRUCTED INPUT (the crew book and log builders of `test_crew_world`).

With no PATH, `crew scan-log` reads every log of the Logs folder not read
before, oldest first (M20-19 fix round). `_read_log_parts` turns an
`OSError` into a `CliError`, which ends the whole command. One log the
user cannot read (a permission left by a copy tool, a file the game or an
uploader holds) that is older than the rest is met first on every run: the
command fails each time and the newer logs, the ones the switch is making
now, are never read. The control reads the same three logs with all of
them readable.
"""

# ruff: noqa: F811 - `make` is test_crewdb's fixture, imported and requested by name

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import crewdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _crew_build import ACCOUNT, FLAVOR
from test_crew_world import (
    SECOND_NAME,
    THIRD_NAME,
    _aged,
    _book,
    _fight,
    _scan,
    _second_log,
    _third_log,
    _write_log,
    owner,  # noqa: F401 - the fixture
)
from test_crewdb import make  # noqa: F401 - the fixture


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="chmod 000 does not make a file unreadable here",
)
@pytest.mark.parametrize("locked", [False, True], ids=["control-all-readable", "older-unreadable"])
def test_one_unreadable_older_log_does_not_stop_the_newer_ones(
    make: Any, owner: Any, locked: bool
) -> None:
    env = make(_book())
    _aged(_write_log(env, _fight()), 1_000_000)
    second = _write_log(env, _second_log(), name=SECOND_NAME)
    _aged(second, 2_000_000)
    _aged(_write_log(env, _third_log(), name=THIRD_NAME), 3_000_000)
    if locked:
        second.chmod(0)
    try:
        for _ in range(2):  # and on the next run too
            result = _scan(env, "--json")
            assert "cannot read the combat log" not in result.output, result.output
        # The first log's fight and the third's are kept, whatever the
        # second is: three sessions' encounters, two when it cannot be read.
        foes = [e.foes for e in crewdb.world_encounters(FLAVOR, ACCOUNT)]
        assert len(foes) == (2 if locked else 3), foes
    finally:
        second.chmod(0o644)
