"""M21-11: the lab-addon's fixes from the launch-build API list.

Nothing here runs Lua (L3). Two halves, as in test_m20_19_combatlog.py:

1. Source pins. Each function M21-11 adds or changes is pinned token for
   token, so an edit to one fails here until the pin and the model below are
   changed with it.
2. Stub clients. Each pinned function has a line-for-line Python model, and
   a stub client stands in for the game: what the Constants table, the
   global, Enum.BindingSet, the scoreboard calls, C_ChatInfo.IsLoggingCombat,
   LoggingCombat, the setting and the realm calls give. Each case the ticket
   names is played through the model.

The model proves the code as pinned. What the real client gives is not yet
confirmed for any of the new reads ([verify] in lab/addon/README.md): that
Constants.MacroConsts and Enum.BindingSet are readable, that
C_ChatInfo.IsLoggingCombat answers addons, and that on Forever the second
return of UnitFullName("player") is the second name (the owner's batched
check, M22-O1).

CONSTRUCTED INPUT: every stub value below is invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import test_lab_addon as g

ACTIONS = g.ADDON / "Actions.lua"
COMBATLOG = g.ADDON / "CombatLog.lua"
CREW = g.ADDON / "Crew.lua"
IDENTITY = g.ADDON / "Identity.lua"
CUSTOMIZATION = g.ADDON / "Customization.lua"


def _flow(path: Path) -> g._Flow:
    return g._Flow(path, g._tokens(path))


def _text(path: Path) -> str:
    return g._render(g._tokens(path))


# ─── 1. pins ────────────────────────────────────────────────────────────────

SLOT_COUNT = (
    "function slotCount ( value ) local count = ns . Number ( value ) "
    "if count and count >= 1 and count % 1 == 0 then return count end "
    "return nil end"
)
ACCOUNT_MACRO_SLOTS = (
    "function accountMacroSlots ( ) "
    'local consts = type ( Constants ) == "table" and Constants . MacroConsts or nil '
    'local fromTable = type ( consts ) == "table" and slotCount ( consts . MAX_ACCOUNT_MACROS ) '
    "or nil "
    'if fromTable then return fromTable , "MacroConsts" end '
    "local fromGlobal = slotCount ( MAX_ACCOUNT_MACROS ) "
    'if fromGlobal then return fromGlobal , "MAX_ACCOUNT_MACROS" end '
    'return ACCOUNT_MACRO_SLOTS , "fallback" end'
)
MACROS_USE = "local perAccount , slotsFrom = accountMacroSlots ( )"
BINDING_SETS = (
    "function bindingSets ( ) "
    'local sets = type ( Enum ) == "table" and Enum . BindingSet or nil '
    'if type ( sets ) ~= "table" then return nil end '
    "local out , members = { } , 0 "
    "for name , value in pairs ( sets ) do "
    'local key = type ( name ) == "string" and enumName ( tostring ( name ) ) or nil '
    'if key and type ( value ) == "number" and members < MAX_ENUM_MEMBERS then '
    "out [ key ] = ns . Number ( value ) members = members + 1 end end return out end"
)
BINDINGS_USE = "record . binding_sets = bindingSets ( )"
SCORE_FALLBACK = (
    'if not read and type ( packed [ 1 ] ) ~= "nil" then '
    "name = packed [ 1 ] side = packed [ 6 ] end"
)
IS_LOGGING = 'local isLogging = ns . Fn ( C_ChatInfo , "IsLoggingCombat" )'
LOG_STATE = (
    f"function logState ( ) stateFrom = nil {IS_LOGGING} "
    "if isLogging then local okIs , onIs = pcall ( isLogging ) "
    'if okIs and type ( onIs ) == "boolean" then stateFrom = "IsLoggingCombat" '
    "return onIs end end "
    "local ok , on = pcall ( LoggingCombat ) "
    'if ok and type ( on ) == "boolean" then stateFrom = "LoggingCombat" return on end '
    "return nil end"
)
ADVANCED_ON = (
    f"function advancedOn ( ) advancedFrom = nil {IS_LOGGING} "
    "if isLogging then local okIs , _ , flag = pcall ( isLogging ) "
    'if okIs and type ( flag ) == "boolean" then advancedFrom = "IsLoggingCombat" '
    "return flag end end "
    'local get = ns . Fn ( C_CVar , "GetCVar" ) if not get then return nil end '
    'local value = ns . Call ( get , "advancedCombatLogging" ) '
    'if value == "1" then advancedFrom = "setting" return true '
    'elseif value == "0" then advancedFrom = "setting" return false end return nil end'
)
RECORD_SOURCES = (
    "state_from = ns . String ( stateFrom ) , advanced_from = ns . String ( advancedFrom ) ,"
)
HOME_REALM = (
    "function homeRealm ( ) if not crewOn ( ) then return end "
    "if crewBlocked ( ) then return end "
    "if homeFound ~= nil then return homeFound , false end "
    "local hidden = false "
    'if type ( GetNormalizedRealmName ) == "function" then '
    "local normalized = GetNormalizedRealmName ( ) "
    "if secret ( normalized ) then hidden = true end "
    'if not secret ( normalized ) and type ( normalized ) == "string" then '
    'local bare = string . gsub ( normalized , "[%s%-]" , "" ) '
    'if bare ~= "" then homeFound = bare return homeFound , false end end end '
    "return nil , hidden end"
)
ADD_NO_SECOND = (
    "function addNoSecond ( ) if not crewOn ( ) then return end "
    "if crewBlocked ( ) then return end "
    "WowLabCrewDB . no_second = ( ns . Number ( WowLabCrewDB . no_second ) or 0 ) + 1 end"
)
MEMBER_NO_SECOND = (
    "local home , hidden = homeRealm ( ) if home == nil and hidden then addSkipped ( ) "
    "return end if home == nil then addNoSecond ( ) end"
)
NO_SECOND_LINE = (
    "local noSecond = ns . Number ( WowLabCrewDB . no_second ) or 0 if noSecond > 0 then "
    'missing = missing .. " " .. noSecond .. " players came back with no second name or '
    'realm and were skipped (not yet confirmed)." end'
)
ROWS_UNKEYED = (
    "local unkeyed = 0",
    'elseif home ~= nil then key = name .. "-" .. home else unkeyed = unkeyed + 1 end',
    "if unkeyed > 0 then record . rows_unkeyed = unkeyed end",
)
IDENTITY_SECOND_NAME = (
    "if not record . realm then "
    'if type ( UnitFullName ) ~= "function" then '
    'realmWhy = realmWhy .. "; the client has no UnitFullName function" '
    'else local second = select ( 2 , UnitFullName ( "player" ) ) '
    "local keptSecond , whySecond = ownText ( second , "
    '"the second return of UnitFullName" ) '
    "if keptSecond then record . second_name = ns . String ( keptSecond ) "
    'record . second_name_from = "full_name" '
    'realmWhy = realmWhy .. "; the second return of UnitFullName, kept as `second_name`" '
    '.. " (taken to be the second name, not yet confirmed)" '
    'else realmWhy = realmWhy .. "; " .. ns . String ( whySecond ) end end end'
)


def pin_problems(
    actions: Path = ACTIONS,
    combatlog: Path = COMBATLOG,
    crew: Path = CREW,
    identity: Path = IDENTITY,
) -> list[str]:
    """Why the sources (or edited copies) are not the pinned M21-11 code."""
    problems: list[str] = []
    flow = _flow(actions)
    text = g._render(flow.tokens)
    for label, pin in (
        ("slotCount", SLOT_COUNT),
        ("accountMacroSlots", ACCOUNT_MACRO_SLOTS),
        ("bindingSets", BINDING_SETS),
    ):
        if g._body(flow, label) != pin:
            problems.append(f"Actions.lua: {label} is not the pinned one")
    if text.count(MACROS_USE) != 1 or text.count("accountMacroSlots ( )") != 2:
        problems.append("Actions.lua: the macros gather does not take the pinned slot count")
    # The global is asked only through slotCount, inside accountMacroSlots.
    if text.count("MAX_ACCOUNT_MACROS )") != 2 or "ns . Number ( MAX_ACCOUNT_MACROS )" in text:
        problems.append("Actions.lua: MAX_ACCOUNT_MACROS is read outside accountMacroSlots")
    if text.count(BINDINGS_USE) != 1 or text.count("bindingSets ( )") != 2:
        problems.append("Actions.lua: the bindings gather does not record the pinned enum")
    for name in ("ACCOUNT_BINDINGS", "CHARACTER_BINDINGS"):
        if name in text:
            problems.append(f"Actions.lua: still asks for {name}")
    flow = _flow(combatlog)
    text = g._render(flow.tokens)
    for label, pin in (("logState", LOG_STATE), ("advancedOn", ADVANCED_ON)):
        if g._body(flow, label) != pin:
            problems.append(f"CombatLog.lua: {label} is not the pinned one")
    if RECORD_SOURCES not in g._body(flow, "record"):
        problems.append("CombatLog.lua: the record does not name the sources")
    if text.count(IS_LOGGING) != 2 or text.count("C_ChatInfo") != 2:
        problems.append("CombatLog.lua: IsLoggingCombat is reached other than by the two reads")
    for name in ("stateFrom", "advancedFrom"):
        sets = len(re.findall(rf"(?<![\w.]){name} = ", text))
        if sets != {"stateFrom": 4, "advancedFrom": 5}[name]:
            problems.append(f"CombatLog.lua: {name} is set other than by its read")
    flow = _flow(crew)
    text = g._render(flow.tokens)
    if g._body(flow, "homeRealm") != HOME_REALM:
        problems.append("Crew.lua: homeRealm is not the pinned one")
    if "UnitFullName" in text:
        problems.append("Crew.lua: UnitFullName is asked again")
    if text.count(SCORE_FALLBACK) != 1 or text.count("side = packed [ 6 ]") != 1:
        problems.append("Crew.lua: the scoreboard probe takes the sixth value without a row")
    # Fix round 1 (domain review): a skip for want of a home realm is
    # counted, on the book and on the match record, and said only then.
    if g._body(flow, "addNoSecond") != ADD_NO_SECOND or text.count("addNoSecond ( )") != 2:
        problems.append("Crew.lua: a member skipped for no second part is not counted once")
    if MEMBER_NO_SECOND not in g._body(flow, "readMember"):
        problems.append("Crew.lua: readMember does not count a member skipped for no second part")
    if NO_SECOND_LINE not in g._body(flow, "fieldsMissing"):
        problems.append("Crew.lua: the skipped line is not said only after a skip")
    if "home realm" in g._body(flow, "fieldsMissing"):
        problems.append("Crew.lua: the skipped line still speaks of a home realm")
    for piece in ROWS_UNKEYED:
        if text.count(piece) != 1:
            problems.append(f"Crew.lua: the match record's unkeyed rows: {piece!r}")
    text = _text(identity)
    if text.count(IDENTITY_SECOND_NAME) != 1:
        problems.append("Identity.lua: the second name is not the pinned read")
    if '"full_name"' in text.replace('record . second_name_from = "full_name"', ""):
        problems.append('Identity.lua: "full_name" is written other than as second_name_from')
    if text.count('UnitFullName ( "player" )') != 1:
        problems.append("Identity.lua: UnitFullName is asked other than once")
    return problems


def test_m21_11_the_changes_are_the_pinned_ones() -> None:
    assert pin_problems() == []


# Constructed: each edit undoes one fix (or breaks its pin) and must fail.
EDITS: dict[str, tuple[Path, str, str]] = {
    "macros: the client's table never read": (
        ACTIONS,
        '    local consts = type(Constants) == "table" and Constants.MacroConsts or nil\n',
        "    local consts = nil\n",
    ),
    "macros: any number taken": (
        ACTIONS,
        "    if count and count >= 1 and count % 1 == 0 then\n",
        "    if count then\n",
    ),
    "bindings: the global asked again": (
        ACTIONS,
        "        record.binding_sets = bindingSets()\n",
        "        record.binding_sets = bindingSets()\n"
        "        record.account_bindings = ns.Number(ACCOUNT_BINDINGS)\n",
    ),
    "scoreboard: the sixth value without a row": (
        CREW,
        '            if not read and type(packed[1]) ~= "nil" then\n',
        "            if not read then\n",
    ),
    "crew: the full name as the home realm": (
        CREW,
        "    return nil, hidden\nend\n",
        '    local _, fullRealm = UnitFullName("player")\n    return fullRealm, hidden\nend\n',
    ),
    "combatlog: LoggingCombat first": (
        COMBATLOG,
        '    local isLogging = ns.Fn(C_ChatInfo, "IsLoggingCombat")\n'
        "    if isLogging then\n        local okIs, onIs",
        "    local isLogging = nil\n    if isLogging then\n        local okIs, onIs",
    ),
    "combatlog: no source in the record": (
        COMBATLOG,
        "        state_from = ns.String(stateFrom),\n",
        "",
    ),
    "identity: the second name as the realm": (
        IDENTITY,
        "                    record.second_name = ns.String(keptSecond)\n",
        "                    record.realm = ns.String(keptSecond)\n",
    ),
}


@pytest.mark.parametrize("tag", sorted(EDITS))
def test_m21_11_constructed_edits_fail_the_pins(tag: str, tmp_path: Path) -> None:
    path, old, new = EDITS[tag]
    real = path.read_text(encoding="utf-8")
    assert real.count(old) == 1, old
    edited = tmp_path / path.name
    edited.write_text(real.replace(old, new, 1), encoding="utf-8")
    kwargs = {
        ACTIONS: "actions",
        COMBATLOG: "combatlog",
        CREW: "crew",
        IDENTITY: "identity",
    }
    assert pin_problems(**{kwargs[path]: edited}), tag


def test_m21_11_customization_no_longer_registers_barber_shop_success() -> None:
    assert "BARBER_SHOP_SUCCESS" not in _text(CUSTOMIZATION)
    # The control stays: whether it gets an entry still shows a refusal.
    assert '"WOWLAB_CONTROL_NOT_A_REAL_EVENT"' in _text(CUSTOMIZATION)


def test_m21_11_the_one_chat_info_read_is_the_only_one() -> None:
    """C_ChatInfo stays a refused table (its sends); CombatLog.lua may name
    it only in the IsLoggingCombat lookup (twice: the state and the flag),
    and no other file may."""
    assert g._forbidden_api_problems(COMBATLOG, g._tokens(COMBATLOG)) == []
    for path in g.SOURCES:
        if path != COMBATLOG:
            assert "C_ChatInfo" not in _text(path), path.name


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # A second lookup of the table, of a call that is not the read.
        (
            "local function logState()\n",
            'local function chatChannels()\n    return ns.Fn(C_ChatInfo, "GetChannelList")\n'
            "end\n\nlocal function logState()\n",
        ),
        # The same table, another field.
        (
            '    local isLogging = ns.Fn(C_ChatInfo, "IsLoggingCombat")\n    if isLogging then\n'
            "        local okIs, onIs",
            '    local isLogging = ns.Fn(C_ChatInfo, "GetChannelList")\n    if isLogging then\n'
            "        local okIs, onIs",
        ),
        # A direct reach, not through ns.Fn.
        (
            '    local isLogging = ns.Fn(C_ChatInfo, "IsLoggingCombat")\n    if isLogging then\n'
            "        local okIs, onIs",
            "    local isLogging = C_ChatInfo.IsLoggingCombat\n    if isLogging then\n"
            "        local okIs, onIs",
        ),
        # Fix round 1 (security review): an index by string.
        (
            '    local isLogging = ns.Fn(C_ChatInfo, "IsLoggingCombat")\n    if isLogging then\n'
            "        local okIs, onIs",
            '    local isLogging = C_ChatInfo["SendAddonMessage"]\n    if isLogging then\n'
            "        local okIs, onIs",
        ),
        # The table bound to a local.
        (
            "local function logState()\n",
            "local function logState()\n    local c = C_ChatInfo\n",
        ),
        # The field name built from two strings.
        (
            '    local isLogging = ns.Fn(C_ChatInfo, "IsLoggingCombat")\n    if isLogging then\n'
            "        local okIs, onIs",
            '    local isLogging = ns.Fn(C_ChatInfo, "IsLogging" .. "Combat")\n'
            "    if isLogging then\n        local okIs, onIs",
        ),
        # The same text through another table's `ns`.
        (
            '    local isLogging = ns.Fn(C_ChatInfo, "IsLoggingCombat")\n    if isLogging then\n'
            "        local okIs, onIs",
            '    local isLogging = t.ns.Fn(C_ChatInfo, "IsLoggingCombat")\n    if isLogging then\n'
            "        local okIs, onIs",
        ),
    ],
    ids=[
        "constructed-second-use",
        "constructed-other-field",
        "constructed-direct",
        "constructed-string-index",
        "constructed-local-alias",
        "constructed-joined-name",
        "constructed-field-ns",
    ],
)
def test_m21_11_constructed_other_chat_info_uses_stay_refused(
    old: str, new: str, tmp_path: Path
) -> None:
    real = COMBATLOG.read_text(encoding="utf-8")
    assert real.count(old) == 1, old
    edited = tmp_path / COMBATLOG.name
    edited.write_text(real.replace(old, new, 1), encoding="utf-8")
    assert any("C_ChatInfo" in p for p in g._forbidden_api_problems(edited, g._tokens(edited)))


def test_m21_11_constructed_the_read_in_another_file_is_refused(tmp_path: Path) -> None:
    real = ACTIONS.read_text(encoding="utf-8")
    edited = tmp_path / ACTIONS.name
    edited.write_text(
        real.replace(
            "local _, ns = ...\n",
            'local _, ns = ...\nlocal peek = ns.Fn(C_ChatInfo, "IsLoggingCombat")\n',
            1,
        ),
        encoding="utf-8",
    )
    assert g._forbidden_api_problems(edited, g._tokens(edited))


# ─── 2. stub clients and the models ─────────────────────────────────────────

NIL = None
ACCOUNT_MACRO_SLOTS_VALUE = 120
MAX_ENUM_MEMBERS = 64


def _lua_number(value: Any) -> float | int | None:
    """ns.Number: a Lua number (not a boolean), else nil."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def slot_count(value: Any) -> float | int | None:
    count = _lua_number(value)
    if count is not None and count >= 1 and count % 1 == 0:
        return count
    return None


@dataclass
class MacroClient:
    """`constants` is the Constants global (a dict, or anything else);
    `global_slots` the global MAX_ACCOUNT_MACROS."""

    constants: Any = NIL
    global_slots: Any = NIL


def account_macro_slots(c: MacroClient) -> tuple[float | int, str]:
    consts = c.constants.get("MacroConsts") if isinstance(c.constants, dict) else None
    from_table = slot_count(consts.get("MAX_ACCOUNT_MACROS")) if isinstance(consts, dict) else None
    if from_table:
        return from_table, "MacroConsts"
    from_global = slot_count(c.global_slots)
    if from_global:
        return from_global, "MAX_ACCOUNT_MACROS"
    return ACCOUNT_MACRO_SLOTS_VALUE, "fallback"


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        # The launch build: the table, no global (the owner's run, 71037).
        (MacroClient({"MacroConsts": {"MAX_ACCOUNT_MACROS": 120}}), (120, "MacroConsts")),
        # The table wins over the global.
        (MacroClient({"MacroConsts": {"MAX_ACCOUNT_MACROS": 72}}, 36), (72, "MacroConsts")),
        # No table: the global.
        (MacroClient(NIL, 36), (36, "MAX_ACCOUNT_MACROS")),
        (MacroClient({"MacroConsts": NIL}, 36), (36, "MAX_ACCOUNT_MACROS")),
        (MacroClient({"MacroConsts": "x"}, 36), (36, "MAX_ACCOUNT_MACROS")),
        # Neither: the addon's own 120.
        (MacroClient(), (120, "fallback")),
        (MacroClient("not a table"), (120, "fallback")),
        # Not a whole number of at least 1: not taken.
        (MacroClient({"MacroConsts": {"MAX_ACCOUNT_MACROS": 0}}), (120, "fallback")),
        (MacroClient({"MacroConsts": {"MAX_ACCOUNT_MACROS": -5}}, 0), (120, "fallback")),
        (MacroClient({"MacroConsts": {"MAX_ACCOUNT_MACROS": 120.5}}), (120, "fallback")),
        (MacroClient({"MacroConsts": {"MAX_ACCOUNT_MACROS": "120"}}), (120, "fallback")),
        (MacroClient({"MacroConsts": {"MAX_ACCOUNT_MACROS": True}}), (120, "fallback")),
    ],
    ids=[
        "constructed-table",
        "constructed-table-over-global",
        "constructed-no-constants",
        "constructed-no-macro-consts",
        "constructed-macro-consts-not-a-table",
        "constructed-neither",
        "constructed-constants-not-a-table",
        "constructed-zero",
        "constructed-negative-and-zero-global",
        "constructed-fraction",
        "constructed-text",
        "constructed-boolean",
    ],
)
def test_m21_11_macro_slots(client: MacroClient, expected: tuple[int, str]) -> None:
    assert account_macro_slots(client) == expected


def _enum_name(value: Any) -> str | None:
    if isinstance(value, str) and len(value) <= 64 and re.match(r"^[A-Za-z][A-Za-z0-9_]*$", value):
        return value
    return None


def binding_sets(enum: Any) -> dict[str, Any] | None:
    """bindingSets over a stub `Enum` global (pairs order: the dict's)."""
    sets = enum.get("BindingSet") if isinstance(enum, dict) else None
    if not isinstance(sets, dict):
        return None
    out: dict[str, Any] = {}
    members = 0
    for name, value in sets.items():
        key = _enum_name(name) if isinstance(name, str) else None
        if key and _lua_number(value) is not None and members < MAX_ENUM_MEMBERS:
            out[key] = value
            members += 1
    return out


def test_m21_11_binding_sets_as_the_client_has_them() -> None:
    client = {"BindingSet": {"Default": 0, "Account": 1, "Character": 2, "Current": 3}}
    assert binding_sets(client) == {"Default": 0, "Account": 1, "Character": 2, "Current": 3}


@pytest.mark.parametrize(
    ("enum", "expected"),
    [
        (NIL, None),
        ({}, None),
        ({"BindingSet": "x"}, None),
        ({"BindingSet": {}}, {}),
        # Kept: a name and a number; left out: anything else.
        ({"BindingSet": {"Account": 1, 7: 2, "bad name": 3, "Text": "4"}}, {"Account": 1}),
        (
            {"BindingSet": {f"M{n}": n for n in range(MAX_ENUM_MEMBERS + 5)}},
            {f"M{n}": n for n in range(MAX_ENUM_MEMBERS)},
        ),
    ],
    ids=[
        "constructed-no-enum",
        "constructed-no-binding-set",
        "constructed-not-a-table",
        "constructed-empty",
        "constructed-filtered",
        "constructed-capped",
    ],
)
def test_m21_11_constructed_binding_sets_bounds(enum: Any, expected: Any) -> None:
    assert binding_sets(enum) == expected


@dataclass
class ScoreClient:
    """`row` is what C_PvP.GetScoreInfo(1) gives (None: no row, as at 71037
    when the scoreboard has none); `legacy` what GetBattlefieldScore(1)
    gives (None: no such function)."""

    row: dict[str, Any] | None = None
    legacy: tuple[Any, ...] | None = None


def first_side(c: ScoreClient) -> int | None:
    """The `first_side` of one pvpSnapshot run (its name and side lines)."""
    read = False
    side: Any = None
    if c.row is not None:
        read = True
        side = c.row.get("faction")
    if c.legacy is not None:
        packed = list(c.legacy) + [None] * 6
        if not read and packed[0] is not None:
            side = packed[5]
    if isinstance(side, int) and not isinstance(side, bool):
        return side
    return None


def shim(row: dict[str, Any] | None) -> tuple[Any, ...]:
    """Blizzard_Deprecated's 12.0.1 GetBattlefieldScore at 71037, as the
    M21-09 finding reads it: it answers even for a row C_PvP.GetScoreInfo
    does not have, with a nil name and faction 0."""
    if row is None:
        return (None, 0, 0, 0, 0, 0)
    return (row.get("name"), 0, 0, 0, 0, row.get("faction", 0))


@pytest.mark.parametrize(
    ("client", "side"),
    [
        # The case M21-11 fixes: no row, the wrapper's 0 is not a side.
        (ScoreClient(None, shim(None)), None),
        # A row: its own faction, the wrapper never asked for it.
        (ScoreClient({"name": "Invented", "faction": 1}, shim({"faction": 1})), 1),
        (ScoreClient({"name": "Invented", "faction": 0}, None), 0),
        # An older client with no C_PvP row but a real legacy row.
        (ScoreClient(None, ("Invented-Realm", 3, 1, 0, 0, 1)), 1),
        # No row and no legacy call.
        (ScoreClient(None, None), None),
    ],
    ids=[
        "constructed-no-row-wrapper",
        "constructed-row",
        "constructed-row-side-0",
        "constructed-legacy-row",
        "constructed-nothing",
    ],
)
def test_m21_11_scoreboard_side(client: ScoreClient, side: int | None) -> None:
    assert first_side(client) == side


@dataclass
class LogClient:
    """`is_logging` is what C_ChatInfo.IsLoggingCombat() gives (None: no
    such function; a tuple: its returns; "raise": it errors); `logging` what
    LoggingCombat() gives; `acl` the advancedCombatLogging setting."""

    is_logging: Any = None
    logging: Any = None
    acl: Any = None
    calls: list[str] = field(default_factory=list)


class LogModel:
    """logState and advancedOn, line for line."""

    def __init__(self, c: LogClient) -> None:
        self.c = c
        self.state_from: str | None = None
        self.advanced_from: str | None = None

    def _is_logging(self) -> tuple[bool, tuple[Any, ...]]:
        self.c.calls.append("IsLoggingCombat")
        if self.c.is_logging == "raise":
            return False, ()
        return True, tuple(self.c.is_logging)

    def log_state(self) -> bool | None:
        self.state_from = None
        if self.c.is_logging is not None:
            ok, values = self._is_logging()
            on = values[0] if values else None
            if ok and isinstance(on, bool):
                self.state_from = "IsLoggingCombat"
                return on
        self.c.calls.append("LoggingCombat")
        on = self.c.logging
        if isinstance(on, bool):
            self.state_from = "LoggingCombat"
            return on
        return None

    def advanced_on(self) -> bool | None:
        self.advanced_from = None
        if self.c.is_logging is not None:
            ok, values = self._is_logging()
            flag = values[1] if len(values) > 1 else None
            if ok and isinstance(flag, bool):
                self.advanced_from = "IsLoggingCombat"
                return flag
        self.c.calls.append("GetCVar")
        if self.c.acl == "1":
            self.advanced_from = "setting"
            return True
        if self.c.acl == "0":
            self.advanced_from = "setting"
            return False
        return None


@pytest.mark.parametrize(
    ("client", "state", "state_from", "calls"),
    [
        # The documented call answers: LoggingCombat() is not asked.
        (LogClient((True, False), False), True, "IsLoggingCombat", ["IsLoggingCombat"]),
        (LogClient((False, True), True), False, "IsLoggingCombat", ["IsLoggingCombat"]),
        # It says neither, or raises: LoggingCombat() decides.
        (
            LogClient((None, None), True),
            True,
            "LoggingCombat",
            ["IsLoggingCombat", "LoggingCombat"],
        ),
        (LogClient("raise", False), False, "LoggingCombat", ["IsLoggingCombat", "LoggingCombat"]),
        # The client has no such function.
        (LogClient(None, True), True, "LoggingCombat", ["LoggingCombat"]),
        # Neither says: no state (the switch goes dormant), no source.
        (LogClient((1, None), "on"), None, None, ["IsLoggingCombat", "LoggingCombat"]),
    ],
    ids=[
        "constructed-documented-on",
        "constructed-documented-off",
        "constructed-documented-neither",
        "constructed-documented-raises",
        "constructed-no-documented-call",
        "constructed-no-state",
    ],
)
def test_m21_11_log_state(
    client: LogClient, state: bool | None, state_from: str | None, calls: list[str]
) -> None:
    model = LogModel(client)
    assert model.log_state() is state
    assert model.state_from == state_from
    assert client.calls == calls


@pytest.mark.parametrize(
    ("client", "advanced", "advanced_from"),
    [
        (LogClient((True, True), acl="0"), True, "IsLoggingCombat"),
        (LogClient((True, False), acl="1"), False, "IsLoggingCombat"),
        (LogClient((True,), acl="1"), True, "setting"),
        (LogClient((True, None), acl="0"), False, "setting"),
        (LogClient(None, acl="1"), True, "setting"),
        (LogClient("raise", acl="0"), False, "setting"),
        (LogClient(None, acl=None), None, None),
        (LogClient((True, "1"), acl="x"), None, None),
    ],
    ids=[
        "constructed-flag-on",
        "constructed-flag-off",
        "constructed-no-flag",
        "constructed-nil-flag",
        "constructed-no-documented-call",
        "constructed-raises",
        "constructed-unknown",
        "constructed-neither-a-boolean",
    ],
)
def test_m21_11_advanced_flag(
    client: LogClient, advanced: bool | None, advanced_from: str | None
) -> None:
    model = LogModel(client)
    assert model.advanced_on() is advanced
    assert model.advanced_from == advanced_from


def test_m21_11_a_later_unknown_state_clears_its_source() -> None:
    """Fix round 1: `state_from` names the call that answered at the last
    read, and nothing when neither did."""
    model = LogModel(LogClient((True, True)))
    assert model.log_state() is True
    assert model.state_from == "IsLoggingCombat"
    model.c.is_logging, model.c.logging = (None, None), None
    assert model.log_state() is None
    assert model.state_from is None


def test_m21_11_a_later_unknown_flag_clears_the_source() -> None:
    model = LogModel(LogClient((True, True)))
    assert model.advanced_on() is True
    model.c.is_logging, model.c.acl = (True, None), None
    assert model.advanced_on() is None
    assert model.advanced_from is None


SECRET = object()


def _own_text(value: Any) -> str | None:
    """ownText's keep rule: a non-empty text the client does not call
    secret (its reasons are pinned elsewhere, test_lab_addon)."""
    if value is SECRET or not isinstance(value, str) or value == "":
        return None
    return value


@dataclass
class RealmClient:
    """What GetRealmName(), GetNormalizedRealmName() and the second return
    of UnitFullName("player") give."""

    realm_name: Any = None
    normalized: Any = None
    full_second: Any = None
    calls: list[str] = field(default_factory=list)


def identity_realm(c: RealmClient) -> dict[str, Any]:
    """The realm and second-name keys of one identity gather."""
    record: dict[str, Any] = {}
    c.calls.append("GetRealmName")
    if kept := _own_text(c.realm_name):
        record.update(realm=kept, realm_from="realm_name")
    if "realm" not in record:
        c.calls.append("GetNormalizedRealmName")
        if kept := _own_text(c.normalized):
            record.update(realm=kept, realm_from="normalized_realm_name", realm_normalized=True)
    if "realm" not in record:
        c.calls.append("UnitFullName")
        if kept := _own_text(c.full_second):
            record.update(second_name=kept, second_name_from="full_name")
    return record


def home_realm(c: RealmClient) -> tuple[str | None, bool]:
    """homeRealm, before its session cache: (realm, hidden)."""
    hidden = False
    c.calls.append("GetNormalizedRealmName")
    if c.normalized is SECRET:
        hidden = True
    elif isinstance(c.normalized, str):
        bare = re.sub(r"[\s\-]", "", c.normalized)
        if bare:
            return bare, False
    return None, hidden


@pytest.mark.parametrize(
    ("client", "record", "home"),
    [
        # Forever as the owner's runs on 71037 and the check of 2026-11-23
        # show it: no realm from either call; the full name's second value is
        # the second name. The book keys no one with it.
        (
            RealmClient(None, "", "Soulrend"),
            {"second_name": "Soulrend", "second_name_from": "full_name"},
            (None, False),
        ),
        # A client with a realm: the full name is never asked.
        (
            RealmClient("Invented Realm", "InventedRealm", "InventedRealm"),
            {"realm": "Invented Realm", "realm_from": "realm_name"},
            ("InventedRealm", False),
        ),
        (
            RealmClient(None, "Invented-Realm", "InventedRealm"),
            {
                "realm": "Invented-Realm",
                "realm_from": "normalized_realm_name",
                "realm_normalized": True,
            },
            ("InventedRealm", False),
        ),
        # Nothing anywhere: no realm, no second name.
        (RealmClient(None, None, None), {}, (None, False)),
        # A secret second value is not kept; a secret realm hides the home.
        (RealmClient(None, SECRET, SECRET), {}, (None, True)),
    ],
    ids=[
        "constructed-forever",
        "constructed-realm-name",
        "constructed-normalized",
        "constructed-nothing",
        "constructed-secret",
    ],
)
def test_m21_11_the_second_name_is_never_a_realm(
    client: RealmClient, record: dict[str, Any], home: tuple[str | None, bool]
) -> None:
    got = identity_realm(client)
    assert got == record
    assert "realm_from" not in got or got["realm_from"] != "full_name"
    if "realm" in got:
        assert "UnitFullName" not in client.calls
    client.calls.clear()
    assert home_realm(client) == home
    assert client.calls == ["GetNormalizedRealmName"]


# The crew book's skips for want of a home realm (fix round 1) -------------


@dataclass
class Book:
    """WowLabCrewDB's counters and keys, as readMember, fieldsMissing and
    the match pass touch them."""

    skipped: int = 0
    no_second: int = 0
    keys: list[str] = field(default_factory=list)


def read_member(book: Book, name: str, second: Any, home: tuple[str | None, bool]) -> None:
    """readMember's keying, line for line from the name test on: `second`
    is UnitName's second value, `home` what homeRealm gave."""
    if isinstance(second, str) and second != "":
        book.keys.append(f"{name}-{second}")
        return
    realm, hidden = home
    if realm is None and hidden:
        book.skipped += 1
        return
    if realm is None:
        book.no_second += 1
    if isinstance(realm, str):
        book.keys.append(f"{name}-{realm}")


def fields_missing_line(book: Book) -> str:
    if book.no_second > 0:
        return (
            f" {book.no_second} players came back with no second name or realm and were "
            "skipped (not yet confirmed)."
        )
    return ""


def score_keys(names: list[Any], home: str | None) -> tuple[list[str | None], int | None]:
    """The match pass's row keys and `rows_unkeyed` (None when none)."""
    keys: list[str | None] = []
    unkeyed = 0
    for name in names:
        key = None
        if isinstance(name, str) and name != "":
            if "-" in name:
                key = name
            elif home is not None:
                key = f"{name}-{home}"
            else:
                unkeyed += 1
        keys.append(key)
    return keys, (unkeyed if unkeyed > 0 else None)


def test_m21_11_a_member_with_a_second_name_is_keyed_by_it() -> None:
    """Forever as the owner saw it: UnitName gives "Tay", "Soulrend"."""
    book = Book()
    read_member(book, "Tay", "Soulrend", (None, False))
    assert book.keys == ["Tay-Soulrend"]
    assert book.no_second == 0
    assert fields_missing_line(book) == ""


def test_m21_11_a_member_with_no_second_part_is_counted_and_said() -> None:
    book = Book()
    read_member(book, "Tay", None, (None, False))
    read_member(book, "Rizz", "", (None, False))
    assert book.keys == [] and book.no_second == 2 and book.skipped == 0
    assert fields_missing_line(book) == (
        " 2 players came back with no second name or realm and were skipped (not yet confirmed)."
    )


def test_m21_11_a_secret_home_is_a_skip_not_a_missing_second_name() -> None:
    book = Book()
    read_member(book, "Tay", None, (None, True))
    assert (book.skipped, book.no_second) == (1, 0)


def test_m21_11_a_home_realm_keys_a_member_as_before() -> None:
    book = Book()
    read_member(book, "Tay", None, ("InventedRealm", False))
    assert book.keys == ["Tay-InventedRealm"] and book.no_second == 0


@pytest.mark.parametrize(
    ("names", "home", "keys", "unkeyed"),
    [
        (["Tay-Soulrend", "Rizz-Other"], None, ["Tay-Soulrend", "Rizz-Other"], None),
        (["Tay", "Rizz-Other", None], None, [None, "Rizz-Other", None], 1),
        (["Tay", "Rizz"], "Home", ["Tay-Home", "Rizz-Home"], None),
    ],
    ids=["constructed-dashed", "constructed-no-home", "constructed-home"],
)
def test_m21_11_scoreboard_rows_with_no_key_are_counted(
    names: list[Any], home: str | None, keys: list[str | None], unkeyed: int | None
) -> None:
    assert score_keys(names, home) == (keys, unkeyed)
