"""M20-19: the combat-log switch (CombatLog.lua; ADR-0026, note of 2026-11-22).

Nothing here runs Lua (L3). Two halves:

1. Source pins. The functions that decide what happens to the log are pinned
   token for token (the rule itself, `run`, `resume` and `want`, as text; the
   rest by the SHA-256 of their rendered tokens), with Core.lua's timer and
   slash branch. Any edit to one of them fails here until the pin, and the
   model below, are changed with it.
2. A stub client. `Switch` is a line-for-line Python model of the pinned
   functions; `Client` stands in for the game (the log's state, the place,
   combat, the timers, Core.lua's out-of-combat rule for a timer). Each
   scenario the ticket names is played through the model: login in the
   world, zoning into an instance, a group turning into a raid, idling,
   combat resuming, a log already on, the call missing, and the rest.

The model proves the rule as pinned; whether the real client behaves as the
stub does (LoggingCombat for addons, its answer with no argument, the log
across /reload) is not yet confirmed, and the README and §21.3 say so.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import test_lab_addon as g

COMBATLOG = g.ADDON / "CombatLog.lua"
CORE = g.ADDON / "Core.lua"

# ─── 1. pins ────────────────────────────────────────────────────────────────

# The rule, as text.
WANT = (
    'function want ( trigger , where ) if where ~= "world" or trigger == "idle" then '
    'return "off" end if idled then return nil end return "on" end'
)
# In combat the only thing that reaches the client: a log the addon turned off
# for idling goes back on.
RESUME = (
    "function resume ( ) if not idled or handsOff then return end "
    "local on = logState ( ) if on == false then turnOn ( WHY_COMBAT ) "
    "elseif on == true then idled = false logOn = true end end"
)
RUN = (
    'function run ( trigger ) if trigger == "arrive" then idled = false '
    "arrivedAt = ns . Number ( time ( ) ) waiting = true "
    "if ns . Wake ( KEY , FIRST_DELAY , ENTER_EVENT ) then return end "
    'trigger = "start" end '
    'if trigger == "start" then started = true trigger = "enter" end '
    'if trigger == "calm" and not started and arrivedAt then '
    "local now = ns . Number ( time ( ) ) "
    "if now and now - arrivedAt >= FIRST_DELAY then started = true end end "
    "if not started then return end "
    'if trigger == "combat" then fighting = true active = ns . Number ( time ( ) ) resume ( ) '
    'elseif trigger == "calm" then fighting = false active = ns . Number ( time ( ) ) '
    "if retry then retry = false resume ( ) end "
    'if waiting then waiting = false check ( "enter" ) end '
    "if mine then armIdle ( ) end "
    'elseif trigger == "enter" then '
    "if fighting or inCombatNow ( ) then waiting = true else waiting = false "
    'check ( "enter" ) end '
    'elseif trigger == "idle" then local now = ns . Number ( time ( ) ) '
    "if fighting or not now or not active then return end "
    'if now - active >= idleMinutes * 60 then check ( "idle" ) '
    "elseif mine then armIdle ( ) end end end"
)
# The rest of what decides, by the SHA-256 of the rendered body. A change to
# one is a change to the model below: update both.
HASHED = {
    "check": "685c1694cec8365921da8e3e1f44100eed84ab12041d866388b0088767b7d1eb",
    "turn": "78ff540ad54d94993dfa0e58a735119c730265ffa6c4425a460a3c97c48f73a1",
    "turnOn": "534838797f8a6f26f9cff024a27a49c4ba0046d82da94f756c398aac416da991",
    "noted": "0e36c3a46268186517e100c77a6054ab15d24825a3db671dea83b79aaf47b5d1",
    "logState": "d45a448e7839389494dcf8ddf64cf1ea6581e998f3d2454f2b3540ff41ab4e22",
    "place": "3f4da679586b0e01b9c389ddc1fc7b041e8f01a540722eaeb0447f555070e6a9",
    "inCombatNow": "7c30f6318c8fef757bc9fed6bd633d0d1bda542e749857a59bcfdfc629f82765",
    "readBack": "2fffd3f7bb7dcbb1401c9d71a73ec54f42076930b9843286038050261f37a852",
    "record": "4fa6a641ba70bd34813b3dde5ac62b3453ebea6468441b29b924f460414cc143",
    "gather": "b341cd959d0128f0372c7d95edc4fc60175d3fe1e9e187c2855a33197bc6c373",
    "armIdle": "ef39d1fbe133b8ad1c63cc31107b705340ce70f4e03f2518420d909d34913fc5",
    "sayOnce": "5b67ebe8cec0ab4c95bc1b282db6c833fe7a44351c69088eb1fe86dfc3b02f56",
}
CORE_WAKE = (
    "function wake ( key , event ) local section = sectionByKey ( key ) "
    "if section and section . immediate and not inCombat then gather ( section , event ) end end"
)
CORE_NS_WAKE = (
    "function ns . Wake ( key , seconds , event ) if not timerAfter or type ( seconds ) ~= "
    '"number" or type ( event ) ~= "string" then return false end '
    "timerAfter ( seconds , function ( ) wake ( key , event ) end ) return true end"
)
TRIGGERS = (
    'local TRIGGERS = { PLAYER_ENTERING_WORLD = "arrive" , WOWLAB_COMBATLOG_ENTER = "start" , '
    'ZONE_CHANGED_NEW_AREA = "enter" , GROUP_ROSTER_UPDATE = "enter" , '
    'PLAYER_REGEN_DISABLED = "combat" , PLAYER_REGEN_ENABLED = "calm" , '
    'WOWLAB_COMBATLOG_IDLE = "idle" , }'
)
EVENTS = (
    "PLAYER_ENTERING_WORLD",
    "ZONE_CHANGED_NEW_AREA",
    "GROUP_ROSTER_UPDATE",
    "PLAYER_REGEN_DISABLED",
    "PLAYER_REGEN_ENABLED",
)


def _flow(path: Path) -> g._Flow:
    return g._Flow(path, g._tokens(path))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def combatlog_pin_problems(path: Path = COMBATLOG) -> list[str]:
    """Why CombatLog.lua (or an edited copy) is not the pinned switch."""
    flow = _flow(path)
    rendered = g._render(flow.tokens)
    problems = []
    for label, pin in (("want", WANT), ("resume", RESUME), ("run", RUN)):
        if g._body(flow, label) != pin:
            problems.append(f"{label} is not the pinned one")
    for label, digest in HASHED.items():
        if _sha(g._body(flow, label)) != digest:
            problems.append(f"{label} is not the pinned one (update the model with the pin)")
    if TRIGGERS not in rendered:
        problems.append("the events' triggers are not the pinned ones")
    for name, value in (("IDLE_MINUTES", "10"), ("FIRST_DELAY", "15")):
        if rendered.count(f"local {name} = {value}") != 1:
            problems.append(f"{name} is not {value}")
    # LoggingCombat is called in three places only: logState, turn's switch
    # and the existence test in gather.
    named = [t for t in flow.tokens if t.kind == "name" and t.text == "LoggingCombat"]
    if len(named) != 3:
        problems.append("LoggingCombat is reached other than by logState, turn and the gather")
    return problems


def test_m20_19_the_switch_is_the_pinned_one() -> None:
    assert combatlog_pin_problems() == []


def test_m20_19_constructed_edits_fail_the_pins(tmp_path: Path) -> None:
    """Constructed: an edit to the rule, to the in-combat path, to the switch
    call or to the idle default fails the pins."""
    real = COMBATLOG.read_text(encoding="utf-8")
    edits = [
        ('    if where ~= "world" or trigger == "idle" then', '    if trigger == "idle" then'),
        ("        resume()\n", '        resume()\n        check("enter")\n'),
        ("local IDLE_MINUTES = 10\n", "local IDLE_MINUTES = 1\n"),
        ("    pcall(LoggingCombat, on)\n", "    pcall(LoggingCombat, true)\n"),
        ('        idled = trigger == "idle"\n', "        idled = true\n"),
        ("    if told[name] then\n", "    if false then\n"),
    ]
    for n, (old, new) in enumerate(edits):
        assert old in real, old
        edited = tmp_path / f"CombatLog-{n}.lua"
        edited.write_text(real.replace(old, new, 1), encoding="utf-8")
        assert combatlog_pin_problems(edited), old


def test_m20_19_core_wake_and_slash_branch() -> None:
    flow = _flow(CORE)
    assert g._body(flow, "wake") == CORE_WAKE
    assert g._body(flow, "ns.Wake") == CORE_NS_WAKE
    slash = g._body(flow, "WOWLAB")
    assert 'elseif command == "combatlog" then' in slash
    assert "combatLogCommand ( key , extra )" in slash
    command = g._body(flow, "combatLogCommand")
    # On and off pause and resume the switch in its own record; a section
    # switched off with /wowlab skip stays off until unskip.
    assert 'ns . CombatLogSwitch ( word == "on" )' in command
    assert "skipCommand" not in command
    assert 'if section . off and word ~= "" then' in command


def test_m20_19_section_spec() -> None:
    text = COMBATLOG.read_text(encoding="utf-8")
    spec = text[text.index("ns.Section({") :]
    for event in EVENTS:
        assert f'"{event}"' in spec, event
    assert "immediate = true" in spec
    assert "on_world" not in spec  # never gathered by a pass, /wowlab save or logout
    assert g._keys_in(COMBATLOG) == ["combatlog"]


# ─── 2. the stub client and the model ───────────────────────────────────────

IDLE_MINUTES = 10
FIRST_DELAY = 15
WHY_WORLD = "open world"
WHY_COMBAT = "combat in the open world"
WHY_INSTANCE = "entered an instance"
WHY_RAID = "in a raid group"
WHY_IDLE = "no combat for the idle time"
TRIGGER = {
    "PLAYER_ENTERING_WORLD": "arrive",
    "WOWLAB_COMBATLOG_ENTER": "start",
    "ZONE_CHANGED_NEW_AREA": "enter",
    "GROUP_ROSTER_UPDATE": "enter",
    "PLAYER_REGEN_DISABLED": "combat",
    "PLAYER_REGEN_ENABLED": "calm",
    "WOWLAB_COMBATLOG_IDLE": "idle",
}


@dataclass
class Client:
    """The game, as the switch sees it. `log` is the combat log's state;
    `answers` False makes LoggingCombat() say neither true nor false;
    `obeys` False leaves the log as it is when asked to switch it."""

    log: bool = False
    has_logging: bool = True
    answers: bool = True
    obeys: bool = True
    instance: bool = False
    raid: bool = False
    lockdown: bool = False
    acl: str | None = "1"
    has_timer: bool = True
    now: int = 1_000_000
    core_in_combat: bool = False  # Core.lua's inCombat, from the REGEN events
    timers: list[tuple[int, str]] = field(default_factory=list)
    switch_calls: list[bool] = field(default_factory=list)  # LoggingCombat(on)
    calls_in_combat: list[str] = field(default_factory=list)
    chat: list[str] = field(default_factory=list)

    def call(self, name: str) -> None:
        if self.core_in_combat:
            self.calls_in_combat.append(name)

    def logging_combat(self, on: bool | None = None) -> bool | None:
        self.call("LoggingCombat" if on is None else f"LoggingCombat({str(on).lower()})")
        if on is not None:
            self.switch_calls.append(on)
            if self.obeys:
                self.log = on
        return self.log if self.answers else None


class Switch:
    """CombatLog.lua's pinned functions, line for line, over a stub client."""

    def __init__(self, client: Client, carried: dict[str, object] | None = None) -> None:
        self.c = client
        self.state: dict[str, object] | None = carried  # ns.state.combatlog
        self.off = False  # section.off (`/wowlab skip combatlog`)
        self.switches = 0
        self.last_at: int | None = None
        self.last_to: str | None = None
        self.last_why: str | None = None
        self.idle_minutes = IDLE_MINUTES
        self.mine = False
        self.paused = False
        self.loaded = False
        self.started = False
        self.own_seen = False
        self.hands_off = False
        self.dormant: str | None = None
        self.refused_in_combat = 0
        self.retry = False
        self.idled = False
        self.waiting = False
        self.active: int | None = None
        self.idle_timer_at: int | None = None
        self.arrived_at: int | None = None
        self.fighting = False
        self.log_on: bool | None = None
        self.advanced: bool | None = None
        self.told: set[str] = set()

    # Core.lua --------------------------------------------------------------

    def fire(self, event: str) -> None:
        """The client fires `event`: Core's own combat flag first (Core.lua's
        handlers register before the section's), then the section's
        immediate gather."""
        if event == "PLAYER_REGEN_DISABLED":
            self.c.core_in_combat = True
        elif event == "PLAYER_REGEN_ENABLED":
            self.c.core_in_combat = False
        if not self.off:
            self.gather(event)

    def wake(self, seconds: float, event: str) -> bool:
        if not self.c.has_timer:
            return False
        self.c.timers.append((self.c.now + int(seconds), event))
        return True

    def advance(self, seconds: int) -> None:
        """Time passes; each timer due runs Core's `wake`, out of combat only."""
        end = self.c.now + seconds
        while True:
            due = sorted(t for t in self.c.timers if t[0] <= end)
            if not due:
                break
            at, event = due[0]
            self.c.timers.remove(due[0])
            self.c.now = at
            if not self.c.core_in_combat and not self.off:
                self.gather(event)
        self.c.now = end

    # CombatLog.lua ---------------------------------------------------------

    def say_once(self, name: str, text: str) -> None:
        if name in self.told:
            return
        self.told.add(name)
        self.c.chat.append(text)

    def read_back(self) -> None:
        held = self.state
        if not isinstance(held, dict) or "absent" in held:
            return
        if isinstance(held.get("idle_minutes"), int):
            self.idle_minutes = held["idle_minutes"]  # type: ignore[assignment]
        was = self.paused
        self.paused = held.get("paused") is True
        if was and not self.paused:
            self.started = True
        if self.loaded:
            return
        self.loaded = True
        for key, attr in (
            ("switches", "switches"),
            ("last_at", "last_at"),
            ("last_to", "last_to"),
            ("last_why", "last_why"),
        ):
            if held.get(key) is not None:
                setattr(self, attr, held[key])
        self.mine = held.get("mine") is True

    def log_state(self) -> bool | None:
        on = self.c.logging_combat()
        return on if isinstance(on, bool) else None

    def place(self) -> str:
        self.c.call("IsInInstance")
        if self.c.instance:
            return "instance"
        self.c.call("IsInRaid")
        return "raid group" if self.c.raid else "world"

    def in_combat_now(self) -> bool:
        return self.c.lockdown

    def advanced_on(self) -> bool | None:
        self.c.call("C_CVar.GetCVar")
        return {"1": True, "0": False}.get(self.c.acl or "")

    def noted(self, to: str, why: str) -> None:
        self.switches += 1
        self.last_at = self.c.now
        self.last_to = to
        self.last_why = why
        self.c.chat.append(f"combat log {to} ({why}).")

    def arm_idle(self) -> None:
        now = self.c.now
        if self.active is None:
            self.active = now
        due = self.active + self.idle_minutes * 60
        if self.idle_timer_at is not None and now < self.idle_timer_at <= due:
            return
        wait = max(due - now, 1)
        if self.wake(wait, "WOWLAB_COMBATLOG_IDLE"):
            self.idle_timer_at = now + wait

    def turn(self, on: bool) -> bool:
        self.c.logging_combat(on)
        if self.log_state() == on:
            self.log_on = on
            return True
        if self.fighting:
            self.refused_in_combat += 1
            self.retry = True
            return False
        self.dormant = "the game did not switch the combat log when asked"
        self.say_once("refused", "the game did not switch the combat log when asked")
        return False

    def turn_on(self, why: str) -> None:
        if not self.turn(True):
            return
        self.mine = True
        self.own_seen = True
        self.idled = False
        self.active = self.c.now
        self.noted("on", why)
        self.arm_idle()
        if self.fighting:
            return
        self.advanced = self.advanced_on()
        if self.advanced is False:
            self.say_once("advanced", "Advanced Combat Logging is off")

    def want(self, trigger: str, where: str) -> str | None:
        if where != "world" or trigger == "idle":
            return "off"
        if self.idled:
            return None
        return "on"

    def check(self, trigger: str) -> None:
        on = self.log_state()
        if on is None:
            self.dormant = "LoggingCombat gave no state"
            self.say_once("no state", "the game did not say whether the combat log is on")
            return
        self.log_on = on
        if self.mine and on:
            self.own_seen = True
        elif self.mine and not on:
            self.mine = False
            if self.own_seen:
                self.hands_off = True
                self.say_once("hands off", "the combat log was turned off outside WowLab")
        where = self.place()
        if where != "world":
            self.idled = False
        if self.hands_off:
            return
        wanted = self.want(trigger, where)
        if wanted == "on" and not on:
            self.turn_on(WHY_WORLD)
        elif wanted == "on" and self.mine:
            self.arm_idle()
        elif wanted == "on":
            self.say_once("already on", "the combat log was already on, not turned on by WowLab")
        elif wanted == "off" and on and self.mine and self.turn(False):
            self.mine = False
            self.idled = trigger == "idle"
            why = {"instance": WHY_INSTANCE, "raid group": WHY_RAID}.get(where, WHY_IDLE)
            self.noted("off", why)

    def resume(self) -> None:
        if not self.idled or self.hands_off:
            return
        on = self.log_state()
        if on is False:
            self.turn_on(WHY_COMBAT)
        elif on is True:
            self.idled = False
            self.log_on = True

    def run(self, trigger: str) -> None:
        if trigger == "arrive":
            self.idled = False
            self.arrived_at = self.c.now
            self.waiting = True
            if self.wake(FIRST_DELAY, "WOWLAB_COMBATLOG_ENTER"):
                return
            trigger = "start"
        if trigger == "start":
            self.started = True
            trigger = "enter"
        if (
            trigger == "calm"
            and not self.started
            and self.arrived_at is not None
            and self.c.now - self.arrived_at >= FIRST_DELAY
        ):
            self.started = True
        if not self.started:
            return
        if trigger == "combat":
            self.fighting = True
            self.active = self.c.now
            self.resume()
        elif trigger == "calm":
            self.fighting = False
            self.active = self.c.now
            if self.retry:
                self.retry = False
                self.resume()
            if self.waiting:
                self.waiting = False
                self.check("enter")
            if self.mine:
                self.arm_idle()
        elif trigger == "enter":
            if self.fighting or self.in_combat_now():
                self.waiting = True
            else:
                self.waiting = False
                self.check("enter")
        elif trigger == "idle":
            if self.fighting or self.active is None:
                return
            if self.c.now - self.active >= self.idle_minutes * 60:
                self.check("idle")
            elif self.mine:
                self.arm_idle()

    def record(self) -> dict[str, object]:
        rec = {
            "idle_minutes": self.idle_minutes,
            "switches": self.switches,
            "last_at": self.last_at,
            "last_to": self.last_to,
            "last_why": self.last_why,
            "mine": self.mine,
            "paused": self.paused or None,
            "log_on": self.log_on,
            "hands_off": self.hands_off,
            "advanced": self.advanced,
            "dormant": self.dormant,
            "refused_in_combat": self.refused_in_combat or None,
        }
        return {k: v for k, v in rec.items() if v is not None}

    def gather(self, event: str) -> None:
        self.read_back()
        if self.dormant or self.paused:
            self.state = self.record()
            return
        if not self.c.has_logging:
            self.dormant = "LoggingCombat missing: the combat log is not switched"
            self.say_once("missing", "this game gives addons no combat-log switch")
            self.state = self.record()
            return
        trigger = TRIGGER.get(event)
        if trigger:
            self.run(trigger)
        self.state = self.record()

    # CombatLog.lua's commands ------------------------------------------------

    def command_switch(self, on: bool) -> None:
        """ns.CombatLogSwitch: writes the pause into the record only."""
        held = self.state
        if not isinstance(held, dict) or "absent" in held:
            return
        if on:
            held.pop("paused", None)
        else:
            held["paused"] = True

    # Scenario helpers ------------------------------------------------------

    def login(self) -> None:
        """Entering the world, then the 15 s until the first check."""
        self.fire("PLAYER_ENTERING_WORLD")
        self.advance(FIRST_DELAY)

    def reload(self) -> Switch:
        """A /reload: a new Lua state with the record the write carried (the
        carry keeps these keys); the log is unchanged and the old session's
        timers die with it."""
        held = self.state or {}
        keep = ("switches", "last_at", "last_to", "last_why", "idle_minutes", "mine", "paused")
        carried = {k: held[k] for k in keep if k in held and held[k] is not False}
        self.c.timers.clear()
        self.c.chat.clear()
        return Switch(self.c, carried)


def _switch(**client: object) -> Switch:
    return Switch(Client(**client))  # type: ignore[arg-type]


def test_m20_19_model_constants_match_the_source() -> None:
    text = COMBATLOG.read_text(encoding="utf-8")
    for name, value in (
        ("WHY_WORLD", WHY_WORLD),
        ("WHY_COMBAT", WHY_COMBAT),
        ("WHY_INSTANCE", WHY_INSTANCE),
        ("WHY_RAID", WHY_RAID),
        ("WHY_IDLE", WHY_IDLE),
    ):
        assert f'local {name} = "{value}"' in text, name
    rendered = g._render(g._tokens(COMBATLOG))
    for event, trigger in TRIGGER.items():
        assert f'{event} = "{trigger}"' in rendered, event


# ─── scenarios ──────────────────────────────────────────────────────────────


def test_m20_19_login_in_the_world_turns_the_log_on_after_the_window() -> None:
    s = _switch()
    s.fire("PLAYER_ENTERING_WORLD")
    s.fire("ZONE_CHANGED_NEW_AREA")  # inside the 15 s: nothing
    s.advance(FIRST_DELAY - 1)
    assert s.c.switch_calls == []
    s.advance(1)
    assert s.c.switch_calls == [True]
    assert s.c.log is True
    assert s.c.chat == [f"combat log on ({WHY_WORLD})."]
    assert s.state is not None
    assert s.state["switches"] == 1 and s.state["mine"] is True
    assert s.state["last_to"] == "on" and s.state["last_why"] == WHY_WORLD


def test_m20_19_skip_in_the_window_stops_the_first_call() -> None:
    s = _switch()
    s.fire("PLAYER_ENTERING_WORLD")
    s.off = True  # `/wowlab combatlog off` (the section's skip) in the 15 s
    s.advance(60)
    assert s.c.switch_calls == []


def test_m20_19_zoning_into_an_instance_turns_its_own_log_off() -> None:
    s = _switch()
    s.login()
    s.c.instance = True
    s.fire("PLAYER_ENTERING_WORLD")
    s.advance(FIRST_DELAY)
    assert s.c.switch_calls == [True, False]
    assert s.c.chat[-1] == f"combat log off ({WHY_INSTANCE})."
    # And on again in the open world after the next loading screen.
    s.c.instance = False
    s.login()
    assert s.c.switch_calls == [True, False, True]
    assert s.c.log is True


def test_m20_19_a_zone_change_into_an_instance_turns_it_off_at_once() -> None:
    s = _switch()
    s.login()
    s.c.instance = True
    s.fire("ZONE_CHANGED_NEW_AREA")
    assert s.c.log is False
    assert s.c.chat[-1] == f"combat log off ({WHY_INSTANCE})."


def test_m20_19_a_group_that_becomes_a_raid_turns_it_off() -> None:
    s = _switch()
    s.login()
    s.c.raid = True
    s.fire("GROUP_ROSTER_UPDATE")
    assert s.c.log is False
    assert s.c.chat[-1] == f"combat log off ({WHY_RAID})."
    s.c.raid = False
    s.fire("GROUP_ROSTER_UPDATE")
    assert s.c.log is True
    assert s.state is not None and s.state["switches"] == 3


def test_m20_19_idle_turns_it_off_and_the_next_fight_back_on() -> None:
    s = _switch()
    s.login()
    s.advance(IDLE_MINUTES * 60 - 1)
    assert s.c.log is True
    s.advance(1)
    assert s.c.log is False
    assert s.c.chat[-1] == f"combat log off ({WHY_IDLE})."
    # A zone change in the open world does not turn it back on.
    s.fire("ZONE_CHANGED_NEW_AREA")
    assert s.c.log is False
    # The next fight does, with nothing else asked in combat.
    s.fire("PLAYER_REGEN_DISABLED")
    assert s.c.log is True
    assert s.c.chat[-1] == f"combat log on ({WHY_COMBAT})."
    assert s.c.calls_in_combat == ["LoggingCombat", "LoggingCombat(true)", "LoggingCombat"]


def test_m20_19_a_fight_restarts_the_idle_time() -> None:
    s = _switch()
    s.login()
    s.advance(9 * 60)
    s.fire("PLAYER_REGEN_DISABLED")
    s.advance(60)
    s.fire("PLAYER_REGEN_ENABLED")
    s.advance(9 * 60)
    assert s.c.log is True  # 9 min after the fight ended
    s.advance(60)
    assert s.c.log is False


def test_m20_19_a_fight_with_the_log_on_asks_the_client_nothing() -> None:
    s = _switch()
    s.login()
    s.fire("PLAYER_REGEN_DISABLED")
    s.c.instance = True  # a zone change in combat waits for the end of the fight
    s.fire("ZONE_CHANGED_NEW_AREA")
    s.fire("GROUP_ROSTER_UPDATE")
    s.advance(IDLE_MINUTES * 60 + 5)  # the idle timer ends in combat: dropped
    assert s.c.calls_in_combat == []
    assert s.c.log is True
    s.fire("PLAYER_REGEN_ENABLED")
    assert s.c.log is False
    assert s.c.chat[-1] == f"combat log off ({WHY_INSTANCE})."


def test_m20_19_a_log_already_on_is_the_players_and_never_turned_off() -> None:
    s = _switch(log=True)
    s.login()
    assert s.c.switch_calls == []
    assert s.c.chat == ["the combat log was already on, not turned on by WowLab"]
    s.c.instance = True
    s.fire("ZONE_CHANGED_NEW_AREA")
    s.c.instance = False
    s.c.raid = True
    s.fire("GROUP_ROSTER_UPDATE")
    s.c.raid = False
    s.fire("GROUP_ROSTER_UPDATE")
    s.advance(3 * IDLE_MINUTES * 60)
    assert s.c.switch_calls == []
    assert s.c.log is True
    assert s.c.chat == ["the combat log was already on, not turned on by WowLab"]  # once
    assert s.state is not None and s.state["mine"] is False


def test_m20_19_a_log_turned_off_outside_wowlab_stays_off() -> None:
    s = _switch()
    s.login()
    s.c.log = False  # /combatlog by hand, or another addon
    s.fire("ZONE_CHANGED_NEW_AREA")
    s.fire("PLAYER_REGEN_DISABLED")
    s.fire("PLAYER_REGEN_ENABLED")
    s.fire("ZONE_CHANGED_NEW_AREA")
    assert s.c.switch_calls == [True]
    assert s.c.chat[-1] == "the combat log was turned off outside WowLab"
    assert s.state is not None and s.state["hands_off"] is True


def test_m20_19_the_call_missing_leaves_it_dormant_with_one_line() -> None:
    s = _switch(has_logging=False)
    s.login()
    s.fire("ZONE_CHANGED_NEW_AREA")
    s.fire("PLAYER_REGEN_DISABLED")
    assert s.c.switch_calls == []
    assert s.c.chat == ["this game gives addons no combat-log switch"]
    assert s.state is not None
    assert s.state["dormant"] == "LoggingCombat missing: the combat log is not switched"


def test_m20_19_a_refused_switch_leaves_it_dormant() -> None:
    s = _switch(obeys=False)
    s.login()
    s.fire("ZONE_CHANGED_NEW_AREA")
    assert s.c.switch_calls == [True]  # asked once, then never again
    assert s.c.chat == ["the game did not switch the combat log when asked"]
    assert s.state is not None
    assert s.state["dormant"] == "the game did not switch the combat log when asked"


def test_m20_19_no_state_from_the_client_leaves_it_dormant() -> None:
    s = _switch(answers=False)
    s.login()
    assert s.c.switch_calls == []
    assert s.state is not None and s.state["dormant"] == "LoggingCombat gave no state"


def test_m20_19_advanced_combat_logging_off_is_said_once() -> None:
    s = _switch(acl="0")
    s.login()
    s.advance(IDLE_MINUTES * 60)
    s.fire("PLAYER_REGEN_DISABLED")
    s.fire("PLAYER_REGEN_ENABLED")
    s.c.raid = True
    s.fire("GROUP_ROSTER_UPDATE")
    s.c.raid = False
    s.fire("GROUP_ROSTER_UPDATE")
    assert s.c.chat.count("Advanced Combat Logging is off") == 1
    assert s.state is not None and s.state["advanced"] is False


def test_m20_19_a_reload_keeps_the_count_and_its_own_log() -> None:
    s = _switch()
    s.login()
    s.c.instance = True
    s.fire("ZONE_CHANGED_NEW_AREA")
    s.c.instance = False
    s.fire("ZONE_CHANGED_NEW_AREA")
    # /reload: the log stays on [verify]; the carried record says it is the
    # addon's, and the old session's timers are gone.
    again = s.reload()
    again.login()
    assert again.c.timers, "no idle timer after the reload"
    again.c.instance = True
    again.fire("ZONE_CHANGED_NEW_AREA")
    assert again.state is not None
    assert again.state["switches"] == 4
    assert again.c.chat == [f"combat log off ({WHY_INSTANCE})."]


def test_m20_19_after_a_reload_its_own_log_still_idles_off() -> None:
    s = _switch()
    s.login()
    again = s.reload()
    again.login()
    again.advance(IDLE_MINUTES * 60 - 1)
    assert again.c.log is True
    again.advance(1)
    assert again.c.log is False


def test_m20_19_a_longer_idle_time_set_mid_session_still_idles_off() -> None:
    s = _switch()
    s.login()
    s.advance(60)
    assert s.state is not None
    s.state["idle_minutes"] = 30  # /wowlab combatlog idle 30
    s.advance(29 * 60 - 1)
    assert s.c.log is True
    s.advance(1)
    assert s.c.log is False
    assert len(s.c.timers) <= 1


def test_m20_19_zone_changes_do_not_pile_up_idle_timers() -> None:
    s = _switch()
    s.login()
    for _ in range(20):
        s.fire("ZONE_CHANGED_NEW_AREA")
    assert len([t for t in s.c.timers if t[1] == "WOWLAB_COMBATLOG_IDLE"]) == 1


def test_m20_19_a_dormant_session_keeps_what_was_carried() -> None:
    carried: dict[str, object] = {
        "switches": 7,
        "last_to": "on",
        "last_why": WHY_WORLD,
        "idle_minutes": 25,
        "mine": True,
    }
    s = Switch(Client(has_logging=False), carried)
    s.login()
    assert s.state is not None
    assert s.state["dormant"] == "LoggingCombat missing: the combat log is not switched"
    for key, value in carried.items():
        assert s.state[key] == value, key
    again = s.reload()
    assert again.state is not None and again.state["switches"] == 7


def test_m20_19_off_then_on_keeps_mine_and_the_count() -> None:
    s = _switch()
    s.login()
    s.command_switch(False)  # /wowlab combatlog off
    s.c.instance = True
    s.fire("ZONE_CHANGED_NEW_AREA")
    assert s.c.log is True  # paused: left as it is
    assert s.state is not None and s.state["paused"] is True and s.state["mine"] is True
    again = s.reload()
    again.login()
    assert again.c.switch_calls == [True]
    again.command_switch(True)  # /wowlab combatlog on
    again.fire("ZONE_CHANGED_NEW_AREA")
    assert again.c.log is False  # still the addon's log: turned off in the instance
    assert again.state is not None and again.state["switches"] == 2


def test_m20_19_a_refusal_in_combat_is_tried_again_after_the_fight() -> None:
    s = _switch()
    s.login()
    s.advance(IDLE_MINUTES * 60)
    assert s.c.log is False
    s.c.obeys = False
    s.fire("PLAYER_REGEN_DISABLED")
    assert s.state is not None and s.state["refused_in_combat"] == 1
    assert "dormant" not in s.state
    s.c.obeys = True
    s.fire("PLAYER_REGEN_ENABLED")
    assert s.c.log is True
    assert s.c.chat[-1] == f"combat log on ({WHY_COMBAT})."


def test_m20_19_a_restart_ends_the_log_without_hands_off() -> None:
    """The carried record says the log was the addon's, but the client was
    restarted: the log is off at the first check. Not the player's hand."""
    s = Switch(Client(log=False), {"switches": 2, "mine": True})
    s.login()
    assert s.c.log is True
    assert s.state is not None and s.state["hands_off"] is False
    assert s.state["switches"] == 3


def test_m20_19_without_a_timer_the_first_check_runs_at_once() -> None:
    s = _switch(has_timer=False)
    s.fire("PLAYER_ENTERING_WORLD")
    assert s.c.log is True


def test_m20_19_a_fight_through_the_window_checks_when_it_ends() -> None:
    s = _switch()
    s.fire("PLAYER_ENTERING_WORLD")
    s.fire("PLAYER_REGEN_DISABLED")
    s.advance(FIRST_DELAY + 5)  # the first check's timer ends in combat: dropped
    assert s.c.switch_calls == []
    s.fire("PLAYER_REGEN_ENABLED")
    assert s.c.log is True


def test_m20_19_a_loading_screen_in_combat_checks_when_the_fight_ends() -> None:
    """After login: a loading screen into an instance whose 15 s timer ends
    in combat still turns the addon's log off once the fight is over."""
    s = _switch()
    s.login()
    s.c.instance = True
    s.fire("PLAYER_ENTERING_WORLD")
    s.fire("PLAYER_REGEN_DISABLED")
    s.advance(FIRST_DELAY + 5)
    assert s.c.log is True and s.c.calls_in_combat == []
    s.fire("PLAYER_REGEN_ENABLED")
    assert s.c.log is False
    assert s.c.chat[-1] == f"combat log off ({WHY_INSTANCE})."


@pytest.mark.parametrize("minutes", [1, 30, 120])
def test_m20_19_the_idle_time_is_the_saved_setting(minutes: int) -> None:
    s = Switch(Client(), {"idle_minutes": minutes})
    s.login()
    s.advance(minutes * 60 - 1)
    assert s.c.log is True
    s.advance(1)
    assert s.c.log is False


# ─── what the addon prints ──────────────────────────────────────────────────


def test_m20_19_every_chat_line_is_in_the_source() -> None:
    """The model's chat lines are the start of the source's lines."""
    text = COMBATLOG.read_text(encoding="utf-8")
    for line in (
        "the combat log was already on, not turned on by WowLab; WowLab leaves it on and will "
        "not turn it off.",
        "the combat log was turned off outside WowLab; WowLab leaves it off until the next "
        "/reload or login.",
        "this game gives addons no combat-log switch, so WowLab does not turn the combat log",
        "the game did not switch the combat log when asked, so WowLab leaves it alone",
        "Advanced Combat Logging is off; wowlab reads more from the log with it on.",
    ):
        assert line in text, line
    # Neither the player's hand nor a menu path is claimed.
    for claim in ("you turned", "as you set it", "game options"):
        assert claim not in text.split("local _, ns = ...")[1], claim
    assert 'ns.Say("combat log " .. to .. " (" .. why .. ").")' in text
