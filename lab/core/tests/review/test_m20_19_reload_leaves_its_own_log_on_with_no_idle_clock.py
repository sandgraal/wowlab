# Probe from review of m20/19-auto-combat-log; reproduces the addon's own log never idling off after a /reload (no idle timer, no idle clock)
"""M20-19 (ADR-0026, note of 2026-11-22): the log goes off "after a stated
idle time with no combat". CombatLog.lua starts the idle clock (`active`)
and the idle timer (`ns.Wake(..., IDLE_EVENT)`) only in `turnOn` and at the
end of a fight (`run`'s "calm" branch). After a /reload (or a relog without
a client restart) the log stays on [verify] and the carried record says it
is the addon's (`mine`); the first check finds it on and does nothing, so
no idle timer is set and `active` stays nil. Standing idle in the open
world, the log then stays on until the next fight, however long that is.

The C_Timer callbacks of the previous session die with its Lua state at a
/reload; the grader's own reload test (`a_reload_keeps_the_count_and_its_own_log`)
reuses the old session's stub client with its timer list, so the old idle
timer still fires there and the gap does not show.

The same root cause (an idle timer that finds the time not yet up arms no
other) shows without a /reload: `/wowlab combatlog idle 30` typed a minute
after the log went on. The 10-minute timer fires, `readBack` takes 30, the
idle check finds 10 < 30 and returns, and nothing is armed again: the log
stays on for hours with no combat.

Played through the grader's own line-for-line model (`Switch`, pinned to
the Lua by text and SHA-256). Constructed scenario; no Lua is run.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[4]
ADDON_TESTS = REPO / "tests" / "addon"


def _model() -> ModuleType:
    if str(ADDON_TESTS) not in sys.path:
        sys.path.insert(0, str(ADDON_TESTS))
    return importlib.import_module("test_m20_19_combatlog")


def _reload(m: ModuleType, before: object) -> object:
    """A /reload: a new Lua state with the carried record; the client's log
    is unchanged and the old session's timers are gone."""
    state = before.state  # type: ignore[attr-defined]
    carried = {
        k: state[k]
        for k in ("switches", "last_at", "last_to", "last_why", "idle_minutes")
        if k in state
    }
    carried["mine"] = state.get("mine") is True
    client = before.c  # type: ignore[attr-defined]
    client.timers.clear()
    client.chat.clear()
    return m.Switch(client, carried)


def test_control_a_fresh_login_idles_its_own_log_off() -> None:
    m = _model()
    s = m.Switch(m.Client())
    s.login()
    assert s.c.log is True
    s.advance(m.IDLE_MINUTES * 60)
    assert s.c.log is False


def test_after_a_reload_its_own_log_idles_off() -> None:
    m = _model()
    s = m.Switch(m.Client())
    s.login()
    assert s.c.log is True and s.state["mine"] is True
    again = _reload(m, s)
    again.login()  # type: ignore[attr-defined]
    assert again.c.log is True  # type: ignore[attr-defined]
    # An hour in the open world with no combat.
    again.advance(6 * m.IDLE_MINUTES * 60)  # type: ignore[attr-defined]
    assert again.c.log is False, "the addon's own log stayed on with no combat for an hour"  # type: ignore[attr-defined]


def test_a_longer_idle_time_set_mid_session_still_idles_off() -> None:
    m = _model()
    s = m.Switch(m.Client())
    s.login()
    s.advance(60)
    s.state["idle_minutes"] = 30  # /wowlab combatlog idle 30 (ns.CombatLogIdle)
    s.advance(5 * 3600)  # five hours, no combat
    assert s.c.log is False, "the addon's own log stayed on five hours with no combat"
