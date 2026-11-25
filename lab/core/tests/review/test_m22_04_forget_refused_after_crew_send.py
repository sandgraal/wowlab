# Probe from review of m22/04-tooltip-record; reproduces `crew forget` (one player and --all) refused for good once `crew send` has written WowLabDeskDB
"""Constructed (L8: the crew book, matches and Spy file of
`test_crew_send.py`, all invented; the write gate writes into a copy of the
captured install under `tmp_path` only).

ADR-0036 term (5) and `docs/LAB_PLAN.md` §20.3: the player can delete what
is stored, per player and all at once, and after a forget "the bytes of the
name are in neither file". `crew send` writes each player's stored key into
the same `WowLab.lua` as `WowLabDeskDB`'s `players` keys, and the addon
never rewrites that table, so it stays until the next send. `crew forget`
still edits only the book's `players` and `current`, and its own check
(a forget refuses while the name is still in the file outside the book's
players) then refuses every forget of a sent player, and `--all` whenever
the table holds anyone: "The name is also elsewhere in ... WowLab.lua,
outside the crew book's players ... Nothing was changed." The only way out
is `wowlab undo` of the send, which the refusal does not name.

Positive control: the same forget with no send first removes the player
from the file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parser"))

from _crew_build import KEY_C
from test_crew_send import _built, _send
from test_crewdb import make  # noqa: F401 - the fixture


def test_forget_of_a_sent_player_removes_them_from_the_file(make: Any) -> None:  # noqa: F811
    env = _built(make)
    _send(env)
    assert KEY_C.encode() in env.data()
    result = env.run("crew", "forget", KEY_C, "--yes")
    assert result.exit_code == 0, result.stderr
    assert KEY_C.encode() not in env.data()


def test_forget_all_after_a_send_empties_the_file(make: Any) -> None:  # noqa: F811
    env = _built(make)
    _send(env)
    result = env.run("crew", "forget", "--all", "--yes")
    assert result.exit_code == 0, result.stderr
    assert KEY_C.encode() not in env.data()


def test_positive_control_forget_without_a_send(make: Any) -> None:  # noqa: F811
    env = _built(make)
    result = env.run("crew", "forget", KEY_C, "--yes")
    assert result.exit_code == 0, result.stderr
    assert KEY_C.encode() not in env.data()
