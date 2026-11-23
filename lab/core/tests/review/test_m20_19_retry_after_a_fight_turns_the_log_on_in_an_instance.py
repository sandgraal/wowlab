# Probe from review of m20/19-auto-combat-log; reproduces a switch refused in combat being retried at the fight's end inside an instance, turning the log on there ("combat in the open world") before the owed check turns it off
"""M20-19 fix round: a switch the game refuses in combat is counted and
retried when the fight ends (`run`'s `calm` branch: `if retry then retry =
false resume() end`, then `if waiting then ... check("enter") end`). The
retry runs before the owed check. A fight that ends inside an instance the
player entered during it (or in a raid group they joined during it) turns
the log on there, with the chat line and the record saying "combat in the
open world", and the owed check then turns it off again: a switch on in an
instance, which the rule (ADR-0026 note of 2026-11-22) never makes, two
chat lines and two counted switches.

The control: the same refused fight ending in the open world turns the log
on once, as the retry means to.

Played through the grader's line-for-line model (`Switch`, pinned to the
Lua by text and SHA-256); the stub client refuses only while in combat.
Constructed scenario; no Lua is run.
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
    "into", [None, "instance", "raid"], ids=["control-world", "instance", "raid"]
)
def test_a_retried_switch_is_never_made_outside_the_open_world(into: str | None) -> None:
    m = _model()
    s = m.Switch(m.Client())
    s.login()
    s.advance(m.IDLE_MINUTES * 60)  # idled off in the open world
    assert s.c.log is False
    s.c.obeys = False  # the game refuses the switch in combat [verify]
    s.fire("PLAYER_REGEN_DISABLED")
    assert s.c.log is False
    if into == "instance":
        s.c.instance = True
        s.fire("ZONE_CHANGED_NEW_AREA")
    elif into == "raid":
        s.c.raid = True
        s.fire("GROUP_ROSTER_UPDATE")
    s.c.obeys = True
    s.fire("PLAYER_REGEN_ENABLED")
    if into is None:
        assert s.c.log is True
        assert s.c.switch_calls[-1] is True
        return
    assert s.c.log is False
    # No switch on after the fight: the place it ended in wants the log off.
    after_fight = s.c.switch_calls[3:]  # [on, off (idle), refused on] came before
    assert True not in after_fight, (after_fight, s.c.chat[-2:])
