# Probe from review of m20/19-auto-combat-log; reproduces `/wowlab combatlog off` typed in a fight leaving `fighting` stuck, so a later `on` in a dungeon checks nothing and the addon's own log stays on there until a pull ends
"""M20-19 fix round 2: `/wowlab combatlog on` asks for a check at once
(Core.lua's `wake`, out of combat), and `readBack` sets `waiting` on resume.
While paused, `gather` returns before `run`, so PLAYER_REGEN_ENABLED never
clears `fighting`. Paused in a fight, the flag stays true after it. Resumed
later out of combat, `run("enter")` sees `fighting` and only sets `waiting`;
the idle check returns on `fighting` too. The addon's own log stays on in
an instance (the rule: off on entering one, ADR-0026 note of 2026-11-22),
however long the player waits, until the first fight there ends.

The control: the same pause and resume made out of combat turns the log
off in the dungeon at once.

Played through the grader's line-for-line model (`Switch`, pinned to the
Lua by text and SHA-256). Constructed scenario; no Lua is run.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
ADDON_TESTS = REPO / "tests" / "addon"


def _model() -> ModuleType:
    if str(ADDON_TESTS) not in sys.path:
        sys.path.insert(0, str(ADDON_TESTS))
    return importlib.import_module("test_m20_19_combatlog")


@pytest.mark.parametrize(
    "in_fight", [False, True], ids=["control-paused-out-of-combat", "paused-in-a-fight"]
)
def test_on_in_a_dungeon_turns_its_own_log_off(in_fight: bool) -> None:
    m = _model()
    s = m.Switch(m.Client())
    s.login()
    assert s.c.log is True and s.mine
    if in_fight:
        s.fire("PLAYER_REGEN_DISABLED")
        s.command_switch(False)  # /wowlab combatlog off, in the fight
        s.fire("PLAYER_REGEN_ENABLED")
    else:
        s.command_switch(False)  # /wowlab combatlog off
    s.c.instance = True  # a loading screen into a dungeon, paused
    s.fire("PLAYER_ENTERING_WORLD")
    s.advance(m.FIRST_DELAY + 5)
    assert s.c.log is True  # paused: left as it is
    s.command_switch(True)  # /wowlab combatlog on, out of combat
    s.advance(3600)  # an hour in the dungeon before the first pull
    assert s.c.log is False, "the addon's own log stayed on in the dungeon after `on`"
