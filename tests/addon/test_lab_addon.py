"""Static checks on the lab-addon sources (M11-01, docs/LAB_PLAN.md §13.1, ADR-0026).

Nothing here runs Lua (L3): the sources are tokenized by a small lexer in
this file and the checks read the tokens. The TOC template is read with
`wowlab_core.toc`. The selene lint (`make lint-lua`) is the other half: it
fails on any global the addon uses that `lab/addon/wow_client.yml` does not
declare.

These checks catch ordinary mistakes and ordinary edits, not an author set on
getting around them. Deliberate obfuscation is caught by review only:

- aliasing a client table or `ns` (`local t = C_Traits`, `local n = ns`) and
  reaching a function or field through the alias;
- building a key or a name at run time with `string.format`, `string.lower` or
  similar and indexing with it;
- changing a guarded key or field inside its `if type(X) == ... then` block
  through an alias of the table (`local d = c` ... `d.option = api`) or through
  a call that writes to it;
- changing an index key inside its guard (`k = ...` inside
  `if type(r[k]) == "number" then`);
- handing a client function out of a section's code as a call's return value
  (`saved = helper()` in a gather, `saved()` in a handler), which the M11-37
  section scan does not follow.

The stored-value check (M11-13) also does not check table keys, and it trusts
the Core.lua plumbing listed in `PLUMBING_STORES`.
"""

from __future__ import annotations

import importlib.util
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from wowlab_core.toc import parse_toc


def _load_constructed() -> ModuleType:
    """tests/addon/_crew_constructed.py, by path (the review probes load this
    grader from elsewhere, where tests/addon is not on sys.path)."""
    path = Path(__file__).with_name("_crew_constructed.py")
    spec = importlib.util.spec_from_file_location("wowlab_m19_01_crew_constructed", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# M19-01: every constructed source that names the crew book lives there.
_CONSTRUCTED = _load_constructed()
BOOK: str = _CONSTRUCTED.BOOK
CREW_ON: str = _CONSTRUCTED.CREW_ON
CREW_PROBE_1: str = _CONSTRUCTED.CREW_PROBE_1
CREW_PROBE: str = _CONSTRUCTED.CREW_PROBE_2  # M20-04: schema 2
CREW_MIGRATE: str = _CONSTRUCTED.CREW_MIGRATE
CREW_PVP_ENTRY_STORE: str = _CONSTRUCTED.CREW_PVP_ENTRY_STORE
CREW_PVP_WINNER_STORE: str = _CONSTRUCTED.CREW_PVP_WINNER_STORE
_CREW_READ: str = _CONSTRUCTED._CREW_READ
_CREW_MEMBER: str = _CONSTRUCTED._CREW_MEMBER
_CREW_COMMAND_HEAD: str = _CONSTRUCTED._CREW_COMMAND_HEAD
_CREW_SOURCE: str = _CONSTRUCTED._CREW_SOURCE
_CREW_SOURCE_1: str = _CONSTRUCTED._CREW_SOURCE_1
_CREW_TAIL: str = _CONSTRUCTED._CREW_TAIL
_CREW_GATE_BODY: str = _CONSTRUCTED._CREW_GATE_BODY
_CREW_GATE_CASES: dict[str, list[tuple[str, str]]] = _CONSTRUCTED._CREW_GATE_CASES
_CREW_GATE_CASES_1: dict[str, list[tuple[str, str]]] = _CONSTRUCTED._CREW_GATE_CASES_1
_CREW_SCHEMA_2_CASES: dict[str, tuple[list[tuple[str, str]], str]] = (
    _CONSTRUCTED._CREW_SCHEMA_2_CASES
)
_CREW_SCHEMA_2_CONTROLS: dict[str, list[tuple[str, str]]] = _CONSTRUCTED._CREW_SCHEMA_2_CONTROLS

ROOT = Path(__file__).resolve().parents[2]
ADDON_ROOT = ROOT / "lab" / "addon"
ADDON = ADDON_ROOT / "WowLab"
TOC = ADDON / "WowLab.toc"
STD = ADDON_ROOT / "wow_client.yml"
README = ADDON_ROOT / "README.md"
SOURCES = sorted(ADDON.glob("*.lua"))

# What stays refused in every file, Crew.lua included (ADR-0026, note of
# 2026-11-20; M20-13): every send (chat, whisper, addon message, mail, a link
# or text put into the chat box), the Battle.net identity (`BNGetInfo` and the
# tables that give a BattleTag), and the readers no ticket builds (the chat
# and community tables, the combat log, the group finder, the friends list,
# each of which also holds a call that acts on the server). The addon-wide
# privacy list of M11-01 to M19-01 (`UnitName`, `GetRealmName`, `UnitGUID`,
# `GetGuildInfo`, `GetMacroBody` and the rest) is gone with that note.
FORBIDDEN_APIS = (
    "BNGetInfo",
    "C_BattleNet",
    "BNGetFriendInfo",
    "C_ChatInfo",
    "C_Club",
    "C_FriendList",
    "SendChatMessage",
    "BNSendWhisper",
    "ChatFrame_OpenChat",
    "ChatEdit_InsertLink",
    "CombatLogGetCurrentEventInfo",
    "C_LFGList",
    "SendAddonMessage",
    "SendMail",
    "ChatFrame_SendTell",
)
# Dynamic global lookup would let a forbidden name hide in a string.
DYNAMIC_LOOKUP = (
    "_G",
    "getglobal",
    "setglobal",
    "rawget",
    "rawset",
    "getfenv",
    "setfenv",
    "loadstring",
    "load",
    "dofile",
    "require",
    # Added in the M11-37 security review: each reaches a global by name or
    # through an environment (`debug.getfenv(print).UnitLevel`), and selene's
    # Lua 5.1 base declares them all.
    "debug",
    "setmetatable",
    "getmetatable",
    "newproxy",
    "module",
    "package",
    "loadfile",
)
# Events that would deliver chat, community or Battle.net data, which no
# ticket reads (M20-13: the guild and friends events left with the privacy
# list).
PRIVATE_EVENT = re.compile(r"^(CHAT_MSG_|CLUB_|BN_)")
TOC_INTERFACE_LINE = "## Interface: @WOWLAB_INTERFACE@"
# APIs whose first argument is a unit token, besides every `Unit*` function.
UNIT_FIRST_ARG = frozenset(
    {
        "GetInventoryItemLink",
        "GetInventoryItemID",
        "GetInventoryItemQuality",
        "GetInventoryItemTexture",
        "GetInventoryItemCount",
        "GetInventoryItemDurability",
        "GetInventorySlotInfo",
        "GetUnitName",
        "CheckInteractDistance",
        "GetPlayerInfoByGUID",
        # M19-01: GetGuildInfo(unit), so even in Crew.lua it is a direct call
        # or a type() test, never a value handed on.
        "GetGuildInfo",
    }
)
UNIT_TOKEN = re.compile(
    r"^(?:target|focus|mouseover|pet|vehicle|npc|none|questnpc|"
    r"softenemy|softfriend|softinteract|anyenemy|anyfriend|anyinteract|"
    r"(?:party|raid|arena|boss|nameplate|partypet|raidpet|arenapet|spectated)\d*)"
    r"(?:target|pet)*$",
    re.IGNORECASE,
)
KIT_STATUS = "confirmed by forever-addon-kit on 69893, re-verify in M11-03"
VERIFY_STATUS = "[verify]"
# Globals the addon defines itself.
OWN_GLOBALS = frozenset({"WowLabCharDB", "WowLabDB", "SLASH_WOWLAB1", BOOK})

# --- M19-01: the one per-file relaxation, for Crew.lua (docs/LAB_PLAN.md §20.1) --
#
# The crew book (ADR-0036) must do what every other addon file is refused. The
# relaxation is keyed on the file's name being exactly `Crew.lua` (case and
# all) and is read through `_relaxed` only; no rule has a second exception.
# Each entry names the rule it relaxes and exactly what it lets through:
#
# - M20-13 (ADR-0026, note of 2026-11-20): the FORBIDDEN_APIS, CLOCK,
#   NAME_FIELDS and STD_PRIVACY relaxations are gone with the addon-wide
#   rules they relaxed. Crew.lua's own rules still hold its name calls
#   (CREW_NAME_CALLS) to the gate and the secret test.
# - UNIT_ARGUMENT: the unit-token literals `party`, `raid` and `target`, and a
#   unit call whose first argument is a local bound once, only from
#   `"party" .. i`, `"raid" .. i` (i a number or a numeric `for` variable) or
#   one of those literals, and never assigned again (`UnitClass(unit)`).
# - CONCAT_INDEX: both concatenated-index rules accept exactly
#   `<name> .. "-" .. <name>` (`players[name .. "-" .. realm]`, or a local
#   bound to that and nothing more): its result holds a `-`, so it can never
#   be an API or field name, which is what those rules guard against. No
#   call, field or other literal may sit in it.
# - SAVED_VARIABLES: `BOOK`, the crew book, beside `WowLabDB` on the
#   TOC's `## SavedVariables:` line, which is then pinned to both; and the
#   name `BOOK` itself, which no other file may write or read.
# - SECTION_SCAN: the M11-37 scan accepts a client reference outside every
#   section in Crew.lua only inside a gated function, inside the pinned
#   helpers `crewBlocked` and `secret`, and, for the typed text only, inside
#   the slash handler `ns.CrewCommand`. Crew.lua is not section code: it runs
#   from roster events and the slash command, `/wowlab skip` does not reach it,
#   and its own switch is `on` (conductor decision, 2026-11-19). Fix round 2:
#   also `time` and `date` in `ns.CrewCommand`, a `type()` test of a name call
#   anywhere, Crew.lua's `ns.On` calls (`_crew_on_call_ok`) among the pinned
#   callers of `ns.On`, and Core.lua's one pinned call of `ns.CrewCommand`.
# - PAIRS: `pairs` over `BOOK.players` or `BOOK.current`,
#   written out or through a local bound once, directly, to one of them.
#
# In exchange, Crew.lua keeps rules of its own (`_crew_problems`):
# - every relaxed call (a name call, or a unit call whose first argument is
#   not the literal "player") sits in a function whose body starts with the on
#   test and then the combat test, both pinned below;
# - every relaxed call is the whole value of a `local a, b = <call>`
#   statement, and each name it binds is used only as `secret(x)` or under an
#   `if`/`elseif` whose condition has `not secret(x)` as a top-level `and`
#   conjunct (§20.1, "Secret values");
# - the pinned helpers are bound once and otherwise only called;
# - it names neither `WowLabDB` nor `WowLabCharDB`, writes no global but
#   `BOOK`, stores nothing under `ns`, reads only CREW_NS_READS from
#   `ns`, and exports only CREW_EXPORTS, as `function ns.X(...)`, each
#   returning nothing or a literal;
# - `BOOK` is only indexed or `type()`-tested; the whole table and its
#   `on` are assigned only inside `ns.CrewCommand` (loading creates nothing);
# - `ns.CrewCommand` starts with CREW_COMMAND_HEAD, and only Core.lua's slash
#   handler calls it, as CREW_COMMAND_CALL (`_crew_command_call_problems`);
# - no function parameter is indexed, called, assigned, stored, returned or
#   handed to `pcall`, `xpcall` or `ns.Call` (the pinned helpers excepted), and
#   no function uses `...`;
# - a unit local may also be the one parameter of a local function that uses
#   it only as a unit argument, when every call of it hands a unit local or a
#   unit literal;
# - `ns.On` is called only in `ns.CrewCommand`, in a gated function, or once
#   at file level for an event Core.lua registers itself at file level, with
#   a gated local function as the handler (no new registration while off).
CREW_FILE = "Crew.lua"
# M20-11 (owner decision, 2026-11-20, ADR-0036's second note of that date):
# also `UnitGUID`, held to the same gate and secret test as the others.
CREW_NAME_CALLS = frozenset(
    {"UnitName", "UnitFullName", "GetNormalizedRealmName", "GetGuildInfo", "UnitGUID"}
)
CREW_DB = BOOK  # the crew book's saved variable, from _crew_constructed.py
_CREW_PATH = Path(CREW_FILE)
NON_LITERAL_UNIT = "<a local name as the unit>"
GATED = "<a function behind the on and combat tests>"
CREW_COMMAND = "ns.CrewCommand"
CREW_ON_CALLS = "<Crew.lua's ns.On calls, as _crew_on_call_ok allows them>"
# Fix round 4: the events §20.1 "When it records" names for the book, the
# only ones Crew.lua may register inside a function (the roster, a name
# arriving, the end of combat); at file level only Core.lua's own.
CREW_EVENTS_1 = frozenset({"GROUP_ROSTER_UPDATE", "UNIT_NAME_UPDATE", "PLAYER_REGEN_ENABLED"})
# M20-04 (§21.2, "What is believed"): the scoreboard probe's candidate events,
# counted since entry; registered only where the roster events may be.
CREW_PVP_EVENTS = frozenset({"UPDATE_BATTLEFIELD_SCORE", "PVP_MATCH_COMPLETE"})
CREW_EVENTS = CREW_EVENTS_1 | CREW_PVP_EVENTS
# Fix round 3: "NONE" (UnitGroupRolesAssigned's no-role answer) and "none"
# (GetInstanceInfo's open-world type) look like unit tokens; in Crew.lua they
# may be compared with `==` / `~=`, nothing else.
CREW_COMPARANDS = frozenset({"NONE", "none"})
# M20-04: at schema 2 also "arena" (GetInstanceInfo's arena type; §21.2: the
# probe runs only in an instance of type `pvp` or `arena`), on the same terms.
CREW_COMPARANDS_2 = frozenset({"arena"})
# The one handler that needs no gate: at ADDON_LOADED for this addon it
# raises the book's probe, only when a book already exists (whether or not it
# is on: §20.1 "A failed load" raises it "like §13.1's", at every load), and
# it creates nothing. Pinned whole, registered only at file level for
# ADDON_LOADED.
CREW_PROBE_EVENT = "ADDON_LOADED"
# The slash handler's first statement, pinned: the typed text is a string or
# nothing happens, so the argument can carry no table or function in or out.
# Follow-up (M19-10 security review): then, before anything else, a book of
# another schema is left alone (CREW_BOOK_SCHEMA_TEST).
CREW_COMMAND_HEAD_1 = (
    'function ns . CrewCommand ( word ) if type ( word ) ~= "string" then return end '
    + _CONSTRUCTED.CREW_BOOK_SCHEMA_TEST
)
# M20-04 (§21.2, "Migration", amending #631): at schema 2 the head lets the
# word `on` through for a schema 1 book, which is migrated (CREW_MIGRATE)
# before the schema refusal is tested; every other word and every other
# schema still meets the one fixed print. The word is the typed text's second
# word (Core.lua hands on the whole `crew on`), lowercased, alone.
CREW_COMMAND_HEAD = (
    'function ns . CrewCommand ( word ) if type ( word ) ~= "string" then return end '
    'if string . match ( string . lower ( word ) , "^%s*%S+%s+on%s*$" ) ~= nil then '
    "crewMigrate ( ) end " + _CONSTRUCTED.CREW_BOOK_SCHEMA_TEST
)
# The file's own schema, pinned: one file-level local, the number literal 1,
# never assigned again, declared nowhere else (§20.1: `schema = 1`). A local
# number literal is a value the M11-13 stored-value check accepts. M20-04:
# the literal is 2 (§21.2); a source declaring `local CREW_SCHEMA = 1` is
# graded with CREW_PINS_1, M19's pins unchanged, until M20-05.
CREW_SCHEMA_NAME = "CREW_SCHEMA"
CREW_SCHEMA_DECL_1 = "local CREW_SCHEMA = 1"
CREW_SCHEMA_DECL = "local CREW_SCHEMA = 2"
# Core.lua's one call of it: in its slash handler, a statement of its own,
# handing on exactly the typed text.
CREW_COMMAND_CALLER = "SlashCmdList.WOWLAB"
CREW_COMMAND_CALL = "ns . CrewCommand ( message )"
CREW_RELAXED: dict[str, frozenset[str]] = {
    "UNIT_ARGUMENT": frozenset({"party", "raid", "target", NON_LITERAL_UNIT, *CREW_COMPARANDS}),
    "CONCAT_INDEX": frozenset({"-"}),
    "SAVED_VARIABLES": frozenset({CREW_DB}),
    "SECTION_SCAN": frozenset(
        {GATED, "crewBlocked", "secret", "crewLoaded", CREW_COMMAND, CREW_ON_CALLS}
    ),
    # M20-04: and, at schema 2, the one walk of the first scoreboard row in
    # the pinned pvpSnapshot (its field names, each value handed to `type`).
    "PAIRS": frozenset({f"{CREW_DB}.players", f"{CREW_DB}.current", "<the row walk>"}),
}
CREW_ROW_WALK = "<the row walk>"
# What Crew.lua may hand to or take from the rest of the addon: the slash
# handler Core.lua calls for `/wowlab crew ...`, and Core's helpers that keep
# nothing (no ns.Section, ns.state, ns.Write or ns.probe: they reach
# WowLabCharDB).
CREW_EXPORTS = frozenset({"CrewCommand"})
CREW_NS_READS = frozenset(
    {"On", "Call", "Fn", "Number", "String", "Bool", "Pack", "Numbers", *CREW_EXPORTS}
)
# Never relaxed, Crew.lua included: every send (§20.1), the combat log and the
# chat and community readers (no ticket builds them). M20-13 (ADR-0026, note
# of 2026-11-20): exactly FORBIDDEN_APIS; `GetPlayerInfoByGUID`,
# `GetRaidRosterInfo`, `GetUnit` and the clock's `os` left with the privacy
# list.
NEVER_RELAXED = frozenset(FORBIDDEN_APIS)
# The on test and the combat test, as the first two statements of a function
# that makes a relaxed call, and the two functions they call, pinned whole: an
# edited test (`return true`, a dropped `not`) would let the calls run with the
# book off or in combat.
CREW_GATE = "if not crewOn ( ) then return end if crewBlocked ( ) then return end"
CREW_BLOCKED = (
    "local function crewBlocked ( ) "
    'if type ( InCombatLockdown ) ~= "function" then return true end '
    "local ok , locked = pcall ( InCombatLockdown ) "
    "if not ok or locked then return true end "
    "return false end"
)
# §20.1 "Secret values": true when the client calls a value secret or cannot
# say; false when it has no issecretvalue (each member is then read inside its
# own pcall).
CREW_SECRET = (
    "local function secret ( value ) "
    'if type ( issecretvalue ) ~= "function" then return false end '
    "local ok , hidden = pcall ( issecretvalue , value ) "
    "return not ok or hidden == true end"
)
CREW_PINNED_1 = {"crewOn": CREW_ON, "crewBlocked": CREW_BLOCKED, "secret": CREW_SECRET}

# --- M20-04: schema 2 (docs/LAB_PLAN.md §21.1, §21.2) ---------------------------
#
# The scoreboard's candidate calls (§21.2). M20-11: no longer pinned to the
# probe's helpers; each is held to the gate, `pcall` and the secret test
# (`_crew_score_problems`). The migration (CREW_MIGRATE) is pinned; it holds
# no client name.
CREW_PVP_CALLS = frozenset(
    {
        "C_PvP",
        "GetNumBattlefieldScores",
        "GetBattlefieldScore",
        "RequestBattlefieldScoreData",
        "GetBattlefieldWinner",
        "GetBattlefieldArenaFaction",
        "IsActiveBattlefieldArena",
    }
)
CREW_PINNED = {**CREW_PINNED_1, "crewMigrate": CREW_MIGRATE}
# The limits and the near-full line (§21.1, §21.2): each a named file-level
# constant holding a number literal, declared once and never assigned again.
CREW_LIMITS = {
    "MAX_PLAYERS": "local MAX_PLAYERS = 20000",
    "MAX_MATCHES": "local MAX_MATCHES = 500",
    "NEAR_FULL_PERCENT": "local NEAR_FULL_PERCENT = 90",
    "NEAR_FULL_SECONDS": "local NEAR_FULL_SECONDS = 604800",
}


def _crew_scoreboard_name(name: str) -> bool:
    """Fix round 1 (security review): a candidate call, or any global named
    `GetBattlefield...` (GetBattlefieldStatData, GetBattlefieldTeamInfo,
    GetBattlefieldStatus, ...), or `C_PvP` (so any member of it). M20-11:
    behind the gate, through `pcall` or in a `type()` test
    (`_crew_score_problems`)."""
    return name in CREW_PVP_CALLS or name.startswith("GetBattlefield")


@dataclass(frozen=True)
class _CrewPins:
    """What Crew.lua is pinned to at one schema. CREW_PINS_1 is M19's, unchanged;
    a source is graded with it only while it declares `local CREW_SCHEMA = 1`
    (the real file until M20-05). Every other source, every constructed one
    included, is graded with CREW_PINS_2, and
    TestM20_05Activation pins the real file to it once M20-05 deletes the
    marker."""

    schema: int
    decl: str
    probe: str
    command_head: str
    pinned: dict[str, str]
    events: frozenset[str]
    comparands: frozenset[str]


CREW_PINS_1 = _CrewPins(
    1,
    CREW_SCHEMA_DECL_1,
    CREW_PROBE_1,
    CREW_COMMAND_HEAD_1,
    CREW_PINNED_1,
    CREW_EVENTS_1,
    frozenset(),
)
CREW_PINS_2 = _CrewPins(
    2, CREW_SCHEMA_DECL, CREW_PROBE, CREW_COMMAND_HEAD, CREW_PINNED, CREW_EVENTS, CREW_COMPARANDS_2
)


def _crew_pins(tokens: list[Token]) -> _CrewPins:
    """CREW_PINS_1 for a source that declares `local CREW_SCHEMA = 1` and not
    `= 2`; CREW_PINS_2 for every other."""
    n = len(CREW_SCHEMA_DECL_1.split(" "))
    rendered = {_render(tokens[k : k + n]) for k in range(len(tokens) - n + 1)}
    if CREW_SCHEMA_DECL_1 in rendered and CREW_SCHEMA_DECL not in rendered:
        return CREW_PINS_1
    return CREW_PINS_2


def _relaxed(path: Path, rule: str) -> frozenset[str]:
    """What `rule` lets `path` do beyond every other file: nothing, unless
    the file is named exactly Crew.lua."""
    return CREW_RELAXED[rule] if path.name == CREW_FILE else frozenset()


# --- A Lua 5.1 lexer (tokens only; nothing is evaluated) ---------------------


@dataclass(frozen=True)
class Token:
    kind: str  # "name", "string", "number", "op"
    text: str  # names and ops as written; strings decoded (roughly), numbers raw
    line: int


_LONG_OPEN = re.compile(r"\[(=*)\[")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"0[xX][0-9A-Fa-f]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_OPS = ("...", "..", "==", "~=", "<=", ">=", *"+-*/%^#<>=(){}[];:,.")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _long_bracket(src: str, pos: int) -> tuple[str, int] | None:
    match = _LONG_OPEN.match(src, pos)
    if not match:
        return None
    close = "]" + match.group(1) + "]"
    end = src.find(close, match.end())
    if end == -1:
        raise ValueError(f"unterminated long bracket at offset {pos}")
    return src[match.end() : end], end + len(close)


def _short_string(src: str, pos: int) -> tuple[str, int]:
    quote = src[pos]
    out: list[str] = []
    i = pos + 1
    while i < len(src):
        ch = src[i]
        if ch == quote:
            return "".join(out), i + 1
        if ch == "\n":
            break
        if ch == "\\":
            nxt = src[i + 1]
            if nxt.isdigit():
                digits = re.match(r"\d{1,3}", src[i + 1 :])
                assert digits is not None
                out.append(chr(int(digits.group(0))))
                i += 1 + len(digits.group(0))
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    raise ValueError(f"unterminated string at offset {pos}")


def tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    line = 1
    while pos < len(src):
        ch = src[pos]
        if ch == "\n":
            line += 1
            pos += 1
        elif ch.isspace():
            pos += 1
        elif src.startswith("--", pos):
            long = _long_bracket(src, pos + 2)
            if long is not None:
                body, end = long
                line += body.count("\n")
                pos = end
            else:
                end = src.find("\n", pos)
                pos = len(src) if end == -1 else end
        elif ch in "'\"":
            text, end = _short_string(src, pos)
            tokens.append(Token("string", text, line))
            pos = end
        elif ch == "[" and _LONG_OPEN.match(src, pos):
            long = _long_bracket(src, pos)
            assert long is not None
            body, end = long
            tokens.append(Token("string", body, line))
            line += body.count("\n")
            pos = end
        elif match := _NAME.match(src, pos):
            tokens.append(Token("name", match.group(0), line))
            pos = match.end()
        elif (match := _NUMBER.match(src, pos)) and (
            ch.isdigit() or src[pos + 1 : pos + 2].isdigit()
        ):
            tokens.append(Token("number", match.group(0), line))
            pos = match.end()
        else:
            for op in _OPS:
                if src.startswith(op, pos):
                    tokens.append(Token("op", op, line))
                    pos += len(op)
                    break
            else:
                raise ValueError(f"unexpected character {ch!r} on line {line}")
    return tokens


def _tokens(path: Path) -> list[Token]:
    return tokenize(path.read_text(encoding="utf-8"))


def _where(path: Path, token: Token) -> str:
    # A constructed source (M19-01) has a bare name, not a path under ROOT.
    where = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    return f"{where}:{token.line}"


def _is_field(tokens: list[Token], i: int) -> bool:
    return i > 0 and tokens[i - 1].kind == "op" and tokens[i - 1].text in {".", ":"}


def _callee(tokens: list[Token], i: int) -> str:
    """The dotted name ending at token i (`C_Traits.GetConfigInfo`)."""
    parts = [tokens[i].text]
    j = i
    while j >= 2 and tokens[j - 1].text in {".", ":"} and tokens[j - 2].kind == "name":
        parts.insert(0, tokens[j - 2].text)
        j -= 2
    return ".".join(parts)


def _is_unit_api(name: str) -> bool:
    return bool(re.match(r"^Unit[A-Z]", name)) or name in UNIT_FIRST_ARG


# --- Fixture sanity -------------------------------------------------------------


def test_the_addon_has_sources() -> None:
    assert TOC.is_file()
    assert SOURCES, "no .lua files under lab/addon/WowLab"


def test_lexer_constructed_input() -> None:
    """Constructed input (a boundary test of this file's own lexer)."""
    src = (
        '-- UnitName in a comment\n--[==[ UnitGUID\n]==]\nlocal s = "a\\"b" .. [[x]]\n'
        "f('player', 0x1F, 1.5e3)\n"
    )
    kinds = [(t.kind, t.text) for t in tokenize(src)]
    assert ("name", "UnitName") not in kinds
    assert ("name", "UnitGUID") not in kinds
    assert ("string", 'a"b') in kinds
    assert ("string", "x") in kinds
    assert ("string", "player") in kinds
    assert ("number", "0x1F") in kinds
    assert ("number", "1.5e3") in kinds
    assert tokenize(src)[-1].line == 5


# --- TOC template ----------------------------------------------------------------


def test_toc_template_has_no_interface_number() -> None:
    doc = parse_toc(TOC.read_bytes())
    directives = doc.get_all("Interface")
    assert len(directives) == 1, "exactly one ## Interface: placeholder line"
    value = directives[0].value
    assert not re.search(r"\d", value), f"## Interface: holds a number: {value!r}"
    assert doc.interface is not None
    assert doc.interface.versions == ()


def test_toc_interface_line_is_the_placeholder() -> None:
    """M11-02 replaces this exact line; pin it."""
    lines = TOC.read_text(encoding="utf-8").splitlines()
    assert lines.count(TOC_INTERFACE_LINE) == 1
    assert [line for line in lines if line.lower().startswith("## interface")] == [
        TOC_INTERFACE_LINE
    ]


def _toc_saved_variables_problems(
    per_character: tuple[str, ...], account: tuple[str, ...], sources: list[Path]
) -> list[str]:
    """With a Crew.lua among the sources the account line is pinned to
    WowLabDB and BOOK (M19-01); without one, no BOOK."""
    problems = []
    if per_character != ("WowLabCharDB",):
        problems.append(f"## SavedVariablesPerCharacter: {per_character}")
    crew = set().union(*(_relaxed(p, "SAVED_VARIABLES") for p in sources))
    if crew:
        if account != ("WowLabDB", *sorted(crew)):
            problems.append(f"## SavedVariables: {account}, expected WowLabDB, {sorted(crew)}")
    elif account not in {(), ("WowLabDB",)}:
        problems.append(f"## SavedVariables: {account} without a {CREW_FILE}")
    return problems


def test_toc_saved_variables() -> None:
    doc = parse_toc(TOC.read_bytes())
    problems = _toc_saved_variables_problems(
        doc.saved_variables_per_character, doc.saved_variables, SOURCES
    )
    assert not problems, problems


def test_toc_saved_variables_constructed() -> None:
    """Constructed inputs (M19-01 boundary cases): the TOC rule with and
    without a Crew.lua among the sources."""
    char = ("WowLabCharDB",)
    both = ("WowLabDB", CREW_DB)
    crew = [Path("Core.lua"), Path(CREW_FILE)]
    plain = [Path("Core.lua"), Path("Gear.lua")]
    assert _toc_saved_variables_problems(char, both, crew) == []
    assert _toc_saved_variables_problems(char, ("WowLabDB",), plain) == []
    assert _toc_saved_variables_problems(char, (), plain) == []
    refused = {
        "the crew book without Crew.lua": (char, both, plain),
        "the crew book alone": (char, (CREW_DB,), plain),
        "Crew.lua without the crew book": (char, ("WowLabDB",), crew),
        "the crew book per character": (("WowLabCharDB", CREW_DB), both, crew),
        "the crew book under another file name": (char, both, [Path("crew.lua")]),
        "a third saved variable": (char, (*both, "WowLabOtherDB"), crew),
    }
    missed = [tag for tag, args in refused.items() if not _toc_saved_variables_problems(*args)]
    assert not missed, missed


def test_toc_loads_every_source_and_nothing_else() -> None:
    doc = parse_toc(TOC.read_bytes())
    listed = [line.path for line in doc.files]
    assert all(not line.conditions and not line.variables for line in doc.files)
    assert sorted(listed) == sorted(p.name for p in SOURCES)
    assert listed[0] == "Core.lua", "Core.lua defines the section registry"


# --- Privacy and flavor rules --------------------------------------------------


def _forbidden_api_problems(path: Path, tokens: list[Token]) -> list[str]:
    """A kept name (a send, the Battle.net identity, a reader no ticket
    builds) as a name, a field, or inside any string, in every file: no file
    is relaxed (M20-13)."""
    bad = []
    for t in tokens:
        if t.kind == "name" and t.text in FORBIDDEN_APIS:
            bad.append(f"{_where(path, t)} {t.text}")
        elif t.kind == "string" and any(api in t.text for api in FORBIDDEN_APIS):
            bad.append(f"{_where(path, t)} {t.text!r}")
    return bad


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_call_no_forbidden_api(path: Path) -> None:
    bad = _forbidden_api_problems(path, _tokens(path))
    assert not bad, bad


def _dynamic_lookups(tokens: list[Token]) -> list[Token]:
    return [
        t
        for i, t in enumerate(tokens)
        if t.kind == "name" and t.text in DYNAMIC_LOOKUP and not _is_field(tokens, i)
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_do_no_dynamic_global_lookup(path: Path) -> None:
    bad = [f"{_where(path, t)} {t.text}" for t in _dynamic_lookups(_tokens(path))]
    assert not bad, bad


def test_dynamic_lookup_constructed_environment_reads() -> None:
    """Constructed inputs (boundary cases from the M11-37 security review):
    each line reaches a client global without `_G` or `getfenv` by name, and
    passed every test and selene before the names were added."""
    lines = {
        "debug": 'local level = debug.getfenv(print).UnitLevel("player")\n',
        "setmetatable": "local env = setmetatable({}, { __index = {} })\n",
        "getmetatable": "local meta = getmetatable(string)\n",
        "newproxy": "local proxy = newproxy(true)\n",
        "module": "module(...)\n",
        "package": "local loaded = package.loaded\n",
        "loadfile": 'local chunk = loadfile("x")\n',
    }
    missed = [
        name
        for name, line in lines.items()
        if name not in {t.text for t in _dynamic_lookups(tokenize(line))}
    ]
    assert not missed, missed


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_key_nothing_on_flavor(path: Path) -> None:
    bad = [
        f"{_where(path, t)} {t.text}"
        for t in _tokens(path)
        if (
            t.kind == "name"
            and (t.text.startswith("WOW_PROJECT") or t.text.startswith("LE_EXPANSION"))
        )
        or (t.kind == "string" and re.search(r"_(retail|classic\w*|ptr\w*|beta\w*)_", t.text))
    ]
    assert not bad, bad


def _unit_argument_ok(path: Path, args: list[Token]) -> bool:
    """Is a unit call's first argument allowed: the literal "player", or in
    Crew.lua one of its literals or a plain local name?"""
    if len(args) != 2 or args[1].text not in {",", ")"}:
        return False
    relaxed = _relaxed(path, "UNIT_ARGUMENT") - CREW_COMPARANDS - CREW_COMPARANDS_2
    if args[0].kind == "string":
        return args[0].text == "player" or args[0].text in relaxed
    return (
        args[0].kind == "name"
        and NON_LITERAL_UNIT in relaxed
        and args[0].text not in _KEYWORDS
        and args[0].text not in OWN_GLOBALS
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_every_unit_token_argument_is_player(path: Path) -> None:
    bad = _unit_argument_problems(path, _tokens(path))
    assert not bad, bad


def _unit_argument_problems(path: Path, tokens: list[Token]) -> list[str]:
    literals = _relaxed(path, "UNIT_ARGUMENT")
    comparands = CREW_COMPARANDS
    if literals:
        # M20-04: "arena" too, in a schema 2 Crew.lua only.
        comparands = comparands | _crew_pins(tokens).comparands
        literals = literals | comparands
    bad = []
    for i, t in enumerate(tokens):
        # Crew.lua: "NONE" (a role) and "none" (an instance type) only as a
        # comparand of == or ~=, never anywhere a unit could be read from.
        comparand = (
            t.kind == "string"
            and t.text in literals & comparands
            and (
                tokens[i - 1].text in {"==", "~="}
                or (i + 1 < len(tokens) and tokens[i + 1].text in {"==", "~="})
            )
        )
        if (
            t.kind == "string"
            and t.text != "player"
            and (t.text not in literals or t.text in comparands)
            and not comparand
            and UNIT_TOKEN.match(t.text)
        ):
            bad.append(f"{_where(path, t)} unit-token literal {t.text!r}")
        if t.kind == "string" and _is_unit_api(t.text):
            # ns.Fn(tbl, "UnitX") would hand a unit API around as a value.
            bad.append(f"{_where(path, t)} unit API named in a string {t.text!r}")
        if t.kind != "name" or not _is_unit_api(t.text):
            continue
        callee = _callee(tokens, i)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None and nxt.text == "(":
            if not _unit_argument_ok(path, tokens[i + 2 : i + 4]):
                bad.append(
                    f'{_where(path, t)} {callee}(...) first argument is not the literal "player"'
                )
            continue
        # Otherwise the only allowed use is an existence test: type(Name).
        is_type_test = (
            i >= 2
            and tokens[i - 1].text == "("
            and tokens[i - 2].text == "type"
            and nxt is not None
            and nxt.text == ")"
        )
        if not is_type_test:
            bad.append(
                f"{_where(path, t)} {callee} used other than as a direct call or type() test"
            )
    return bad


def _render(tokens: list[Token]) -> str:
    return " ".join(f'"{t.text}"' if t.kind == "string" else t.text for t in tokens)


# M20-04 pinned the scoreboard probe's helpers (pvpInstance, pvpWon,
# pvpSnapshot) whole here. M20-11 (owner decision, 2026-11-20) dropped the
# pins: they held the probe to type names only. The gate, the pcall and the
# secret test of every scoreboard call are `_crew_score_problems`'.


def _statement_at(tokens: list[Token], start: int, length: int) -> str:
    return _render(tokens[start : start + length])


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_register_no_private_events(path: Path) -> None:
    bad = [
        f"{_where(path, t)} {t.text}"
        for t in _tokens(path)
        if t.kind == "string" and PRIVATE_EVENT.match(t.text)
    ]
    assert not bad, bad


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_build_no_index_by_concatenation(path: Path) -> None:
    """`t["Unit" .. "Name"]` would assemble a forbidden name at run time."""
    bad = _concat_index_problems(path, _tokens(path))
    assert not bad, bad


def _name_dash_name(tokens: list[Token]) -> bool:
    """Exactly `<name> .. "-" .. <name>`: two plain names (no field, call or
    keyword) joined by the one literal `"-"`."""
    return (
        [(x.kind, x.text) for x in tokens[1:4]] == [("op", ".."), ("string", "-"), ("op", "..")]
        and len(tokens) == 5
        and all(x.kind == "name" and x.text not in _KEYWORDS for x in (tokens[0], tokens[4]))
    )


_NAME_DASH_NAME = re.compile(r'^\s*[A-Za-z_]\w*\s*\.\.\s*"-"\s*\.\.\s*[A-Za-z_]\w*\s*;?\s*$')


def _concat_index_problems(path: Path, tokens: list[Token]) -> list[str]:
    """`..` inside `[...]`. In Crew.lua only, a bracket holding exactly
    `<name> .. "-" .. <name>` (`players[name .. "-" .. realm]`) may."""
    literals = _relaxed(path, "CONCAT_INDEX")
    bad = []
    depth: list[int] = []
    concat: dict[int, Token] = {}  # opening bracket -> its first `..`
    for i, t in enumerate(tokens):
        if t.kind != "op":
            continue
        if t.text == "[":
            depth.append(i)
        elif t.text == "]" and depth:
            open_ = depth.pop()
            first = concat.pop(open_, None)
            if first is not None and not (literals and _name_dash_name(tokens[open_ + 1 : i])):
                bad.append(f"{_where(path, first)} '..' inside [...]")
        elif t.text == ".." and depth:
            concat.setdefault(depth[-1], t)
    for first in concat.values():
        bad.append(f"{_where(path, first)} '..' inside [...]")
    return bad


def _fn_call_problems(path: Path, tokens: list[Token]) -> list[str]:
    """Checks, on tokens: every `ns.Fn(` call other than its definition
    (`function ns.Fn(`) is exactly `ns.Fn(<Name>, "<string literal>")`; `ns.Fn`
    never appears except followed by `(` (so it is never aliased or passed
    around); and no string literal is "Fn" (so it is not reached as `ns["Fn"]`)."""
    problems = []
    for i, t in enumerate(tokens):
        if t.kind == "string" and t.text == "Fn":
            problems.append(f"{_where(path, t)} string literal 'Fn'")
        if [x.text for x in tokens[i : i + 3]] == ["ns", ".", "Fn"] and (
            i + 3 >= len(tokens) or tokens[i + 3].text != "("
        ):
            problems.append(f"{_where(path, t)} ns.Fn not followed by '('")
    for i in range(len(tokens) - 3):
        if [t.text for t in tokens[i : i + 4]] != ["ns", ".", "Fn", "("]:
            continue
        if i > 0 and tokens[i - 1].text == "function":
            continue  # function ns.Fn(tbl, key): the definition in Core.lua
        args = tokens[i + 4 : i + 8]
        ok = (
            len(args) == 4
            and args[0].kind == "name"
            and args[1].text == ","
            and args[2].kind == "string"
            and args[3].text == ")"
        )
        if not ok:
            problems.append(f"{_where(path, tokens[i])} {_render(tokens[i : i + 8])!r}")
    return problems


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_every_fn_lookup_uses_a_literal_key(path: Path) -> None:
    problems = _fn_call_problems(path, _tokens(path))
    assert not problems, problems


def test_pet_rows_bind_only_species_and_owned() -> None:
    """GetPetInfoByIndex's first return is a battle-pet GUID: it is only ever
    reached as `local a, b = select(2, ns.Call(byIndex, ...))`. A lookup by a
    computed key could hide a second handle, so every `ns.Fn` call must use a
    literal key (the review probe for M11-01 checks this through here)."""
    problems = []
    lookups = 0
    for path in SOURCES:
        tokens = _tokens(path)
        problems += _fn_call_problems(path, tokens)
        handles: set[str] = set()
        for i, t in enumerate(tokens):
            if t.kind == "string" and t.text == "GetPetInfoByIndex":
                lookups += 1
                # local <handle> = ns.Fn(C_PetJournal, "GetPetInfoByIndex")
                head = _render(tokens[i - 9 : i])
                if not re.fullmatch(r"local \w+ = ns \. Fn \( C_PetJournal ,", head):
                    problems.append(f"{_where(path, t)} looked up other than into a local handle")
                else:
                    handles.add(tokens[i - 8].text)
            elif t.kind == "name" and t.text == "GetPetInfoByIndex":
                problems.append(f"{_where(path, t)} named directly")
        for i, t in enumerate(tokens):
            if t.kind != "name" or t.text not in handles or tokens[i - 1].text == "local":
                continue
            before, after = tokens[i - 1].text, tokens[i + 1].text
            if before in {"and", "not", "("} and after in {"and", ")"}:
                continue  # an existence test: not (a and handle and b)
            # Exactly two names bound, from return 2 on.
            call = _render(tokens[i - 13 : i + 2])
            if not re.fullmatch(r"local \w+ , \w+ = select \( 2 , ns \. Call \( \w+ ,", call):
                problems.append(f"{_where(path, t)} {call!r}")
    assert lookups == 1
    assert not problems, problems


_BINARY_OPS = frozenset(
    {"and", "or", "..", "+", "-", "*", "/", "%", "^", "==", "~=", "<", ">", "<=", ">="}
)
_CONTINUES = _BINARY_OPS | {".", ":", "[", "(", "{"}


def _std_top_level() -> set[str]:
    return {name.split(".")[0] for name in _std_globals()}


def _chain_end(tokens: list[Token], i: int) -> tuple[str, int]:
    """The dotted name `A.B.C` starting at token i, and the index after it."""
    parts = [tokens[i].text]
    j = i + 1
    while j + 1 < len(tokens) and tokens[j].text == "." and tokens[j + 1].kind == "name":
        parts.append(tokens[j + 1].text)
        j += 2
    return ".".join(parts), j


def _ends_expression(tokens: list[Token], j: int) -> bool:
    return j >= len(tokens) or (
        tokens[j].text not in _CONTINUES and tokens[j].kind not in {"string", "number"}
    )


def _classify_rhs(tokens: list[Token], j: int, std: set[str]) -> bool:
    """Is the expression starting at token j one of: a table constructor
    `{...}`, `nil`, a std-declared client table `A.B`, or
    `type(A) == "table" and A.B or nil` with `A.B` std-declared? Nothing may
    follow it in the same expression."""
    if j >= len(tokens):
        return False
    first = tokens[j]
    if first.text == "{":
        depth = 0
        for k in range(j, len(tokens)):
            if tokens[k].text == "{":
                depth += 1
            elif tokens[k].text == "}":
                depth -= 1
                if depth == 0:
                    return _ends_expression(tokens, k + 1)
        return False
    if first.text == "nil":
        return _ends_expression(tokens, j + 1)
    if first.kind == "name" and first.text == "type":
        head = [t.text for t in tokens[j : j + 7]]
        guarded = (
            len(head) == 7
            and head[1] == "("
            and head[3] == ")"
            and head[4] == "=="
            and tokens[j + 5].kind == "string"
            and tokens[j + 5].text == "table"
            and head[6] == "and"
        )
        if guarded:
            chain, k = _chain_end(tokens, j + 7)
            if chain in std and chain.split(".")[0] == head[2]:
                tail = [t.text for t in tokens[k : k + 2]]
                return tail == ["or", "nil"] and _ends_expression(tokens, k + 2)
        return False
    if first.kind == "name":
        chain, k = _chain_end(tokens, j)
        return chain in std and _ends_expression(tokens, k)
    return False


def _function_params(tokens: list[Token]) -> set[str]:
    params = set()
    for i, t in enumerate(tokens):
        if t.text != "function":
            continue
        k = i + 1
        while k < len(tokens) and tokens[k].text != "(":
            k += 1
        k += 1
        while k < len(tokens) and tokens[k].text != ")":
            if tokens[k].kind == "name":
                params.add(tokens[k].text)
            k += 1
    return params


def _bindings(tokens: list[Token], name: str) -> list[tuple[str, int]]:
    """Every place `name` is assigned or bound, as (kind, index):
    ("assign", index of the first rhs token) for `[local] name = rhs`;
    ("multi", i) for a multiple assignment or a loop key; ("loop", index of
    the iterated name) for `for k, name in pairs|ipairs(X) do`."""
    found = []
    for i, t in enumerate(tokens):
        if t.kind != "name" or t.text != name or _is_field(tokens, i):
            continue
        prev = tokens[i - 1].text if i > 0 else ""
        nxt = tokens[i + 1].text if i + 1 < len(tokens) else ""
        # for <names> in ...
        k = i
        while k > 0 and tokens[k - 1].text == "," and tokens[k - 2].kind == "name":
            k -= 2
        in_for = k > 0 and tokens[k - 1].text == "for"
        if in_for:
            names_end = i
            while tokens[names_end + 1].text == ",":
                names_end += 2
            position = (i - k) // 2
            source = tokens[names_end + 2 : names_end + 6]
            ok = (
                tokens[names_end + 1].text == "in"
                and position == 1
                and len(source) == 4
                and source[0].text in {"pairs", "ipairs"}
                and source[1].text == "("
                and source[2].kind == "name"
                and source[3].text == ")"
            )
            found.append(("loop", names_end + 4) if ok else ("multi", i))
            continue
        if nxt == "," or (prev == "," and tokens[k - 1].text == "local"):
            # part of a name list; is it being assigned?
            j = i
            while tokens[j + 1].text == ",":
                j += 2
            if tokens[j + 1].text == "=" or prev == ",":
                found.append(("multi", i))
            continue
        if nxt == "=":
            found.append(("assign", i + 2))
    return found


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_pairs_and_next_never_walk_an_api_result(path: Path) -> None:
    """Walking the keys of an API result could copy unknown fields (a name,
    a GUID) into the capture. On tokens, across line breaks: the argument of
    `pairs`/`next` is a bare name, and EVERY assignment or binding of that name
    in the file must be classifiable as a table constructor `{...}`, `nil`, a
    std-declared client table (`Enum.X`, `Constants`, optionally as
    `type(Enum) == "table" and Enum.X or nil`), or the value of a
    `for _, v in pairs|ipairs(Y)` loop over a name that passes the same test.
    A name with no binding must be a std-declared client table. Function
    parameters, multiple assignments and anything else fail (closed). In
    Crew.lua only (M19-01, PAIRS), `pairs` may also walk `BOOK.players`
    or `BOOK.current`, written out or through a local bound once,
    directly, to one of them."""
    problems = _pairs_problems(path, _tokens(path))
    assert not problems, problems


_CREW_ROW_BINDING = "local okRow , row = pcall ( C_PvP . GetScoreInfo ,"


def _crew_row_walk(path: Path, tokens: list[Token], i: int) -> bool:
    """M20-04: is the `pairs` at i a walk of a scoreboard row in a schema 2
    Crew.lua? M20-11 (owner decision, 2026-11-20): no longer only in the
    pinned pvpSnapshot; in any function that binds `row` as
    _CREW_ROW_BINDING (what is done with each value is the secret rule's,
    `_crew_score_problems`)."""
    if CREW_ROW_WALK not in _relaxed(path, "PAIRS") or _crew_pins(tokens).schema != 2:
        return False
    if _render(tokens[i : i + 4]) != "pairs ( row )":
        return False
    flow = _Flow(path, tokens)
    fn = flow.function_at(i)
    if fn is None:
        return False
    n = len(_CREW_ROW_BINDING.split(" "))
    return any(
        _render(tokens[k : k + n]) == _CREW_ROW_BINDING and flow.function_at(k) is fn
        for k in range(fn.start, min(i, len(tokens) - n))
    )


def _crew_book_walk(path: Path, tokens: list[Token], i: int) -> bool:
    """Crew.lua: is the `pairs` at i a walk of one of the book's two tables,
    written out or through a local bound once and directly to one?"""
    walks = _relaxed(path, "PAIRS")
    if tokens[i].text != "pairs" or not walks or tokens[i + 1].text != "(":
        return False
    close = next((j for j in range(i + 2, len(tokens)) if tokens[j].text == ")"), len(tokens))
    inside = "".join(x.text for x in tokens[i + 2 : close])
    if inside in walks and all(x.kind != "string" for x in tokens[i + 2 : close]):
        return True
    if close != i + 3 or tokens[i + 2].kind != "name":
        return False
    braces: list[str] = []
    in_constructor = set()
    for k, x in enumerate(tokens):
        if x.kind == "op" and x.text in {"{", "(", "["}:
            braces.append(x.text)
        elif x.kind == "op" and x.text in {"}", ")", "]"} and braces:
            braces.pop()
        elif braces and braces[-1] == "{":
            in_constructor.add(k)
    # A constructor key (`{ players = {} }`) binds nothing.
    found = [
        b
        for b in _bindings(tokens, tokens[i + 2].text)
        if not (
            b[0] == "assign" and b[1] - 2 in in_constructor and tokens[b[1] - 3].text in {"{", ","}
        )
    ]
    if len(found) != 1 or found[0][0] != "assign":
        return False
    j = found[0][1]
    value = "".join(x.text for x in tokens[j : j + 3])
    return (
        value in walks
        and tokens[j - 3].text == "local"
        and all(x.kind == "name" or x.text == "." for x in tokens[j : j + 3])
        and _ends_expression(tokens, j + 3)
    )


def _pairs_problems(path: Path, tokens: list[Token]) -> list[str]:
    std = _std_globals()
    params = _function_params(tokens)
    problems = []

    def allowed(name: str, depth: int = 0) -> bool:
        if depth > 4 or name in params:
            return False
        found = _bindings(tokens, name)
        if not found:
            return name in std or any(g.startswith(name + ".") for g in std)
        for kind, j in found:
            if kind == "assign" and _classify_rhs(tokens, j, std):
                continue
            if kind == "loop" and allowed(tokens[j].text, depth + 1):
                continue
            return False
        return True

    for i, t in enumerate(tokens):
        if t.kind != "name" or t.text not in {"pairs", "next"} or _is_field(tokens, i):
            continue
        if _crew_book_walk(path, tokens, i) or _crew_row_walk(path, tokens, i):
            continue
        arg, close = tokens[i + 2], tokens[i + 3]
        if tokens[i + 1].text != "(" or arg.kind != "name" or close.text != ")":
            problems.append(f"{_where(path, t)} {t.text} over {_render(tokens[i + 1 : i + 6])!r}")
        elif not allowed(arg.text):
            problems.append(
                f"{_where(path, t)} {t.text}({arg.text}): a binding is not a built or client table"
            )
    return problems


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_std_globals_are_never_indexed_by_brackets(path: Path) -> None:
    """A global declared in wow_client.yml (client or the addon's own) is never
    directly followed by `[`, so no field of it is reached by a computed key."""
    tokens = _tokens(path)
    top = _std_top_level()
    bad = [
        f"{_where(path, t)} {t.text}["
        for i, t in enumerate(tokens[:-1])
        if t.kind == "name"
        and t.text in top
        and not _is_field(tokens, i)
        and tokens[i + 1].text == "["
    ]
    assert not bad, bad


_CONCAT = re.compile(r"(?<!\.)\.\.(?!\.)")  # the `..` operator, not the `...` vararg


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_concatenated_value_is_used_as_an_index(path: Path) -> None:
    """Checks, line by line: a name on the left of an assignment whose
    right-hand side contains the `..` operator (on that line or on
    continuation lines starting with `..`) is never a token inside `[...]`
    anywhere in the file. With the `..`-inside-`[...]` rule above, this keeps
    concatenated strings out of index expressions; it does not trace values
    through function calls or table fields. In Crew.lua only, one name bound
    to exactly `<name> .. "-" .. <name>` on one line
    (`local key = name .. "-" .. realm`) may be used as an index (M19-01)."""
    bad = _concat_value_problems(path, path.read_text(encoding="utf-8"))
    assert not bad, bad


def _concat_value_problems(path: Path, text: str) -> list[str]:
    tokens = tokenize(text)
    literals = _relaxed(path, "CONCAT_INDEX")
    # Line-based: `local a, b = <expr>` / `a = <expr>`, plus continuation lines
    # that start with `..` (they extend the previous assignment).
    source = [re.sub(r"--.*$", "", line) for line in text.splitlines()]
    assignment = re.compile(
        r"^\s*(?:local\s+)?([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=(?!=)(.*)$"
    )
    assigned: set[str] = set()
    last: list[str] = []
    for line in source:
        match = assignment.match(line)
        if match:
            last = [name.strip() for name in match.group(1).split(",")]
            if _CONCAT.search(re.sub(r'"[^"]*"', '""', match.group(2))):
                # Crew.lua: one name bound to exactly `<name> .. "-" .. <name>`.
                exact = literals and len(last) == 1 and _NAME_DASH_NAME.match(match.group(2))
                if not exact:
                    assigned.update(last)
        elif _CONCAT.match(line.strip()):
            assigned.update(last)  # a continuation is never the exact shape
        elif line.strip():
            last = []
    bad = []
    brackets = 0
    for t in tokens:
        if t.text == "[":
            brackets += 1
        elif t.text == "]":
            brackets -= 1
        elif brackets > 0 and t.kind == "name" and t.text in assigned:
            bad.append(f"{_where(path, t)} {t.text} (assigned a concatenation) used as an index")
    return bad


# --- Stored values: every value from outside the addon is type-checked (M11-13) -
#
# A small data-flow pass over the tokens of every source. Nothing is evaluated.
# A value is *tainted* when it can hold something the addon did not build: a
# client global or anything read from one, a call to a client or unknown
# function, `...`, a function parameter, `WowLabCharDB`/`WowLabDB` as loaded, a
# call to an addon function that can return a tainted value, or a local that is
# bound or assigned any of those anywhere in its scope. Every *store* (a table
# constructor entry, an assignment to a field or a global) must hold an
# untainted value, or exactly a name or field chain X directly inside an
# `if`/`elseif` whose condition has a top-level `and`-conjunct
# `type(X) == "number"` ("string", "boolean") with X's root not reassigned in
# that block. Untainted: literals, constructors (each entry is itself a store),
# function literals, comparisons, `not`, `#`, `type(...)`, the inline form
# `type(X) == "number" and X or nil`, calls into Lua's string (except gmatch)
# and math libraries, the addon namespace `ns` and its fields, and calls to
# addon functions whose every `return` passes the same test (so `ns.Number`,
# `ns.String`, `ns.Bool`, `ns.Numbers` and `ns.Absent` are untainted, and
# `ns.Call`/`ns.Fn` are not). Always tainted: a `pcall(...)` result, a call
# through an index or parentheses (`fns[1]()`, `(h)()`), a method call, and a
# Lua library read as a value (`string`). Table keys are not checked. Rules that
# keep this sound: no `table.insert`/`tinsert` (a store through a call), every
# `ns.Section` takes a literal `{ ... }`, each `ns` function is defined once,
# and no source rebinds `type`, `pcall`, the other builtins it trusts, or `ns`.

_KEYWORDS = frozenset(
    {
        "and",
        "break",
        "do",
        "else",
        "elseif",
        "end",
        "false",
        "for",
        "function",
        "goto",
        "if",
        "in",
        "local",
        "nil",
        "not",
        "or",
        "repeat",
        "return",
        "then",
        "true",
        "until",
        "while",
    }
)
_COMPARISON = frozenset({"==", "~=", "<", ">", "<=", ">="})
# Operators that bind at least as tightly as `==`: a chain next to one is not a
# side of `==` (`x .. r.a == "open"` compares the concatenation).
_TIGHTER = frozenset({"..", "+", "-", "*", "/", "%", "^", "#", "not", *_COMPARISON})
_SCALAR_TYPES = frozenset({"number", "string", "boolean"})
# Lua 5.1 globals the addon calls. A call's result is as tainted as its
# arguments, except `type`, and string (not gmatch) and math, which return
# strings and numbers.
_LUA_BUILTINS = frozenset(
    {
        "type",
        "ipairs",
        "pairs",
        "next",
        "select",
        "tostring",
        "tonumber",
        "unpack",
        "pcall",
        "print",
        "error",
        "string",
        "table",
        "math",
    }
)
_OPEN = {"(": ")", "[": "]", "{": "}"}
_TOP = "<top level>"

# Core.lua plumbing that moves the addon's own tables around (the section and
# handler registries, gathered records into ns.state and WowLabCharDB). These
# stores are not checked; a function holding one is treated as returning a
# tainted value. Keyed (file, function, store as rendered tokens); each must
# match exactly once, so an edited plumbing line fails until reviewed here.
PLUMBING_STORES: dict[tuple[str, str, str], str] = {
    ("Core.lua", "ns.Pack", "{} = ..."): "ns.Pack's vararg container; its result is tainted",
    ("Core.lua", "ns.Pack", 'packed . n = select ( "#" , ... )'): "the vararg count",
    ("Core.lua", "ns.On", "list [ # list + 1 ] = fn"): "the event-handler registry; never saved",
    (
        "Core.lua",
        "ns.Section",
        "ns . sections [ # ns . sections + 1 ] = section",
    ): "the section registry; never saved",
    # M11-21: a section registers its events in `listen` at ADDON_LOADED, not
    # in ns.Section, so a switched-off section never registers one.
    (
        "Core.lua",
        "listen",
        "section . events_unregistered = section . events_unregistered or { }",
    ): "the section's list of events the client refused",
    (
        "Core.lua",
        "listen",
        "missing [ # missing + 1 ] = event",
    ): "an event name from the section's own `events` literal",
    (
        "Core.lua",
        "gather",
        "ns . state [ section . key ] = record",
    ): "a gather result, checked as a table (test_every_gather_and_carry_returns_checked_values)",
    (
        "Core.lua",
        "place",
        "node [ path [ # path ] ] = record",
    ): "a record from ns.state or ns.Absent, placed into WowLabCharDB",
    (
        "Core.lua",
        'ns.On("ADDON_LOADED")',
        "ns . state [ section . key ] = record",
    ): "a carry result, checked as a table (test_every_gather_and_carry_returns_checked_values)",
}


@dataclass
class _Decl:
    """A local, parameter or loop variable, visible from `start` to `stop`."""

    name: str
    start: int
    stop: int
    fixed: bool | None  # a fixed taint, or None: computed from `sources`
    sources: list[tuple[int, int]]
    function: int | None = None  # for `local function f`: the head of f


@dataclass(frozen=True)
class _Block:
    kind: str  # "function", "loop", "do", "repeat", "if", "elseif", "else"
    head: int  # the keyword that opens it
    start: int  # the first token inside it
    stop: int  # the token that closes it (end, until, elseif, else)
    cond: tuple[int, int] | None  # for "if"/"elseif": the condition's token range


@dataclass(frozen=True)
class _Store:
    text: str  # rendered: "<target> = <value>"
    at: int
    value: tuple[int, int]


class _Flow:
    """Scopes, blocks, bindings and stores of one source file, on tokens."""

    def __init__(self, path: Path, tokens: list[Token]) -> None:
        self.path = path
        self.tokens = tokens
        self.pair: dict[int, int] = {}
        self.opener: list[int | None] = []
        self._brackets()
        self.blocks = self._blocks()
        self.functions = {b.head: b for b in self.blocks if b.kind == "function"}
        self.labels = {head: self._label(head) for head in self.functions}
        self.decls: dict[str, list[_Decl]] = {}
        self.namespace: _Decl | None = None
        self.stores: list[_Store] = []
        self.assigned: list[tuple[str, int]] = []  # (root name, token) of every binding
        self.rebound: list[tuple[str, int]] = []  # `name = ...` and `name.x = ...` targets
        self.skip: set[int] = set()
        self._scan()

    # structure

    def _brackets(self) -> None:
        stack: list[int] = []
        for k, t in enumerate(self.tokens):
            self.opener.append(stack[-1] if stack else None)
            if t.kind != "op":
                continue
            if t.text in _OPEN:
                stack.append(k)
            elif t.text in {")", "]", "}"}:
                o = stack.pop()
                self.pair[o], self.pair[k] = k, o
        if stack:
            raise ValueError(f"unbalanced bracket in {self.path.name}")

    def block_end(self, i: int) -> int:
        depth = 0
        for k in range(i, len(self.tokens)):
            t = self.tokens[k]
            if t.kind != "name":
                continue
            if t.text in {"function", "if", "do", "repeat"}:
                depth += 1
            elif t.text in {"end", "until"}:
                depth -= 1
                if depth == 0:
                    return k
        raise ValueError(f"no matching end for line {self.tokens[i].line}")

    def _blocks(self) -> list[_Block]:
        blocks: list[_Block] = []
        stack: list[list] = []  # [kind, head, start, cond_start, cond]

        def close(entry: list, k: int) -> None:
            blocks.append(_Block(entry[0], entry[1], entry[2], k, entry[4]))

        for k, t in enumerate(self.tokens):
            if t.kind != "name":
                continue
            x = t.text
            if x == "function":
                paren = k + 1
                while self.tokens[paren].text != "(":
                    paren += 1
                stack.append(["function", k, self.pair[paren] + 1, None, None])
            elif x in {"for", "while"}:
                stack.append(["loop", k, None, None, None])
            elif x == "do":
                if stack and stack[-1][0] == "loop" and stack[-1][2] is None:
                    stack[-1][2] = k + 1
                else:
                    stack.append(["do", k, k + 1, None, None])
            elif x == "repeat":
                stack.append(["repeat", k, k + 1, None, None])
            elif x in {"if", "elseif"}:
                if x == "elseif":
                    close(stack.pop(), k)
                stack.append([x, k, None, k + 1, None])
            elif x == "then":
                stack[-1][2] = k + 1
                stack[-1][4] = (stack[-1][3], k)
            elif x == "else":
                close(stack.pop(), k)
                stack.append(["else", k, k + 1, None, None])
            elif x in {"end", "until"}:
                close(stack.pop(), k)
        if stack:
            raise ValueError(f"unclosed block in {self.path.name}")
        return blocks

    def _label(self, head: int) -> str:
        t = self.tokens
        if t[head + 1].text != "(":
            k = head + 1
            while t[k].text != "(":
                k += 1
            return "".join(x.text for x in t[head + 1 : k])
        if head >= 2 and t[head - 1].text == "=" and t[head - 2].kind == "name":
            return t[head - 2].text
        if (
            head >= 6
            and t[head - 1].text == ","
            and t[head - 2].kind == "string"
            and [x.text for x in t[head - 6 : head - 2]] == ["ns", ".", "On", "("]
        ):
            return f'ns.On("{t[head - 2].text}")'
        return f"function@{t[head].line}"

    def function_at(self, i: int) -> _Block | None:
        inside = [b for b in self.functions.values() if b.head <= i <= b.stop]
        return max(inside, key=lambda b: b.head) if inside else None

    def label_at(self, i: int) -> str:
        fn = self.function_at(i)
        return self.labels[fn.head] if fn else _TOP

    def block_at(self, i: int) -> _Block | None:
        inside = [b for b in self.blocks if b.start <= i < b.stop]
        return max(inside, key=lambda b: b.start) if inside else None

    def statement_level(self, k: int) -> bool:
        """Is token k outside every bracket opened in its own function body?"""
        o = self.opener[k]
        fn = self.function_at(k)
        return o is None or (fn is not None and o < fn.start)

    def chain_end(self, s: int) -> int | None:
        """End of the name/field chain `a.b[c].d` starting at s (no calls)."""
        t = self.tokens
        if s >= len(t) or t[s].kind != "name" or t[s].text in _KEYWORDS or _is_field(t, s):
            return None
        k = s + 1
        while k < len(t):
            if (
                t[k].text == "."
                and t[k].kind == "op"
                and k + 1 < len(t)
                and t[k + 1].kind == "name"
            ):
                k += 2
            elif t[k].text == "[" and t[k].kind == "op":
                k = self.pair[k] + 1
            else:
                break
        return k

    def expr_end(self, j: int) -> int:
        t = self.tokens
        k, operand = j, False
        while k < len(t):
            x = t[k]
            if x.kind == "op":
                if x.text in _OPEN:
                    k, operand = self.pair[k] + 1, True
                    continue
                if x.text in {")", "]", "}", ",", ";", "="}:
                    return k
                if x.text == "...":
                    if operand:
                        return k
                    k, operand = k + 1, True
                    continue
                k, operand = k + 1, False
                continue
            if x.kind == "string" or x.kind == "number":
                if operand and x.kind != "string":
                    return k
                k, operand = k + 1, True
                continue
            if x.text == "function":
                if operand:
                    return k
                k, operand = self.block_end(k) + 1, True
                continue
            if x.text in {"and", "or", "not"}:
                k, operand = k + 1, False
                continue
            if x.text in {"nil", "true", "false"}:
                if operand:
                    return k
                k, operand = k + 1, True
                continue
            if x.text in _KEYWORDS:
                return k
            if operand and t[k - 1].text not in {".", ":"}:
                return k
            k, operand = k + 1, True
        return len(t)

    def exprlist(self, j: int) -> list[tuple[int, int]]:
        out = []
        while True:
            e = self.expr_end(j)
            if e > j:
                out.append((j, e))
            if e < len(self.tokens) and self.tokens[e].text == "," and self.tokens[e].kind == "op":
                j = e + 1
                continue
            return out

    def targets(self, eq: int) -> int:
        """Start of the target list of the assignment whose `=` is at eq."""
        t = self.tokens
        k, start = eq - 1, eq
        while k >= 0:
            x = t[k]
            if x.kind == "op" and x.text == "]":
                if k + 1 < eq and t[k + 1].kind == "name":
                    break
                k = self.pair[k]
                start = k
                k -= 1
                continue
            if x.kind == "op" and x.text in {".", ","}:
                start, k = k, k - 1
                continue
            if x.kind == "name" and x.text not in _KEYWORDS:
                if k + 1 < eq and t[k + 1].kind == "name":
                    break
                start, k = k, k - 1
                continue
            break
        return start

    def entries(self, open_: int) -> list[tuple[str, int, int]]:
        """(key, value start, value end) for each entry of the constructor at open_."""
        t = self.tokens
        close = self.pair[open_]
        out, k = [], open_ + 1
        while k < close:
            a = j = k
            while j < close:
                x = t[j]
                if x.kind == "op" and x.text in _OPEN:
                    j = self.pair[j] + 1
                elif x.kind == "name" and x.text == "function":
                    j = self.block_end(j) + 1
                elif x.kind == "op" and x.text in {",", ";"}:
                    break
                else:
                    j += 1
            if j > a:
                if t[a].kind == "name" and a + 1 < j and t[a + 1].text == "=":
                    out.append((f"{{{t[a].text}}}", a + 2, j))
                elif t[a].text == "[" and t[a].kind == "op" and t[self.pair[a] + 1].text == "=":
                    key = _render(t[a + 1 : self.pair[a]])
                    out.append((f"{{[{key}]}}", self.pair[a] + 2, j))
                else:
                    out.append(("{}", a, j))
            k = j + 1
        return out

    def scope_stop(self, k: int) -> int:
        block = self.block_at(k)
        return block.stop if block else len(self.tokens)

    def resolve(self, name: str, i: int) -> _Decl | None:
        visible = [d for d in self.decls.get(name, []) if d.start <= i <= d.stop]
        return max(visible, key=lambda d: d.start) if visible else None

    def _declare(self, decl: _Decl, at: int) -> None:
        self.decls.setdefault(decl.name, []).append(decl)
        self.assigned.append((decl.name, at))

    # bindings and stores

    def _scan(self) -> None:
        t = self.tokens
        for fn in self.functions.values():
            paren = fn.head + 1
            while t[paren].text != "(":
                paren += 1
            for k in range(paren + 1, self.pair[paren]):
                if t[k].kind == "name":
                    self._declare(_Decl(t[k].text, fn.head, fn.stop, True, []), k)
        for k, x in enumerate(t):
            if x.kind == "name" and x.text == "local":
                self._local(k)
            elif x.kind == "name" and x.text == "for":
                self._for(k)
        for k, x in enumerate(t):
            if x.kind == "op" and x.text == "=" and self.statement_level(k):
                self._assignment(k)
            elif x.kind == "op" and x.text == "{":
                for key, s, e in self.entries(k):
                    self.stores.append(_Store(f"{key} = {_render(t[s:e])}", s, (s, e)))
        for k, x in enumerate(t):
            if x.kind == "op" and x.text in _COMPARISON:
                self.skip.update(range(self._left(k), k))
                self.skip.update(range(k + 1, self._right(k + 1)))
            elif (x.kind == "op" and x.text == "#") or (x.kind == "name" and x.text == "not"):
                self.skip.update(range(k + 1, self._right(k + 1)))

    def _local(self, k: int) -> None:
        t = self.tokens
        if t[k + 1].text == "function":
            name = k + 2
            decl = _Decl(t[name].text, name, self.scope_stop(k), False, [], function=k + 1)
            self._declare(decl, name)
            return
        names = [k + 1]
        while t[names[-1] + 1].text == ",":
            names.append(names[-1] + 2)
        after = names[-1] + 1
        exprs = self.exprlist(after + 1) if after < len(t) and t[after].text == "=" else []
        start = exprs[-1][1] if exprs else after
        for n, at in enumerate(names):
            source = exprs[min(n, len(exprs) - 1)] if exprs else None
            decl = _Decl(t[at].text, start, self.scope_stop(k), None, [source] if source else [])
            if source is None:
                decl.fixed = False
            elif (
                t[at].text == "ns"
                and self.function_at(k) is None
                and _render(t[source[0] : source[1]]) == "..."
            ):
                decl.fixed = False  # `local _, ns = ...`: the addon's own namespace
                self.namespace = decl
            self._declare(decl, at)

    def _for(self, k: int) -> None:
        t = self.tokens
        loop = next(b for b in self.blocks if b.head == k)
        names = [k + 1]
        while t[names[-1] + 1].text == ",":
            names.append(names[-1] + 2)
        after = names[-1] + 1
        if t[after].text == "=":
            for at in names:
                self._declare(_Decl(t[at].text, loop.start, loop.stop, False, []), at)
            return
        s, e = after + 1, loop.start - 1  # the iterator expression, `in` .. `do`
        inner = None
        if t[s].text in {"ipairs", "pairs"} and t[s + 1].text == "(" and self.pair[s + 1] == e - 1:
            inner = (s + 2, e - 1)
        for n, at in enumerate(names):
            if inner is not None and t[s].text == "ipairs" and n == 0:
                decl = _Decl(t[at].text, loop.start, loop.stop, False, [])
            else:
                decl = _Decl(t[at].text, loop.start, loop.stop, None, [inner or (s, e)])
            self._declare(decl, at)

    def _assignment(self, eq: int) -> None:
        t = self.tokens
        start = self.targets(eq)
        if start == eq or (start > 0 and t[start - 1].text in {"local", "for"}):
            return
        targets: list[tuple[int, int]] = []
        a = k = start
        while k < eq:
            if t[k].kind == "op" and t[k].text == "[":
                k = self.pair[k] + 1
                continue
            if t[k].kind == "op" and t[k].text == ",":
                targets.append((a, k))
                a = k + 1
            k += 1
        targets.append((a, eq))
        exprs = self.exprlist(eq + 1)
        for n, (ts, te) in enumerate(targets):
            value = exprs[min(n, len(exprs) - 1)] if exprs else (eq + 1, eq + 1)
            root = t[ts].text
            self.assigned.append((root, eq))
            self.rebound.append((root if te == ts + 1 else root + ".", ts))
            if te == ts + 1:
                decl = self.resolve(root, eq)
                if decl is not None:
                    if decl.fixed is False:
                        decl.fixed = None
                    decl.sources.append(value)
                    continue
            text = f"{_render(t[ts:te])} = {_render(t[value[0] : value[1]])}"
            self.stores.append(_Store(text, ts, value))

    def _left(self, k: int) -> int:
        t = self.tokens
        j = k - 1
        while j >= 0:
            y = t[j]
            if y.kind == "op" and y.text in {")", "]", "}"}:
                o = self.pair[j]
                before = t[o - 1] if o > 0 else None
                if (
                    y.text != "}"
                    and before is not None
                    and (
                        (before.kind == "name" and before.text not in _KEYWORDS)
                        or (before.kind == "op" and before.text in {")", "]"})
                    )
                ):
                    j = o - 1
                    continue
                return o
            if y.kind == "name" and y.text not in _KEYWORDS:
                if j >= 2 and t[j - 1].kind == "op" and t[j - 1].text in {".", ":"}:
                    j -= 2
                    continue
                return j
            if y.kind in {"string", "number"} or y.text in {"nil", "true", "false", "..."}:
                return j
            return k
        return 0

    def _right(self, j: int) -> int:
        t = self.tokens
        n = len(t)
        while j < n and (
            (t[j].kind == "op" and t[j].text in {"-", "#"})
            or (t[j].kind == "name" and t[j].text == "not")
        ):
            j += 1
        if j >= n:
            return j
        x = t[j]
        if x.kind == "op" and x.text in {"(", "{"}:
            k = self.pair[j] + 1
        elif x.kind == "name" and x.text == "function":
            k = self.block_end(j) + 1
        elif (
            x.kind in {"string", "number"}
            or (
                x.kind == "name" and (x.text not in _KEYWORDS or x.text in {"nil", "true", "false"})
            )
            or x.text == "..."
        ):
            k = j + 1
        else:
            return j
        while k < n:
            y = t[k]
            if y.kind == "op" and y.text in {".", ":"} and k + 1 < n and t[k + 1].kind == "name":
                k += 2
            elif y.kind == "op" and y.text in {"[", "("}:
                k = self.pair[k] + 1
            else:
                break
        return k

    # the checks

    def inline(self, i: int, kinds: frozenset[str] = _SCALAR_TYPES) -> int | None:
        """If `type(X) == "<kind>" and X or nil` starts at i, the index of its `nil`."""
        t = self.tokens
        try:
            if t[i].text != "type" or t[i + 1].text != "(":
                return None
            xe = self.chain_end(i + 2)
            if xe is None or t[xe].text != ")":
                return None
            x = [(a.kind, a.text) for a in t[i + 2 : xe]]
            k = xe + 1
            if not (
                t[k].text == "=="
                and t[k + 1].kind == "string"
                and t[k + 1].text in kinds
                and t[k + 2].text == "and"
            ):
                return None
            m = k + 3 + len(x)
            if [(a.kind, a.text) for a in t[k + 3 : m]] != x:
                return None
            if not (t[m].text == "or" and t[m + 1].text == "nil"):
                return None
        except IndexError:
            return None
        if not _ends_expression(t, m + 2):
            return None
        if i > 0 and t[i - 1].text in _TIGHTER | {".", ":"}:
            return None
        return m + 1

    def has_type_conjunct(self, cond: tuple[int, int], x: list[tuple[str, str]]) -> bool:
        t = self.tokens
        conjuncts: list[list[tuple[str, str]]] = [[]]
        depth = 0
        for k in range(*cond):
            y = t[k]
            if y.kind == "op" and y.text in _OPEN:
                depth += 1
            elif y.kind == "op" and y.text in {")", "]", "}"}:
                depth -= 1
            if depth == 0 and y.kind == "name" and y.text == "or":
                return False
            if depth == 0 and y.kind == "name" and y.text == "and":
                conjuncts.append([])
            else:
                conjuncts[-1].append((y.kind, y.text))
        head = [("name", "type"), ("op", "("), *x, ("op", ")"), ("op", "==")]
        return any(
            c[:-1] == head and c[-1] in {("string", k) for k in _SCALAR_TYPES} for c in conjuncts
        )

    def guarded(self, s: int, e: int) -> bool:
        """Is tokens[s:e] a chain X stored inside `if type(X) == "<scalar>" and ... then`?"""
        if self.chain_end(s) != e:
            return False
        x = [(a.kind, a.text) for a in self.tokens[s:e]]
        root = self.tokens[s].text
        fn = self.function_at(s)
        floor = fn.head if fn else -1
        for b in self.blocks:
            if b.kind not in {"if", "elseif"} or b.cond is None or b.head < floor:
                continue
            if not (b.start <= s < b.stop):
                continue
            rebound = any(name == root and b.start <= at < b.stop for name, at in self.assigned)
            if not rebound and self.has_type_conjunct(b.cond, x):
                return True
        return False

    def tainted(self, s: int, e: int, program: _Program) -> bool:
        t = self.tokens
        i = s
        while i < e:
            x = t[i]
            if (
                x.kind == "name"
                and x.text == "type"
                and not _is_field(t, i)
                and i + 1 < e
                and t[i + 1].text == "("
            ):
                end = self.inline(i)
                i = (end if end is not None and end < e else self.pair[i + 1]) + 1
                continue
            if i in self.skip or x.kind in {"string", "number"}:
                i += 1
                continue
            if x.kind == "op":
                if x.text == "...":
                    return True
                if x.text in {"[", ")"}:
                    after = self.pair[i] + 1 if x.text == "[" else i + 1
                    k = after
                    while k < e:
                        if (
                            t[k].kind == "op"
                            and t[k].text == "."
                            and k + 1 < e
                            and t[k + 1].kind == "name"
                        ):
                            k += 2
                        elif t[k].kind == "op" and t[k].text == "[":
                            k = self.pair[k] + 1
                        else:
                            break
                    if k < e and (self.call_start(k) or (t[k].kind == "op" and t[k].text == ":")):
                        return (
                            True  # a call through an index or parentheses (`a[1].f()`, `a[1]:f()`)
                        )
                    i = after
                    continue
                i = self.pair[i] + 1 if x.text == "{" else i + 1
                continue
            if x.text == "function":
                i = self.block_end(i) + 1
                continue
            if x.text in _KEYWORDS or _is_field(t, i):
                i += 1
                continue
            j = i + 1
            while j + 1 < e and t[j].text == "." and t[j].kind == "op" and t[j + 1].kind == "name":
                j += 2
            if j < e and t[j].kind == "op" and t[j].text == ":":
                return True  # a method call on some object: unknown function
            is_call = j < e and (
                (t[j].kind == "op" and t[j].text in {"(", "{"}) or t[j].kind == "string"
            )
            if not is_call:
                if program.name_tainted(self, x.text, i):
                    return True
                i = j
                continue
            verdict = program.call(self, i, j)
            args_end = self.pair[j] + 1 if t[j].kind == "op" else j + 1
            if verdict is True:
                return True
            if verdict is None and t[j].text == "(" and self.tainted(j + 1, args_end - 1, program):
                return True
            i = args_end
            while i < e and t[i].kind == "op" and t[i].text in {".", "[", ":", "("}:
                if t[i].text in {":", "("}:
                    return True  # a call on a call's result
                i = i + 2 if t[i].text == "." else self.pair[i] + 1
        return False

    def call_start(self, k: int) -> bool:
        t = self.tokens
        return k < len(t) and (
            (t[k].kind == "op" and t[k].text in {"(", "{"}) or t[k].kind == "string"
        )

    def returns(self, head: int) -> list[tuple[int, int]]:
        t = self.tokens
        out = []
        for k in range(head, self.functions[head].stop):
            if t[k].kind == "name" and t[k].text == "return":
                fn = self.function_at(k)
                if fn is not None and fn.head == head:
                    out += self.exprlist(k + 1)
        return out


class _Program:
    """Every source's flow, with taint and function safety solved together."""

    def __init__(self, sources: list[Path]) -> None:
        self.flows = {p.name: _Flow(p, _tokens(p)) for p in sources}
        self.ns_functions: dict[str, tuple[str, int]] = {}
        for name, flow in self.flows.items():
            for head, label in flow.labels.items():
                if label.startswith("ns.") and flow.tokens[head + 1].text == "ns":
                    self.ns_functions[label[3:]] = (name, head)
        self.hits: list[tuple[str, str, str]] = []
        self.unsafe: set[tuple[str, int]] = set()
        for name, flow in self.flows.items():
            for store in flow.stores:
                key = (name, flow.label_at(store.at), store.text)
                if key in PLUMBING_STORES:
                    self.hits.append(key)
                    fn = flow.function_at(store.at)
                    if fn is not None:
                        self.unsafe.add((name, fn.head))
        self.tainted: set[int] = set()
        changed = True
        while changed:
            changed = False
            for name, flow in self.flows.items():
                for decls in flow.decls.values():
                    for d in decls:
                        if (
                            d.fixed is None
                            and id(d) not in self.tainted
                            and any(flow.tainted(s, e, self) for s, e in d.sources)
                        ):
                            self.tainted.add(id(d))
                            changed = True
                for head in flow.functions:
                    if (name, head) not in self.unsafe and not all(
                        self.value_ok(flow, s, e) for s, e in flow.returns(head)
                    ):
                        self.unsafe.add((name, head))
                        changed = True

    def value_ok(self, flow: _Flow, s: int, e: int) -> bool:
        return not flow.tainted(s, e, self) or flow.guarded(s, e)

    def store_ok(self, flow: _Flow, store: _Store) -> bool:
        key = (flow.path.name, flow.label_at(store.at), store.text)
        return key in PLUMBING_STORES or self.value_ok(flow, *store.value)

    def name_tainted(self, flow: _Flow, name: str, i: int) -> bool:
        decl = flow.resolve(name, i)
        if decl is None:
            return True  # client, saved, unknown globals, and Lua libraries read as values
        return decl.fixed is True or (decl.fixed is None and id(decl) in self.tainted)

    def call(self, flow: _Flow, i: int, j: int) -> bool | None:
        """True: the call's result is tainted; False: it is not; None: as tainted
        as its arguments."""
        names = [flow.tokens[k].text for k in range(i, j, 2)]
        decl = flow.resolve(names[0], i)
        if decl is not None:
            if decl is flow.namespace and len(names) == 2 and names[1] in self.ns_functions:
                return self.ns_functions[names[1]] in self.unsafe
            if decl.function is not None and decl.fixed is False and len(names) == 1:
                return (flow.path.name, decl.function) in self.unsafe
            return True
        if names[0] == "type" and len(names) == 1:
            return False
        if names[0] == "pcall":
            return True  # it calls its first argument, whatever that is
        if names[0] == "math" or (names[0] == "string" and names[1:] != ["gmatch"]):
            return len(names) == 1
        return None if names[0] in _LUA_BUILTINS else True


def _program() -> _Program:
    try:
        return _Program(SOURCES)
    except (ValueError, IndexError, KeyError, StopIteration) as err:  # fail closed
        raise AssertionError(f"the sources could not be analysed: {err!r}") from err


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_every_stored_value_is_type_checked(path: Path) -> None:
    """Security review of M11-01 (deferred to M11-13): no value the addon did
    not build (an API return, a client constant, a field of an API table, a
    parameter, the loaded file) is stored unless it is type-checked as a
    scalar where it is stored: `ns.Number(...)`, `ns.String(...)`,
    `ns.Bool(...)`, the inline form, or a direct store inside
    `if type(X) == "<scalar>" ... then`. So a whole API table is never written.
    See the comment above `_KEYWORDS` for the rules."""
    program = _program()
    flow = program.flows[path.name]
    problems = [
        f"{_where(path, flow.tokens[store.at])} in {flow.label_at(store.at)}: "
        f"`{store.text}` stores a value that is not type-checked"
        for store in flow.stores
        if not program.store_ok(flow, store)
    ]
    assert not problems, problems


def test_plumbing_exemptions_match_exactly_once() -> None:
    program = _program()
    counts = {key: program.hits.count(key) for key in PLUMBING_STORES}
    assert all(n == 1 for n in counts.values()), counts


def test_every_gather_and_carry_returns_checked_values() -> None:
    """ns.state holds only what a section's `gather` or `carry` returns (the
    plumbing above moves it into WowLabCharDB). Each of them is a function
    literal or a local function whose every return value passes the stored-value
    rules, so neither can hand back a table from the client or the file."""
    program = _program()
    problems = []
    sections = 0
    for name, flow in program.flows.items():
        t = flow.tokens
        for k in range(len(t) - 4):
            if [x.text for x in t[k : k + 5]] != ["ns", ".", "Section", "(", "{"]:
                continue
            sections += 1
            for key, s, e in flow.entries(k + 4):
                if key not in {"{gather}", "{carry}"}:
                    continue
                head = None
                if t[s].text == "function" and flow.block_end(s) == e - 1:
                    head = s
                elif e == s + 1 and t[s].kind == "name":
                    decl = flow.resolve(t[s].text, s)
                    head = decl.function if decl is not None else None
                if head is None:
                    problems.append(
                        f"{_where(flow.path, t[s])} {key[1:-1]} is not an addon function"
                    )
                elif (name, head) in program.unsafe:
                    problems.append(
                        f"{_where(flow.path, t[s])} {key[1:-1]} can return an unchecked value"
                    )
    assert sections, "expected ns.Section calls"
    assert not problems, problems


# Names the stored-value check trusts; no source may rebind them.
_TRUSTED_NAMES = _LUA_BUILTINS | {"ns"}


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_store_nothing_through_table_library(path: Path) -> None:
    """Review of M11-13: `table.insert(out, api_table)` stores through a call,
    which the stored-value check does not read. The addon appends with
    `t[#t + 1] = v`; `table` is used only as `table.sort` or (M20-11, the
    oldest match dropped) `table.remove`, which stores nothing, and `tinsert`
    (and a string naming either) never appears."""
    tokens = _tokens(path)
    bad = []
    for i, t in enumerate(tokens):
        if t.kind == "string" and t.text in {"insert", "tinsert"}:
            bad.append(f"{_where(path, t)} string {t.text!r}")
        if t.kind != "name" or _is_field(tokens, i):
            continue
        if t.text == "tinsert":
            bad.append(f"{_where(path, t)} tinsert")
        if t.text == "table" and [x.text for x in tokens[i + 1 : i + 3]] not in (
            [".", "sort"],
            [".", "remove"],
        ):
            bad.append(f"{_where(path, t)} {_render(tokens[i : i + 3])!r}")
    assert not bad, bad


def test_every_section_call_takes_a_literal_spec() -> None:
    """Review of M11-13: the gather/carry check reads the spec at the call site,
    so every `ns.Section` is exactly `ns.Section({ ... })`: not
    `ns.Section(spec)`, `ns.Section{ ... }` or `ns.Section(factory())`. The
    namespace is never indexed by brackets, and no string literal is
    "Section", so the function is not reached another way."""
    program = _program()
    bad = []
    for flow in program.flows.values():
        t = flow.tokens
        for i, tok in enumerate(t):
            if tok.kind == "string" and tok.text == "Section":
                bad.append(f"{_where(flow.path, tok)} string 'Section'")
            if tok.kind != "name" or tok.text != "ns" or _is_field(t, i):
                continue
            if i + 1 < len(t) and t[i + 1].text == "[":
                bad.append(f"{_where(flow.path, tok)} ns[...]")
            if [x.text for x in t[i + 1 : i + 3]] != [".", "Section"]:
                continue
            if i > 0 and t[i - 1].text == "function":
                continue  # the definition in Core.lua
            ok = (
                i + 4 < len(t)
                and t[i + 3].kind == "op"
                and t[i + 3].text == "("
                and t[i + 4].kind == "op"
                and t[i + 4].text == "{"
                and flow.pair[i + 4] + 1 == flow.pair[i + 3]
            )
            if not ok:
                bad.append(f"{_where(flow.path, tok)} {_render(t[i : i + 6])!r}")
    assert not bad, bad


def test_every_ns_function_is_defined_once() -> None:
    """Security review of M11-13: a second `function ns.Numbers(list) return
    list end` (in a file loaded later) would replace the checked helper at run
    time. Each `ns.X` is defined by exactly one `function ns.X(` in all the
    sources, and never assigned as `ns.X = ...`."""
    program = _program()
    defined: dict[str, list[str]] = {}
    for flow in program.flows.values():
        for head, label in flow.labels.items():
            if label.startswith("ns.") and flow.tokens[head + 1].text == "ns":
                defined.setdefault(label, []).append(_where(flow.path, flow.tokens[head]))
    problems = [
        f"{name} defined {len(at)} times: {at}" for name, at in defined.items() if len(at) > 1
    ]
    for flow in program.flows.values():
        for store in flow.stores:
            head = store.text.split(" = ", 1)[0]
            if head.startswith("ns . ") and "ns." + head[5:].split(" ")[0] in defined:
                problems.append(f"{_where(flow.path, flow.tokens[store.at])} `{store.text}`")
    assert not problems, problems


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_trusted_names_are_never_rebound(path: Path) -> None:
    """Review of M11-13: `local type = function() return "number" end` would
    make every `type(X) == "number"` guard pass. No local, parameter, loop
    variable, local or global function, or assignment binds a name in
    `_LUA_BUILTINS` (`type`, `pcall`, `ipairs`, `pairs`, `next`, `select`,
    `tostring`, `tonumber`, `unpack`, `print`, `error`, `string`, `table`,
    `math`) or `ns`, and no field of `string`, `math` or `table` is assigned or
    defined (`function string.sub()`); the one `local _, ns = ...` at the top
    of each file is the exception."""
    program = _program()
    flow = program.flows[path.name]
    t = flow.tokens
    bad = []
    for name in _TRUSTED_NAMES:
        for decl in flow.decls.get(name, []):
            if decl is not flow.namespace:
                bad.append(f"{_where(path, t[min(decl.start, len(t) - 1)])} binds {name}")
    for name, at in flow.rebound:
        if name in _TRUSTED_NAMES or name in {"string.", "math.", "table."}:
            bad.append(f"{_where(path, t[at])} assigns {_render(t[at : at + 3])!r}")
    for head, label in flow.labels.items():
        root = t[head + 1]
        if root.kind == "name" and root.text in _TRUSTED_NAMES - {"ns"} and root.text != "(":
            bad.append(f"{_where(path, t[head])} function {label}")
    assert not bad, bad


_INDEXED_CALL_HEAD = """local _, ns = ...

local function characterData()
    return ns.Call(ns.Fn(C_BarberShop, "GetCurrentCharacterData"))
end

ns.Section({
    key = "zz",
    path = { "zz" },
    on_world = true,
    gather = function()
"""
_INDEXED_CALL_TAIL = """    end,
})
"""
# Constructed inputs (boundary cases for this grader, from the #106 security
# review), not addon sources: a call reached through an index and a field or
# method. Each must fail the stored-value or gather/carry check.
_INDEXED_CALLS = {
    "constructed-index-then-field-call": """        local readers = { { read = characterData } }
        local record = {}
        for i = 1, #readers do
            record[i] = readers[i].read()
        end
        return record
""",
    "constructed-index-then-method-call": """        local readers = { { read = characterData } }
        return { cd = readers[1]:read() }
""",
    "constructed-index-then-field-call-with-argument": """        local readers = { { read = characterData } }
        return { cd = readers[1].read(1) }
""",
}


def test_stored_value_check_taints_calls_through_an_index() -> None:
    """Constructed inputs (boundary tests of the stored-value check itself; no
    fixture argument, so the review probes can call it bare)."""
    missed = []
    for tag, body in sorted(_INDEXED_CALLS.items()):
        with tempfile.TemporaryDirectory(prefix="wowlab-addon-") as tmp:
            source = Path(tmp) / "Constructed.lua"
            source.write_text(_INDEXED_CALL_HEAD + body + _INDEXED_CALL_TAIL, encoding="utf-8")
            program = _Program([source])
        flow = program.flows[source.name]
        unchecked = [store.text for store in flow.stores if not program.store_ok(flow, store)]
        gather = next(head for head, label in flow.labels.items() if label == "gather")
        if not unchecked and (source.name, gather) not in program.unsafe:
            missed.append(tag)
    assert not missed, missed


# --- carry: the saved record is copied only through checked values ------------


def _carry_chain_ok(flow: _Flow, i: int, end: int) -> bool:
    """Is the chain tokens[i:end] (starting `r.`, `r[`, `c.` or `c[`) used in one
    of the four allowed forms, entirely on one line?"""
    t = flow.tokens

    def one_line(a: int, b: int) -> bool:
        return len({x.line for x in t[a : b + 1]}) == 1

    def after_ok(k: int) -> bool:
        return k >= len(t) or not (
            t[k].text in _TIGHTER | {".", ":", "[", "("} or t[k].kind in {"string", "number"}
        )

    # 1. The argument of type(...) or ipairs(...).
    if (
        t[i - 1].text == "("
        and t[i - 2].kind == "name"
        and t[i - 2].text in {"type", "ipairs"}
        and not _is_field(t, i - 2)
        and t[end].text == ")"
        and one_line(i - 2, end)
    ):
        return True
    # 2. One side of == "<literal>".
    if (
        t[end].text == "=="
        and t[end + 1].kind == "string"
        and t[i - 1].text not in _TIGHTER
        and after_ok(end + 2)
        and one_line(i, end + 1)
    ):
        return True
    if (
        t[i - 1].text == "=="
        and t[i - 2].kind == "string"
        and t[i - 3].text not in _TIGHTER
        and after_ok(end)
        and one_line(i - 2, end - 1)
    ):
        return True
    # 3. The second X of `type(X) == "number" and X or nil` (the first is form 1).
    start = i - 6 - (end - i)
    nil = flow.inline(start, frozenset({"number"})) if start >= 0 else None
    if nil is not None and nil == end + 1 and one_line(start, nil):
        return True
    # 4. The whole right-hand side of an assignment directly inside
    #    `if type(X) == "number" then`.
    block = flow.block_at(i)
    if (
        t[i - 1].kind == "op"
        and t[i - 1].text == "="
        and flow.statement_level(i - 1)
        and _ends_expression(t, end)
        and not (end < len(t) and t[end].text == ",")
        and block is not None
        and block.kind == "if"
        and block.cond is not None
    ):
        x = [(a.kind, a.text) for a in t[i:end]]
        cond = [(a.kind, a.text) for a in t[block.cond[0] : block.cond[1]]]
        head = [("name", "type"), ("op", "("), *x, ("op", ")"), ("op", "=="), ("string", "number")]
        target = flow.targets(i - 1)
        if cond == head and one_line(block.head, block.start - 1) and one_line(target, end - 1):
            return True
    return False


def test_carried_customization_is_rebuilt_from_checked_values() -> None:
    """Security review, M11-01 (moved onto tokens in M11-13): `carry` returns a
    NEW table (`out`, bound once to a constructor, never reassigned) or nil, and
    every chain starting `r.`, `r[`, `c.` or `c[` inside it appears only as the
    argument of `type(...)`/`ipairs(...)`, one side of `== "<literal>"`, the X
    in `type(X) == "number" and X or nil`, or the whole right-hand side of an
    assignment directly inside `if type(X) == "number" then`, each on one line.
    Anything else fails: a call argument (`table.insert(t, r.choices[i])`), a
    table-constructor entry, `#r.choices`, `~=`, or any form split across lines."""
    path = ADDON / "Customization.lua"
    tokens = _tokens(path)
    flow = _Flow(path, tokens)
    body_start, body_end = _carry_body(tokens)
    problems = []
    outs = 0
    for i in range(body_start, body_end):
        t = tokens[i]
        if t.kind != "name" or _is_field(tokens, i):
            continue
        if t.text == "return":
            value = [x.text for x in tokens[i + 1 : i + 2]]
            if value not in (["nil"], ["out"]) or not _ends_expression(tokens, i + 2):
                problems.append(
                    f"{_where(path, t)} carry returns {_render(tokens[i + 1 : i + 4])!r}"
                )
        if t.text == "out" and tokens[i + 1].text == "=":
            if tokens[i - 1].text == "local" and tokens[i + 2].text == "{":
                outs += 1
            else:
                problems.append(
                    f"{_where(path, t)} `out` bound to something other than a new table"
                )
        if t.text in {"r", "c"} and tokens[i + 1].text in {".", "["}:
            end = flow.chain_end(i)
            if end is None or not _carry_chain_ok(flow, i, end):
                problems.append(f"{_where(path, t)} {_render(tokens[i - 3 : (end or i) + 3])!r}")
    if outs != 1:
        problems.append(f"`out` must be bound once, to a new table ({outs} bindings)")
    assert not problems, problems


def _carry_body(tokens: list[Token]) -> tuple[int, int]:
    """Token range of the body of `carry = function(saved) ... end` (block
    keywords counted, so it does not depend on formatting)."""
    head = ["carry", "=", "function", "(", "saved", ")"]
    start = next(i for i in range(len(tokens)) if [t.text for t in tokens[i : i + 6]] == head)
    depth = 1
    for k in range(start + 6, len(tokens)):
        text = tokens[k].text if tokens[k].kind == "name" else ""
        if text in {"function", "if", "do", "repeat"}:
            depth += 1
        elif text in {"end", "until"}:
            depth -= 1
            if depth == 0:
                return start + 6, k
    raise AssertionError("carry has no matching end")


def test_carry_reads_saved_tables_only_through_fields() -> None:
    """Security review, M11-01 (round 3), on tokens: inside `carry`, the names
    `saved`, `r` and `c` (the saved file, its customization record and each
    saved choice) appear only immediately followed by `.` or `[`, as the sole
    argument of `type(...)` or `ipairs(...)`, or where they are bound
    (`local r =`, `for _, c in`). Passing one to a call (`table.insert(t, c)`),
    storing it as a value or putting it in a table constructor fails."""
    path = ADDON / "Customization.lua"
    tokens = _tokens(path)
    body_start, body_end = _carry_body(tokens)
    bad = []
    for i in range(body_start, body_end):
        t = tokens[i]
        if t.kind != "name" or t.text not in {"saved", "r", "c"} or _is_field(tokens, i):
            continue
        prev, nxt = tokens[i - 1].text, tokens[i + 1].text
        if nxt in {".", "["}:
            continue
        if prev == "(" and tokens[i - 2].text in {"type", "ipairs"} and nxt == ")":
            continue
        if t.text == "r" and prev == "local" and nxt == "=":
            continue
        if t.text == "c" and prev == "," and nxt == "in":
            continue
        bad.append(f"{_where(path, t)} {_render(tokens[i - 3 : i + 3])!r}")
    assert not bad, bad


# --- API inventory: std file, sources, README -----------------------------------


def _std_globals(std: Path | None = None) -> set[str]:
    names = set()
    in_globals = False
    for line in (std or STD).read_text(encoding="utf-8").splitlines():
        if line.startswith("globals:"):
            in_globals = True
            continue
        if in_globals and (match := re.match(r"^  ([A-Za-z_][\w.]*):\s*$", line)):
            names.add(match.group(1))
    return names


def _fn_lookups(tokens: list[Token]) -> set[str]:
    """`ns.Fn(Table, "Field")` pairs, as `Table.Field`."""
    found = set()
    for i in range(len(tokens) - 6):
        window = tokens[i : i + 7]
        if (
            [t.text for t in window[:4]] == ["ns", ".", "Fn", "("]
            and window[4].kind == "name"
            and window[5].text == ","
            and window[6].kind == "string"
        ):
            found.add(f"{window[4].text}.{window[6].text}")
    return found


def _dotted_references(tokens: list[Token]) -> set[str]:
    refs = set()
    for i, t in enumerate(tokens):
        if t.kind == "name" and not _is_field(tokens, i):
            refs.add(t.text)
            j, parts = i, [t.text]
            while (
                j + 2 < len(tokens) and tokens[j + 1].text == "." and tokens[j + 2].kind == "name"
            ):
                parts.append(tokens[j + 2].text)
                refs.add(".".join(parts))
                j += 2
    return refs


def test_every_fn_lookup_is_declared_in_the_std() -> None:
    lookups = set().union(*(_fn_lookups(_tokens(p)) for p in SOURCES))
    assert lookups, "expected ns.Fn lookups"
    missing = sorted(lookups - _std_globals())
    assert not missing, f"ns.Fn names not declared in {STD.name}: {missing}"


def test_every_std_global_is_used() -> None:
    used: set[str] = set()
    for path in SOURCES:
        tokens = _tokens(path)
        used |= _fn_lookups(tokens) | _dotted_references(tokens)
    unused = sorted(_std_globals() - used)
    assert not unused, f"declared in {STD.name} but unused: {unused}"


def test_readme_lists_every_api_with_a_status() -> None:
    rows = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*`([^`]+)`\s*\|(.*)\|\s*$", line)
        if match:
            rows[match.group(1)] = match.group(2)
    problems = []
    for name in sorted(_std_globals() - OWN_GLOBALS):
        row = rows.get(name)
        if row is None:
            problems.append(f"{name}: not in the README API table")
        elif KIT_STATUS not in row and VERIFY_STATUS not in row:
            problems.append(f"{name}: no status ({KIT_STATUS!r} or {VERIFY_STATUS!r})")
    # And the other way: the README lists nothing the std file does not declare.
    for name in sorted(set(rows) - _std_globals()):
        problems.append(f"{name}: in the README API table but not declared in {STD.name}")
    assert not problems, problems


# M11-21: the per-section off switch -------------------------------------------
#
# Source scans, not behaviour tests (constructed checks on the addon's own
# source; nothing here runs Lua, L3). A client assertion inside a section
# crashes the client at every login and a crash writes nothing, so the owner
# switches the section off with `/wowlab skip <section>`. These read the
# Core.lua tokens and check that a switched-off section cannot reach its
# gather, its carry or an event registration from any entry point: its events,
# entering the world, `/wowlab save` or PLAYER_LOGOUT.

PLAN = ROOT / "docs" / "LAB_PLAN.md"
RUNBOOK = ROOT / "docs" / "handoffs" / "M11-03.md"
SWITCHED_OFF_REASON = "switched off by the owner"
OFF_GUARD = "if section . off then return end"
SKIP_COMMANDS = ("skip", "unskip")


def _core() -> _Flow:
    core = ADDON / "Core.lua"
    return _Flow(core, _tokens(core))


def _body(flow: _Flow, label: str) -> str:
    heads = [h for h, name in flow.labels.items() if name == label]
    assert len(heads) == 1, f"expected one function {label} in {flow.path.name}, found {len(heads)}"
    return _render(flow.tokens[heads[0] : flow.block_end(heads[0]) + 1])


def _keys_in(path: Path) -> list[str]:
    """The `key` of every ns.Section spec in one source (a literal, or a
    file-level `local KEY = "..."`)."""
    keys = []
    text = path.read_text(encoding="utf-8")
    constants = dict(re.findall(r'^local ([A-Z_]+) = "([^"]+)"$', text, flags=re.M))
    for spec in re.findall(r"ns\.Section\(\{(.*?)\n\}\)", text, flags=re.S):
        match = re.search(r'^\s*key = (?:"([^"]+)"|([A-Z_]+)),$', spec, flags=re.M)
        assert match, f"{path.name}: an ns.Section spec without a key"
        keys.append(match.group(1) or constants[match.group(2)])
    return keys


def _section_keys() -> list[str]:
    return [key for path in SOURCES for key in _keys_in(path)]


def test_skip_section_keys_are_the_plan_keys() -> None:
    """The keys `/wowlab skip` accepts are the section keys of §13.1."""
    keys = _section_keys()
    assert len(keys) == len(set(keys)), keys
    assert set(keys) == {
        "gear",
        "spec",
        "talents.class",
        "talents.legacy",
        "customization",
        "collections.mounts",
        "collections.toys",
        "collections.pets",
        "collections.appearances",
        "currencies",
        "professions",
        # Schema 3 (M13-05).
        "spellbook",
        "actionbars",
        "bindings",
        # Schema 4 (M15-01).
        "identity",
        # Schema 5 (M20-13).
        "macros",
    }


def test_slash_command_has_skip_and_unskip() -> None:
    """`/wowlab skip`, `/wowlab unskip`; both refused until ADDON_LOADED ran."""
    slash = _body(_core(), "WOWLAB")
    for command in SKIP_COMMANDS:
        assert f'command == "{command}"' in slash, f"/wowlab {command} is not handled"
    assert 'if not ns . probe then say ( "not loaded yet." ) return end skipCommand (' in slash


def test_skip_without_a_section_lists_the_switched_off_ones() -> None:
    body = _body(_core(), "skipCommand")
    assert 'if key == "" then' in body
    assert 'say ( "switched off: " .. ( skipped == "" and "no section" or skipped ) )' in body


def test_unknown_section_key_is_refused_with_the_valid_keys() -> None:
    flow = _core()
    body = _body(flow, "skipCommand")
    refusal = (
        "local section = sectionByKey ( key ) if not section then "
        'say ( "no such section. Sections: " .. keyList ( isSection ) ) return end'
    )
    assert refusal in body
    # The refusal comes before anything is switched off or saved.
    assert body.index(refusal) < body.index("switchOff ( section )")
    assert body.index(refusal) < body.index("putSkip ( WowLabCharDB )")
    assert _body(flow, "isSection") == "function isSection ( ) return true end"


def test_gather_does_nothing_for_a_switched_off_section() -> None:
    """Every gather goes through Core.lua's `gather` (events, entering the
    world through gatherDirty, `/wowlab save`, PLAYER_LOGOUT), and it returns
    before calling the section when the section is off."""
    flow = _core()
    assert _body(flow, "gather").startswith(f"function gather ( section , event ) {OFF_GUARD}")
    called, read = [], []
    for path in SOURCES:
        tokens = _tokens(path)
        for i in range(2, len(tokens) - 3):
            if [t.text for t in tokens[i : i + 3]] != ["section", ".", "gather"]:
                continue
            where = (path.name, _Flow(path, tokens).label_at(i))
            if tokens[i + 3].text == "(" or [t.text for t in tokens[i - 2 : i]] == ["pcall", "("]:
                called.append(where)
            else:
                read.append((*where, _render(tokens[i - 4 : i + 4])))
    assert called == [("Core.lua", "gather")], called
    # The one other mention is ns.Write testing whether the section has a gather.
    assert read == [("Core.lua", "ns.Write", "section . off and section . gather then")], read


def test_no_entry_point_marks_a_switched_off_section() -> None:
    flow = _core()
    assert "if section . on_world and not section . off and" in _body(
        flow, 'ns.On("PLAYER_ENTERING_WORLD")'
    )
    assert "if section . on_world and not section . off then gather ( section ) end" in _body(
        flow, "ns.Refresh"
    )
    assert _body(flow, "onEvent").startswith(f"function onEvent ( fired ) {OFF_GUARD}")


def test_skip_list_is_read_at_addon_loaded_before_carry_and_events() -> None:
    """At ADDON_LOADED the saved skip list is read first; a switched-off
    section is not carried and registers no event. ns.Section registers
    nothing; `listen` does, and only from ADDON_LOADED."""
    flow = _core()
    loaded = _body(flow, 'ns.On("ADDON_LOADED")')
    read = loaded.index("loadSkip ( WowLabCharDB )")
    carry = loaded.index("if section . carry and not section . off then")
    listen = loaded.index("if not section . off then listen ( section ) end")
    assert read < carry < listen, loaded
    assert "pcall ( section . carry" in loaded
    assert "ns . On" not in _body(flow, "ns.Section")
    t = flow.tokens
    calls = [
        flow.label_at(i)
        for i, tok in enumerate(t)
        if tok.text == "listen" and t[i + 1].text == "(" and t[i - 1].text != "function"
    ]
    assert calls == ['ns.On("ADDON_LOADED")'], calls
    assert 'local list = type ( saved ) == "table" and saved . skip or nil' in _body(
        flow, "loadSkip"
    )


def test_switching_off_removes_the_section_handler() -> None:
    """`/wowlab skip` during a session: the section's one handler is taken off
    every event it registered, and an event no other section uses is
    unregistered from the frame; a pending gather is dropped."""
    flow = _core()
    body = _body(flow, "switchOff")
    for part in (
        "section . off = true",
        "section . dirty = false",
        "ns . skip [ section . key ] = true",
        "ns . state [ section . key ] = nil",
        "off ( event , section . listener )",
        "section . listener = nil",
    ):
        assert part in body, part
    assert "section . listener = onEvent" in _body(flow, "listen")
    assert "ns . On ( event , onEvent )" in _body(flow, "listen")
    assert "pcall ( frame . UnregisterEvent , frame , event )" in _body(flow, "off")


def test_switched_off_section_is_written_absent_with_the_owner_reason() -> None:
    flow = _core()
    assert f'local SWITCHED_OFF = "{SWITCHED_OFF_REASON}"' in _render(flow.tokens)
    write = _body(flow, "ns.Write")
    # A section with no gather (collections.appearances) keeps its own reason.
    assert (
        "if section . off and section . gather then record = ns . Absent ( SWITCHED_OFF ) else"
        in write
    )
    assert write.index("putSkip ( db )") < write.index("WowLabCharDB = db")


def test_skip_list_is_saved_as_wowlabchardb_skip() -> None:
    """`WowLabCharDB.skip = { "<section key>", ... }` in section order, left out
    when empty; written at ADDON_LOADED, by the commands and by ns.Write, so it
    survives the SavedVariables round-trip."""
    flow = _core()
    put = _body(flow, "putSkip")
    assert "list [ # list + 1 ] = ns . String ( section . key )" in put
    assert "if # list > 0 then db . skip = list else db . skip = nil end" in put
    assert "putSkip ( WowLabCharDB )" in _body(flow, 'ns.On("ADDON_LOADED")')
    assert "putSkip ( WowLabCharDB )" in _body(flow, "skipCommand")


def test_readme_and_plan_document_the_switch() -> None:
    readme = README.read_text(encoding="utf-8")
    plan = PLAN.read_text(encoding="utf-8")
    runbook = " ".join(RUNBOOK.read_text(encoding="utf-8").split())  # joined across line breaks
    for command in SKIP_COMMANDS:
        assert f"/wowlab {command} <section>" in readme, command
        assert f"/wowlab {command} <section>" in plan, command
    for text in (readme, plan):
        assert SWITCHED_OFF_REASON in text
        assert "skip = {" in text
    for key in _section_keys():
        assert f"`{key}`" in readme, f"README does not list the section key {key}"
    assert "/wowlab skip <section>" in runbook
    # Fix rounds 1 and 2: the 15 s window and the per-character switch.
    for text in (readme, plan):
        assert "per character" in text
        assert "15 s" in text
        assert "10 s" not in text.split("### 13.2")[0].split("### 13.1")[-1]
    assert "ask the conductor which section key the crash report's file and line" in runbook
    assert "within the 15 s the addon announces" in runbook
    assert "Type the skip before any `/reload` or logout" in runbook
    assert "10 s" not in runbook


def test_readme_maps_each_file_to_its_section_keys() -> None:
    """A crash report names a file and line, not a section: the README maps
    every source that registers sections to its keys."""
    readme = README.read_text(encoding="utf-8")
    mapped = 0
    for path in SOURCES:
        keys = _keys_in(path)
        if keys:
            mapped += 1
            line = f"- `{path.name}`: " + ", ".join(f"`{k}`" for k in keys)
            assert line in readme, f"README lacks the line {line!r}"
    assert mapped >= 6, mapped


# Fix round 1 (domain review): the first on-world pass after ADDON_LOADED waits
# (15 s since fix round 2) on its own timer and says so, so `/wowlab skip` can
# be typed before it.
# Source scans on Core.lua tokens, constructed checks; no Lua runs (L3).


def _first_pass_delay(flow: _Flow) -> int:
    match = re.search(r"local FIRST_PASS_DELAY = (\d+) ", _render(flow.tokens))
    assert match, "expected `local FIRST_PASS_DELAY = <n>` in Core.lua"
    return int(match.group(1))


def test_first_pass_runs_on_its_own_timer() -> None:
    flow = _core()
    delay = _first_pass_delay(flow)
    assert delay == 15
    first = _body(flow, "startFirstPass")
    assert first.startswith(
        "function startFirstPass ( ) if firstPassStarted then return end firstPassStarted = true"
    )
    # M20-13: the first pass, like every later one, waits for combat to end.
    assert (
        "timerAfter ( FIRST_PASS_DELAY , function ( ) firstPassDone = true "
        "if inCombat then firstPassWaiting = true else gatherDirty ( ) end end )" in first
    )
    # Not merged into, nor swallowed by, the change-event debounce.
    assert "pending" not in first.split() and "schedule" not in first.split()
    assert "pending = false if firstPassDone then gatherDirty ( ) end" in _body(flow, "schedule")
    world = _body(flow, 'ns.On("PLAYER_ENTERING_WORLD")')
    assert (
        "if timerAfter and not firstPassDone then startFirstPass ( ) else schedule ( ) end" in world
    )
    t = flow.tokens
    calls = [
        flow.label_at(i)
        for i, tok in enumerate(t)
        if tok.text == "startFirstPass" and t[i + 1].text == "(" and t[i - 1].text != "function"
    ]
    assert calls == ['ns.On("PLAYER_ENTERING_WORLD")'], calls


def test_first_pass_is_announced_in_chat() -> None:
    """The line is built from FIRST_PASS_DELAY, so it cannot drift from the timer."""
    flow = _core()
    first = _body(flow, "startFirstPass")
    line = (
        'say ( "recording in " .. FIRST_PASS_DELAY .. " s. To switch a section off first: '
        '/wowlab skip <section>  (/wowlab skip lists them)" )'
    )
    assert line in first
    assert first.index(line) < first.index("timerAfter (")
    # `/wowlab skip` with no section does list every key, as the line says.
    assert 'say ( "sections: " .. keyList ( isSection ) )' in _body(flow, "skipCommand")


def test_skip_before_the_first_pass_takes_effect() -> None:
    """A skip typed during the 15 s: switchOff clears `dirty`, the first pass
    gathers only dirty sections (gatherDirty), and gather refuses an off
    section anyway. A logout inside the window still records (PLAYER_LOGOUT
    runs gatherDirty)."""
    flow = _core()
    assert "section . dirty = false" in _body(flow, "switchOff")
    dirty = _body(flow, "gatherDirty")
    assert "if section . dirty then gather ( section ) end" in dirty
    assert _body(flow, "gather").startswith(f"function gather ( section , event ) {OFF_GUARD}")
    # M20-13: out of combat only (nothing is gathered in combat).
    assert 'if not inCombat then gatherDirty ( ) end ns . Write ( "logout" )' in _body(
        flow, 'ns.On("PLAYER_LOGOUT")'
    )


def test_unskip_of_a_section_already_back_on_says_so() -> None:
    body = _body(_core(), "skipCommand")
    assert (
        'elseif section . off then say ( section . key .. " is already switched back on '
        'from the next /reload or login." ) return else'
    ) in body


# M11-22: say why a value is missing --------------------------------------------
#
# Source scans on the addon's own tokens (constructed checks; no Lua runs, L3).
# From the M11-03 domain review: three places where a capture could not tell
# "the client returned nothing" from "the call failed" or "never ran". Every
# value stored here is also held to the stored-value rules above
# (test_every_stored_value_is_type_checked).

NO_SAVED_LOADOUT = "the client returned no last-selected loadout for this spec"
EMPTY_EXPORT = "C_Traits.GenerateImportString returned an empty string"


def _section_source(path: Path, key: str) -> str:
    """The rendered tokens of the one `ns.Section({ ... })` call in `path`
    whose spec has `key = "<key>"` (or a file-level `local KEY = "<key>"`)."""
    tokens = _tokens(path)
    constants = dict(
        re.findall(r'^local ([A-Z_]+) = "([^"]+)"$', path.read_text(encoding="utf-8"), flags=re.M)
    )
    spellings = {f'key = "{key}" ,'} | {
        f"key = {name} ," for name, v in constants.items() if v == key
    }
    heads = [
        i
        for i in range(len(tokens) - 4)
        if [t.text for t in tokens[i : i + 4]] == ["ns", ".", "Section", "("]
    ]
    found = []
    for head in heads:
        depth, end = 0, head + 3
        for end in range(head + 3, len(tokens)):
            if tokens[end].kind != "op":
                continue
            if tokens[end].text in {"(", "{", "["}:
                depth += 1
            elif tokens[end].text in {")", "}", "]"}:
                depth -= 1
                if depth == 0:
                    break
        text = _render(tokens[head : end + 1])
        if any(spelling in text for spelling in spellings):
            found.append(text)
    assert len(found) == 1, (
        f"expected one ns.Section with key {key} in {path.name}, found {len(found)}"
    )
    return found[0]


def test_never_gathered_section_is_written_with_events_unregistered() -> None:
    """(1) ns.Write writes `events_unregistered` (the refused events, or an
    empty list: the key is always there, so a missing key means a file from
    before M11-22) on every record it writes for a section that registered a
    non-empty `events` list this session: gathered, carried, or the
    never-gathered `not_gathered` absent record. `section.listener` is set by
    `listen` and cleared by `switchOff`, so a section switched off at load or
    this session gets nothing but the owner reason. `gather` no longer
    attaches the list itself, so ns.Write is the one place."""
    flow = _core()
    write = _body(flow, "ns.Write")
    assert (
        "if section . off and section . gather then record = ns . Absent ( SWITCHED_OFF ) "
        'else record = ns . state [ section . key ] or ns . Absent ( section . not_gathered or "not gathered this session" ) '
        'if section . listener and type ( section . events ) == "table" and # section . events > 0 then '
        "record . events_unregistered = section . events_unregistered or { } end "
        # M20-13 (schema 5): `probes` only in a debug session.
        "quiet ( record ) end place ( db , section . path , record )"
    ) in write, write
    attach = "record . events_unregistered = section . events_unregistered or { }"
    rendered = [_render(_tokens(path)) for path in SOURCES]
    assert sum(text.count(attach) for text in rendered) == 1
    assert "events_unregistered" not in _body(flow, "gather")
    # The list itself is built only in `listen`, from the section's own
    # `events` literal, and a switched-off section never listens.
    assert "section . events_unregistered = section . events_unregistered or { }" in _body(
        flow, "listen"
    )
    assert "if not section . off then listen ( section ) end" in _body(
        flow, 'ns.On("ADDON_LOADED")'
    )
    # `listener` marks "registered this session": set only in listen, cleared
    # in switchOff.
    sets = [
        (flow.label_at(i), _render(flow.tokens[i : i + 5]))
        for i in range(len(flow.tokens) - 5)
        if [t.text for t in flow.tokens[i : i + 4]] == ["section", ".", "listener", "="]
    ]
    assert sorted(sets) == [
        ("listen", "section . listener = onEvent"),
        ("switchOff", "section . listener = nil"),
    ], sets


def test_the_sections_this_is_for_have_events_and_not_gathered() -> None:
    """customization (BARBER_SHOP_OPEN) is the motivating case: it has events
    and a not_gathered reason. collections.appearances has no events, so its
    M11-20 reason gets nothing added."""
    barber = _section_source(ADDON / "Customization.lua", "customization")
    assert 'events = { "BARBER_SHOP_OPEN" , "BARBER_SHOP_APPEARANCE_APPLIED" } ,' in barber
    assert "not_gathered = " in barber
    appearances = _section_source(ADDON / "Collections.lua", "collections.appearances")
    assert "events =" not in appearances
    assert "gather =" not in appearances
    assert "not_gathered = " in appearances


def test_currencies_record_the_row_count() -> None:
    """(2) `rows`: the count GetCurrencyListSize gave, beside `list`, and
    `headers`, every header row seen, so `rows - headers - #list` is the rows
    whose id could not be read. The loop walks the same count."""
    section = _section_source(ADDON / "Currencies.lua", "currencies")
    assert "local rows = ns . Call ( size )" in section
    assert 'for index = 1 , type ( rows ) == "number" and rows or 0 do' in section
    assert "local ids , seen , headers , collapsed = { } , { } , 0 , 0" in section
    assert "if info . isHeader then headers = headers + 1 if not info . isHeaderExpanded then" in (
        section
    )
    assert (
        "local record = { list = list , filtered = true , headers = headers , "
        "headers_collapsed = collapsed , rows = ns . Number ( rows ) , }"
    ) in section
    assert section.count("rows = ns . Number ( rows )") == 1
    assert section.count("headers = headers + 1") == 1


def test_talents_class_says_why_there_is_no_saved_loadout() -> None:
    """(3) Exactly one of `last_selected_config` and
    `last_selected_config_absent`: nil from the client is "no saved loadout";
    an error (pcall, not ns.Call, so the two are told apart) and a value that
    is not a number have their own reasons."""
    section = _section_source(ADDON / "Talents.lua", "talents.class")
    assert (
        "if not lastSaved then "
        'record . last_selected_config_absent = "C_ClassTalents.GetLastSelectedSavedConfigID missing" '
        "elseif not ( spec and spec . id ) then "
        'record . last_selected_config_absent = "no spec id to ask with" '
        "else local ok , value = pcall ( lastSaved , spec . id ) "
        "if not ok then "
        'record . last_selected_config_absent = "C_ClassTalents.GetLastSelectedSavedConfigID raised an error" '
        'elseif type ( value ) == "number" then record . last_selected_config = value '
        f'elseif value == nil then record . last_selected_config_absent = "{NO_SAVED_LOADOUT}" '
        "else "
        'record . last_selected_config_absent = "C_ClassTalents.GetLastSelectedSavedConfigID returned no number" '
        "end end"
    ) in section, section
    assert section.count("record . last_selected_config =") == 1


def test_talents_class_says_why_there_is_no_export() -> None:
    """(3) Exactly one of `export` and `export_absent`, whatever the client
    does: the function missing, raising an error, returning an empty string,
    or returning no string."""
    section = _section_source(ADDON / "Talents.lua", "talents.class")
    assert (
        'local export = ns . Fn ( C_Traits , "GenerateImportString" ) '
        'if not export then record . export_absent = "C_Traits.GenerateImportString missing" '
        "else local ok , text = pcall ( export , configID ) "
        'if not ok then record . export_absent = "C_Traits.GenerateImportString raised an error" '
        'elseif type ( text ) == "string" and text ~= "" then record . export = text '
        f'elseif text == "" then record . export_absent = "{EMPTY_EXPORT}" '
        'else record . export_absent = "C_Traits.GenerateImportString returned no string" '
        "end end"
    ) in section, section
    assert section.count("record . export =") == 1


def test_readme_and_plan_document_why_a_value_is_missing() -> None:
    readme = README.read_text(encoding="utf-8")
    plan = PLAN.read_text(encoding="utf-8")
    section = plan.split("### 13.2")[0].split("### 13.1")[-1]
    assert "Amended 2026-09-29 (M11-22" in section
    for text in (readme, section):
        joined = " ".join(text.split())  # literals may wrap across lines
        assert NO_SAVED_LOADOUT in joined
        assert "export_absent" in joined
        assert "`rows`" in joined
        assert "`headers`" in joined
        assert "An empty list is the answer" in joined
        assert "`carry` drops the saved list" in joined
        assert "no saved loadout is selected for this spec **[verify]**" in joined
    assert f'`"{EMPTY_EXPORT}"`' in readme
    assert "export | export_absent" in readme
    assert "headers, headers_collapsed, rows, filter" in readme


# M11-29: the barber-shop record after an applied change ---------------------------
#
# Source scans on the addon's own tokens (constructed checks; no Lua runs, L3).
# In the M11-23 capture (1.60.1.70058), after one applied change, the record's
# last write came from an open, with no `chr_model_id`, and the file could not
# say why. The addon changes only so the next capture can, as schema 2 (the
# first format change after M11-04): `events_received` counts every event the
# customization section registered, six count-only events (one a made-up
# control name) are counted and never gathered on, and `chr_model_id_absent`
# records how the model-id call went. Every value stored here is also held to
# the stored-value rules above (test_every_stored_value_is_type_checked).
#
# Sources are found through ADDON at call time (the review probes point ADDON
# at an edited copy); the docs, like PLAN above, are bound once to the repo.

FORMATS = ROOT / "docs" / "LAB_FORMATS.md"
BARBER_CHANGE_EVENTS = ("BARBER_SHOP_OPEN", "BARBER_SHOP_APPEARANCE_APPLIED")
BARBER_COUNT_ONLY = (
    "BARBER_SHOP_RESULT",
    "BARBER_SHOP_CLOSE",
    "BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE",
    "BARBER_SHOP_COST_UPDATE",
    "BARBER_SHOP_SUCCESS",
    "WOWLAB_CONTROL_NOT_A_REAL_EVENT",
)
# Made up on purpose: whether it gets an entry shows whether the client
# refuses a name it does not know.
CONTROL_EVENT = "WOWLAB_CONTROL_NOT_A_REAL_EVENT"
MODEL_ABSENT = (
    "C_BarberShop.GetViewingChrModel missing",
    "C_BarberShop.GetViewingChrModel raised an error",
    "C_BarberShop.GetViewingChrModel returned nil",
    "C_BarberShop.GetViewingChrModel returned no number",
)
# The reader's EventName shape (wowlab_core.labaddon): a name it would refuse
# would make the whole file unreadable.
_READER_EVENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def _customization_lua() -> Path:
    return ADDON / "Customization.lua"


def _literal_list(section: str, field: str) -> tuple[str, ...]:
    match = re.search(rf"{field} = \{{ ((?:\"[A-Z0-9_]+\" ,? ?)+)\}} ,", section)
    assert match, f"expected a literal `{field} = {{ ... }}` in the section"
    return tuple(re.findall(r'"([A-Z0-9_]+)"', match.group(1)))


def test_m11_29_customization_counts_six_more_events_and_gathers_on_two() -> None:
    """The change events are unchanged; the count-only list is a literal of
    six event names, none of them a change event, each a name the reader
    accepts and none a private event, the last a control that names no
    client event (not BARBER_SHOP_*, and the addon's own prefix). No other
    section has a count-only list."""
    section = _section_source(_customization_lua(), "customization")
    assert _literal_list(section, "events") == BARBER_CHANGE_EVENTS
    assert _literal_list(section, "count_only") == BARBER_COUNT_ONLY
    assert not set(BARBER_COUNT_ONLY) & set(BARBER_CHANGE_EVENTS)
    assert CONTROL_EVENT.startswith("WOWLAB_") and "NOT_A_REAL_EVENT" in CONTROL_EVENT
    for name in BARBER_CHANGE_EVENTS + BARBER_COUNT_ONLY:
        assert _READER_EVENT_NAME.match(name), name
        assert not PRIVATE_EVENT.match(name), name
    others = [
        path.name
        for path in SOURCES
        if path.name != "Customization.lua" and "count_only =" in _render(_tokens(path))
    ]
    assert others == [], others


def test_m11_29_count_only_events_are_counted_and_never_gather() -> None:
    """In `listen`: the count comes before the change-event test, and a
    count-only event returns before `gather`, `dirty` or `schedule`, so the
    record still comes only from the two change events (never at close).
    Every registered event starts at 0; a refused count-only event gets no
    entry and never reaches events_unregistered."""
    flow = _core()
    listen = _body(flow, "listen")
    assert listen.startswith(
        "function listen ( section ) local changes = { } "
        "for _ , event in ipairs ( section . events or { } ) do changes [ event ] = true end "
        # M20-13 (schema 5): count-only events and their counts in a debug
        # session only.
        "local counts = nil if section . count_only and ns . debug then counts = { } end"
    ), listen
    assert _body(flow, "onEvent") == (
        f"function onEvent ( fired ) {OFF_GUARD} "
        "if counts then counts [ fired ] = ( ns . Number ( counts [ fired ] ) or 0 ) + 1 end "
        # M13-05: a retry event schedules a still-dirty section and returns.
        "if retries [ fired ] then if section . dirty then schedule ( ) end return end "
        "if not changes [ fired ] then return end "
        "if section . immediate then gather ( section , fired ) "
        "else section . dirty = true schedule ( ) end end"
    )
    assert "section . listener = onEvent section . events_received = counts" in listen
    assert (
        "local registered = ns . On ( event , onEvent ) if not registered then "
        "section . events_unregistered = section . events_unregistered or { } "
        "local missing = section . events_unregistered missing [ # missing + 1 ] = event "
        "elseif counts then counts [ event ] = 0 end end"
    ) in listen
    count_only_loop = (
        "if counts then for _ , event in ipairs ( section . count_only or { } ) do "
        "if ns . On ( event , onEvent ) then counts [ event ] = 0 end end end"
    )
    assert listen.endswith(count_only_loop + " end"), listen
    # events_received is set in `listen` only; nothing else writes the count.
    sets = [
        (path.name, _Flow(path, tokens).label_at(i))
        for path in SOURCES
        for tokens in [_tokens(path)]
        for i in range(len(tokens) - 3)
        if [t.text for t in tokens[i : i + 4]] == ["section", ".", "events_received", "="]
    ]
    assert sets == [("Core.lua", "listen")], sets


def test_m11_29_switching_off_removes_the_count_only_handlers() -> None:
    """`/wowlab skip customization` takes the one handler off the count-only
    events too, before the listener is forgotten."""
    body = _body(_core(), "switchOff")
    assert (
        "for _ , event in ipairs ( section . events or { } ) do off ( event , section . listener ) end "
        "for _ , event in ipairs ( section . count_only or { } ) do off ( event , section . listener ) end "
        "section . listener = nil"
    ) in body, body


def test_m11_29_write_puts_events_received_on_every_record_of_a_listening_section() -> None:
    """ns.Write attaches the counts to whatever record it placed (gathered,
    carried or never gathered), only while the section listens; a switched-off
    section has no listener and keeps its plain owner reason. It is the one
    place that writes the key, and neither gather nor carry does."""
    flow = _core()
    write = _body(flow, "ns.Write")
    attach = "record . events_received = section . events_received"
    assert (
        "place ( db , section . path , record ) "
        f"if section . listener and section . events_received then {attach} end end putSkip ( db )"
    ) in write, write
    rendered = [_render(_tokens(path)) for path in SOURCES]
    assert sum(text.count("events_received =") for text in rendered) == 2  # listen and here
    assert sum(text.count(attach) for text in rendered) == 1
    section = _section_source(_customization_lua(), "customization")
    assert "events_received" not in section


def test_m11_29_model_id_or_the_reason_it_is_missing() -> None:
    """Exactly one of `chr_model_id` and `chr_model_id_absent` on a gathered
    record. The call goes through pcall directly, not ns.Call, so an error is
    told apart from nil; a number is stored only inside its type test."""
    section = _section_source(_customization_lua(), "customization")
    missing, error, nil, not_number = MODEL_ABSENT
    assert (
        'local model = ns . Fn ( C_BarberShop , "GetViewingChrModel" ) '
        f'if not model then record . chr_model_id_absent = "{missing}" '
        "else local ok , id = pcall ( model ) "
        f'if not ok then record . chr_model_id_absent = "{error}" '
        'elseif type ( id ) == "number" then record . chr_model_id = id '
        f'elseif id == nil then record . chr_model_id_absent = "{nil}" '
        f'else record . chr_model_id_absent = "{not_number}" '
        "end end return record end"
    ) in section, section
    assert section.count("record . chr_model_id =") == 1
    assert section.count("record . chr_model_id_absent =") == len(MODEL_ABSENT)
    assert "ns . Call ( model )" not in section


def test_m11_29_carry_keeps_the_model_number_but_not_the_reason_or_the_counts() -> None:
    """The reason and the counts describe one visit or one session: carry
    rebuilds the record without either (a carried record may hold neither
    chr_model_id nor its reason), and still copies chr_model_id as a number."""
    tokens = _tokens(_customization_lua())
    start, end = _carry_body(tokens)
    carry = _render(tokens[start:end])
    assert 'ipairs ( { "recorded_load" , "race_id" , "sex" , "chr_model_id" } )' in carry
    for key in ("chr_model_id_absent", "events_received", "events_unregistered"):
        assert key not in carry, key


def test_m11_29_schema_2_and_the_probe_survives_the_bump() -> None:
    """Schema 2 (§13.1: the first format change after M11-04). Both
    variables take `ns.SCHEMA`; nothing read back at ADDON_LOADED looks at the
    schema (the probe, the skip list and the carried record are read by key),
    and the logout write carries the probe, so a schema-1 file's
    `probe.loads` keeps rising across the bump."""
    flow = _core()
    rendered = _render(flow.tokens)
    # Schema 4 since M15-01 (3 since M13-05); the bump keeps every check below.
    assert "ns . SCHEMA = 5" in rendered
    assert rendered.count("ns . SCHEMA =") == 1
    assert "local db = { schema = ns . SCHEMA , probe = copyProbe ( ) ," in _body(flow, "ns.Write")
    assert "WowLabDB = { schema = ns . SCHEMA }" in _body(flow, "ns.Write")
    assert (
        "local prior = saved . probe "
        'if type ( prior ) == "table" and type ( prior . loads ) == "number" then '
        "probe . loads = ns . Number ( prior . loads + 1 )"
    ) in _body(flow, "loadProbe")
    loaded = _body(flow, 'ns.On("ADDON_LOADED")')
    assert "ns . probe = loadProbe ( WowLabCharDB )" in loaded
    assert "WowLabCharDB . probe = copyProbe ( )" in loaded
    for label in ("loadProbe", "loadSkip", 'ns.On("ADDON_LOADED")'):
        assert "schema" not in _body(flow, label).replace("schema = ns . SCHEMA", ""), label
    tokens = _tokens(_customization_lua())
    start, end = _carry_body(tokens)
    assert "schema" not in _render(tokens[start:end])


def test_m11_29_readme_and_plan_say_what_is_confirmed_and_what_stays_verify() -> None:
    raw_readme = README.read_text(encoding="utf-8")
    readme = " ".join(raw_readme.split())
    plan_text = PLAN.read_text(encoding="utf-8")
    plan = " ".join(plan_text.split("### 13.2")[0].split("### 13.1")[-1].split())
    formats = " ".join(FORMATS.read_text(encoding="utf-8").split())
    assert "Amended 2026-09-29 (M11-29" in plan
    assert "Follow-up, 2026-09-29 (M11-29, schema 2" in formats
    for text in (readme, plan):
        for reason in MODEL_ABSENT:
            assert reason in text, reason
        for name in BARBER_COUNT_ONLY:
            assert f"`{name}`" in text, name
        assert "`events_received`" in text
        assert "`chr_model_id_absent`" in text
        assert "Still **[verify]**: that `BARBER_SHOP_APPEARANCE_APPLIED` ever reaches" in text
        assert "`BARBER_SHOP_OPEN` fires and reaches the section" in text
        assert "M11-29 capture step" in text
        assert "schema 2" in text
        assert "the schema stays 1" not in text.split("(M11-29")[-1]
    # The README's layout is schema 4's since M15-01 (3's since M13-05), and
    # schema 1 is still read.
    assert "### Table layout (schema 5)" in raw_readme
    assert "  schema = 5," in raw_readme
    assert "The Lab still reads schema 1 files" in readme
    # M4: the file shows only that the last write came from an open.
    for text in (readme, plan, formats):
        assert "the record was not rewritten" not in text
        assert "last write came from an open" in text
    assert "rules out a second sit, not an open the client fires itself" in formats
    # M5: the reason is how the call went, not why; the hypothesis stays one.
    for text in (readme, plan):
        assert "when the section gathered" in text
        assert "stays **[verify]**" in text
    for line in (
        "the function is not on this client",
        "it exists but refused this call",
        "it named no model at that moment",
    ):
        assert line in readme and line in plan, line
    # P2 and the delta: IsEventValid is in the kit list; the file does not
    # record that it ran on 70058; three limits.
    for text in (readme, formats):
        assert "the file does not record whether it did on 70058" in text
        assert "never been seen refusing any name" in text
        assert "only a count of 1 or more" in text
    # The runbook: cautions and steps, with the stops (round 2).
    for part in (
        "## M11-29 capture step",
        "Do not change the body type",
        "`C_BarberShop.SetSelectedSex`",
        "`wowlab undo` cannot reverse",
        "a second sit in the same session spoils the counts",
        "have enough for the price the shop shows on Accept",
        "WowLab is probably not running",
        "type `/wowlab`; if the addon loaded, it answers with two lines starting `WowLab:`",
        "stop and report",
        "If the barber shop does not open, log out, capture anyway and report it; do not redo.",
        "left to right, then top to bottom",
        "No `/reload` between the visit and the logout",
        "Do not log that character in again before capturing",
        "check that the new colour shows on the character",
        "it is not an apply signal and nothing may be recorded on it (§13.1)",
        "says the client answered the Accept, not that it succeeded",
        "is expected at 1 on any visit, applied or cancelled (on Retail, **[verify]**)",
        "saw shop activity during the visit",
        "either the applied event fired before the choices updated, or it was not the Accept's",
        "neither noted position: report both; item 11 stays open",
        "at the old position the last open saw the old look: both opens came before the "
        "Accept, or the second came before the choices updated; item 12 stays open",
        "most likely the applied event fired",
        "every other entry, 0 included, is a name this client knows",
        "because the control tests the addon's whole gate",
        "is not known to be safe on Forever",
    ):
        assert part in readme, part
    # One row per outcome, in the order the first that fits applies, with a
    # catch-all last.
    table = readme.split("How the capture reads.")[1].split("Read with every row")[0]
    rows = [
        "**`schema = 1`**",
        '**`customization = { absent = "switched off by the owner" }`**',
        "**The shop did not open (step 3), or the colour did not change (step 4)**",
        "**`carried = true`**",
        '**`customization = { absent = "no barber-shop visit recorded with the addon '
        'enabled" }` with `OPEN` at 0 or no entry**',
        "**You sat or clicked Accept more than once (notes from steps 3 and 4)**",
        "**`customization` absent with a gather reason and counts**",
        '**`recorded_at = "applied"` with `APPLIED` at 1 or more**',
        '**`recorded_at = "open"`, `APPLIED` at 1 or more, `OPEN` at 2 or more, and you sat once**',
        '**`recorded_at = "open"`, `APPLIED` at 1 or more, `OPEN` at 1**',
        '**`recorded_at = "open"` with `APPLIED` at 0 or no entry, and the new colour '
        "visible on the character (step 4)**",
        "**None of these**",
    ]
    at = [table.find(row) for row in rows]
    assert -1 not in at, [row for row, i in zip(rows, at, strict=True) if i == -1]
    assert at == sorted(at), at
    assert table.count("- **") == len(rows)
    assert f"**`{CONTROL_EVENT}`**" in readme
    assert "chr_model_id | chr_model_id_absent, events_received" in readme


# M11-37: every client API call sits inside a section ---------------------------
#
# Source scans on the addon's own tokens (constructed checks; no Lua runs, L3).
# From the M11-29 reviews: nothing checked where a new client call went, and a
# `C_BarberShop.GetViewingChrModel` call added at ADDON_LOADED in a copy passed
# every test. `pcall` catches Lua errors, not client assertions, so a client
# call must sit where a `/wowlab skip` typed in the session stops it
# (docs/AGENT_WORKFLOW.md; lab/addon/README.md, "API status").
#
# *Switchable* means exactly that. A section's gather runs on the first
# on-world pass (15 s after entering the world), on its section's change
# events, on `/wowlab save` and at logout, and Core.lua returns before calling
# it for a switched-off section. A section's carry is not switchable (ruling,
# M11-37 review): Core.lua runs every carry at ADDON_LOADED, before the world
# is visible, so only a skip saved in an earlier session stops it, and a crash
# there repeats until the addon is unticked. Nor is anything run at load or by
# an event handler outside a section.
#
# A *client API reference* is a global name the sources read or write that is
# neither a Lua 5.1 standard global (`_LUA51_GLOBALS`; `print` counts as Lua)
# nor one of the addon's own (`OWN_GLOBALS`). selene fails any global that
# wow_client.yml does not declare, so these are the std's names, and the scan
# checks that it sees exactly them. A *handle* is a file-level local that is
# ever bound or assigned a value holding a client global, a handle, or a local
# or parameter that holds either (Core.lua's `frame`, `timerAfter`,
# `isEventValid`). Round 4: the hold is followed across functions too, into a
# followed function's parameter when a direct call passes one, and out of a
# followed function that returns one (a reference to that function brings it
# in, under `pcall` too); and a file-level local that section code assigns is
# a handle whatever the value, since section code is where client objects are
# made. Each use of a handle is a reference too. A reference is named as written, fields
# and one method joined: `C_Timer.After`, `frame.RegisterEvent`,
# `frame:SetScript`. The sources do no dynamic global lookup and never index a
# std global or `ns` by brackets (tests above), and rule 3 below keeps `ns`
# from being aliased, so every client call is made through a reference.
#
# A *client-given value* (round 3) is one the client passes in: every
# parameter, and the `...`, of a function the scan does not follow. It
# follows a local function or `ns` function referred to only by direct calls,
# and a section's carry literal (its one caller is pinned below). Every other
# function (a literal handed to `SetScript`, a timer, `hooksecurefunc` or
# `ns.On`, one stored in `SlashCmdList` or a table, a global function) may be
# called by the client, so the event frame, the chat edit box and an event's
# payload arrive as client-given values. So does a local or loop variable ever
# given one, a followed function's parameter that a direct call passes one,
# and what a followed function returning one returns. Each use of one is a
# reference too, named as written (`f:UnregisterAllEvents`, `event`, `...`).
#
# *Section code* is what only a gather runs: the `gather` of each
# `ns.Section({ ... })` (a function literal or a local function), and any local
# function, `ns` function or function literal that section code calls on the
# spot and nothing else refers to. On the spot: `f(...)`, `ns.F(...)`,
# or where a callee calls it now (`_CALLS_ON_THE_SPOT`: pcall,
# xpcall, ns.Call, a table.sort comparator). A function literal written as a
# field of a section spec is labelled with the section's key:
# `gather("gear")`, `carry("customization")`.
#
# 1. Every client API reference and every use of a client-given value
#    outside section code matches an entry of
#    `SECTIONLESS_API`, keyed (file, function, reference as written), which
#    pins how many times it is used each way there, and why. The function
#    label must name exactly one function in its file, and the reference must
#    sit in that function. A named function that holds such a reference is
#    referred to only by direct calls, from the callers pinned in
#    `SECTIONLESS_CALLERS` with their number of calls.
# 2. Section code hands no function on (ruling, M11-37 review). A value that
#    may be a function (a function literal, an addon function, a client
#    global, `ns.Fn(...)`, what `next`, `pairs`, `ipairs`, `select` or
#    `unpack` return from one, or a local, loop variable or helper parameter
#    ever given one) is never passed to a call other than one that uses it on
#    the spot (`_ON_THE_SPOT`, or a helper that is itself section code, whose
#    parameter is then followed), never stored in a table, a field, a global
#    or a local declared outside section code (a function statement such as
#    `function ns.later()` counts), and never returned. A function handed to
#    `ns.On`, a timer, `SetScript` or a hook would run after `/wowlab skip`.
#    Exceptions: `HANDED_ON`, each with its reason.
# 3. Each file binds its `...` once, as `local <name>, ns = ...`, and uses it
#    nowhere else; every other `ns` is `ns.Name` (not `ns:Name`, which hands
#    the namespace on as `self`), and every `ns.state` is indexed or assigned
#    where it stands. No function is defined with `:` (round 3), so no `self`
#    is a hidden parameter; a `self` that is not a declared name is a global
#    and fails rule 1. So the namespace is reached under no other name: no
#    parameter can hold it, since bare `ns` and `ns:` are both refused.
#
# Every exception must match, so none goes stale. A new call outside a
# section, or a function handed on from one, fails until someone adds it to
# these lists deliberately. Review only: a function returned by a call other
# than `ns.Fn`, `next`, `pairs`, `ipairs`, `select` or `unpack` (a client
# call's result, or a field of it); a client object that section code stores
# in a table (ns.state, a record) and code outside a section later reaches
# through that table (a client call's result is data to the scan, and a
# client-given value used inside section code is not counted); a way to
# reach the client that the
# dynamic-lookup test above does not name; and which addon functions Core.lua's
# own handlers call (`ns.Refresh()` added to its PLAYER_ENTERING_WORLD handler
# would gather before the 15 s window). Raw bidirectional control characters:
# ruff's PLE2502 for Python, tests/repo/test_bidi_controls.py for the raw bytes
# of every tracked file and every tracked file name.

# The Lua 5.1 standard globals (the `lua51` base of wow_client.yml).
_LUA51_GLOBALS = frozenset(
    {
        "_G",
        "_VERSION",
        "assert",
        "collectgarbage",
        "coroutine",
        "debug",
        "dofile",
        "error",
        "gcinfo",
        "getfenv",
        "getmetatable",
        "io",
        "ipairs",
        "load",
        "loadfile",
        "loadstring",
        "math",
        "module",
        "newproxy",
        "next",
        "os",
        "package",
        "pairs",
        "pcall",
        "print",
        "rawequal",
        "rawget",
        "rawset",
        "require",
        "select",
        "setfenv",
        "setmetatable",
        "string",
        "table",
        "tonumber",
        "tostring",
        "type",
        "unpack",
        "xpcall",
    }
)

# How a SECTIONLESS_API reference may be used:
#   call   called where it stands (`X(...)`, `X:m(...)`, the first argument of
#          `pcall` or `ns.Call`);
#   test   only inspected (inside `type(...)`, compared, under `not`, the left
#          side of `and`, a whole `if` or `while` condition);
#   bind   in the value of the file-level local that is its handle;
#   self   the self argument after its own method: `pcall(H.M, H, ...)`;
#   store  the target of an assignment.

# Client API references that run outside every section: (how many references
# of each use there are, reason). A second reference in the same place, or
# the same count used another way (a call where there was a test), is a new
# call too, so it fails until the entry changes.
SECTIONLESS_API: dict[tuple[str, str, str], tuple[dict[str, int], str]] = {
    # Core.lua infrastructure: the event frame, the timer, the event check,
    # the `client` block and the slash command.
    ("Core.lua", _TOP, "CreateFrame"): ({"call": 1}, "the one event frame all events arrive on"),
    ("Core.lua", _TOP, "frame:SetScript"): ({"call": 1}, "the frame's OnEvent dispatcher"),
    ("Core.lua", _TOP, "C_Timer"): ({"test": 1}, "timerAfter: tests that the client has C_Timer"),
    ("Core.lua", _TOP, "C_Timer.After"): ({"test": 1, "bind": 1}, "timerAfter, bound once"),
    ("Core.lua", _TOP, "C_EventUtils"): ({"test": 1}, "isEventValid: tests for C_EventUtils"),
    ("Core.lua", _TOP, "C_EventUtils.IsEventValid"): (
        {"test": 1, "bind": 1},
        "isEventValid, bound once",
    ),
    ("Core.lua", _TOP, "SlashCmdList.WOWLAB"): ({"store": 1}, "registers /wowlab; no call"),
    ("Core.lua", "ns.On", "isEventValid"): (
        {"test": 1, "call": 1},
        "an event the client does not know is not registered",
    ),
    ("Core.lua", "ns.On", "frame.RegisterEvent"): ({"call": 1}, "registers one, through pcall"),
    ("Core.lua", "ns.On", "frame"): ({"self": 1}, "RegisterEvent's self argument"),
    ("Core.lua", "off", "frame.UnregisterEvent"): ({"call": 1}, "unregisters one, through pcall"),
    ("Core.lua", "off", "frame"): ({"self": 1}, "UnregisterEvent's self argument"),
    ("Core.lua", "schedule", "timerAfter"): (
        {"test": 1, "call": 1},
        "the change-event pass, at most once every GATHER_INTERVAL s (M20-13)",
    ),
    ("Core.lua", "startFirstPass", "timerAfter"): ({"call": 1}, "the 15 s first-pass window"),
    ("Core.lua", 'ns.On("PLAYER_ENTERING_WORLD")', "timerAfter"): (
        {"test": 1},
        "whether there is a 15 s window; never called here",
    ),
    ("Core.lua", "clientInfo", "GetBuildInfo"): ({"test": 1, "call": 1}, "the `client` block"),
    # M20-13 (schema 5): a /reload in combat sends no PLAYER_REGEN_DISABLED, so
    # combat is asked once per loading screen; only `true` or `false` is kept.
    ("Core.lua", 'ns.On("PLAYER_ENTERING_WORLD")', "InCombatLockdown"): (
        {"test": 1, "call": 1},
        "whether the player is in combat as the world is entered, through pcall",
    ),
    # M20-13 (schema 5; ADR-0026, note of 2026-11-20): the time of each
    # section's gather and of the addon's write.
    ("Core.lua", "gather", "time"): ({"call": 1}, "the time of a section's gather"),
    ("Core.lua", "ns.Write", "time"): ({"call": 1}, "the time of the addon's write"),
    # Client-given values (round 3): what the client passes to a function it
    # calls, and where Core.lua takes it. The dispatcher's first parameter
    # (the event frame) and the slash handler's second (the chat edit box)
    # are not used at all, so any use of either fails.
    ("Core.lua", 'frame:SetScript("OnEvent")', "event"): (
        {"value": 2},
        "the event name: looks up its handlers and is passed to each",
    ),
    ("Core.lua", 'frame:SetScript("OnEvent")', "..."): (
        {"value": 1},
        "the event's payload, passed on to each handler unread",
    ),
    ("Core.lua", "onEvent", "fired"): (
        {"value": 4, "test": 1},
        "the event name: a key of the section's counts, looked up in its retry events "
        "(M13-05), tested against its change events, passed to gather",
    ),
    ("Core.lua", "gather", "event"): (
        {"value": 1},
        "the event name, passed through pcall to the section's gather (section code)",
    ),
    ("Core.lua", 'ns.On("ADDON_LOADED")', "name"): (
        {"test": 1},
        "the loaded addon's name, compared with this addon's",
    ),
    ("Core.lua", "SlashCmdList.WOWLAB", "message"): (
        {"value": 1},
        "the typed text, read by string.match and never stored",
    ),
    # keyList's predicates: keyList calls each with one of ns.sections, not a
    # client value, but the scan does not follow a function handed to a helper.
    ("Core.lua", "isSkipped", "section.key"): ({"test": 1}, "a section's key, looked up"),
    ("Core.lua", "isBackOnAtReload", "section.off"): ({"test": 1}, "whether a section is off"),
    ("Core.lua", "isBackOnAtReload", "section.key"): ({"test": 1}, "a section's key, looked up"),
    # The one entry outside Core.lua. It runs only while the customization
    # section holds a carried record, which a switched-off section never does
    # (test_m11_37_the_race_check_calls_the_client_only_for_a_carried_record).
    ("Customization.lua", "raceID", "UnitRace"): (
        {"test": 1, "call": 1},
        "the gather's race id, and the PLAYER_ENTERING_WORLD check that drops a carried "
        "record made for another race",
    ),
}

# Each named function that holds a SECTIONLESS_API reference, with the only
# functions that may refer to it and how many direct calls each makes. A
# section's gather or carry literal is labelled with its key: `gather("gear")`.
SECTIONLESS_CALLERS: dict[tuple[str, str], dict[tuple[str, str], int]] = {
    ("Core.lua", "ns.On"): {
        # ADDON_LOADED, PLAYER_ENTERING_WORLD, PLAYER_LOGOUT, and (M20-13)
        # PLAYER_REGEN_DISABLED and PLAYER_REGEN_ENABLED, which track combat
        ("Core.lua", _TOP): 5,
        # the change events, the retry events (M13-05) and the count-only events
        ("Core.lua", "listen"): 3,
        ("Customization.lua", _TOP): 1,  # the race check
    },
    # the retry events (M13-05), the change events and the count-only events
    ("Core.lua", "off"): {("Core.lua", "switchOff"): 3},
    ("Core.lua", "schedule"): {
        ("Core.lua", "onEvent"): 2,  # a change event, and a retry event (M13-05)
        ("Core.lua", 'ns.On("PLAYER_ENTERING_WORLD")'): 1,
    },
    ("Core.lua", "startFirstPass"): {("Core.lua", 'ns.On("PLAYER_ENTERING_WORLD")'): 1},
    ("Core.lua", "clientInfo"): {("Core.lua", "ns.Write"): 1},
    # M20-13: `gather` and `ns.Write` read the clock (SECTIONLESS_API).
    ("Core.lua", "gather"): {
        ("Core.lua", "gatherDirty"): 1,
        ("Core.lua", "onEvent"): 1,  # an immediate section's change event
        ("Core.lua", "ns.Refresh"): 1,
    },
    ("Core.lua", "ns.Write"): {
        ("Core.lua", 'ns.On("PLAYER_LOGOUT")'): 1,
        ("Core.lua", "ns.Refresh"): 1,
    },
    ("Customization.lua", "raceID"): {
        ("Customization.lua", 'gather("customization")'): 1,
        ("Customization.lua", 'ns.On("PLAYER_ENTERING_WORLD")'): 1,
    },
}

# Callees that use a function argument on the spot and keep nothing: the
# callee as written -> (argument positions, None for all; reason). What they
# RETURN is followed separately (`_RETURN_THEIR_ARGUMENTS`).
_ON_THE_SPOT: dict[str, tuple[frozenset[int] | None, str]] = {
    "pcall": (frozenset({0}), "calls its first argument now"),
    "xpcall": (frozenset({0, 1}), "calls its first argument now, the handler on an error"),
    "ns . Call": (frozenset({0}), "pcall on its first argument (pinned below)"),
    "ns . Fn": (frozenset({0}), "reads one field of its first argument (pinned below)"),
    "type": (None, "returns the type's name"),
    "tostring": (None, "returns a string"),
    "pairs": (None, "returns an iterator over its argument (followed)"),
    "ipairs": (None, "returns an iterator over its argument (followed)"),
    "next": (None, "returns one entry of its argument (followed)"),
    "ns . Number": (None, "returns a number or nil (pinned below)"),
    "ns . String": (None, "returns a string or nil (pinned below)"),
    "ns . Bool": (None, "returns a boolean or nil (pinned below)"),
    "ns . Numbers": (None, "returns a new list of numbers (pinned below)"),
    "table . sort": (frozenset({1}), "calls the comparator while it sorts"),
}
# Of those, the ones that call a function argument where it stands, so a
# function literal there is section code when the call is.
_CALLS_ON_THE_SPOT: dict[str, frozenset[int]] = {
    "pcall": frozenset({0}),
    "xpcall": frozenset({0, 1}),
    "ns . Call": frozenset({0}),
    "table . sort": frozenset({1}),
}
# Builtins whose result is one of their arguments or an entry of one: a call
# to one may be a function when an argument may be.
_RETURN_THEIR_ARGUMENTS = frozenset({"next", "pairs", "ipairs", "select", "unpack"})

# Values that may be functions, handed on from section code, each with why it
# still runs on the spot: (file, function, the call as written) -> reason.
HANDED_ON: dict[tuple[str, str, str], str] = {
    ("Gear.lua", "itemLevel", "ns . Call ( fromSlot , ItemLocation , slot )"): (
        "ItemLocation is the mixin's self: this is ItemLocation:CreateFromEquipmentSlot(slot), "
        "called now through ns.Call"
    ),
    # talents.legacy walks the client's Enum.TraitConfigType and Constants
    # tables with pairs; a loop variable over a client table may be a function
    # as far as the scan knows. Each value goes to a call that uses it now.
    ("Talents.lua", "legacyConfigs", "ns . Call ( byType , value )"): (
        "an Enum.TraitConfigType value, handed now to C_Traits.GetConfigsByType through ns.Call"
    ),
    ("Talents.lua", "legacyConfigs", 'string . find ( key , "SYSTEM_ID" , 1 , true )'): (
        "a Constants key, tested as a string just before; string.find reads it now"
    ),
    ("Talents.lua", "legacyConfigs", "ns . Call ( bySystem , value )"): (
        "a Constants value, tested as a number just before, handed now to "
        "C_Traits.GetConfigIDBySystemID through ns.Call"
    ),
}

# Operators whose result is a boolean, a number or a string, never a function.
_VALUE_OPS = _COMPARISON | {"..", "+", "-", "*", "/", "%", "^", "#"}


@dataclass(frozen=True)
class _ApiRef:
    """One client API reference: a client global, or a use of a handle."""

    file: str
    function: str  # the innermost enclosing function's label, or _TOP
    head: int | None  # that function's head token, None at top level
    api: str  # as written: `C_Timer.After`, `frame:SetScript`
    line: int
    client: bool  # a client global (False: a handle or a client-given value)
    in_section: bool
    use: str  # call, test, bind, self, store, or value
    given: bool = False  # a client-given value (a parameter the client fills)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.file, self.function, self.api)


@dataclass(frozen=True)
class _CallSite:
    """A call written with parentheses: `f(...)`, `a.b(...)`, `o:m(...)`."""

    file: str
    open: int  # the `(`
    start: int  # the callee's first token
    callee: str  # rendered: `ns . On`, `f : SetScript`
    method: bool
    args: list[tuple[int, int]]


def _api_span(tokens: list[Token], k: int) -> tuple[str, int]:
    """The reference at k as written (its field chain, and a method if one
    follows), and the index after it."""
    name, j = tokens[k].text, k + 1
    while (
        j + 1 < len(tokens)
        and tokens[j].kind == "op"
        and tokens[j].text in {".", ":"}
        and tokens[j + 1].kind == "name"
    ):
        name += tokens[j].text + tokens[j + 1].text
        j += 2
        if tokens[j - 2].text == ":":
            break
    return name, j


class _SectionScan:
    """Section code, client API references and handed-on functions (M11-37)."""

    def __init__(self, sources: list[Path]) -> None:
        self.flows = {p.name: _Flow(p, _tokens(p)) for p in sources}
        self.failures: list[tuple[str, str]] = []  # (rule, message)
        # The name token of each declaration (local, parameter, loop variable,
        # local function), by id of the _Decl: _Flow records both in one order.
        self.decl_at: dict[str, dict[int, int]] = {}
        for name, flow in self.flows.items():
            ats: dict[str, list[int]] = {}
            for n, at in flow.assigned:
                if flow.tokens[at].kind == "name" and flow.tokens[at].text == n:
                    ats.setdefault(n, []).append(at)
            self.decl_at[name] = {
                id(d): at
                for n, ds in flow.decls.items()
                for d, at in zip(ds, ats.get(n, []), strict=True)
            }
        self.declared = {name: set(at.values()) for name, at in self.decl_at.items()}
        self.ns_heads: dict[str, tuple[str, int]] = {}
        for name, flow in self.flows.items():
            for head, label in flow.labels.items():
                if label.startswith(("ns.", "ns:")) and flow.tokens[head + 1].text == "ns":
                    self.ns_heads[label[3:]] = (name, head)
        # Function labels, with each function literal written as a field of a
        # section spec labelled by its section: `gather("gear")`.
        self.labels = {name: dict(flow.labels) for name, flow in self.flows.items()}
        for name, flow in self.flows.items():
            for head, label in self._handler_labels(flow).items():
                self.labels[name][head] = label
            for head, label in self._section_labels(flow).items():
                self.labels[name][head] = label
        self.roots = self._roots()
        self.named = self._named()
        # Each reference to a local or `ns` function, by (file, first token).
        self.named_at = {(f, s): fid for fid, refs in self.named.items() for f, s, _ in refs}
        self.section = self._solve()
        self.params = self._params()
        self.calls = [call for name in self.flows for call in self._calls(name)]
        self.mf = self._may_be_functions()
        # Client-given values (rule 1): decl ids, functions whose `...` is
        # given, and followed functions that return a given value.
        self.given: set[int] = set()
        self.given_varargs: set[tuple[str, int]] = set()
        self.returns_given: set[tuple[str, int]] = set()
        self.followed = self._followed()
        self._solve_given()
        self.handles = self._solve_handles()
        self.refs = [ref for name in self.flows for ref in self._refs(name)]
        self.handed_on_used: set[tuple[str, str, str]] = set()
        self._check_sectionless()
        self._check_callers()
        self._check_handed_on()
        self._check_aliases()

    def fail(self, rule: str, message: str) -> None:
        self.failures.append((rule, message))

    def label_at(self, file: str, k: int) -> str:
        fn = self.flows[file].function_at(k)
        return self.labels[file][fn.head] if fn else _TOP

    def at(self, file: str, k: int) -> str:
        return f"{file}:{self.flows[file].tokens[k].line} in {self.label_at(file, k)}"

    @staticmethod
    def _spec_calls(flow: _Flow) -> list[int]:
        """The `{` of each `ns.Section({ ... })` spec."""
        t = flow.tokens
        call = ["ns", ".", "Section", "(", "{"]
        return [
            k + 4
            for k in range(len(t) - 4)
            if not _is_field(t, k) and [x.text for x in t[k : k + 5]] == call
        ]

    def _section_labels(self, flow: _Flow) -> dict[int, str]:
        """`field("key")` for each function literal written as a spec field."""
        t = flow.tokens
        out = {}
        for open_ in self._spec_calls(flow):
            entries = flow.entries(open_)
            key = "?"
            for field, s, e in entries:
                if field == "{key}" and e == s + 1 and t[s].kind == "string":
                    key = t[s].text
                elif field == "{key}" and e == s + 1 and t[s].kind == "name":
                    decl = flow.resolve(t[s].text, s)
                    source = decl.sources[0] if decl is not None and decl.sources else None
                    if source and source[1] == source[0] + 1 and t[source[0]].kind == "string":
                        key = t[source[0]].text
            for field, s, e in entries:
                if t[s].kind == "name" and t[s].text == "function" and flow.block_end(s) == e - 1:
                    out[s] = f'{field[1:-1]}("{key}")'
        return out

    def _handler_labels(self, flow: _Flow) -> dict[int, str]:
        """Labels for function literals the line-number label would name:
        `frame:SetScript("OnEvent")` for the literal after a call's first
        string argument, `SlashCmdList.WOWLAB` for one assigned to a field."""
        t = flow.tokens
        out = {}
        for head in flow.functions:
            if t[head + 1].text != "(":
                continue  # a function statement names itself
            if (
                flow.labels[head].startswith("function@")
                and head >= 4
                and t[head - 1].text == ","
                and t[head - 2].kind == "string"
                and t[head - 3].kind == "op"
                and t[head - 3].text == "("
            ):
                start = self._callee_start(flow, head - 4)
                callee = "".join(x.text for x in t[start : head - 3])
                out[head] = f'{callee}("{t[head - 2].text}")'
            elif head >= 3 and t[head - 1].kind == "op" and t[head - 1].text == "=":
                start = flow.targets(head - 1)
                if start < head - 2 and flow.resolve(t[start].text, start) is None:
                    out[head] = "".join(x.text for x in t[start : head - 1])  # a global's field
        return out

    # what is a reference

    def _variable(self, flow: _Flow, k: int) -> bool:
        """Is token k a name used as a variable: not a keyword, a field, a
        declaration or a table-constructor key?"""
        x = flow.tokens[k]
        return (
            x.kind == "name"
            and x.text not in _KEYWORDS
            and not _is_field(flow.tokens, k)
            and k not in self.declared[flow.path.name]
            and not self._constructor_key(flow, k)
        )

    def client(self, flow: _Flow, k: int) -> bool:
        """Is token k a client global: a global name neither Lua's nor the
        addon's own? (`self` too: rule 3 allows no method definition, so a
        `self` is never a receiver.)"""
        text = flow.tokens[k].text
        return (
            self._variable(flow, k)
            and text not in _LUA51_GLOBALS
            and text not in OWN_GLOBALS
            and flow.resolve(text, k) is None
        )

    def _use_of(self, flow: _Flow, k: int, decls: set[int]) -> bool:
        """Is token k a use of one of `decls` (by id)?"""
        if not self._variable(flow, k):
            return False
        decl = flow.resolve(flow.tokens[k].text, k)
        return decl is not None and id(decl) in decls

    @staticmethod
    def _constructor_key(flow: _Flow, k: int) -> bool:
        """Is token k the key of a `{ key = value }` entry?"""
        t = flow.tokens
        o = flow.opener[k]
        if o is None or t[o].text != "{" or k + 1 >= len(t):
            return False
        if not (t[k + 1].kind == "op" and t[k + 1].text == "="):
            return False
        fn = flow.function_at(k)
        if fn is not None and o < fn.start:
            return False  # a statement in a function literal inside the constructor
        return t[k - 1].kind == "op" and t[k - 1].text in {"{", ",", ";"}

    def _holds_client(
        self,
        flow: _Flow,
        k: int,
        holds: set[int],
        returns: set[tuple[str, int]],
        varargs: set[tuple[str, int]],
    ) -> bool:
        """Does token k bring a client value in: a client global, a local or
        parameter that holds one, a reference to a followed function that
        returns one, or `...` in a function a call passes one through?"""
        x = flow.tokens[k]
        name = flow.path.name
        if x.kind == "op" and x.text == "...":
            fn = flow.function_at(k)
            return fn is not None and k >= fn.start and (name, fn.head) in varargs
        if self.client(flow, k) or self._use_of(flow, k, holds):
            return True
        return self.named_at.get((name, k)) in returns

    def _solve_handles(self) -> dict[str, set[int]]:
        """ids of the handles, by file: file-level locals that are ever bound
        or assigned a value holding a client value, followed through locals,
        through what a followed function returns and through the parameters a
        direct call passes one to (round 4), and every file-level local that
        section code assigns, whatever the value (section code is where the
        client's objects are made)."""
        holds: set[int] = set()
        returns: set[tuple[str, int]] = set()
        varargs: set[tuple[str, int]] = set()

        def brings(flow: _Flow, s: int, e: int) -> bool:
            return any(self._holds_client(flow, k, holds, returns, varargs) for k in range(s, e))

        changed = True
        while changed:
            changed = False
            for flow in self.flows.values():
                for decls in flow.decls.values():
                    for d in decls:
                        if id(d) in holds or d.function is not None:
                            continue
                        if any(brings(flow, s, e) for s, e in d.sources):
                            holds.add(id(d))
                            changed = True
            for call in self.calls:
                fid = self._named_callee(call.file, call.start, call.open)
                if fid is None or fid not in self.followed:
                    continue
                params = self.params[fid]
                offset = 1 if call.method else 0
                flow = self.flows[call.file]
                for p, (a, b) in enumerate(call.args):
                    if not brings(flow, a, b):
                        continue
                    last = p == len(call.args) - 1
                    for q in range(p + offset, len(params) if last else p + offset + 1):
                        param = params[q] if q < len(params) else None
                        if param is not None and param not in holds:
                            holds.add(param)
                            changed = True
                    if (last or p + offset >= len(params)) and fid not in varargs:
                        varargs.add(fid)
                        changed = True
            for fid in sorted(self.followed - returns):
                name, head = fid
                flow = self.flows[name]
                if any(brings(flow, s, e) for s, e in flow.returns(head)):
                    returns.add(fid)
                    changed = True
        out: dict[str, set[int]] = {}
        for name, flow in self.flows.items():
            at = self.decl_at[name]
            out[name] = {
                id(d)
                for ds in flow.decls.values()
                for d in ds
                if d.function is None
                and flow.function_at(at[id(d)]) is None
                and (
                    id(d) in holds or any(s >= 1 and self.inside(name, s - 1) for s, _ in d.sources)
                )
            }
        return out

    def _use(self, flow: _Flow, s: int, e: int) -> str:
        """How the reference tokens[s:e] is used (see SECTIONLESS_API)."""
        t = flow.tokens
        n = len(t)
        if flow.call_start(e):
            return "call"
        if s >= 2 and t[s - 1].kind == "op" and t[s - 1].text == "(" and e < n:
            callee = _render(t[self._callee_start(flow, s - 2) : s - 1])
            if t[e].text in {",", ")"} and callee in {"pcall", "ns . Call"}:
                return "call"
            if t[e].text == ")" and callee == "type":
                return "test"
        if (
            s >= 6
            and e < n
            and t[e].text in {",", ")"}
            and [x.text for x in t[s - 6 : s - 3]] == ["pcall", "(", t[s].text]
            and t[s - 3].text == "."
            and t[s - 1].text == ","
        ):
            return "self"
        if s in flow.skip or (e < n and t[e].kind == "name" and t[e].text == "and"):
            return "test"
        if (
            t[s - 1].kind == "name"
            and t[s - 1].text in {"if", "elseif", "while"}
            and e < n
            and t[e].text in {"then", "do"}
        ):
            return "test"
        if e < n and t[e].kind == "op" and t[e].text == "=" and flow.statement_level(e):
            return "store"
        if flow.function_at(s) is None:
            for decls in flow.decls.values():
                for d in decls:
                    if id(d) in self.handles[flow.path.name] and any(
                        a <= s < b for a, b in d.sources
                    ):
                        return "bind"
        return "value"

    def _refs(self, name: str) -> list[_ApiRef]:
        flow = self.flows[name]
        t = flow.tokens
        out = []
        for k in range(len(t)):
            client = self.client(flow, k)
            handle = not client and self._use_of(flow, k, self.handles[name])
            given = not client and not handle and self.given_at(flow, k)
            if client or handle or given:
                api, end = _api_span(t, k)
                fn = flow.function_at(k)
                out.append(
                    _ApiRef(
                        name,
                        self.label_at(name, k),
                        fn.head if fn else None,
                        api,
                        t[k].line,
                        client,
                        self.inside(name, k),
                        self._use(flow, k, end),
                        given,
                    )
                )
        return out

    # what is section code

    def _roots(self) -> dict[tuple[str, int], set[int]]:
        """Each section's gather function, with the spec tokens that name it.
        A carry is not a root: it runs at ADDON_LOADED (ruling above)."""
        roots: dict[tuple[str, int], set[int]] = {}
        for name, flow in self.flows.items():
            t = flow.tokens
            for open_ in self._spec_calls(flow):
                for key, s, e in flow.entries(open_):
                    if key != "{gather}":
                        continue
                    literal = t[s].kind == "name" and t[s].text == "function"
                    if literal and flow.block_end(s) == e - 1:
                        roots.setdefault((name, s), set())
                        continue
                    one_name = e == s + 1 and t[s].kind == "name"
                    decl = flow.resolve(t[s].text, s) if one_name else None
                    if decl is not None and decl.function is not None:
                        roots.setdefault((name, decl.function), set()).add(s)
                    else:
                        self.fail(
                            "outside",
                            f"{name}:{t[s].line} gather is neither a function literal "
                            "nor a local function",
                        )
        return roots

    def _named(self) -> dict[tuple[str, int], list[tuple[str, int, int]]]:
        """Every reference, as (file, start, end token), to each local function
        (`f`) and `ns` function (`ns.F`, `ns:F`)."""
        named: dict[tuple[str, int], list[tuple[str, int, int]]] = {}
        for fid in self.ns_heads.values():
            named[fid] = []
        for name, flow in self.flows.items():
            for decls in flow.decls.values():
                for d in decls:
                    if d.function is not None:
                        named[(name, d.function)] = []
        for name, flow in self.flows.items():
            t = flow.tokens
            for k, x in enumerate(t):
                if not self._variable(flow, k):
                    continue
                decl = flow.resolve(x.text, k)
                if decl is None:
                    continue
                if decl.function is not None:
                    named[(name, decl.function)].append((name, k, k + 1))
                elif (
                    decl is flow.namespace
                    and not (k > 0 and t[k - 1].text == "function")
                    and k + 2 < len(t)
                    and t[k + 1].kind == "op"
                    and t[k + 1].text in {".", ":"}
                    and t[k + 2].text in self.ns_heads
                ):
                    named[self.ns_heads[t[k + 2].text]].append((name, k, k + 3))
        return named

    def inside(
        self,
        file: str,
        k: int,
        section: set[tuple[str, int]] | None = None,
        exclude: int | None = None,
    ) -> bool:
        """Does token k run only as section code (is the innermost function around it)?"""
        flow = self.flows[file]
        around = [b for b in flow.functions.values() if b.head <= k <= b.stop and b.head != exclude]
        if not around:
            return False  # a file's top level
        fn = max(around, key=lambda b: b.head)
        return (file, fn.head) in (self.section if section is None else section)

    def argument_of(self, file: str, s: int, e: int) -> tuple[str, int, int] | None:
        """If tokens[s:e] is a whole argument of a call written with
        parentheses: (the callee as written, its first token, the position)."""
        flow = self.flows[file]
        t = flow.tokens
        o = flow.opener[s] if s < len(t) else None
        if o is None or o < 1 or t[o].kind != "op" or t[o].text != "(":
            return None
        p = t[o - 1]
        if not (
            (p.kind == "name" and p.text not in _KEYWORDS)
            or (p.kind == "op" and p.text in {")", "]"})
        ):
            return None  # a parenthesised expression, or a function's parameter list
        for position, (a, b) in enumerate(flow.exprlist(o + 1)):
            if a == s and b == e:
                start = self._callee_start(flow, o - 1)
                return _render(t[start:o]), start, position
        return None

    def trusted(self, file: str, callee: str, start: int) -> bool:
        """Is the callee the builtin or addon helper its name says (not a local
        of the same name)?"""
        flow = self.flows[file]
        root = flow.tokens[start].text
        if root == "ns":
            return flow.resolve("ns", start) is flow.namespace
        return flow.resolve(root, start) is None

    def called_here(self, file: str, s: int, e: int) -> bool:
        """Is the function expression tokens[s:e] called where it stands:
        followed by call arguments, or at a position where the callee calls it
        now (`_CALLS_ON_THE_SPOT`: pcall, xpcall, ns.Call, a table.sort
        comparator)?"""
        if self.flows[file].call_start(e):
            return True
        arg = self.argument_of(file, s, e)
        if arg is None:
            return False
        callee, start, position = arg
        return position in _CALLS_ON_THE_SPOT.get(callee, frozenset()) and self.trusted(
            file, callee, start
        )

    def _solve(self) -> set[tuple[str, int]]:
        """The largest set of functions that run only as section code."""
        section = {(name, head) for name, flow in self.flows.items() for head in flow.functions}
        changed = True
        while changed:
            changed = False
            for fid in sorted(section):
                if not self._runs_in_section(fid, section):
                    section.discard(fid)
                    changed = True
        return section

    def _runs_in_section(self, fid: tuple[str, int], section: set[tuple[str, int]]) -> bool:
        name, head = fid
        entries = self.roots.get(fid)
        refs = self.named.get(fid)
        if refs is None:  # a function literal
            end = self.flows[name].block_end(head) + 1
            return entries is not None or (
                self.inside(name, head, section, exclude=head) and self.called_here(name, head, end)
            )
        uses = [(f, s, e) for f, s, e in refs if not (f == name and entries and s in entries)]
        if entries is None and not uses:
            return False  # never referenced: fail closed
        return all(self.inside(f, s, section) and self.called_here(f, s, e) for f, s, e in uses)

    # calls, and values that may be functions

    @staticmethod
    def _callee_start(flow: _Flow, k: int) -> int:
        """The first token of the prefix expression that ends at token k."""
        t = flow.tokens
        while k >= 0:
            if t[k].kind == "name":
                if k >= 2 and t[k - 1].kind == "op" and t[k - 1].text in {".", ":"}:
                    k -= 2
                    continue
                return k
            if t[k].kind == "op" and t[k].text in {")", "]"}:
                o = flow.pair[k]
                before = t[o - 1] if o >= 1 else None
                if before is not None and (
                    (before.kind == "name" and before.text not in _KEYWORDS)
                    or (before.kind == "op" and before.text in {")", "]"})
                ):
                    k = o - 1
                    continue
                return o
            return k
        return 0

    def _calls(self, name: str) -> list[_CallSite]:
        flow = self.flows[name]
        t = flow.tokens
        headers = set()
        for head in flow.functions:
            paren = head + 1
            while t[paren].text != "(":
                paren += 1
            headers.add(paren)
        out = []
        for o, x in enumerate(t):
            if not (x.kind == "op" and x.text == "(") or o == 0 or o in headers:
                continue
            p = t[o - 1]
            if not (
                (p.kind == "name" and p.text not in _KEYWORDS)
                or (p.kind == "op" and p.text in {")", "]"})
            ):
                continue
            start = self._callee_start(flow, o - 1)
            method = o >= 2 and t[o - 2].kind == "op" and t[o - 2].text == ":"
            args = [(a, b) for a, b in flow.exprlist(o + 1) if b <= flow.pair[o]]
            out.append(_CallSite(name, o, start, _render(t[start:o]), method, args))
        return out

    def _params(self) -> dict[tuple[str, int], list[int | None]]:
        """Each function's parameters, as decl ids in order (None for `...`)."""
        out: dict[tuple[str, int], list[int | None]] = {}
        for name, flow in self.flows.items():
            t = flow.tokens
            by_token = {at: d_id for d_id, at in self.decl_at[name].items()}
            for head in flow.functions:
                paren = head + 1
                while t[paren].text != "(":
                    paren += 1
                ids: list[int | None] = []
                for k in range(paren + 1, flow.pair[paren]):
                    if t[k].kind == "name":
                        ids.append(by_token.get(k))
                    elif t[k].text == "...":
                        ids.append(None)
                if t[head + 1].text != "(" and any(
                    t[k].text == ":" for k in range(head + 1, paren)
                ):
                    ids.insert(0, None)  # `function a:b()` takes self first
                out[(name, head)] = ids
        return out

    def _helper(self, call: _CallSite) -> tuple[str, int] | None:
        """The section-code function a call calls by name, if it is one."""
        flow = self.flows[call.file]
        t = flow.tokens
        fid = None
        if call.start == call.open - 1:
            decl = flow.resolve(t[call.start].text, call.start)
            if decl is not None and decl.function is not None:
                fid = (call.file, decl.function)
        elif (
            call.open - call.start == 3
            and t[call.start].text == "ns"
            and flow.resolve("ns", call.start) is flow.namespace
            and t[call.start + 2].text in self.ns_heads
        ):
            fid = self.ns_heads[t[call.start + 2].text]
        return fid if fid in self.section else None

    def _split(self, flow: _Flow, s: int, e: int, word: str) -> list[tuple[int, int]]:
        """tokens[s:e] split at each top-level `word` (`and`, `or`)."""
        t = flow.tokens
        parts, a, k = [], s, s
        while k < e:
            x = t[k]
            if x.kind == "op" and x.text in _OPEN:
                k = flow.pair[k] + 1
                continue
            if x.kind == "name" and x.text == "function":
                k = flow.block_end(k) + 1
                continue
            if x.kind == "name" and x.text == word:
                parts.append((a, k))
                a = k + 1
            k += 1
        parts.append((a, e))
        return parts

    def _plain(self, flow: _Flow, s: int, e: int) -> bool:
        """Does tokens[s:e] hold, at its top level, an operator whose result is
        never a function (a comparison, arithmetic, `..`, `#`, `not`)?"""
        t = flow.tokens
        k = s
        while k < e:
            x = t[k]
            if x.kind == "op" and x.text in _OPEN:
                k = flow.pair[k] + 1
                continue
            if x.kind == "name" and x.text == "function":
                k = flow.block_end(k) + 1
                continue
            if (x.kind == "op" and x.text in _VALUE_OPS) or (x.kind == "name" and x.text == "not"):
                return True
            k += 1
        return False

    def may_be_function(self, flow: _Flow, s: int, e: int, mf: set[int]) -> bool:
        """May the expression tokens[s:e] be a function? (See rule 2.)"""
        t = flow.tokens
        if s >= e:
            return False
        ors = self._split(flow, s, e, "or")
        if len(ors) > 1:
            return any(self.may_be_function(flow, a, b, mf) for a, b in ors)
        ands = self._split(flow, s, e, "and")
        if len(ands) > 1:
            return self.may_be_function(flow, *ands[-1], mf)  # else it is false or nil
        if self._plain(flow, s, e):
            return False
        x = t[s]
        if x.kind in {"string", "number"} or x.text in {"nil", "true", "false", "..."}:
            return False
        if x.kind == "name" and x.text == "function":
            return True
        if x.kind == "op" and x.text == "{":
            return False  # a constructor: its entries are stores (rule 2)
        if x.kind == "op" and x.text == "(" and flow.pair[s] == e - 1:
            return self.may_be_function(flow, s + 1, e - 1, mf)
        if x.kind != "name":
            return True  # something else: fail closed
        k, calls, first_call, last_call = s + 1, 0, e, False
        while k < e:
            y = t[k]
            if y.kind == "op" and y.text == "." and k + 1 < e and t[k + 1].kind == "name":
                k, last_call = k + 2, False
            elif y.kind == "op" and y.text == "[":
                k, last_call = flow.pair[k] + 1, False
            elif y.kind == "op" and y.text == ":" and flow.call_start(k + 2):
                first_call = min(first_call, k)
                k = k + 2
                k = flow.pair[k] + 1 if t[k].kind == "op" else k + 1
                calls, last_call = calls + 1, True
            elif flow.call_start(k):
                first_call = min(first_call, k)
                k = flow.pair[k] + 1 if t[k].kind == "op" else k + 1
                calls, last_call = calls + 1, True
            else:
                return True  # fail closed
        if last_call:
            # ns.Fn(...) returns a function; next, pairs, ipairs, select and
            # unpack return an argument or an entry of one. Any other call's
            # result is taken as data (a client call's result: review only).
            callee = _render(t[s:first_call])
            if calls != 1 or not self.trusted(flow.path.name, callee, s):
                return False
            if callee == "ns . Fn":
                return True
            if callee in _RETURN_THEIR_ARGUMENTS and t[first_call].text == "(":
                args = flow.exprlist(first_call + 1)
                return any(self.may_be_function(flow, a, b, mf) for a, b in args)
            return False
        if calls:
            return False  # a field of what a call returned: review only
        decl = flow.resolve(x.text, s)
        if decl is None:
            return x.text not in OWN_GLOBALS  # a client global (or a Lua builtin) as a value
        if decl is flow.namespace:
            return s + 2 < e and t[s + 2].text in self.ns_heads
        if decl.function is not None:
            return True
        return id(decl) in mf

    def _may_be_functions(self) -> set[int]:
        """ids of the locals, loop variables and parameters that may hold a
        function. A loop over `pairs(T)` or `ipairs(T)` gives its variables
        what T may hold."""
        mf: set[int] = set()
        changed = True
        while changed:
            changed = False
            for flow in self.flows.values():
                for decls in flow.decls.values():
                    for d in decls:
                        if id(d) in mf or d.function is not None:
                            continue
                        if any(self.may_be_function(flow, s, e, mf) for s, e in d.sources):
                            mf.add(id(d))
                            changed = True
            for call in self.calls:
                if not self.inside(call.file, call.open) or self._on_the_spot(call) is not None:
                    continue
                helper = self._helper(call)
                if helper is None:
                    continue
                params = self.params[helper]
                offset = 1 if call.method else 0
                flow = self.flows[call.file]
                for p, (a, b) in enumerate(call.args):
                    if p + offset < len(params) and self.may_be_function(flow, a, b, mf):
                        param = params[p + offset]
                        if param is not None and param not in mf:
                            mf.add(param)
                            changed = True
        return mf

    # client-given values

    def _followed(self) -> set[tuple[str, int]]:
        """The functions whose every caller the scan sees: a local function or
        an `ns` function referred to only by direct calls. Any other function
        (a literal, one handed to anything, one stored anywhere, a global
        function) may be called by the client, so it takes client values.
        A section's carry literal is followed too: its one caller is
        `pcall(section.carry, WowLabCharDB)` at ADDON_LOADED (pinned by
        test_m11_37_section_code_runs_only_behind_the_off_switch), and the
        saved table holds only strings, numbers, booleans and tables."""
        followed = {
            fid
            for fid, refs in self.named.items()
            if all(self.flows[f].call_start(e) for f, _, e in refs)
        }
        for name, flow in self.flows.items():
            for head, label in self._section_labels(flow).items():
                if label.startswith('carry("'):
                    followed.add((name, head))
        return followed

    def _named_callee(self, file: str, start: int, open_: int) -> tuple[str, int] | None:
        """The local or `ns` function a call written `f(...)` or `ns.F(...)`
        calls, if it is one."""
        flow = self.flows[file]
        t = flow.tokens
        if start == open_ - 1:
            decl = flow.resolve(t[start].text, start)
            if decl is not None and decl.function is not None:
                return (file, decl.function)
        elif (
            open_ - start == 3
            and t[start].text == "ns"
            and flow.resolve("ns", start) is flow.namespace
            and t[start + 2].text in self.ns_heads
        ):
            return self.ns_heads[t[start + 2].text]
        return None

    def given_at(self, flow: _Flow, k: int) -> bool:
        """Is token k a client-given value: a use of a given local or
        parameter, or `...` in a function whose `...` is given?"""
        x = flow.tokens[k]
        if x.kind == "op" and x.text == "...":
            fn = flow.function_at(k)
            return (
                fn is not None
                and k >= fn.start  # not the `...` of its parameter list
                and (flow.path.name, fn.head) in self.given_varargs
            )
        return self._use_of(flow, k, self.given)

    def _holds_given(self, flow: _Flow, s: int, e: int) -> bool:
        """Is there a given value anywhere in tokens[s:e], outside the bodies
        of function literals (fail closed)?"""
        t = flow.tokens
        k = s
        while k < e:
            if t[k].kind == "name" and t[k].text == "function":
                k = flow.block_end(k) + 1
                continue
            if self.given_at(flow, k):
                return True
            k += 1
        return False

    def carries(self, flow: _Flow, s: int, e: int) -> bool:
        """May the expression tokens[s:e] be, or hold, a client-given value?"""
        t = flow.tokens
        if s >= e:
            return False
        ors = self._split(flow, s, e, "or")
        if len(ors) > 1:
            return any(self.carries(flow, a, b) for a, b in ors)
        ands = self._split(flow, s, e, "and")
        if len(ands) > 1:
            return self.carries(flow, *ands[-1])
        if self._plain(flow, s, e):
            return False
        x = t[s]
        if e == s + 1 and (x.kind in {"string", "number"} or x.text in {"nil", "true", "false"}):
            return False
        if e == s + 1 and x.text == "...":
            return self.given_at(flow, s)
        if x.kind == "name" and x.text == "function" and flow.block_end(s) == e - 1:
            return False  # a closure: its own body is checked where it is
        if x.kind == "op" and x.text == "(" and flow.pair[s] == e - 1:
            return self.carries(flow, s + 1, e - 1)
        if x.kind != "name" or x.text in _KEYWORDS:
            return self._holds_given(flow, s, e)  # a constructor, or anything else
        if self.given_at(flow, s):
            return True
        k, calls, first_call, last_call = s + 1, 0, e, False
        while k < e:
            y = t[k]
            if y.kind == "op" and y.text == "." and k + 1 < e and t[k + 1].kind == "name":
                k, last_call = k + 2, False
            elif y.kind == "op" and y.text == "[":
                k, last_call = flow.pair[k] + 1, False
            elif y.kind == "op" and y.text == ":" and flow.call_start(k + 2):
                first_call = min(first_call, k)
                k = k + 2
                k = flow.pair[k] + 1 if t[k].kind == "op" else k + 1
                calls, last_call = calls + 1, True
            elif flow.call_start(k):
                first_call = min(first_call, k)
                k = flow.pair[k] + 1 if t[k].kind == "op" else k + 1
                calls, last_call = calls + 1, True
            else:
                return self._holds_given(flow, s, e)
        if not calls:
            return False  # a local that is not given, or a field of one
        if t[first_call].text == "(":
            if self._named_callee(flow.path.name, s, first_call) in self.returns_given:
                return True
            callee = _render(t[s:first_call])
            if (
                last_call
                and calls == 1
                and callee in _RETURN_THEIR_ARGUMENTS
                and self.trusted(flow.path.name, callee, s)
            ):
                return any(self.carries(flow, a, b) for a, b in flow.exprlist(first_call + 1))
        return False  # what any other call returns: see the review-only note

    def _solve_given(self) -> None:
        """Every parameter (and the `...`) of a function the scan does not
        follow is client-given; so is a local or loop variable ever given
        one, a followed function's parameter a direct call passes one, and
        what a followed function returning one returns."""
        for name, flow in self.flows.items():
            for head in flow.functions:
                fid = (name, head)
                if fid not in self.followed:
                    self.given.update(p for p in self.params[fid] if p is not None)
                    self.given_varargs.add(fid)
        changed = True
        while changed:
            changed = False
            for flow in self.flows.values():
                for decls in flow.decls.values():
                    for d in decls:
                        if id(d) in self.given or d.function is not None:
                            continue
                        if any(self.carries(flow, s, e) for s, e in d.sources):
                            self.given.add(id(d))
                            changed = True
            for call in self.calls:
                fid = self._named_callee(call.file, call.start, call.open)
                if fid is None or fid not in self.followed:
                    continue
                params = self.params[fid]
                offset = 1 if call.method else 0
                flow = self.flows[call.file]
                for p, (a, b) in enumerate(call.args):
                    if not self.carries(flow, a, b):
                        continue
                    last = p == len(call.args) - 1
                    for q in range(p + offset, len(params) if last else p + offset + 1):
                        param = params[q] if q < len(params) else None
                        if param is not None and param not in self.given:
                            self.given.add(param)
                            changed = True
                    if (last or p + offset >= len(params)) and fid not in self.given_varargs:
                        self.given_varargs.add(fid)
                        changed = True
            for fid in sorted(self.followed - self.returns_given):
                name, head = fid
                flow = self.flows[name]
                if any(self.carries(flow, s, e) for s, e in flow.returns(head)):
                    self.returns_given.add(fid)
                    changed = True

    def _on_the_spot(self, call: _CallSite) -> frozenset[int] | None:
        """The argument positions a trusted callee uses on the spot, or None."""
        entry = _ON_THE_SPOT.get(call.callee)
        if entry is None or not self.trusted(call.file, call.callee, call.start):
            return None
        positions = entry[0]
        return frozenset(range(len(call.args))) if positions is None else positions

    # the checks

    def _head(self, file: str, label: str, rule: str) -> int | None:
        """The one function `label` names in `file` (None: the top level); a
        label that names none or several is a failure."""
        if label == _TOP:
            return None
        heads = [h for h, x in self.labels.get(file, {}).items() if x == label]
        if len(heads) != 1:
            self.fail("label", f"{file}: {label} names {len(heads)} functions ({rule})")
            return -1
        return heads[0]

    def _outside(self, ref: _ApiRef) -> str:
        where = f"{ref.file}:{ref.line} in {ref.function}: `{ref.api}`"
        if ref.given:
            return (
                f"{where} is a value the client passes in (a parameter of a function the "
                "client may call, or a local given one), used outside every section's "
                "gather; use it only inside one, or add the use to SECTIONLESS_API with "
                "its reason"
            )
        if ref.head is not None and self.inside(ref.file, ref.head, exclude=ref.head):
            return (
                f"{where} is in a function a gather hands on instead of calling it there, "
                "so it may run after /wowlab skip; call the client in the gather itself"
            )
        return (
            f"{where} is outside every section's gather; move it into one, or add it to "
            "SECTIONLESS_API with its reason"
        )

    @staticmethod
    def _counts(counts: dict[str, int] | dict[tuple[str, str], int]) -> str:
        return ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))

    def _crew_allows(self, ref: _ApiRef) -> bool:
        """M19-01, SECTION_SCAN: Crew.lua's references outside every section,
        allowed only in a gated function, in the pinned helpers, and (the
        typed text only) in the slash handler."""
        if ref.file == "Core.lua":
            # The typed text handed on at Core's one pinned call, and only
            # while a Crew.lua is among the sources.
            return (
                any(CREW_COMMAND in _relaxed(Path(n), "SECTION_SCAN") for n in self.flows)
                and ref.function == CREW_COMMAND_CALLER
                and ref.given
                and ref.api == "message"
                and _crew_command_call_lines(self.flows[ref.file]) == {ref.line}
            )
        allowed = _relaxed(Path(ref.file), "SECTION_SCAN")
        if not allowed:
            return False
        if ref.use == "test" and ref.api in CREW_NAME_CALLS and not ref.given:
            return True  # a presence test, `type(UnitName)`: not a call of it
        if ref.head is None:
            return False
        if ref.function in {"crewBlocked", "secret"} & allowed:
            return not ref.given  # pinned whole
        if ref.function == "crewLoaded" and "crewLoaded" in allowed:
            # The pinned probe handler: its `name` is the loaded addon's.
            flow = self.flows[ref.file]
            head = ref.head
            pinned = _crew_pins(flow.tokens).probe
            return _render(flow.tokens[head - 1 : flow.block_end(head) + 1]) == pinned
        if ref.function == CREW_COMMAND and CREW_COMMAND in allowed:
            return ref.given or ref.api in {"time", "date"}
        return GATED in allowed and _crew_gated(self.flows[ref.file], ref.head)

    def _check_sectionless(self) -> None:
        heads = {
            (file, label): self._head(file, label, "SECTIONLESS_API")
            for file, label in {(k[0], k[1]) for k in SECTIONLESS_API}
        }
        used: dict[tuple[str, str, str], dict[str, int]] = {}
        for ref in self.refs:
            if ref.in_section or self._crew_allows(ref):
                continue
            if ref.key not in SECTIONLESS_API or heads[(ref.file, ref.function)] != ref.head:
                self.fail("outside", self._outside(ref))
                continue
            counts = used.setdefault(ref.key, {})
            counts[ref.use] = counts.get(ref.use, 0) + 1
        # A client-given value is never stored in a field, a table or a
        # global outside section code: a pinned use swapped for a store would
        # keep its count and hide the value from the scan.
        for name, flow in self.flows.items():
            for store in flow.stores:
                if store.text.startswith("{") or self.inside(name, store.at):
                    continue  # a constructor entry goes where its constructor does
                if self.carries(flow, *store.value):
                    self.fail(
                        "outside",
                        f"{self.at(name, store.at)}: `{store.text}` stores a client-given "
                        "value outside every section's gather",
                    )
        for key, (uses, _) in sorted(SECTIONLESS_API.items()):
            if key not in used:
                self.fail("stale", f"SECTIONLESS_API entry {key} matches no reference")
            elif used[key] != uses:
                self.fail(
                    "use",
                    f"{key[0]} in {key[1]}: `{key[2]}` is used as {self._counts(used[key])}; "
                    f"SECTIONLESS_API allows {self._counts(uses)}",
                )

    def _check_callers(self) -> None:
        holders = {
            (ref.file, ref.head)
            for ref in self.refs
            if not ref.in_section
            and not ref.given  # its own parameter: who calls it is what makes it given
            and ref.key in SECTIONLESS_API
            and ref.head is not None
        }
        pinned_seen = set()
        for file, head in sorted(holders):
            refs = self.named.get((file, head))
            if refs is None:
                continue  # a function literal: registered where it is written
            label = self.labels[file][head]
            pinned = SECTIONLESS_CALLERS.get((file, label))
            if pinned is None:
                self.fail("callers", f"{file}: {label} calls the client; pin its callers")
                continue
            pinned_seen.add((file, label))
            callers: dict[tuple[str, str], int] = {}
            for f, s, e in refs:
                if (file, label) == ("Core.lua", "ns.On") and _crew_on_call_ok(
                    self.flows[f], s, _core_file_events(self.flows.get("Core.lua"))
                ):
                    continue  # M19-01: Crew.lua's ns.On calls, by their own rule
                if not self.flows[f].call_start(e):
                    self.fail(
                        "callers",
                        f"{self.at(f, s)}: {label} is referred to other than by a direct call",
                    )
                    continue
                caller = self.label_at(f, s)
                if caller != _TOP:
                    self._head(f, caller, f"a caller of {label}")
                callers[(f, caller)] = callers.get((f, caller), 0) + 1
            if callers != pinned:
                self.fail(
                    "callers",
                    f"{file}: {label} is called from {self._counts(callers)}; "
                    f"SECTIONLESS_CALLERS pins {self._counts(pinned)}",
                )
        for key in sorted(set(SECTIONLESS_CALLERS) - pinned_seen):
            self.fail("stale", f"SECTIONLESS_CALLERS entry {key} matches no function")

    def _handed_on(self, file: str, k: int, text: str, what: str) -> None:
        """Report a function handed on in the function around token k (the
        call's `(`, the constructor's `{`, the store's target, the `return`),
        unless HANDED_ON lists it."""
        key = (file, self.label_at(file, k), text)
        fn = self.flows[file].function_at(k)
        if key in HANDED_ON and self._head(file, key[1], "HANDED_ON") == (fn.head if fn else None):
            self.handed_on_used.add(key)
            return
        self.fail("handed-on", f"{self.at(file, k)}: {what}")

    def _check_handed_on(self) -> None:
        for call in self.calls:
            if not self.inside(call.file, call.open):
                continue
            flow = self.flows[call.file]
            t = flow.tokens
            spot = self._on_the_spot(call)
            helper = self._helper(call) if spot is None else None
            params = self.params[helper] if helper is not None else []
            offset = 1 if call.method else 0
            text = _render(t[call.start : flow.pair[call.open] + 1])
            for p, (a, b) in enumerate(call.args):
                if not self.may_be_function(flow, a, b, self.mf):
                    continue
                if (spot is not None and p in spot) or (
                    p + offset < len(params) and params[p + offset] is not None
                ):
                    continue
                self._handed_on(
                    call.file,
                    call.open,
                    text,
                    f"`{_render(t[a:b])}` may be a function; handed to `{call.callee}`",
                )
        for name, flow in self.flows.items():
            for store in flow.stores:
                # A constructor entry's own token may be a function literal's
                # head; the entry sits in the function around its `{`.
                k = flow.opener[store.at] if store.text.startswith("{") else store.at
                if (
                    k is not None
                    and self.inside(name, k)
                    and self.may_be_function(flow, *store.value, self.mf)
                ):
                    what = f"`{store.text}` stores what may be a function"
                    self._handed_on(name, k, store.text, what)
            # A local declared outside section code (a file-level local, or one
            # of a handler) outlives the gather: giving it a function hands the
            # function on as surely as a table field does.
            at = self.decl_at[name]
            for decls in flow.decls.values():
                for d in decls:
                    if d.function is not None or self.inside(name, at[id(d)]):
                        continue
                    for s, e in d.sources:
                        if not (s >= 1 and self.inside(name, s - 1)):
                            continue
                        if self.may_be_function(flow, s, e, self.mf):
                            text = f"{d.name} = {_render(flow.tokens[s:e])}"
                            what = (
                                f"`{text}` gives a local declared outside section code "
                                "what may be a function"
                            )
                            self._handed_on(name, s - 1, text, what)
            t = flow.tokens
            for head in flow.functions:
                # `function ns.later()` or `function Later()` written in section
                # code stores a function where other code can call it later.
                named_statement = t[head + 1].text != "(" and t[head - 1].text != "local"
                if named_statement and self.inside(name, head, exclude=head):
                    text = f"function {self.labels[name][head]}"
                    self._handed_on(name, head - 1, text, f"`{text}` stores a function")
                if (name, head) not in self.section:
                    continue
                for s, e in flow.returns(head):
                    if self.may_be_function(flow, s, e, self.mf):
                        text = f"return {_render(flow.tokens[s:e])}"
                        what = (
                            f"`{text}` returns what may be a function; call it where it is "
                            "looked up and return what the call gives"
                        )
                        self._handed_on(name, s - 1, text, what)
        for key in sorted(set(HANDED_ON) - self.handed_on_used):
            self.fail("stale", f"HANDED_ON entry {key} matches nothing")

    def _check_aliases(self) -> None:
        for name, flow in self.flows.items():
            t = flow.tokens
            # The file's `...` (the addon name and its namespace table) is
            # bound once, as `local <name>, ns = ...`, and used nowhere else,
            # so the namespace is only ever reached as `ns`.
            bindings = 0
            for k in range(len(t)):
                if not (t[k].kind == "op" and t[k].text == "..." and flow.function_at(k) is None):
                    continue
                binding = (
                    k >= 5
                    and t[k - 5].text == "local"
                    and t[k - 4].kind == "name"
                    and [x.text for x in t[k - 3 : k]] == [",", "ns", "="]
                    and flow.expr_end(k) == k + 1
                    and not (k + 1 < len(t) and t[k + 1].text == ",")
                )
                if binding:
                    bindings += 1
                else:
                    self.fail(
                        "vararg",
                        f"{self.at(name, k)}: the file's `...` is used other than as "
                        "`local <name>, ns = ...`",
                    )
            if bindings > 1:
                self.fail("vararg", f"{name}: the file's `...` is bound {bindings} times")
            # No method definition: its hidden `self` is whatever the caller
            # passes first, a name the scan would not follow.
            for head in flow.functions:
                paren = head + 1
                while t[paren].text != "(":
                    paren += 1
                if any(t[j].kind == "op" and t[j].text == ":" for j in range(head + 1, paren)):
                    self.fail(
                        "method",
                        f"{self.at(name, head)}: `function {self.labels[name][head]}` is a "
                        "method definition; write it with `.` and name its parameters",
                    )
            for k in range(len(t)):
                if t[k].text != "ns" or not self._variable(flow, k):
                    continue
                if flow.resolve("ns", k) is not flow.namespace:
                    continue
                nxt = t[k + 1] if k + 1 < len(t) else None
                if nxt is not None and nxt.kind == "op" and nxt.text == ":":
                    self.fail(
                        "ns",
                        f"{self.at(name, k)}: `ns:` hands the namespace on as `self`; "
                        "write ns.Name",
                    )
                    continue
                if nxt is None or nxt.kind != "op" or nxt.text != ".":
                    self.fail("ns", f"{self.at(name, k)}: `ns` used bare; write ns.Name")
                    continue
                if nxt.text == "." and k + 2 < len(t) and t[k + 2].text == "state":
                    after = t[k + 3] if k + 3 < len(t) else None
                    if after is None or after.kind != "op" or after.text not in {"[", "="}:
                        self.fail(
                            "state",
                            f"{self.at(name, k)}: `ns.state` used other than indexed or assigned",
                        )


def _section_scan(sources: list[Path] | None = None) -> _SectionScan:
    """The scan of `sources`, or of the sources under ADDON as it is now (the
    review probes point ADDON at an edited copy)."""
    try:
        return _SectionScan(sorted(ADDON.glob("*.lua")) if sources is None else sources)
    except (ValueError, IndexError, KeyError, StopIteration) as err:  # fail closed
        raise AssertionError(f"the sources could not be analysed: {err!r}") from err


def _failures(scan: _SectionScan, *rules: str) -> list[str]:
    return [message for rule, message in scan.failures if rule in rules]


def test_m11_37_every_client_api_call_is_inside_a_section() -> None:
    """Rule 1: every client API reference sits in section code, which a
    `/wowlab skip` typed in the session stops, or is a SECTIONLESS_API entry
    in the one function its label names, used only in the ways it lists. See
    the comment above `_LUA51_GLOBALS`."""
    scan = _section_scan()
    assert not _failures(scan, "outside", "use", "label"), _failures(
        scan, "outside", "use", "label"
    )


def test_m11_37_every_exception_is_used() -> None:
    """An entry of SECTIONLESS_API, SECTIONLESS_CALLERS or HANDED_ON that
    matches nothing is stale: it fails until it is removed."""
    scan = _section_scan()
    assert not _failures(scan, "stale"), _failures(scan, "stale")


def test_m11_37_functions_that_call_the_client_outside_a_section_have_pinned_callers() -> None:
    """Rule 1: a named function holding a SECTIONLESS_API reference is referred
    to only by direct calls, from exactly the functions SECTIONLESS_CALLERS
    pins, so it cannot be handed to a handler or a timer, or called at load."""
    scan = _section_scan()
    assert not _failures(scan, "callers", "label"), _failures(scan, "callers", "label")


def test_m11_37_section_code_hands_no_function_on() -> None:
    """Rule 2 (ruling, M11-37 review): a gather hands no function on to
    something that could run it after `/wowlab skip`: not to ns.On, a timer,
    SetScript or a hook, not into a table or a global, not as a return value."""
    scan = _section_scan()
    assert not _failures(scan, "handed-on"), _failures(scan, "handed-on")


def test_m11_37_ns_and_ns_state_are_never_aliased() -> None:
    """Rule 3: each file binds its `...` once, as `local <name>, ns = ...`, and
    uses it nowhere else (no `local _, addon = ...`, no `select(2, ...)`);
    `ns` appears only as `ns.Name` (never `ns:Name`, which passes it as
    `self`), `ns.state` only indexed or assigned where it stands, and no
    function is defined with `:`. So the namespace, and the state the race
    check reads, cannot be reached through a name the scan does not follow,
    `self` included."""
    scan = _section_scan()
    rules = ("vararg", "ns", "state", "method")
    assert not _failures(scan, *rules), _failures(scan, *rules)


def test_m11_37_the_scan_sees_exactly_the_std_names() -> None:
    """The scan's client globals are the names wow_client.yml declares (less
    the addon's own), so it neither misses a client name nor takes a local for
    one; and every file that registers a section has client references inside
    section code, so the section test is not passing vacuously."""
    scan = _section_scan()
    roots = {re.split(r"[.:]", ref.api)[0] for ref in scan.refs if ref.client}
    std = {name.split(".")[0] for name in _std_globals()} - OWN_GLOBALS
    assert roots == std, (sorted(roots - std), sorted(std - roots))
    with_sections = {path.name for path in sorted(ADDON.glob("*.lua")) if _keys_in(path)}
    in_section = {ref.file for ref in scan.refs if ref.in_section}
    assert in_section == with_sections, (sorted(in_section), sorted(with_sections))


def test_m11_37_section_code_runs_only_behind_the_off_switch() -> None:
    """The scan's premise. Only Core.lua reads a section's gather or carry: it
    calls the gather in `gather`, which returns first for a switched-off
    section, tests for one in ns.Write, and tests for and calls the carry at
    ADDON_LOADED only for a section that is not off. No source names either in
    a string. The helpers `_ON_THE_SPOT` trusts do what it says: ns.Call is
    `pcall` on its first argument, ns.Fn reads one field, and the type filters
    return nothing but their scalar."""
    reads = []
    for path in sorted(ADDON.glob("*.lua")):
        tokens = _tokens(path)
        flow = _Flow(path, tokens)
        for k, tok in enumerate(tokens):
            if tok.kind == "string" and tok.text in {"gather", "carry"}:
                reads.append((path.name, flow.label_at(k), f"string {tok.text!r}"))
            elif tok.kind == "name" and tok.text in {"gather", "carry"} and _is_field(tokens, k):
                reads.append((path.name, flow.label_at(k), _render(tokens[k - 2 : k + 1])))
    assert sorted(reads) == sorted(
        [
            ("Core.lua", "gather", "section . gather"),
            ("Core.lua", "ns.Write", "section . gather"),
            ("Core.lua", 'ns.On("ADDON_LOADED")', "section . carry"),
            ("Core.lua", 'ns.On("ADDON_LOADED")', "section . carry"),
        ]
    ), reads
    core = _core()
    assert _body(core, "gather").startswith(
        f"function gather ( section , event ) {OFF_GUARD} "
        "local ok , record = pcall ( section . gather , event )"
    )
    assert (
        "if section . carry and not section . off then "
        "local ok , record = pcall ( section . carry , WowLabCharDB )"
    ) in _body(core, 'ns.On("ADDON_LOADED")')
    assert _body(core, "ns.Call") == (
        "function ns . Call ( fn , ... ) local results = ns . Pack ( pcall ( fn , ... ) ) "
        "if not results [ 1 ] then return nil end return unpack ( results , 2 , results . n ) end"
    )
    assert _body(core, "ns.Fn") == (
        'function ns . Fn ( tbl , key ) if type ( tbl ) == "table" and type ( tbl [ key ] ) == '
        '"function" then return tbl [ key ] end return nil end'
    )
    for name, kind in (("Number", "number"), ("String", "string"), ("Bool", "boolean")):
        assert _body(core, f"ns.{name}") == (
            f'function ns . {name} ( value ) if type ( value ) == "{kind}" then return value end '
            "return nil end"
        )
    assert _body(core, "ns.Numbers") == (
        "function ns . Numbers ( list ) local out = { } "
        'if type ( list ) == "table" then for _ , value in ipairs ( list ) do '
        'if type ( value ) == "number" then out [ # out + 1 ] = value end end end return out end'
    )


def test_m11_37_the_race_check_calls_the_client_only_for_a_carried_record() -> None:
    """The one SECTIONLESS_API entry outside Core.lua. `raceID` (UnitRace) runs
    from the customization gather and from Customization.lua's
    PLAYER_ENTERING_WORLD race check (its callers are pinned in
    SECTIONLESS_CALLERS, each a direct call). The check returns before calling
    it unless ns.state holds a carried customization record. A switched-off
    section never holds one: switchOff clears its entry, and ns.state is
    otherwise written only by `gather` (which refuses an off section), by the
    carry at ADDON_LOADED (skipped for an off section) and by the race check
    itself (an absent record); rule 3 keeps ns.state from being written
    through an alias or a second binding of the file's `...`. So `/wowlab skip
    customization` stops this call too. It runs on every PLAYER_ENTERING_WORLD
    (every loading screen) while a carried record is held, not after the 15 s
    window."""
    scan = _section_scan()
    flow = scan.flows["Customization.lua"]
    check = _body(flow, 'ns.On("PLAYER_ENTERING_WORLD")')
    assert check.startswith(
        "function ( ) local record = ns . state [ KEY ] "
        'if type ( record ) ~= "table" or not record . carried '
        'or type ( record . race_id ) ~= "number" then return end '
        "local race = raceID ( )"
    ), check
    assert check.count("raceID") == 1, check
    assert SECTIONLESS_CALLERS[("Customization.lua", "raceID")] == {
        ("Customization.lua", 'gather("customization")'): 1,
        ("Customization.lua", 'ns.On("PLAYER_ENTERING_WORLD")'): 1,
    }
    stores = sorted(
        (name, f.label_at(store.at), store.text)
        for name, f in scan.flows.items()
        for store in f.stores
        if store.text.startswith("ns . state ")
    )
    assert stores == sorted(
        [
            ("Core.lua", _TOP, "ns . state = { }"),
            ("Core.lua", "gather", "ns . state [ section . key ] = record"),
            ("Core.lua", "switchOff", "ns . state [ section . key ] = nil"),
            ("Core.lua", 'ns.On("ADDON_LOADED")', "ns . state [ section . key ] = record"),
            (
                "Customization.lua",
                'ns.On("PLAYER_ENTERING_WORLD")',
                "ns . state [ KEY ] = ns . Absent "
                '( "dropped: the recorded race no longer matches the character" )',
            ),
        ]
    ), stores


# Constructed inputs (boundary cases for this grader), not addon sources: a
# copy of the real sources with ordinary edits to one file, as
# (file, [(text found once, its replacement), ...]).
_ADDON_LOADED = "    ns.probe = loadProbe(WowLabCharDB)\n"
_REFRESH = "function ns.Refresh()\n"
_CORE_WORLD = "    if timerAfter and not firstPassDone then\n"
_CORE_ON = "    if not handlers[event] then\n"
_RACE_CHECK = "    local race = raceID()\n"
_MODEL_LOOKUP = '        local model = ns.Fn(C_BarberShop, "GetViewingChrModel")\n'
_CARRY_GUARD = (
    '        if type(r) ~= "table" or type(r.absent) ~= "nil" or type(r.choices) ~= "table" then\n'
    "            return nil\n"
    "        end\n"
)
_BARBER_GATHER = "    gather = function(event)\n"
_DISPATCH = 'frame:SetScript("OnEvent", function(_, event, ...)\n'
_DISPATCH_WITH_FRAME = 'frame:SetScript("OnEvent", function(f, event, ...)\n'
_SLASH = "SlashCmdList.WOWLAB = function(message)\n"
_SLASH_WITH_BOX = "SlashCmdList.WOWLAB = function(message, box)\n"
_SCANNER = "local scanner = nil\n"
_NEW_SCANNER = 'local function newScanner()\n    return CreateFrame("GameTooltip")\nend\n'
_HIDE_SCANNER = (
    "function ns.HideScanner()\n    if scanner then\n        scanner:Hide()\n    end\nend\n"
)
_KEY = 'local KEY = "customization"\n'
_PROFESSIONS_GATHER = "    gather = function()\n"
_PROFESSIONS_SLOTS = "        local slots = ns.Pack(GetProfessions())\n"
_PROF = "        local prof = GetProfessions\n"
_GEAR_SLOTS = "        local first, last ="
_GEAR_TOP = "local _, ns = ...\n"


def _in_professions(lines: str) -> tuple[str, list[tuple[str, str]]]:
    return ("Professions.lua", [(_PROFESSIONS_SLOTS, lines + _PROFESSIONS_SLOTS)])


# Each must leave a failure of this rule whose message holds this text.
_M11_37_CASES: dict[str, tuple[tuple[str, list[tuple[str, str]]], tuple[str, str]]] = {
    # The M11-29 review's copy.
    "constructed-model-call-at-addon-loaded": (
        ("Core.lua", [(_ADDON_LOADED, _ADDON_LOADED + _MODEL_LOOKUP[4:] + "    ns.Call(model)\n")]),
        ("outside", 'in ns.On("ADDON_LOADED"): `C_BarberShop`'),
    ),
    # Ruling: a carry runs at ADDON_LOADED, before a skip can be typed.
    "constructed-model-call-in-the-carry": (
        (
            "Customization.lua",
            [(_CARRY_GUARD, _CARRY_GUARD + _MODEL_LOOKUP + "        pcall(model)\n")],
        ),
        ("outside", 'in carry("customization"): `C_BarberShop`'),
    ),
    "constructed-direct-call-at-file-level": (
        ("Gear.lua", [(_GEAR_TOP, _GEAR_TOP + 'local level = UnitLevel("player")\n')]),
        ("outside", "in <top level>: `UnitLevel`"),
    ),
    "constructed-section-helper-also-called-by-a-handler": (
        (
            "Gear.lua",
            [
                (
                    "ns.Section({\n",
                    'ns.On("PLAYER_LEVEL_UP", function()\n    itemLevel(1, "")\nend)\n\n'
                    "ns.Section({\n",
                )
            ],
        ),
        ("outside", "in itemLevel: `C_Item`"),
    ),
    "constructed-ns-helper-called-from-core": (
        ("Core.lua", [(_REFRESH, _REFRESH + "    ns.Spec()\n")]),
        ("outside", "in ns.Spec: `C_SpecializationInfo`"),
    ),
    "constructed-ns-method-call-from-core": (
        ("Core.lua", [(_REFRESH, _REFRESH + "    ns:Spec()\n")]),
        ("outside", "in ns.Spec: `C_SpecializationInfo`"),
    ),
    "constructed-ns-alias-in-a-handler": (
        (
            "Gear.lua",
            [
                (
                    _GEAR_TOP,
                    _GEAR_TOP
                    + 'ns.On("PLAYER_LEVEL_UP", function()\n    local n = ns\n    n.Spec()\nend)\n',
                )
            ],
        ),
        ("ns", "`ns` used bare"),
    ),
    "constructed-infrastructure-call-in-a-new-place": (
        ("Core.lua", [(_REFRESH, _REFRESH + "    GetBuildInfo()\n")]),
        ("outside", "in ns.Refresh: `GetBuildInfo`"),
    ),
    "constructed-new-method-on-the-event-frame": (
        ("Core.lua", [(_ADDON_LOADED, _ADDON_LOADED + "    frame:Hide()\n")]),
        ("outside", 'in ns.On("ADDON_LOADED"): `frame:Hide`'),
    ),
    "constructed-alias-of-the-event-frame": (
        ("Core.lua", [(_CORE_ON, "    local f = frame\n    f:RegisterAllEvents()\n" + _CORE_ON)]),
        ("use", "in ns.On: `frame` is used as self 1, value 1"),
    ),
    "constructed-timer-called-where-only-tested": (
        ("Core.lua", [(_CORE_WORLD, "    timerAfter(0, ns.Refresh)\n" + _CORE_WORLD)]),
        ("use", "`timerAfter` is used as call 1, test 1; SECTIONLESS_API allows test 1"),
    ),
    "constructed-client-block-at-file-load": (
        ("Core.lua", [("SLASH_WOWLAB1 = ", "clientInfo()\nSLASH_WOWLAB1 = ")]),
        ("callers", "clientInfo is called from"),
    ),
    "constructed-second-function-with-an-allowed-label": (
        (
            "Customization.lua",
            [
                (
                    _KEY,
                    _KEY + 'ns.raceID = function()\n    return select(3, UnitRace("player"))\nend\n'
                    'ns.On("PLAYER_LEVEL_UP", function()\n    ns.raceID()\nend)\n',
                )
            ],
        ),
        ("label", "raceID names 2 functions"),
    ),
    "constructed-function-field-beside-gather": (
        (
            "Professions.lua",
            [
                (
                    '    key = "professions",\n',
                    '    key = "professions",\n'
                    "    check = function()\n        return GetProfessions()\n    end,\n",
                )
            ],
        ),
        ("outside", 'in check("professions"): `GetProfessions`'),
    ),
    "constructed-timer-in-the-race-check": (
        ("Customization.lua", [(_RACE_CHECK, "    C_Timer.After(1, raceID)\n" + _RACE_CHECK)]),
        ("outside", 'in ns.On("PLAYER_ENTERING_WORLD"): `C_Timer.After`'),
    ),
    "constructed-client-function-kept-in-a-file-local": (
        (
            "Customization.lua",
            [
                (_KEY, _KEY + "local later = nil\n"),
                (_MODEL_LOOKUP, _MODEL_LOOKUP + "        later = model\n"),
                (_RACE_CHECK, "    if later then\n        later()\n    end\n" + _RACE_CHECK),
            ],
        ),
        ("outside", 'in ns.On("PLAYER_ENTERING_WORLD"): `later`'),
    ),
    "constructed-state-through-an-alias": (
        (
            "Customization.lua",
            [
                (
                    _KEY,
                    _KEY + 'ns.On("PLAYER_LEVEL_UP", function()\n    local s = ns.state\n'
                    "    s.customization = { carried = true, race_id = 0 }\nend)\n",
                )
            ],
        ),
        ("state", "`ns.state` used other than indexed or assigned"),
    ),
    # Rule 2: section code hands no function on.
    "constructed-handler-registered-from-a-gather": (
        (
            "Professions.lua",
            [
                (
                    _PROFESSIONS_GATHER,
                    _PROFESSIONS_GATHER + '        ns.On("SKILL_LINES_CHANGED", function()\n'
                    "            GetProfessions()\n        end)\n",
                )
            ],
        ),
        ("handed-on", "handed to `ns . On`"),
    ),
    "constructed-captured-local-in-a-handler": (
        _in_professions(
            _PROF
            + '        ns.On("PLAYER_LEVEL_UP", function()\n            prof()\n        end)\n'
        ),
        ("handed-on", "handed to `ns . On`"),
    ),
    "constructed-captured-local-in-a-timer": (
        _in_professions(
            _PROF + '        local after = ns.Fn(C_Timer, "After")\n        if after then\n'
            "            after(1, function()\n                prof()\n            end)\n"
            "        end\n"
        ),
        ("handed-on", "handed to `after`"),
    ),
    "constructed-captured-local-in-a-frame-script": (
        _in_professions(
            _PROF + '        local f = CreateFrame("Frame")\n'
            '        f:SetScript("OnEvent", function()\n            prof()\n        end)\n'
        ),
        ("handed-on", "handed to `f : SetScript`"),
    ),
    "constructed-captured-local-in-a-hook": (
        _in_professions(
            _PROF + '        hooksecurefunc("ToggleCharacter", function()\n'
            "            prof()\n        end)\n"
        ),
        ("handed-on", "handed to `hooksecurefunc`"),
    ),
    "constructed-client-global-registered-as-a-handler": (
        _in_professions('        ns.On("PLAYER_TARGET_CHANGED", GetProfessions)\n'),
        ("handed-on", "`GetProfessions` may be a function; handed to `ns . On`"),
    ),
    "constructed-client-function-local-registered-as-a-handler": (
        _in_professions(_MODEL_LOOKUP.replace("model", "later") + '        ns.On("X", later)\n'),
        ("handed-on", "`later` may be a function; handed to `ns . On`"),
    ),
    "constructed-client-function-local-handed-to-the-timer": (
        _in_professions(
            '        local after = ns.Fn(C_Timer, "After")\n'
            + _MODEL_LOOKUP.replace("model", "later")
            + "        after(20, later)\n"
        ),
        ("handed-on", "`later` may be a function; handed to `after`"),
    ),
    "constructed-section-helper-handed-to-a-timer": (
        ("Gear.lua", [(_GEAR_SLOTS, "        C_Timer.After(1, itemLevel)\n" + _GEAR_SLOTS)]),
        ("handed-on", "`itemLevel` may be a function; handed to `C_Timer . After`"),
    ),
    "constructed-second-call-in-an-allowed-place": (
        (
            "Core.lua",
            [("SLASH_WOWLAB1 = ", 'frame:SetScript("OnUpdate", nil)\nSLASH_WOWLAB1 = ')],
        ),
        ("use", "`frame:SetScript` is used as call 2"),
    ),
    "constructed-function-statement-in-a-gather": (
        _in_professions(_PROF + "        function ns.later()\n            prof()\n        end\n"),
        ("handed-on", "`function ns.later` stores a function"),
    ),
    "constructed-client-function-stored-in-ns": (
        _in_professions("        ns.later = GetProfessions\n"),
        ("handed-on", "`ns . later = GetProfessions` stores what may be a function"),
    ),
    "constructed-client-function-returned-by-a-helper": (
        (
            "Gear.lua",
            [
                (
                    "ns.Section({\n",
                    "local function getter()\n"
                    '    return ns.Fn(C_Item, "GetCurrentItemLevel")\nend\n\n'
                    "ns.Section({\n",
                ),
                (_GEAR_SLOTS, '        ns.On("X", getter())\n' + _GEAR_SLOTS),
            ],
        ),
        ("handed-on", "returns what may be a function"),
    ),
    "constructed-race-check-registered-from-the-gather": (
        (
            "Customization.lua",
            [(_BARBER_GATHER, _BARBER_GATHER + '        ns.On("UNIT_AURA", raceID)\n')],
        ),
        ("callers", "raceID is referred to other than by a direct call"),
    ),
    "constructed-race-check-handed-to-a-timer": (
        (
            "Customization.lua",
            [
                (
                    _BARBER_GATHER,
                    _BARBER_GATHER + '        local after = ns.Fn(C_Timer, "After")\n'
                    "        if after then\n            after(1, raceID)\n        end\n",
                )
            ],
        ),
        ("callers", "raceID is referred to other than by a direct call"),
    ),
    # Round 2 (M11-37 reviews).
    "constructed-client-call-in-a-handed-on-literal": (
        _in_professions(
            '        ns.On("SKILL_LINES_CHANGED", function()\n'
            "            GetProfessions()\n        end)\n"
        ),
        ("outside", "is in a function a gather hands on instead of calling it there"),
    ),
    "constructed-second-binding-of-the-vararg": (
        (
            "Customization.lua",
            [
                (
                    _KEY,
                    _KEY + 'local _, n = ...\nns.On("PLAYER_LEVEL_UP", function()\n'
                    "    n.Spec()\nend)\n",
                )
            ],
        ),
        ("vararg", "the file's `...` is used other than as `local <name>, ns = ...`"),
    ),
    "constructed-select-of-the-vararg": (
        (
            "Customization.lua",
            [
                (
                    _KEY,
                    _KEY + 'local n = select(2, ...)\nns.On("PLAYER_LEVEL_UP", function()\n'
                    "    n.Spec()\nend)\n",
                )
            ],
        ),
        ("vararg", "the file's `...` is used other than as `local <name>, ns = ...`"),
    ),
    "constructed-state-through-a-second-binding": (
        (
            "Customization.lua",
            [
                (
                    _KEY,
                    _KEY + 'local _, n = ...\nns.On("PLAYER_LEVEL_UP", function()\n'
                    "    n.state.customization = { carried = true, race_id = 0 }\nend)\n",
                )
            ],
        ),
        ("vararg", "the file's `...` is used other than as `local <name>, ns = ...`"),
    ),
    "constructed-one-more-handler-at-file-level": (
        ("Customization.lua", [(_KEY, _KEY + 'ns.On("PLAYER_LEVEL_UP", function() end)\n')]),
        ("callers", "ns.On is called from"),
    ),
    "constructed-function-given-to-a-file-local-through-a-helper": (
        (
            "Customization.lua",
            [
                (
                    _KEY,
                    _KEY + "local lastModel = nil\nlocal function remember(fn)\n"
                    "    lastModel = fn\nend\n"
                    'ns.On("PLAYER_TARGET_CHANGED", function()\n    if lastModel then\n'
                    "        pcall(lastModel)\n    end\nend)\n",
                ),
                (
                    _BARBER_GATHER,
                    _BARBER_GATHER
                    + '        remember(ns.Fn(C_BarberShop, "GetViewingChrModel"))\n',
                ),
            ],
        ),
        ("handed-on", "`lastModel = fn` gives a local declared outside section code"),
    ),
    "constructed-function-local-given-to-a-file-local-through-a-helper": (
        (
            "Customization.lua",
            [
                (
                    _KEY,
                    _KEY + "local lastModel = nil\nlocal function remember(fn)\n"
                    "    lastModel = fn\nend\n",
                ),
                (_MODEL_LOOKUP, _MODEL_LOOKUP + "        remember(model)\n"),
            ],
        ),
        ("handed-on", "`lastModel = fn` gives a local declared outside section code"),
    ),
    "constructed-function-read-with-next": (
        _in_professions(
            "        local _, fn = next(C_BarberShop)\n"
            '        local after = ns.Fn(C_Timer, "After")\n        after(20, fn)\n'
        ),
        ("handed-on", "`fn` may be a function; handed to `after`"),
    ),
    "constructed-table-read-with-pairs": (
        _in_professions(
            "        local _, bs = pairs(C_BarberShop)\n"
            '        local after = ns.Fn(C_Timer, "After")\n'
            "        after(20, bs.GetViewingChrModel)\n"
        ),
        ("handed-on", "`bs . GetViewingChrModel` may be a function; handed to `after`"),
    ),
    "constructed-loop-over-a-client-namespace": (
        _in_professions(
            '        for _, f in pairs(C_Timer) do\n            ns.On("X", f)\n        end\n'
        ),
        ("handed-on", "`f` may be a function; handed to `ns . On`"),
    ),
    "constructed-test-turned-into-a-call": (
        (
            "Core.lua",
            [
                (
                    "    if not timerAfter then\n        if inCombat then\n"
                    "            waitingForCombat = true\n        else\n"
                    "            gatherDirty()\n        end\n        return\n    end\n",
                    "    timerAfter(0, gatherDirty)\n",
                )
            ],
        ),
        ("use", "`timerAfter` is used as call 2; SECTIONLESS_API allows call 1, test 1"),
    ),
    "constructed-second-call-instead-of-a-test": (
        (
            "Customization.lua",
            [
                (
                    '    if type(UnitRace) ~= "function" then\n        return nil\n    end\n',
                    '    local _ = UnitRace("player")\n',
                )
            ],
        ),
        ("use", "`UnitRace` is used as call 2; SECTIONLESS_API allows call 1, test 1"),
    ),
    # Round 3: `self`, and values the client passes in.
    "constructed-self-in-an-ns-method": (
        ("Gear.lua", [(_GEAR_TOP, _GEAR_TOP + "function ns:Later()\n    self.Spec()\nend\n")]),
        ("method", "`function ns:Later` is a method definition"),
    ),
    "constructed-ns-method-call-hands-ns-on": (
        ("Core.lua", [(_REFRESH, _REFRESH + "    ns:Write()\n")]),
        ("ns", "`ns:` hands the namespace on as `self`"),
    ),
    "constructed-method-on-a-local-table": (
        (
            "Gear.lua",
            [(_GEAR_TOP, _GEAR_TOP + "local M = {}\nfunction M:Go()\n    return self\nend\n")],
        ),
        ("method", "`function M:Go` is a method definition"),
    ),
    "constructed-method-with-self-called-by-a-gather": (
        (
            "Gear.lua",
            [
                (
                    "ns.Section({\n",
                    'function ns:Level()\n    return self.Number(UnitLevel("player"))\nend\n\n'
                    "ns.Section({\n",
                ),
                (_GEAR_SLOTS, "        ns:Level()\n" + _GEAR_SLOTS),
            ],
        ),
        ("method", "`function ns:Level` is a method definition"),
    ),
    "constructed-event-frame-parameter": (
        ("Core.lua", [(_DISPATCH, _DISPATCH_WITH_FRAME + "    f:UnregisterAllEvents()\n")]),
        ("outside", 'in frame:SetScript("OnEvent"): `f:UnregisterAllEvents`'),
    ),
    "constructed-slash-edit-box-parameter": (
        (
            "Core.lua",
            [(_SLASH, _SLASH_WITH_BOX + '    if box then\n        box:SetText("")\n    end\n')],
        ),
        ("outside", "in SlashCmdList.WOWLAB: `box:SetText`"),
    ),
    "constructed-event-frame-through-a-local": (
        ("Core.lua", [(_DISPATCH, _DISPATCH_WITH_FRAME + "    local g = f\n    g:Hide()\n")]),
        ("outside", 'in frame:SetScript("OnEvent"): `g:Hide`'),
    ),
    "constructed-event-frame-handed-to-a-helper": (
        (
            "Core.lua",
            [
                (
                    _DISPATCH,
                    "local function hide(x)\n    x:Hide()\nend\n\n"
                    + _DISPATCH_WITH_FRAME
                    + "    hide(f)\n",
                ),
            ],
        ),
        ("outside", "in hide: `x:Hide`"),
    ),
    "constructed-event-frame-returned-by-a-helper": (
        (
            "Core.lua",
            [
                (
                    _DISPATCH,
                    "local function same(x)\n    return x\nend\n\n"
                    + _DISPATCH_WITH_FRAME
                    + "    local h = same(f)\n    h:Hide()\n",
                ),
            ],
        ),
        ("outside", 'in frame:SetScript("OnEvent"): `h:Hide`'),
    ),
    "constructed-event-frame-stored-in-a-field": (
        ("Core.lua", [(_DISPATCH, _DISPATCH_WITH_FRAME + "    ns.frame = f\n")]),
        ("outside", "`ns . frame = f` stores a client-given value"),
    ),
    # Round 4: a client object reaching a file-level local through a helper.
    "constructed-helper-returns-the-object-into-a-file-local": (
        (
            "Gear.lua",
            [
                (_GEAR_TOP, _GEAR_TOP + _SCANNER + _NEW_SCANNER + _HIDE_SCANNER),
                (_GEAR_SLOTS, "        scanner = scanner or newScanner()\n" + _GEAR_SLOTS),
            ],
        ),
        ("outside", "in ns.HideScanner: `scanner:Hide`"),
    ),
    "constructed-helper-stores-its-parameter-in-a-file-local": (
        (
            "Gear.lua",
            [
                (
                    _GEAR_TOP,
                    _GEAR_TOP
                    + _SCANNER
                    + "local function keep(made)\n    scanner = made\nend\n"
                    + _HIDE_SCANNER,
                ),
                (_GEAR_SLOTS, '        keep(CreateFrame("GameTooltip"))\n' + _GEAR_SLOTS),
            ],
        ),
        ("outside", "in ns.HideScanner: `scanner:Hide`"),
    ),
    "constructed-helper-returns-it-second-into-a-file-local": (
        (
            "Gear.lua",
            [
                (
                    _GEAR_TOP,
                    _GEAR_TOP
                    + _SCANNER
                    + 'local function make()\n    return true, CreateFrame("GameTooltip")\nend\n'
                    + _HIDE_SCANNER,
                ),
                (
                    _GEAR_SLOTS,
                    "        local ok, made = make()\n        scanner = made\n" + _GEAR_SLOTS,
                ),
            ],
        ),
        ("outside", "in ns.HideScanner: `scanner:Hide`"),
    ),
    # Assigned outside section code: only the return of raceID (UnitRace)
    # makes `lastRace` a handle.
    "constructed-pinned-helper-return-kept-in-a-file-local": (
        (
            "Customization.lua",
            [
                (_KEY, _KEY + "local lastRace = nil\n"),
                (_RACE_CHECK, "    lastRace = raceID()\n    local race = lastRace\n"),
            ],
        ),
        ("outside", 'in ns.On("PLAYER_ENTERING_WORLD"): `lastRace`'),
    ),
    "constructed-hook-parameter": (
        (
            "Gear.lua",
            [
                (
                    _GEAR_TOP,
                    _GEAR_TOP
                    + 'hooksecurefunc("ChatEdit_OnShow", function(box)\n    box:Hide()\nend)\n',
                )
            ],
        ),
        ("outside", 'in hooksecurefunc("ChatEdit_OnShow"): `box:Hide`'),
    ),
    "constructed-event-payload-in-an-ns-on-handler": (
        (
            "Gear.lua",
            [
                (
                    _GEAR_TOP,
                    _GEAR_TOP
                    + 'ns.On("PLAYER_LEVEL_UP", function(_, level)\n    local x = level.x\nend)\n',
                )
            ],
        ),
        ("outside", 'in ns.On("PLAYER_LEVEL_UP"): `level.x`'),
    ),
}
# Controls: calls added inside section code, called on the spot, leave nothing.
_M11_37_CONTROLS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "control-call-in-a-gather": _in_professions('        local level = UnitLevel("player")\n'),
    "control-helper-called-only-by-a-gather": (
        "Gear.lua",
        [
            (
                "ns.Section({\n",
                "local function average()\n    return GetAverageItemLevel()\nend\n\nns.Section({\n",
            ),
            (_GEAR_SLOTS, "        average()\n" + _GEAR_SLOTS),
        ],
    ),
    "control-literal-under-pcall-in-a-gather": _in_professions(
        "        pcall(function()\n            return GetProfessions()\n        end)\n"
    ),
    "control-client-function-local-called-on-the-spot": _in_professions(
        _PROF + "        if prof then\n            ns.Call(prof)\n            prof()\n        end\n"
    ),
    # Round 2: shapes that looked like failures before and are not.
    "control-second-section-in-the-race-check-file": (
        "Customization.lua",
        [
            (
                "-- A carried record for another race",
                'ns.Section({\n    key = "customization.extra",\n    path = { "extra" },\n'
                "    on_world = true,\n    gather = function()\n"
                '        return { level = ns.Number(UnitLevel("player")) }\n    end,\n})\n\n'
                "-- A carried record for another race",
            )
        ],
    ),
    "control-comparator-literal-in-a-gather": (
        "Professions.lua",
        [
            (
                "        return { list = list }\n",
                "        table.sort(list, function(a, b)\n"
                "            return GetProfessionInfo(a.position) == GetProfessionInfo(b.position)\n"
                "        end)\n        return { list = list }\n",
            )
        ],
    ),
    "control-xpcall-bodies-in-a-gather": _in_professions(
        "        xpcall(function()\n            return GetProfessions()\n        end, function()\n"
        '            return UnitLevel("player")\n        end)\n'
    ),
    # Round 3: a gather's own parameter (it is given) used inside the gather,
    # and the event frame's handle used where SECTIONLESS_API allows it.
    "control-given-value-used-inside-a-gather": (
        "Customization.lua",
        [(_BARBER_GATHER, _BARBER_GATHER + "        local upper = event and event:upper()\n")],
    ),
}


def _m11_37_edited_scan(name: str, edits: list[tuple[str, str]]) -> _SectionScan:
    with tempfile.TemporaryDirectory(prefix="wowlab-m11-37-") as tmp:
        for path in sorted(ADDON.glob("*.lua")):
            text = path.read_text(encoding="utf-8")
            if path.name == name:
                for found, replacement in edits:
                    assert text.count(found) == 1, f"{name}: {found!r} is not there exactly once"
                    text = text.replace(found, replacement)
            (Path(tmp) / path.name).write_text(text, encoding="utf-8")
        return _section_scan(sorted(Path(tmp).glob("*.lua")))


def test_m11_37_a_call_added_outside_a_section_fails() -> None:
    """Constructed inputs (boundary tests of the scan; no fixture argument, so
    the review probes can call it bare): each edit calls the client where a
    skip typed in the session would not stop it, and the scan must say so
    under the expected rule."""
    missed = []
    for tag, ((name, edits), (rule, text)) in sorted(_M11_37_CASES.items()):
        scan = _m11_37_edited_scan(name, edits)
        if not any(text in message for message in _failures(scan, rule)):
            missed.append(f"{tag}: expected {rule} {text!r}, got {scan.failures}")
    assert not missed, missed


def test_m11_37_a_call_added_inside_a_section_passes() -> None:
    """Constructed inputs (controls for the test above): a call added to a
    gather, or to a helper or function only a gather calls on the spot, leaves
    no failure."""
    wrong = []
    for tag, (name, edits) in sorted(_M11_37_CONTROLS.items()):
        scan = _m11_37_edited_scan(name, edits)
        if scan.failures:
            wrong.append(f"{tag}: {scan.failures}")
    assert not wrong, wrong


# M13-05: schema 3's spellbook, actionbars and bindings -------------------------
#
# Source scans of Actions.lua, constructed checks; nothing here runs Lua (L3).
# The M11-37 scan above already proves that every client call in Actions.lua
# sits in section code that `/wowlab skip` stops (no SECTIONLESS_API entry
# names the file). These add the second rule of §15.2: nothing in the three
# sections is read in combat. Each gather asks `combatReason()` as its first
# statement and returns `ns.Blocked(reason)` at once when it gives a reason
# (Core.lua then keeps the earlier record and leaves the section dirty);
# every other client reference in the file, and every call of a helper that
# may reach one, comes after that check. PLAYER_REGEN_ENABLED is each
# section's retry event, never a change event, so the end of a fight
# re-gathers only a section a blocked gather left dirty.

ACTIONS = ADDON / "Actions.lua"
# M20-13 (schema 5): and `macros`, under the same combat and secret rules.
M13_05_KEYS = ("spellbook", "actionbars", "bindings", "macros")
COMBAT_CHECK = (
    "local blocked = combatReason ( ) if blocked then return ns . Blocked ( blocked ) end"
)
COMBAT_EVENT = "PLAYER_REGEN_ENABLED"
COMBAT_CONSTANT = (
    'local IN_COMBAT = "in combat: this section reads only out of combat, '
    'and gathers again when combat ends"'
)
COMBAT_REASON = (
    "local function combatReason ( ) "
    'if type ( InCombatLockdown ) ~= "function" then '
    'return "InCombatLockdown missing, so whether the player is in combat is not known; '
    'nothing was read" end '
    "local ok , locked = pcall ( InCombatLockdown ) "
    'if not ok then return "InCombatLockdown raised an error, so whether the player is in '
    'combat is not known; nothing was read" end '
    "if locked then return IN_COMBAT end "
    "return nil end"
)


def _m13_05_sections(flow: _Flow) -> list[tuple[str, dict[str, tuple[int, int]]]]:
    """(key, entries) of each `ns.Section({ ... })` spec in one file."""
    t = flow.tokens
    out = []
    for k in range(len(t) - 4):
        if [x.text for x in t[k : k + 5]] != ["ns", ".", "Section", "(", "{"]:
            continue
        entries = {key: (s, e) for key, s, e in flow.entries(k + 4)}
        key_s = entries.get("{key}", (k, k))[0]
        out.append((t[key_s].text if t[key_s].kind == "string" else "?", entries))
    return out


def _m13_05_combat_problems(path: Path) -> list[str]:
    """Why `path` (Actions.lua, or an edited copy) may read in combat."""
    t = _tokens(path)
    flow = _Flow(path, t)
    problems: list[str] = []
    n_check = len(COMBAT_CHECK.split(" "))
    regions: list[tuple[int, int]] = []  # (after the check, end of the gather)
    gathers: set[int] = set()
    checks: list[int] = []
    keys: list[str] = []
    for key, entries in _m13_05_sections(flow):
        keys.append(key)
        events_s, events_e = entries.get("{events}", (0, 0))
        if COMBAT_EVENT in {x.text for x in t[events_s:events_e] if x.kind == "string"}:
            problems.append(f"{key}: {COMBAT_EVENT} is a change event (it would re-read)")
        retry_s, retry_e = entries.get("{retry}", (0, 0))
        if [x.text for x in t[retry_s:retry_e] if x.kind == "string"] != [COMBAT_EVENT]:
            problems.append(f"{key}: its retry events are not exactly {COMBAT_EVENT}")
        if "{gather}" not in entries or t[entries["{gather}"][0]].text != "function":
            problems.append(f"{key}: its gather is not a function literal")
            continue
        head = entries["{gather}"][0]
        gathers.add(head)
        body = flow.pair[head + 1] + 1
        if _render(t[body : body + n_check]) != COMBAT_CHECK:
            problems.append(f"{key}: the gather does not start with the combat check")
            continue
        checks.append(body + 3)  # the `combatReason` token
        regions.append((body + n_check, flow.block_end(head)))
    if tuple(keys) != M13_05_KEYS:
        problems.append(f"sections {keys}, expected {list(M13_05_KEYS)}")

    def in_region(k: int) -> bool:
        return any(s <= k < e for s, e in regions)

    # Every function that is not a gather, and where each is referred to.
    defined: dict[str, int] = {}
    for head, label in flow.labels.items():
        if head in gathers:
            continue
        if not _NAME.fullmatch(label) or t[head - 1].text != "local":
            problems.append(f"{label}: Actions.lua defines a function other than a local one")
        defined[label] = head
    refs: dict[str, list[int]] = {name: [] for name in defined}
    for k, x in enumerate(t):
        if x.kind == "name" and x.text in refs and not _is_field(t, k):
            if t[k - 1].text == "function":
                continue  # the definition itself
            refs[x.text].append(k)
    if "combatReason" not in defined:
        return [*problems, "no combatReason function"]
    if sorted(refs["combatReason"]) != sorted(checks):
        problems.append(
            "combatReason is referred to other than as the first statement of each gather: "
            f"lines {[t[k].line for k in refs['combatReason']]}"
        )
    # A helper runs after the check when every reference to it does.
    after: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, at in refs.items():
            if name in after or name == "combatReason":
                continue
            if all(in_region(k) or flow.label_at(k) in after for k in at):
                after.add(name)
                changed = True
    std = _std_top_level()
    for k, x in enumerate(t):
        if x.kind != "name" or x.text not in std or _is_field(t, k):
            continue
        label = flow.label_at(k)
        if label == "combatReason":
            if x.text != "InCombatLockdown":
                problems.append(f"line {x.line}: combatReason reads {x.text}")
            continue
        if in_region(k) or label in after:
            continue
        problems.append(f"line {x.line}: {x.text} is reached before or without the combat check")
    for name in sorted(set(refs) - after - {"combatReason"}):
        lines = [t[k].line for k in refs[name] if not in_region(k)]
        problems.append(f"{name}: called before or without the combat check (lines {lines})")
    # combatReason and the reason it gives in combat are pinned whole: any
    # statement added to either (`locked = false`, an early `return nil`,
    # IN_COMBAT set to nil) may make every gather read in combat.
    head = defined["combatReason"]
    if _render(t[head - 1 : flow.block_end(head) + 1]) != COMBAT_REASON:
        problems.append("combatReason is not the pinned one")
    if _render(t).count(COMBAT_CONSTANT) != 1:
        problems.append("IN_COMBAT is not the pinned constant")
    for name in ("combatReason", "IN_COMBAT"):
        if [n for n, _ in flow.assigned].count(name) != 1:
            problems.append(f"{name} is bound more than once")
    return problems


def test_m13_05_the_new_sections_read_nothing_in_combat() -> None:
    """Every client call of spellbook, actionbars and bindings comes after
    the gather's combat check, which reads nothing in combat or when it
    cannot tell, and each section gathers again when combat ends."""
    assert _m13_05_combat_problems(ACTIONS) == []


def test_m13_05_the_new_sections_are_switchable_and_need_no_exception() -> None:
    """The M11-37 rules hold for Actions.lua with no listed exception: every
    call sits in section code that `/wowlab skip` stops."""
    scan = _section_scan()
    assert not [f for f in scan.failures if "Actions.lua" in f], scan.failures
    for table in (SECTIONLESS_API, SECTIONLESS_CALLERS, HANDED_ON):
        assert not [key for key in table if key[0] == "Actions.lua"], table


_M13_05_GATHER = "        local blocked = combatReason()\n"
_M13_05_RETURN = "        if blocked then\n            return ns.Blocked(blocked)\n        end\n"

# Constructed edits of Actions.lua (boundary tests of the combat scan): each
# reads the client before, or without, the combat check. The first match of
# an edit is the spellbook gather's.
_M13_05_CASES: dict[str, list[tuple[str, str]]] = {
    "a call before the check": [
        (
            _M13_05_GATHER,
            '        local early = ns.Fn(C_SpellBook, "GetNumSpellBookSkillLines")\n'
            + _M13_05_GATHER,
        )
    ],
    "the check removed": [(_M13_05_GATHER + _M13_05_RETURN, "")],
    "a helper called before the check": [
        (
            "local IN_COMBAT",
            "local function early()\n    return GetActionInfo(1)\nend\n\nlocal IN_COMBAT",
        ),
        (_M13_05_GATHER, "        early()\n" + _M13_05_GATHER),
    ],
    "a file-level read": [("local IN_COMBAT", "local early = GetNumBindings()\nlocal IN_COMBAT")],
    "combat end not registered": [('    retry = { "PLAYER_REGEN_ENABLED" },\n', "")],
    "combat end as a change event": [
        ('{ "SPELLS_CHANGED" }', '{ "SPELLS_CHANGED", "PLAYER_REGEN_ENABLED" }')
    ],
    "the reason written over the record": [
        ("return ns.Blocked(blocked)", "return ns.Absent(blocked)")
    ],
    "combatReason reads another API": [
        ("    if locked then\n", '    if locked or UnitLevel("player") then\n')
    ],
    # Round 3: combatReason and IN_COMBAT are pinned whole.
    "the lock forced off": [("    if locked then\n", "    locked = false\n    if locked then\n")],
    "an early return": [
        (
            "local function combatReason()\n",
            "local function combatReason()\n    if InCombatLockdown then\n        return nil\n    end\n",
        )
    ],
    "the in-combat reason cleared": [
        ("local FIRST_ACTION_SLOT", "IN_COMBAT = nil\nlocal FIRST_ACTION_SLOT")
    ],
}


def _m13_05_edited(edits: list[tuple[str, str]]) -> list[str]:
    text = ACTIONS.read_text(encoding="utf-8")
    for found, replacement in edits:
        assert found in text, found
        text = text.replace(found, replacement, 1)
    with tempfile.TemporaryDirectory(prefix="wowlab-m13-05-") as tmp:
        path = Path(tmp) / ACTIONS.name
        path.write_text(text, encoding="utf-8")
        return _m13_05_combat_problems(path)


def test_m13_05_constructed_combat_edits_fail() -> None:
    # No fixture argument: the review probes call every test here bare.
    missed = [tag for tag, edits in sorted(_M13_05_CASES.items()) if not _m13_05_edited(edits)]
    assert not missed, missed


def test_m13_05_constructed_call_after_the_check_passes() -> None:
    """Control: the same call added after the check is accepted."""
    late = '        local late = ns.Fn(C_SpellBook, "GetNumSpellBookSkillLines")\n'
    edits = [(_M13_05_GATHER + _M13_05_RETURN, _M13_05_GATHER + _M13_05_RETURN + late)]
    assert _m13_05_edited(edits) == []


def _m13_05_underscore_reads(path: Path) -> list[str]:
    """Where `_` is read in `path`. In Lua `_` is an ordinary local, so a
    discarded value bound to it (the macro's name) is only discarded while
    nothing reads it: `_` may be declared, never used."""
    t = _tokens(path)
    flow = _Flow(path, t)
    declared = {at for name, at in flow.assigned if name == "_"}
    return [
        f"line {x.line}: _ is read"
        for k, x in enumerate(t)
        if x.kind == "name" and x.text == "_" and not _is_field(t, k) and k not in declared
    ]


# M20-13 (ADR-0026, note of 2026-11-20): the M13-05 macro pin (GetMacroInfo
# bound only to probe, the name to `_`, the body to nothing) and the
# plain-command rule of `bindings` are gone. `macros` keeps every macro's
# name, icon and body, and `bindings` every command the client returns, each
# value cleaned first (the secret rule below).


def test_m20_13_bindings_ask_and_store_every_command_as_given() -> None:
    """Every command GetBinding gives is handed to GetBindingKey and stored as
    it is (cleaned first); every key a command has is kept as it is. No count
    of left-out commands or keys is written any more."""
    text = _render(_tokens(ACTIONS))
    assert "local okBinding , found = pcall ( getBinding , index )" in text
    assert "local keys = ns . Pack ( pcall ( getKey , found ) )" in text
    assert text.count("getKey ,") == 1
    assert "{ command = ns . String ( found ) , keys = kept }" in text
    assert (
        'if type ( given ) == "string" then kept [ # kept + 1 ] = ns . String ( given ) end' in text
    )
    for gone in ("commands_left_out", "keys_left_out", "command ( found )", "keyName"):
        assert gone not in text, gone


def test_m20_13_macros_walk_only_the_client_s_own_indices() -> None:
    """`macros` asks GetMacroInfo only for an index GetNumMacros counts:
    1 to the account count, and MAX_ACCOUNT_MACROS + 1 on for this
    character's, each run bounded by the addon's own MAX_MACROS_PER_SCOPE (an
    out-of-range index into a C API is the crash class in
    docs/DATA_SOURCES.md, 2026-09-28). The name, icon and body are cleaned
    before they are stored."""
    text = _render(_tokens(ACTIONS))
    assert "local MAX_MACROS_PER_SCOPE = 256" in text
    assert (
        '{ scope = "account" , first = 1 , last = math . min ( accountCount , MAX_MACROS_PER_SCOPE ) }'
    ) in text
    assert (
        'scope = "character" , first = perAccount + 1 , '
        "last = perAccount + math . min ( characterCount , MAX_MACROS_PER_SCOPE ) ,"
    ) in text
    assert "local perAccount = ns . Number ( MAX_ACCOUNT_MACROS )" in text
    # M20-16: the constant is nil until the macro window loads; the addon's
    # own 120 is used then, and the record says which.
    assert "local ACCOUNT_MACRO_SLOTS = 120" in text
    assert (
        'local slotsFrom = "MAX_ACCOUNT_MACROS" if not perAccount then '
        'perAccount = ACCOUNT_MACRO_SLOTS slotsFrom = "fallback" end'
    ) in text
    assert "account_slots_from = slotsFrom ," in text
    assert "is not a number" not in text
    assert "local okMacro , name , icon , body = pcall ( getInfo , index )" in text
    assert text.count("pcall ( getInfo ,") == 1
    for value in ("name", "icon", "body"):
        assert f"{value} = clean ( isSecret , secret , info , {value} )" in text


# Round 2 of the domain review: a blocked gather keeps the good record ---------


def test_m13_05_a_blocked_gather_keeps_the_record_and_stays_dirty() -> None:
    """Core.lua's `gather`: a section that returns `ns.Blocked(reason)` keeps
    what it gathered earlier this session (no store at all), and writes the
    reason as an absent record only when nothing was gathered; either way it
    stays dirty, so the next pass tries again. A section with a retry event
    keeps its earlier record through a gather that raised in the same way
    (round 3: without issecretvalue a secret value raises); with nothing
    gathered the error is written. Any other result marks the section
    gathered and clean. `ns.Blocked` builds the sentinel from a checked
    string and nothing else."""
    flow = _core()
    assert _body(flow, "ns.Blocked") == (
        "function ns . Blocked ( reason ) return { blocked = ns . String ( reason ) } end"
    )
    assert _body(flow, "gather") == (
        f"function gather ( section , event ) {OFF_GUARD} "
        "local ok , record = pcall ( section . gather , event ) "
        'local blocked = ok and type ( record ) == "table" and type ( record . blocked ) == "string" '
        "local kept = blocked or ( not ok and section . retry ~= nil ) "
        "if kept and section . gathered then section . dirty = true return end "
        "if blocked then record = ns . Absent ( record . blocked ) "
        'elseif not ok then record = ns . Absent ( "error: " .. tostring ( record ) ) '
        'elseif type ( record ) ~= "table" then '
        'record = ns . Absent ( "the section gathered nothing" ) end '
        "ns . state [ section . key ] = record "
        "if blocked then section . dirty = true "
        "else section . gathered = true section . dirty = false "
        # M20-13 (schema 5): the time of the gather.
        "gatheredAt [ section . key ] = ns . Number ( time ( ) ) "
        # M20-16: a record without its section's `again_without` field
        # leaves the section dirty, so a later pass gathers it again.
        "if section . again_without and record [ section . again_without ] == nil then "
        "section . dirty = true end end end"
    )
    # `gathered` is set in `gather` only, so a carried record does not count.
    sets = [
        (path.name, _Flow(path, tokens).label_at(i))
        for path in SOURCES
        for tokens in [_tokens(path)]
        for i in range(len(tokens) - 3)
        if [t.text for t in tokens[i : i + 3]] == ["section", ".", "gathered"]
        and tokens[i + 3].text == "="
    ]
    assert sets == [("Core.lua", "gather")], sets
    # Only Actions.lua's three gathers return the sentinel.
    users = [
        (path.name, i)
        for path in SOURCES
        for tokens in [_tokens(path)]
        for i in range(len(tokens) - 2)
        if _render(tokens[i : i + 3]) == "ns . Blocked" and tokens[i - 1].text == "return"
    ]
    assert [name for name, _ in users] == ["Actions.lua"] * len(M13_05_KEYS), users


def test_m13_05_the_retry_event_never_marks_a_section_dirty() -> None:
    """A retry event (PLAYER_REGEN_ENABLED) schedules a pass only for a
    section that is still dirty, and returns before the change-event path;
    it is registered, listed as refused and switched off like a change
    event."""
    flow = _core()
    listen = _body(flow, "listen")
    assert (
        "local retries = { } for _ , event in ipairs ( section . retry or { } ) do "
        "retries [ event ] = true end"
    ) in listen
    assert "if retries [ fired ] then if section . dirty then schedule ( ) end return end" in listen
    assert (
        "for _ , event in ipairs ( section . retry or { } ) do if not ns . On ( event , onEvent ) "
        "then if not section . events_unregistered then section . events_unregistered = { } end "
        "local refused = section . events_unregistered "
        "refused [ # refused + 1 ] = ns . String ( event ) "
        "elseif counts then counts [ event ] = 0 end end"
    ) in listen
    assert (
        "for _ , event in ipairs ( section . retry or { } ) do off ( event , section . listener ) end"
    ) in _body(flow, "switchOff")
    users = sorted(
        path.name
        for path in SOURCES
        for flow_ in [_Flow(path, _tokens(path))]
        for key, entries in _m13_05_sections(flow_)
        if "{retry}" in entries
    )
    assert users == ["Actions.lua"] * len(M13_05_KEYS), users


# Secret values: every value read from the client goes through `clean` -----

# What Actions.lua may call directly: its own local functions, these Lua
# builtins and these `ns` helpers (Core.lua; none of them calls a function
# it is handed). Every other call is refused, whether of a client global, a
# handle bound from one, an alias of either, a call through an index or
# parentheses, or a method call: a client function is reached only as the
# first argument of `pcall`. Every `pcall` but the two pinned ones (clean's
# issecretvalue test, combatReason's InCombatLockdown) has one of two shapes:
#
#   local ok, v1, ..., vn = pcall(f, ...)
#   if ok then <vi = clean(isSecret, check, probe, vi), each vi in order>
#   else v1, ..., vn = nil, ..., nil end
#
# so on every path each value is cleaned or cleared before anything reads
# it (a value bound to `_` is never read at all); or
#
#   local keys = ns.Pack(pcall(f, ...))
#
# read only as `keys[1]` (pcall's flag), `keys.n` and `keys[i]` handed to
# clean. None of the names called may be rebound, so a call goes to the
# function named here. Round 4: `isSecret` is always one of the three pinned
# binds (or a parameter each caller hands one of them bare), and a cleaned
# value is never copied to another name or handed to a function that could
# read a field of it (`_m13_05_sink_problems`).
#
# What this does not pin: `clean` tests a table as a whole, so a field is
# tested only where the source reads it through clean; values read from the
# client's static tables and constants (Enum.*, ACCOUNT_BINDINGS,
# CHARACTER_BINDINGS) are not call results and are not tested; and nothing
# here proves issecretvalue exists or answers truthfully ([verify]).
_M13_05_BUILTINS = frozenset({"type", "pcall", "pairs", "tostring", "math.min", "string.find"})
_M13_05_NS = frozenset(
    {"ns.Section", "ns.Fn", "ns.Blocked", "ns.String", "ns.Number", "ns.Bool", "ns.Pack"}
)
_M13_05_PINNED_PCALLS = {
    "clean": "pcall ( isSecret , value )",
    "combatReason": "pcall ( InCombatLockdown )",
}
_M13_05_CLEAN = (
    "local function clean ( isSecret , check , entry , value ) "
    'if type ( value ) == "nil" or not isSecret then return value , false end '
    "local ok , secret = pcall ( isSecret , value ) "
    'tally ( check , ok , type ( secret ) == "boolean" ) '
    "if ok and secret == false then return value , false end "
    "entry . secret = ( ns . Number ( entry . secret ) or 0 ) + 1 return nil , true end"
)
_M13_05_SECRET_BIND = (
    'local isSecret = type ( issecretvalue ) == "function" and issecretvalue or nil'
)
_M13_05_ID = r"[A-Za-z_][A-Za-z0-9_]*"
_M13_05_ARG = rf"{_M13_05_ID}(?: \. {_M13_05_ID})?"


def _m13_05_enclosing_call(t: list[Token], k: int) -> int | None:
    """The `(` of the innermost call around token k, or None."""
    depth = 0
    for j in range(k - 1, -1, -1):
        if t[j].text in {")", "}", "]"}:
            depth += 1
        elif t[j].text in {"(", "{", "["}:
            if depth == 0:
                return j if t[j].text == "(" else None
            depth -= 1
    return None


def _m13_05_cleaned_last(t: list[Token], start: int, end: int) -> bool:
    """Tokens start..end-1 are the last argument of a `clean (` call whose
    first argument is `isSecret`."""
    j = _m13_05_enclosing_call(t, start)
    return (
        j is not None
        and t[j - 1].text == "clean"
        and t[j + 1].text == "isSecret"
        and t[j + 2].text == ","
        and t[start - 1].text == ","
        and end < len(t)
        and t[end].text == ")"
    )


def _m13_05_call_problems(t: list[Token], flow: _Flow, own: set[str]) -> list[str]:
    """Every call in the file is of a function named in `own`,
    `_M13_05_BUILTINS` or `_M13_05_NS`, through a name nothing rebinds."""
    problems: list[str] = []
    allowed = own | _M13_05_BUILTINS | _M13_05_NS
    for k in range(1, len(t)):
        if t[k].kind == "op" and t[k].text == ":":
            problems.append(f"line {t[k].line}: a method call")
        if not flow.call_start(k):
            continue
        prev = t[k - 1]
        if prev.kind == "name" and prev.text not in _KEYWORDS:
            j = k - 1
            while j >= 2 and t[j - 1].text in {".", ":"}:
                j -= 2
            if t[j - 1].text == "function":
                continue  # a definition
            callee = _callee(t, k - 1)
            if callee not in allowed:
                problems.append(f"line {prev.line}: {callee} is called directly")
        elif prev.text in {")", "]"} or prev.kind == "string":
            problems.append(f"line {prev.line}: a call through an expression")
    bound = [name for name, _ in flow.assigned]
    for root in sorted({name.split(".")[0] for name in _M13_05_BUILTINS}):
        if root in bound:
            problems.append(f"{root} is rebound")
    for name in [*sorted(own), "ns"]:
        if bound.count(name) != 1:
            problems.append(f"{name} is bound {bound.count(name)} times, not once")
    return problems


def _m13_05_uses(flow: _Flow, name: str, after: int) -> list[int]:
    """The uses from token `after` on of the local `name` visible there."""
    t = flow.tokens
    decl = flow.resolve(name, after)
    if decl is None:
        return []
    return [
        i
        for i in range(after, len(t))
        if t[i].kind == "name"
        and t[i].text == name
        and not _is_field(t, i)
        and not _m13_05_constructor_key(flow, i)
        and flow.resolve(name, i) is decl
    ]


def _m13_05_constructor_key(flow: _Flow, i: int) -> bool:
    """Token i is a key in a table constructor (`{ keys = kept }`)."""
    o = flow.opener[i]
    return o is not None and flow.tokens[o].text == "{" and flow.tokens[i + 1].text == "="


def _m13_05_packed_problems(t: list[Token], flow: _Flow, name: str, close: int) -> list[str]:
    """`local name = ns.Pack(pcall(...))`: read only as `name[1]`, `name.n`
    and `name[i]` handed to clean."""
    problems = []
    for i in _m13_05_uses(flow, name, close + 2):
        if _render(t[i : i + 4]) == f"{name} [ 1 ]" or _render(t[i : i + 3]) == f"{name} . n":
            continue
        if t[i + 1].text == "[" and _m13_05_cleaned_last(t, i, i + 4):
            continue
        problems.append(f"line {t[i].line}: {name} from pcall is read before clean")
    return problems


# The file's own functions a cleaned value may be handed to: each takes one
# value, reads only its type, length and pattern match, and returns it only
# as a checked string (never a table), so nothing reads a field of it.
_M13_05_SANITIZERS = {
    "plainText": (
        "local function plainText ( value ) "
        'if type ( value ) == "string" and # value > 0 and # value <= 255 '
        'and not string . find ( value , "[%c|]" ) then return value end return nil end'
    ),
    "word": (
        "local function word ( value ) "
        'if type ( value ) == "string" and # value <= 32 '
        'and string . find ( value , "^%a+$" ) then return value end return nil end'
    ),
    # M20-13 (schema 5): `command` and `keyName` are gone; every binding
    # command and key is stored as the client returns it.
}
_M13_05_STORES = ("ns . Number (", "ns . String (", "ns . Bool (")
_M13_05_COMPARE = frozenset({"==", "~=", "<", "<=", ">", ">="})


def _m13_05_sink_problems(t: list[Token], flow: _Flow, v: str, uses: list[int]) -> list[str]:
    """A cleaned value `v` (a pcall's, or a local bound from `clean`) is
    read only where it cannot be copied or read as a table later: `type(v)`,
    `v.field` as clean's last argument, the one argument of `ns.Number`,
    `ns.String`, `ns.Bool` or a pinned sanitizer, an operand of a comparison
    or of `+`/`-`, the first argument of `math.min`, or an argument of a
    `pcall` of anything but the file's own functions (the client takes it).
    Never assigned to another name, returned, put in a table, or handed to
    any other function, so no alias can read a field of it."""
    own = {label for head, label in flow.labels.items() if t[head - 1].text == "local"}
    problems = []
    for i in uses:
        nxt, prev = t[i + 1].text, t[i - 1].text
        if nxt in {".", "[", ":"}:
            if not (nxt == "." and _m13_05_cleaned_last(t, i, i + 3)):
                problems.append(f"line {t[i].line}: a field of {v} is read before clean")
            continue
        sole = prev == "(" and nxt == ")"
        if sole and _render(t[i - 2 : i + 2]) == f"type ( {v} )":
            continue
        if sole and _render(t[i - 4 : i]) in _M13_05_STORES:
            continue
        if sole and t[i - 2].text in _M13_05_SANITIZERS and not _is_field(t, i - 2):
            continue
        if nxt in _M13_05_COMPARE | {"+", "-"} or prev in _M13_05_COMPARE | {"+", "-"}:
            continue
        if _render(t[i - 4 : i + 2]) == f"math . min ( {v} ,":
            continue
        j = _m13_05_enclosing_call(t, i)
        if (
            j is not None
            and t[j - 1].text == "pcall"
            and t[j + 1].text not in own
            and prev == ","
            and nxt in {",", ")"}
        ):
            continue
        problems.append(f"line {t[i].line}: {v} is handed on (copied, stored or passed)")
    return problems


def _m13_05_pcall_problems(t: list[Token], flow: _Flow, k: int, blessed: set[int]) -> list[str]:
    """Why the `pcall` at token k may hand a value on before clean. The
    `clean` calls of a valid clean-or-clear block go into `blessed`."""
    where = f"line {t[k].line}"
    if t[k + 1].text != "(":
        return [f"{where}: pcall is used as a value"]
    close = flow.pair[k + 1]
    label = flow.label_at(k)
    if label in _M13_05_PINNED_PCALLS:
        if _render(t[k : close + 1]) == _M13_05_PINNED_PCALLS[label]:
            return []
        return [f"{where}: {label}'s pcall is not the pinned one"]
    if (
        k >= 7
        and _render(t[k - 5 : k]) == "= ns . Pack ("
        and t[k - 7].text == "local"
        and t[k - 6].kind == "name"
        and t[close + 1].text == ")"
    ):
        return _m13_05_packed_problems(t, flow, t[k - 6].text, close)
    # `local ok, v1, ..., vn = pcall(...)`, then the clean-or-clear block.
    names: list[str] = []
    j = k - 2
    while t[k - 1].text == "=" and t[j].kind == "name" and t[j].text not in _KEYWORDS:
        names.insert(0, t[j].text)
        if t[j - 1].text != ",":
            break
        j -= 2
    if t[k - 1].text != "=" or t[j - 1].text != "local" or not names:
        return [f"{where}: pcall's values are not bound by a `local` statement of their own"]
    ok, values = names[0], [n for n in names[1:] if n != "_"]
    if not values:
        return []
    if t[close + 1].text != "if":
        return [f"{where}: pcall's values are not cleaned at once"]
    end = flow.block_end(close + 1)

    def cleaned(v: str) -> str:
        args = rf"isSecret , {_M13_05_ARG} , {_M13_05_ARG} , {v}"
        return rf"{v} (?:, {_M13_05_ID} )?= clean \( {args} \)"

    block = (
        rf"if {ok} then {' '.join(cleaned(v) for v in values)} "
        rf"else {' , '.join(values)} = {' , '.join(['nil'] * len(values))} end"
    )
    if not re.fullmatch(block, _render(t[close + 1 : end + 1])):
        return [
            f"{where}: pcall's values {values} are not each cleaned when {ok} and cleared "
            "otherwise, at once"
        ]
    blessed.update(i for i in range(close + 1, end) if t[i].text == "clean")
    problems = []
    for v in values:
        problems += _m13_05_sink_problems(t, flow, v, _m13_05_uses(flow, v, end))
    return problems


def _m13_05_clean_problems(t: list[Token], flow: _Flow, blessed: set[int]) -> list[str]:
    """Every value `clean` gives is either a pcall's value in its
    clean-or-clear block (`blessed`), the one argument of `ns.Number`,
    `ns.String` or `ns.Bool`, or bound by `local x = clean(...)`, and then
    read only as `_m13_05_sink_problems` allows."""
    problems = []
    for k, x in enumerate(t):
        if x.text != "clean" or _is_field(t, k) or t[k - 1].text == "function":
            continue
        if flow.label_at(k) == "clean" or k in blessed:
            continue
        close = flow.pair[k + 1]
        if _render(t[k - 4 : k]) in _M13_05_STORES and t[close + 1].text == ")":
            continue
        if t[k - 1].text == "=" and t[k - 3].text == "local" and t[k - 2].kind == "name":
            v = t[k - 2].text
            problems += _m13_05_sink_problems(t, flow, v, _m13_05_uses(flow, v, close + 1))
            continue
        problems.append(f"line {x.line}: clean's value is not stored, checked or bound locally")
    return problems


def _m13_05_is_secret_problems(t: list[Token], flow: _Flow) -> list[str]:
    """`isSecret` is one of the three pinned bindings to issecretvalue, or a
    parameter of the file's own function that each caller hands one of them
    bare. It is never assigned or shadowed, and read only as a bare argument
    (and inside the pinned `clean`), so no edit can turn the test off."""
    problems: list[str] = []
    n_bind = len(_M13_05_SECRET_BIND.split(" "))
    binds = {
        id(flow.resolve("isSecret", k + n_bind))
        for k in range(len(t))
        if _render(t[k : k + n_bind]) == _M13_05_SECRET_BIND
    }
    params: dict[str, tuple[int, int]] = {}  # function -> (its param decl, position)
    for head, label in flow.labels.items():
        opening = next(p for p in range(head, len(t)) if t[p].text == "(")
        names = [x.text for x in t[opening + 1 : flow.pair[opening]] if x.kind == "name"]
        if "isSecret" in names:
            decl = flow.resolve("isSecret", flow.functions[head].start)
            params[label] = (id(decl), names.index("isSecret"))
    allowed = binds | {decl for decl, _ in params.values()}
    declared = [at for name, at in flow.assigned if name == "isSecret"]
    if len(declared) != len(binds) + len(params) or len(binds) != len(M13_05_KEYS):
        problems.append(
            f"isSecret is bound {len(declared)} times, not only by the pinned binds and "
            f"the parameters of {sorted(params)}"
        )
    for i, x in enumerate(t):
        if x.text != "isSecret" or _is_field(t, i) or i in declared:
            continue
        if flow.label_at(i) == "clean":
            continue  # pinned whole
        decl = flow.resolve("isSecret", i)
        if decl is None or id(decl) not in allowed:
            problems.append(f"line {x.line}: isSecret is not a pinned binding here")
        if not (t[i - 1].text in {"(", ","} and t[i + 1].text in {")", ","}):
            problems.append(f"line {x.line}: isSecret is used other than as a bare argument")
            continue
        j = _m13_05_enclosing_call(t, i)
        if j is None:
            problems.append(f"line {x.line}: isSecret is not an argument")
            continue
        callee = t[j - 1].text
        if callee in params:
            position = sum(1 for p in range(j + 1, i) if t[p].text == "," and flow.opener[p] == j)
            if position != params[callee][1]:
                problems.append(f"line {x.line}: isSecret is handed to {callee} out of place")
    # Every caller of a function taking isSecret hands it on bare, in place.
    for label, (_, position) in params.items():
        for k, x in enumerate(t):
            if x.text != label or _is_field(t, k) or t[k + 1].text != "(":
                continue
            if t[k - 1].text == "function":
                continue
            j = k + 1
            args = [
                p for p in range(j + 1, flow.pair[j]) if t[p].text == "," and flow.opener[p] == j
            ]
            bounds = [j, *args, flow.pair[j]]
            if len(bounds) - 1 <= position:
                problems.append(f"line {x.line}: {label} is called without isSecret")
                continue
            arg = t[bounds[position] + 1 : bounds[position + 1]]
            if _render(arg) != "isSecret":
                problems.append(f"line {x.line}: {label} is handed {_render(arg)!r}, not isSecret")
    for label, body in _M13_05_SANITIZERS.items():
        heads = [h for h, name in flow.labels.items() if name == label]
        if len(heads) != 1 or _render(t[heads[0] - 1 : flow.block_end(heads[0]) + 1]) != body:
            problems.append(f"{label} is not the pinned sanitizer")
    return problems


def _m13_05_secret_problems(path: Path) -> list[str]:
    """Why a value read from the client in `path` (Actions.lua, or an edited
    copy) may be compared, used or stored before issecretvalue was asked
    about it, on any path."""
    t = _tokens(path)
    flow = _Flow(path, t)
    text = _render(t)
    problems: list[str] = []
    if text.count(_M13_05_CLEAN) != 1:
        problems.append("clean is not the pinned one")
    if text.count(_M13_05_SECRET_BIND) != len(M13_05_KEYS):
        problems.append("a gather does not bind isSecret to issecretvalue")
    if text.count('probe ( "issecretvalue" , isSecret )') != len(M13_05_KEYS):
        problems.append("a gather does not probe issecretvalue")
    own = {label for head, label in flow.labels.items() if t[head - 1].text == "local"}
    problems += _m13_05_call_problems(t, flow, own)
    problems += _m13_05_underscore_reads(path)
    problems += _m13_05_is_secret_problems(t, flow)
    blessed: set[int] = set()
    for k, x in enumerate(t):
        if x.kind == "name" and x.text == "pcall" and not _is_field(t, k):
            problems += _m13_05_pcall_problems(t, flow, k, blessed)
    problems += _m13_05_clean_problems(t, flow, blessed)
    return problems


def test_m13_05_every_client_value_is_asked_about_before_use() -> None:
    """§15.2 and the round-2 domain review: secret values are not only a
    combat matter (restricted content may make them too, [verify]). Where
    the client has issecretvalue, each gather tests every value a client call
    gave, and every field of a table it gave, before the value is compared,
    used or stored, on every path; one it calls secret, or cannot say about,
    is dropped and counted on its API's probe as `secret`. Round 3: the rule
    is structural, not a list of handles: no client function is called but
    through `pcall`, and every `pcall`'s values are cleaned or cleared at
    once."""
    assert _m13_05_secret_problems(ACTIONS) == []


_M13_05_SECRET_CASES: dict[str, list[tuple[str, str]]] = {
    "a field read raw": [
        (
            "ns.Number(clean(isSecret, probes.test, probes.item, info.spellID))",
            "ns.Number(info.spellID)",
        )
    ],
    "a count used raw": [
        ("            count = clean(isSecret, probes.test, probes.lines, count)\n", "")
    ],
    "a binding key read raw": [
        ("clean(isSecret, secret, key, keys[i])", "keys[i]"),
    ],
    "the action type used raw": [
        ("                kind = clean(isSecret, secret, action, kind)\n", ""),
    ],
    "an unknown answer kept": [("if ok and secret == false then", "if ok and not secret then")],
    "a gather without the test": [
        (
            'local isSecret = type(issecretvalue) == "function" and issecretvalue or nil',
            "local isSecret = nil",
        )
    ],
    # Round 3: the rule is structural. Each of these reads a client value
    # that no named handle gave, or skips clean on one path.
    "a handle called directly": [
        ("local okCount, count = pcall(numLines)", "local okCount, count = true, numLines()")
    ],
    "a client global called directly": [
        (
            "        local okCount, total = pcall(getCount)\n",
            "        local total0 = GetNumBindings()\n"
            + "        local okCount, total = pcall(getCount)\n",
        )
    ],
    "a handle under another name": [
        (
            "        local okCount, total = pcall(getCount)\n",
            "        local n = getCount\n        local okCount, total = pcall(n)\n"
            "        record.raw = ns.Number(total)\n",
        )
    ],
    "pcall's value taken through select": [
        (
            "local okCount, total = pcall(getCount)",
            "local okCount, total = true, select(2, pcall(getCount))",
        )
    ],
    "clean skipped on one path": [
        ("            if ok then\n", "            if ok and slot > 1 then\n")
    ],
    "the error message kept": [("                kind, id, subType = nil, nil, nil\n", "")],
    "a table handed on": [
        ('    tally(probes.item, ok, type(info) == "table")\n', "    local raw = info\n")
    ],
    "a builtin rebound": [("local IN_COMBAT", "local tostring = GetActionInfo\nlocal IN_COMBAT")],
    # Round 4: isSecret is only ever a pinned bind, and a cleaned value is
    # never copied or handed to a function that could read its fields.
    "isSecret cleared after the bind": [
        ("        local action = probe(", "        isSecret = nil\n        local action = probe(")
    ],
    "isSecret shadowed": [
        (
            "        for index = 1, math.min(total, MAX_BINDINGS) do\n",
            "        local isSecret = false\n        for index = 1, math.min(total, MAX_BINDINGS) do\n",
        )
    ],
    "isSecret passed through an expression": [
        (
            "line, bank, itemInfo, itemName, isSecret)",
            "line, bank, itemInfo, itemName, isSecret and nil)",
        )
    ],
    "a cleaned value copied": [
        (
            "        record.commands = commands\n",
            "        local alias = total\n        record.raw = ns.Number(alias.x)\n",
        )
    ],
    "a cleaned value handed to a helper": [
        ("        record.commands = commands\n", "        record.raw = ns.Number(probe(total))\n")
    ],
    "a sanitizer that reads a field": [
        (
            "local function word(value)\n",
            'local function word(value)\n    local _ = type(value) == "table" and value.x\n',
        )
    ],
    "a method call": [
        ("local okCount, total = pcall(getCount)", "local okCount, total = pcall(C_X.Y, C_X:Z())")
    ],
}


def test_m13_05_constructed_secret_edits_fail() -> None:
    missed = []
    for tag, edits in sorted(_M13_05_SECRET_CASES.items()):
        text = ACTIONS.read_text(encoding="utf-8")
        for found, replacement in edits:
            assert found in text, found
            text = text.replace(found, replacement, 1)
        with tempfile.TemporaryDirectory(prefix="wowlab-m13-05-") as tmp:
            path = Path(tmp) / ACTIONS.name
            path.write_text(text, encoding="utf-8")
            if not _m13_05_secret_problems(path):
                missed.append(tag)
    assert not missed, missed


def test_m13_05_get_macro_info_only_for_a_macro_index() -> None:
    """A macro slot's id is the macro's index only when the client gives no
    subtype (nil or ""); with one it is the spell or item the macro shows
    [verify]. GetMacroInfo is called only for an index, never with a subtype
    that was dropped as secret: an out-of-range index into a C API is the
    crash class in docs/DATA_SOURCES.md (2026-09-28)."""
    text = _render(_tokens(ACTIONS))
    assert ("subType , hiddenSubType = clean ( isSecret , secret , action , subType )") in text
    assert (
        'local macroIndex = not hiddenSubType and ( subType == nil or subType == "" ) '
        'if kind == "macro" and macroIndex and type ( id ) == "number" and getMacro then '
        "local okMacro , _ , icon = pcall ( getMacro , id )"
    ) in text
    assert text.count("pcall ( getMacro ,") == 1


def test_m13_05_spellbook_records_the_client_item_types() -> None:
    """`item_types` is Enum.SpellBookItemType as the client has it, name to
    number, so a reader names each item's `type` without assuming numbers;
    `bindings` records the client's ACCOUNT_BINDINGS and CHARACTER_BINDINGS
    for the same reason."""
    text = _render(_tokens(ACTIONS))
    assert (
        'local itemTypes = type ( Enum ) == "table" and Enum . SpellBookItemType or nil'
    ) in text
    assert "for name , value in pairs ( itemTypes ) do" in text
    assert "out [ key ] = ns . Number ( value )" in text
    assert "item_types = spellbookItemTypes ( )" in text
    assert "record . account_bindings = ns . Number ( ACCOUNT_BINDINGS )" in text
    assert "record . character_bindings = ns . Number ( CHARACTER_BINDINGS )" in text


def test_m13_05_readme_and_plan_document_schema_3() -> None:
    readme = " ".join(README.read_text(encoding="utf-8").split())
    plan = " ".join(PLAN.read_text(encoding="utf-8").split("### 13.2")[0].split())
    for text in (readme, plan):
        assert "schema 3" in text
        for key in M13_05_KEYS:
            assert f"`{key}`" in text, key
        assert "`left_out_config`" in text
        assert "`left_out_config_absent`" in text
        assert "`probes`" in text
        assert "out of combat" in text


# M19-01: what Crew.lua alone may do (docs/LAB_PLAN.md §20.1, ADR-0036) -------
#
# Source scans; nothing here runs Lua (L3). On 2026-11-19 no Crew.lua exists
# (M19-10 writes it), so every rule below is proven on constructed sources:
# `_CREW_SOURCE` is a constructed Crew.lua, not addon code, and holds no
# player's name or realm (ADR-0036 term 3). The relaxations are the ones
# CREW_RELAXED lists, read through `_relaxed` only. No test here takes an
# argument but `path`: the review probes call every test bare or per source.


def _crew_book_problems(path: Path, tokens: list[Token]) -> list[str]:
    """No file but Crew.lua names BOOK, as a name or in a string."""
    if CREW_DB in _relaxed(path, "SAVED_VARIABLES"):
        return []
    return [
        f"{_where(path, t)} {t.text!r}"
        for t in tokens
        if (t.kind == "name" and t.text == CREW_DB) or (t.kind == "string" and CREW_DB in t.text)
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_only_crew_lua_names_the_crew_book(path: Path) -> None:
    bad = _crew_book_problems(path, _tokens(path))
    assert not bad, bad


def _crew_command_call_lines(flow: _Flow | None) -> set[int]:
    """The lines of Core.lua holding exactly CREW_COMMAND_CALL, alone on the
    line, inside its slash handler, with `message` its parameter, never
    assigned."""
    if flow is None:
        return set()
    t = flow.tokens
    n = len(CREW_COMMAND_CALL.split(" "))
    lines = set()
    for k in range(len(t) - n + 1):
        if _render(t[k : k + n]) != CREW_COMMAND_CALL or _is_field(t, k):
            continue
        fn = flow.function_at(k)
        caller = (
            ".".join(x.text for x in t[fn.head - 4 : fn.head - 1] if x.text != ".") if fn else ""
        )
        if fn is None or caller != CREW_COMMAND_CALLER or t[fn.head - 1].text != "=":
            continue
        line = [x for x in t if x.line == t[k].line]
        decl = flow.resolve("message", k + 4)
        rebound = any(r.rstrip(".") == "message" for r, ts in flow.rebound if fn.head < ts)
        if line == t[k : k + n] and decl is not None and not rebound:
            lines.add(t[k].line)
    return lines


def _crew_command_call_problems(path: Path, tokens: list[Token]) -> list[str]:
    """`CrewCommand` is named only by Crew.lua's definition and Core.lua's
    one pinned call (CREW_COMMAND_CALL in its slash handler)."""
    if path.name == CREW_FILE:
        return []
    named = [
        k
        for k, x in enumerate(tokens)
        if (x.kind == "name" and x.text == "CrewCommand")
        or (x.kind == "string" and "CrewCommand" in x.text)
    ]
    if not named:
        return []
    if path.name == "Core.lua":
        lines = _crew_command_call_lines(_Flow(path, tokens))
        if len(lines) == 1 and len(named) == 1 and tokens[named[0]].line in lines:
            return []
    return [
        f"{_where(path, tokens[k])} CrewCommand named outside the one pinned call" for k in named
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_only_core_calls_the_crew_command(path: Path) -> None:
    bad = _crew_command_call_problems(path, _tokens(path))
    assert not bad, bad


def _core_file_events(flow: _Flow | None) -> set[str]:
    """The events Core.lua registers at file level (`ns.On("X", ...)`)."""
    if flow is None:
        return set()
    t = flow.tokens
    return {
        t[k + 4].text
        for k in range(len(t) - 4)
        if _render(t[k : k + 4]) == "ns . On ("
        and t[k + 4].kind == "string"
        and flow.function_at(k) is None
    }


def _core_events_on_disk() -> set[str]:
    core = ADDON / "Core.lua"
    return _core_file_events(_Flow(core, _tokens(core))) if core.is_file() else set()


def _crew_on_call_ok(flow: _Flow, s: int, core_events: set[str]) -> bool:
    """Crew.lua's `ns.On(` at token s (the `ns`, or the `On`): inside
    `ns.CrewCommand` or a gated function; or the one file-level call, for an
    event Core.lua already registers at file level (so no new registration is
    made while the book is off), whose handler is a gated local function."""
    if CREW_ON_CALLS not in _relaxed(flow.path, "SECTION_SCAN"):
        return False
    t = flow.tokens
    k = s if t[s].text == "ns" else s - 2
    if k < 0 or _render(t[k : k + 4]) != "ns . On (" or _is_field(t, k):
        return False
    # Every call: `ns.On("<EVENT>", <local function>)`, nothing else.
    shape = (
        t[k + 4].kind == "string"
        and t[k + 5].text == ","
        and t[k + 6].kind == "name"
        and t[k + 7].text == ")"
    )
    if not shape:
        return False
    fn = flow.function_at(k)
    pins = _crew_pins(t)
    if fn is not None:
        # Fix round 4: inside, only the events §20.1 names for the book.
        # M20-04: at schema 2 also the probe's two (CREW_PVP_EVENTS).
        return (
            (flow.labels.get(fn.head) == CREW_COMMAND or _crew_gated(flow, fn.head))
            and t[k + 4].text in pins.events
            and _crew_handler_ok(flow, k + 6, gated=True)
        )
    # Fix round 3: at most one file-level call per event, each for an event
    # Core.lua registers at file level.
    same_event = [
        j
        for j in range(len(t) - 4)
        if _render(t[j : j + 4]) == "ns . On ("
        and flow.function_at(j) is None
        and t[j + 4].text == t[k + 4].text
    ]
    if same_event != [k] or t[k + 4].text not in core_events:
        return False
    if _crew_handler_ok(flow, k + 6, gated=True):
        return True
    decl = flow.resolve(t[k + 6].text, k)
    if decl is None or decl.function is None or not _crew_handler_ok(flow, k + 6, gated=False):
        return False
    # The ungated one: the pinned probe handler, for ADDON_LOADED only.
    head = decl.function
    return (
        t[k + 4].text == CREW_PROBE_EVENT
        and t[k + 6].text == "crewLoaded"
        and _render(t[head - 1 : flow.block_end(head) + 1]) == pins.probe
    )


def _crew_handler_ok(flow: _Flow, h: int, gated: bool) -> bool:
    """The handler named at token h of a Crew.lua `ns.On`: a local function,
    never assigned again. Gated: not `crewLoaded`, behind the gate, and
    taking no parameter but `_` or `event`, never read (fix round 4: Core
    calls a handler as `fn(event, ...)`, and the payload may be a GUID or a
    name). Not gated: only `crewLoaded` (pinned by the caller)."""
    t = flow.tokens
    name = t[h].text
    decl = flow.resolve(name, h)
    if decl is None or decl.function is None:
        return False
    if any(r.rstrip(".") == name and flow.resolve(name, ts) is decl for r, ts in flow.rebound):
        return False  # the handler's local assigned another function
    if not gated:
        return name == "crewLoaded"
    head = decl.function
    if name == "crewLoaded" or not _crew_gated(flow, head):
        return False
    fn = flow.functions[head]
    opening = next(p for p in range(head, len(t)) if t[p].text == "(")
    params = [x for x in t[opening + 1 : flow.pair[opening]] if x.text != ","]
    if any(x.text not in {"_", "event"} for x in params):
        return False
    for x in params:
        param = flow.resolve(x.text, fn.start)
        if any(
            y.text == x.text and not _is_field(t, j) and flow.resolve(x.text, j) is param
            for j, y in enumerate(t[fn.start : fn.stop], start=fn.start)
        ):
            return False
    return True


def _pcall_literal(t: list[Token], head: int) -> bool:
    """Is the function literal at `head` the first argument of `pcall(`,
    so it runs at once, inside the function that holds it?"""
    return (
        head >= 2
        and t[head - 1].text == "("
        and t[head - 2].kind == "name"
        and t[head - 2].text == "pcall"
        and not _is_field(t, head - 2)
    )


def _on_player(args: list[Token]) -> bool:
    """Is a unit call's first argument exactly the literal "player"?"""
    return (
        len(args) == 2
        and args[0].kind == "string"
        and args[0].text == "player"
        and args[1].text in {",", ")"}
    )


_CREW_GATE_LEN = len(CREW_GATE.split(" "))
_CREW_OTHER_TABLES = ("WowLabDB", "WowLabCharDB")


def _crew_gated(flow: _Flow, head: int | None) -> bool:
    """Does the function at `head` start with CREW_GATE? A function literal
    handed straight to `pcall(` stands for the function that holds it."""
    t = flow.tokens
    fn = flow.functions.get(head) if head is not None else None
    while fn is not None and _pcall_literal(t, fn.head):
        fn = flow.function_at(fn.head - 1)
    return fn is not None and _render(t[fn.start : fn.start + _CREW_GATE_LEN]) == CREW_GATE


def _crew_relaxed_calls(t: list[Token]) -> list[int]:
    """The callee token of each relaxed call: a name call, or a unit call
    whose first argument is not the literal "player"."""
    out = []
    for k, x in enumerate(t):
        if x.kind != "name" or k + 1 >= len(t) or t[k + 1].text != "(":
            continue
        if (x.text in CREW_NAME_CALLS and not _is_field(t, k)) or (
            _is_unit_api(x.text) and not _on_player(t[k + 2 : k + 4])
        ):
            out.append(k)
    return out


def _crew_secret_guarded(flow: _Flow, decl: _Decl, name: str, u: int) -> bool:
    """Is token u inside an `if`/`elseif` block, or later in its condition,
    whose condition has `not secret ( name )` as a top-level `and` conjunct
    (and no top-level `or`)?"""
    t = flow.tokens
    want = f"not secret ( {name} )"
    for b in flow.blocks:
        if b.kind not in {"if", "elseif"} or b.cond is None:
            continue
        cs, ce = b.cond
        inside = b.start <= u < b.stop
        if not inside and not cs <= u < ce:
            continue
        depth = flow.opener[cs]
        if any(t[q].text == "or" and flow.opener[q] == depth for q in range(cs, ce)):
            continue
        for p in range(cs, ce - 4):
            if (
                _render(t[p : p + 5]) == want
                and flow.opener[p] == depth
                and (p == cs or t[p - 1].text == "and")
                and t[p + 5].text in {"and", "then"}
                and flow.resolve(name, p + 3) is decl
                and (inside or p + 5 <= u)
            ):
                return True
    return False


def _crew_secret_problems(flow: _Flow, k: int) -> list[str]:
    """§20.1 "Secret values": the relaxed call at k is the whole value of a
    `local a, b = <call>`, and each name it binds is never assigned again
    and is read only as `secret(x)` or under `not secret(x)`."""
    t = flow.tokens
    x = t[k]
    close = flow.pair[k + 1]
    names: list[str] = []
    j = k - 1
    if t[j].text == "=":
        j -= 1
        while j >= 0 and t[j].kind == "name" and t[j].text not in _KEYWORDS:
            names.insert(0, t[j].text)
            if t[j - 1].text != ",":
                j -= 1
                break
            j -= 2
    if not names or t[j].text != "local" or not _ends_expression(t, close + 1):
        return [f"line {x.line}: {x.text}(...) is not the whole value of a local"]
    problems = []
    for name in names:
        if name == "_":
            continue
        decl = flow.resolve(name, close + 1)
        if decl is None:
            problems.append(f"line {x.line}: {name} is not bound here")
            continue
        for root, ts in flow.rebound:
            if root.rstrip(".") == name and flow.resolve(name, ts) is decl:
                problems.append(f"line {t[ts].line}: {name} is assigned again")
        for u in range(close + 1, len(t)):
            y = t[u]
            if y.kind != "name" or y.text != name or _is_field(t, u):
                continue
            if flow.resolve(name, u) is not decl:
                continue
            if t[u - 1].text == "(" and t[u - 2].text == "secret" and t[u + 1].text == ")":
                continue
            if not _crew_secret_guarded(flow, decl, name, u):
                problems.append(
                    f"line {y.line}: {name} (from {x.text}) is used before the secret test"
                )
    return problems


def _crew_unit_problems(flow: _Flow, u: str) -> list[str]:
    """A local used as a unit is bound only by `local u = "party" .. i`,
    `"raid" .. i` (i a number or a numeric for variable not assigned in its
    loop) or one of the unit literals, and never assigned again. Fix round 2:
    or it is the one parameter of a unit helper (`_crew_unit_helper_problems`)."""
    t = flow.tokens
    good = _crew_unit_bindings(flow, u)
    helpers = [
        p + 2
        for p in range(len(t) - 5)
        if [t[p].text, t[p + 1].text, t[p + 3].text, t[p + 4].text, t[p + 5].text]
        == ["local", "function", "(", u, ")"]
        and t[p + 2].kind == "name"
    ]
    problems = []
    if not flow.decls.get(u) or good + len(helpers) != len(flow.decls[u]):
        problems.append(f"{u} is used as a unit but bound other than from a party or raid token")
    if any(root.rstrip(".") == u for root, _ in flow.rebound):
        problems.append(f"{u} is used as a unit and assigned again")
    for h in helpers:
        problems += _crew_unit_helper_problems(flow, h, u)
    return problems


def _crew_unit_decls(flow: _Flow, u: str) -> list[_Decl]:
    """The declarations made by `local u = <party or raid token>` statements."""
    t = flow.tokens
    good: list[_Decl] = []
    for p in range(len(t) - 4):
        if [t[p].text, t[p + 1].text, t[p + 2].text] != ["local", u, "="]:
            continue
        v = t[p + 3]
        ok = False
        if v.kind != "string":
            continue
        if v.text in {"party", "raid", "target"} and _ends_expression(t, p + 4):
            ok = True
        elif v.text in {"party", "raid"} and t[p + 4].text == ".." and _ends_expression(t, p + 6):
            n = t[p + 5]
            ok = n.kind == "number" or (n.kind == "name" and _crew_for_variable(flow, n.text, p))
        if ok:
            good += [d for d in flow.decls.get(u, []) if p < d.start <= p + 8]
    return good


def _crew_unit_bindings(flow: _Flow, u: str) -> int:
    return len(_crew_unit_decls(flow, u))


def _crew_unit_local_ok(flow: _Flow, v: str, k: int) -> bool:
    """Is `v` at token k a party or raid token local, never assigned again?"""
    decl = flow.resolve(v, k)
    return (
        decl is not None
        and any(d is decl for d in _crew_unit_decls(flow, v))
        and not any(
            root.rstrip(".") == v and flow.resolve(v, ts) is decl for root, ts in flow.rebound
        )
    )


def _crew_unit_helper_problems(flow: _Flow, h: int, u: str) -> list[str]:
    """`local function H(u)` at token h (H's name): u is used in H only as
    the first argument of a unit call; H is defined once and referred to only
    as `H(<unit local>)` or `H("party" | "raid" | "target")`."""
    t = flow.tokens
    name = t[h].text
    head = h - 1
    fn = flow.functions[head]
    decl = flow.resolve(u, fn.start)
    problems = []
    for k in range(fn.start, fn.stop):
        if t[k].text != u or t[k].kind != "name" or _is_field(t, k):
            continue
        if flow.resolve(u, k) is not decl:
            continue
        if not (
            t[k - 1].text == "(" and _is_unit_api(t[k - 2].text) and t[k + 1].text in {",", ")"}
        ):
            problems.append(f"line {t[k].line}: {name}'s {u} is used other than as a unit")
    if len(flow.decls.get(name, [])) != 1:
        problems.append(f"{name} is bound more than once")
    for k, x in enumerate(t):
        if x.kind != "name" or x.text != name or _is_field(t, k) or k == h:
            continue
        arg = t[k + 2] if k + 3 < len(t) else None
        ok = (
            arg is not None
            and t[k + 1].text == "("
            and t[k + 3].text == ")"
            and (
                (arg.kind == "string" and arg.text in {"party", "raid", "target"})
                or (arg.kind == "name" and _crew_unit_local_ok(flow, arg.text, k + 2))
            )
        )
        if not ok:
            problems.append(f"line {x.line}: {name} is called with other than a unit")
    return problems


def _crew_for_variable(flow: _Flow, n: str, p: int) -> bool:
    """Is `n` the variable of a numeric `for` around token p, never assigned
    inside that loop?"""
    t = flow.tokens
    for b in flow.blocks:
        if b.kind != "loop" or not b.head < p < b.stop:
            continue
        if [t[b.head].text, t[b.head + 1].text, t[b.head + 2].text] != ["for", n, "="]:
            continue
        return not any(root.rstrip(".") == n and b.head < ts < b.stop for root, ts in flow.rebound)
    return False


def _crew_problems(path: Path, text: str, pins: _CrewPins | None = None) -> list[str]:
    """Crew.lua's own rules, in exchange for its relaxations (see
    CREW_RELAXED): the gate, the secret test, the pinned helpers, the unit
    locals, what it may share with the rest of the addon, and who may turn
    the book on. Nothing for any other file. M20-04: graded with `pins`, or
    else with the pins of the schema the source declares (`_crew_pins`)."""
    if path.name != CREW_FILE:
        return []
    t = tokenize(text)
    flow = _Flow(path, t)
    pins = pins or _crew_pins(t)
    problems: list[str] = []
    calls = _crew_relaxed_calls(t)

    def in_command(k: int) -> bool:
        fn = flow.function_at(k)
        return fn is not None and flow.labels.get(fn.head) == CREW_COMMAND

    # A name call is called or type()-tested, nothing else.
    for k, x in enumerate(t):
        if x.kind == "name" and x.text in CREW_NAME_CALLS and not _is_field(t, k):
            nxt = t[k + 1].text if k + 1 < len(t) else ""
            type_test = k >= 2 and t[k - 1].text == "(" and t[k - 2].text == "type" and nxt == ")"
            if not type_test and nxt != "(":
                problems.append(f"line {x.line}: {x.text} used other than as a call or type() test")
    # The gate, the secret test and the unit locals of every relaxed call.
    units: set[str] = set()
    for k in calls:
        fn = flow.function_at(k)
        if not _crew_gated(flow, fn.head if fn else None):
            problems.append(
                f"line {t[k].line}: {t[k].text}(...) is not behind the on and combat tests"
            )
        problems += _crew_secret_problems(flow, k)
        if _is_unit_api(t[k].text) and t[k + 2].kind == "name":
            units.add(t[k + 2].text)
    for u in sorted(units):
        problems += _crew_unit_problems(flow, u)
    # The pinned helpers: defined once as the pinned `local function`, and
    # otherwise only ever called.
    for name, pinned in pins.pinned.items():
        defs = [
            k
            for k in range(2, len(t))
            if t[k].text == name and t[k - 1].text == "function" and t[k - 2].text == "local"
        ]
        if len(defs) != 1 or _render(t[defs[0] - 2 : flow.block_end(defs[0] - 1) + 1]) != pinned:
            problems.append(f"{name} is not the pinned one, defined once")
        for k, x in enumerate(t):
            if x.kind != "name" or x.text != name or _is_field(t, k) or k in defs:
                continue
            if t[k - 1].text == "function" or k + 1 >= len(t) or t[k + 1].text != "(":
                problems.append(f"line {x.line}: {name} is bound or used other than called")
    for name in (CREW_DB, "InCombatLockdown", "issecretvalue", "type", "pcall"):
        if name in flow.decls:
            problems.append(f"{name} is declared as a local or a parameter")
    # Nothing leaves the book: no other saved table, no global but the book,
    # no store under ns, only CREW_NS_READS read from ns, only CREW_EXPORTS
    # defined, each returning nothing or a literal.
    for k, x in enumerate(t):
        if (x.kind == "name" and x.text in _CREW_OTHER_TABLES) or (
            x.kind == "string" and any(n in x.text for n in _CREW_OTHER_TABLES)
        ):
            problems.append(f"line {x.line}: Crew.lua names {x.text!r}")
        if x.kind == "name" and x.text == "ns" and not _is_field(t, k) and k + 2 < len(t):
            if t[k + 1].text == ":":
                problems.append(f"line {x.line}: ns used as a method receiver")
            if t[k + 1].text != "." or t[k + 2].kind != "name":
                continue
            field = t[k + 2].text
            if t[k - 1].text == "function":
                if field not in CREW_EXPORTS or t[k + 3].text != "(":
                    problems.append(f"line {x.line}: Crew.lua defines ns.{field}")
            elif field not in CREW_NS_READS:
                problems.append(f"line {x.line}: Crew.lua reads ns.{field}")
        global_function = (
            x.kind == "name"
            and x.text == "function"
            and k + 1 < len(t)
            and t[k + 1].kind == "name"
            and t[k - 1].text != "local"
            and t[k + 1].text != "ns"
            and flow.resolve(t[k + 1].text, k) is None
        )
        if global_function:
            problems.append(f"line {x.line}: Crew.lua defines the global {t[k + 1].text}")
    for root, ts in flow.rebound:
        base = root.rstrip(".")
        if base == "ns":
            problems.append(f"line {t[ts].line}: Crew.lua stores under ns")
        elif base != CREW_DB and flow.resolve(base, ts) is None:
            problems.append(f"line {t[ts].line}: Crew.lua writes the global {base}")
    for head, label in flow.labels.items():
        if not label.startswith("ns.") or t[head + 1].text != "ns":
            continue
        for s, e in flow.returns(head):
            literal = e - s == 1 and (
                t[s].kind in {"string", "number"} or t[s].text in {"nil", "true", "false"}
            )
            if e > s and not literal:
                problems.append(f"{label} returns {_render(t[s:e])!r}, not nothing or a literal")
    # The book: only indexed or type()-tested; the whole table and its `on`
    # assigned only in the slash handler.
    for k, x in enumerate(t):
        if x.kind != "name" or x.text != CREW_DB or _is_field(t, k):
            continue
        nxt = t[k + 1].text if k + 1 < len(t) else ""
        if nxt in {".", "["}:
            continue
        if t[k - 1].text == "(" and t[k - 2].text == "type" and nxt == ")":
            continue
        if nxt == "=" and t[k - 1].text != "," and in_command(k):
            continue
        problems.append(f"line {x.line}: {CREW_DB} used whole outside the slash handler")
    for root, ts in flow.rebound:
        if root != CREW_DB + ".":
            continue
        if t[ts + 1].text == ".":
            key, after = t[ts + 2].text, ts + 3
        else:
            close = flow.pair[ts + 1]
            inner = t[ts + 2 : close]
            if len(inner) != 1 or inner[0].kind != "string":
                problems.append(
                    f"line {t[ts].line}: {CREW_DB}[...] stored under a key not written out"
                )
                continue
            key, after = inner[0].text, close + 1
        if key == "on" and t[after].text in {"=", ","} and not in_command(ts):
            problems.append(f"line {t[ts].line}: {CREW_DB}.on set outside the slash handler")
    problems += _crew_parameter_problems(flow, pins)
    problems += _crew_book_key_problems(flow)
    # The slash handler's first statement, pinned (fix round 2, item 1).
    # Then the schema branch: one `print("<a plain string literal>")` (the
    # wording is the addon's) and `return`, nothing else (follow-up, #631).
    # M20-04: at schema 2 the migration's test sits between the two.
    n_head = len(pins.command_head.split(" "))
    for head, label in flow.labels.items():
        if label != CREW_COMMAND:
            continue
        branch = t[head + n_head : head + n_head + 6]
        ok = (
            _render(t[head : head + n_head]) == pins.command_head
            and [x.text for x in branch[:2]] == ["print", "("]
            and len(branch) == 6
            and branch[2].kind == "string"
            and [(x.kind, x.text) for x in branch[3:]]
            == [("op", ")"), ("name", "return"), ("name", "end")]
        )
        if not ok:
            problems.append(
                f"{CREW_COMMAND} does not start with the pinned string and schema tests"
            )
    problems += _crew_schema_problems(flow, pins)
    problems += _crew_write_problems(flow, pins)
    if pins.schema == 2:
        problems += _crew_schema_2_problems(flow)
    # ns.On, only where _crew_on_call_ok allows it (item 4).
    events = _core_events_on_disk()
    for k in range(len(t) - 3):
        on_call = _render(t[k : k + 4]) == "ns . On (" and not _is_field(t, k)
        if on_call and not _crew_on_call_ok(flow, k, events):
            problems.append(f"line {t[k].line}: ns.On called where Crew.lua may not register")
    return problems


_CREW_BOOK_TABLES = ("players", "current")


def _crew_book_key_problems(flow: _Flow) -> list[str]:
    """Fix round 4, cut by M20-11 (owner decision, 2026-11-20): the rule that
    a key of `BOOK.players` / `.current` is built only from the name calls,
    and that the two tables are never handed on, existed for what the book
    may hold and are gone (a scoreboard row's name is a key now). What stays
    keeps the schema and limit rules seeing every store: the two tables are
    assigned whole only as `{}`, `BOOK` is never indexed by brackets, and no
    table constructor in Crew.lua has a `[...] =` key."""
    t = flow.tokens
    problems: list[str] = []
    for k in range(len(t) - 3):
        if t[k].text != CREW_DB or _is_field(t, k) or t[k + 1].text != ".":
            continue
        if t[k + 2].text not in _CREW_BOOK_TABLES or t[k + 3].text != "=":
            continue
        if _render(t[k + 4 : k + 6]) != "{ }" or not _ends_expression(t, k + 6):
            problems.append(f"line {t[k].line}: a book table assigned other than {{}}")
    for k in range(len(t) - 1):
        if t[k].text == CREW_DB and t[k + 1].text == "[" and not _is_field(t, k):
            # Fix round 5: never, read or written (`BOOK["players"] =
            # fake` would get past every rule on `.players`).
            problems.append(f"line {t[k].line}: {CREW_DB} indexed through brackets")
        opener = flow.opener[k]
        bracket_key = (
            t[k].kind == "op"
            and t[k].text == "["
            and opener is not None
            and t[opener].text == "{"
            and t[k - 1].text in {"{", ","}
        )
        if bracket_key:
            problems.append(f"line {t[k].line}: a table constructor with a [...] key")
        if (
            t[k].text in _CREW_BOOK_TABLES
            and t[k + 1].text == "="
            and t[k - 1].text in {"{", ","}
            and (_render(t[k + 2 : k + 4]) != "{ }" or t[k + 4].text not in {",", "}"})
        ):
            problems.append(f"line {t[k].line}: a book table built other than {{}}")
    return problems


def _crew_constant_problems(flow: _Flow, name: str, decl: str) -> list[str]:
    """`name` is exactly one file-level `decl`, never assigned again,
    declared nowhere else (no local, parameter or loop variable of that
    name)."""
    t = flow.tokens
    n = len(decl.split(" "))
    at = [k for k in range(len(t) - n + 1) if _render(t[k : k + n]) == decl]
    problems = []
    if len(at) != 1 or flow.function_at(at[0]) is not None or not _ends_expression(t, at[0] + n):
        problems.append(f"{name} is not one file-level `{decl}`")
    if len(flow.decls.get(name, [])) != 1:
        problems.append(f"{name} is declared more than once")
    if any(r.rstrip(".") == name for r, _ in flow.rebound):
        problems.append(f"{name} is assigned again")
    return problems


def _crew_schema_problems(flow: _Flow, pins: _CrewPins) -> list[str]:
    """Follow-up (M19-10 security review): the file's schema constant is
    exactly one file-level `local CREW_SCHEMA = <n>` (CREW_SCHEMA_DECL at
    schema 2), never assigned again, declared nowhere else."""
    return _crew_constant_problems(flow, CREW_SCHEMA_NAME, pins.decl)


def _crew_write_problems(flow: _Flow, pins: _CrewPins) -> list[str]:
    """Follow-up (M19-10 security review): every store into a table field
    or index (`x.f = ...`, `x[k] = ...`, the book's included) is made behind
    the gate (whose `crewOn` tests the schema), in `ns.CrewCommand` (whose
    pinned head refuses a book of another schema), in the pinned
    `crewLoaded`, or into a table built by a constructor in the same
    function. So no other function can write into a book, of any schema.
    M20-04: at schema 2 also in the pinned migration (CREW_MIGRATE), which
    stores the new number and an empty `matches` into a schema 1 book only.
    Globals and `ns` are already refused elsewhere."""
    t = flow.tokens
    problems = []
    ungated = {pins.probe} | ({CREW_MIGRATE} if pins.schema == 2 else set())
    for root, ts in flow.rebound:
        if not root.endswith("."):
            continue
        base = root[:-1]
        fn = flow.function_at(ts)
        head = fn.head if fn else None
        if head is not None and (
            _crew_gated(flow, head)
            or flow.labels.get(head) == CREW_COMMAND
            or _render(t[head - 1 : flow.block_end(head) + 1]) in ungated
        ):
            continue
        decl = flow.resolve(base, ts)
        fresh = (
            decl is not None
            and fn is not None
            and any(
                [t[p].text, t[p + 1].text, t[p + 2].text, t[p + 3].text]
                == ["local", base, "=", "{"]
                and flow.function_at(p) is fn
                and p < decl.start <= flow.pair[p + 3] + 2
                for p in range(fn.start, fn.stop - 3)
            )
            and not any(r == base and flow.resolve(base, s) is decl for r, s in flow.rebound)
        )
        if not fresh:
            problems.append(
                f"line {t[ts].line}: {base} written outside the gate and the slash handler"
            )
    return problems


def _crew_schema_2_problems(flow: _Flow) -> list[str]:
    """M20-04 (docs/LAB_PLAN.md §21.1, §21.2): what a schema 2 Crew.lua keeps,
    beside M19's rules and the pins of CREW_PINS_2:
    - the limits: CREW_LIMITS, each one file-level constant;
    - the migration: `crewMigrate` called only where its two pins call it
      (in `crewLoaded`, for a book that is on, and in the slash handler's
      head, for the word `on`);
    - the scoreboard calls behind the gate, through `pcall`, their values
      held to the secret test (`_crew_score_problems`, M20-11);
    - the near-full line: `warned` stored once, as `<book>.warned = <local>`
      right before the line is printed, in a gated function, under an `if`
      whose condition compares with NEAR_FULL_SECONDS and holds
      NEAR_FULL_PERCENT, MAX_PLAYERS and MAX_MATCHES;
    - `matched` read and written before a counter is raised, and the oldest
      match dropped before one is added (`_crew_match_problems`, M20-11);
      `matches` created only empty in a constructor; `schema` stored only by
      the migration, or as `schema = CREW_SCHEMA` in a constructor.
    M20-11 (owner decision, 2026-11-20) removed the probe's pinned stores
    and helpers and the refusal of the per-player counters, `matched` and
    `matches` entries: they served what the book may hold."""
    t = flow.tokens
    problems: list[str] = []
    for name, decl in CREW_LIMITS.items():
        problems += _crew_constant_problems(flow, name, decl)

    pinned_texts = {**CREW_PINNED, "crewLoaded": CREW_PROBE}

    def pinned_function(k: int, names: set[str]) -> bool:
        """Is token k inside one of the named pinned functions, as pinned (a
        function literal handed to `pcall(` stands for the one holding it)?"""
        fn = flow.function_at(k)
        while fn is not None and _pcall_literal(t, fn.head):
            fn = flow.function_at(fn.head - 1)
        name = flow.labels.get(fn.head) if fn is not None else None
        if fn is None or name not in names:
            return False
        return _render(t[fn.head - 1 : flow.block_end(fn.head) + 1]) == pinned_texts[name]

    def type_test(k: int) -> bool:
        """Is the name at token k the whole argument of `type(...)`, possibly
        with one `.field`?"""
        if k < 2 or t[k - 1].text != "(" or t[k - 2].text != "type":
            return False
        close = flow.pair.get(k - 1)
        return close == k + 1 or (close == k + 3 and t[k + 1].text == ".")

    head_end: dict[int, int] = {
        h: h + len(CREW_COMMAND_HEAD.split(" "))
        for h, lab in flow.labels.items()
        if lab == CREW_COMMAND
    }

    def in_head(k: int) -> bool:
        return any(h <= k < e for h, e in head_end.items())

    # The migration's callers.
    for k, x in enumerate(t):
        if x.kind != "name" or x.text != "crewMigrate" or _is_field(t, k):
            continue
        if t[k - 1].text == "function" and t[k - 2].text == "local":
            continue
        if not (pinned_function(k, {"crewLoaded"}) or in_head(k)):
            problems.append(f"line {x.line}: crewMigrate used outside its two pinned calls")

    # M20-11 (owner decision, 2026-11-20): the probe's pins, its two pinned
    # stores and "type names only" are gone, and so is "the scoreboard calls
    # only in the pinned helpers". What stays is about the client: every
    # scoreboard call behind the gate, through `pcall`, after a test that it
    # exists; and what a call returns held to the secret test
    # (`_crew_score_problems`).
    problems += _crew_score_problems(flow, type_test)
    # The near-full line and `warned`.
    warned = [
        k
        for k in range(1, len(t) - 2)
        if t[k].text == "warned" and t[k - 1].text == "." and t[k + 1].text in {"=", ","}
    ]
    if len(warned) != 1:
        problems.append(f"warned is stored {len(warned)} times, not once")
    for w in warned:
        k = w - 2
        shape = (
            t[k].text == CREW_DB
            and not _is_field(t, k)
            and t[w + 1].text == "="
            and t[w + 2].kind == "name"
            and t[w + 3].text in {"say", "print"}
            and t[w + 4].text == "("
            and flow.statement_level(k)
        )
        fn = flow.function_at(w)
        if not shape or fn is None or not _crew_gated(flow, fn.head):
            problems.append(
                f"line {t[w].line}: warned is not stored as `{CREW_DB}.warned = <local>` "
                "right before the line is printed, behind the gate"
            )
            continue
        conds = [
            b
            for b in flow.blocks
            if b.kind in {"if", "elseif"} and b.cond is not None and b.start <= w < b.stop
        ]
        cond = max(conds, key=lambda b: b.start).cond if conds else None
        words = [x.text for x in t[cond[0] : cond[1]]] if cond else []
        compared = any(
            words[i] == "NEAR_FULL_SECONDS"
            and (
                (i > 0 and words[i - 1] in {">=", ">", "<", "<="})
                or (i + 1 < len(words) and words[i + 1] in {">=", ">", "<", "<="})
            )
            for i in range(len(words))
        )
        rendered = _render(t[cond[0] : cond[1]]) if cond else ""
        shares = all(
            f"{limit} * NEAR_FULL_PERCENT" in rendered for limit in ("MAX_PLAYERS", "MAX_MATCHES")
        )
        if not compared or not shares:
            problems.append(
                f"line {t[w].line}: the near-full line is not under a test of the seven days "
                "and of 90% of either limit"
            )

    # Fix round 2 (code review): the book assigned whole only from a table
    # constructor written in place, so every key a new book holds is one the
    # constructor-key rules below see (no local book, call or expression).
    for k in range(len(t) - 2):
        if t[k].text != CREW_DB or _is_field(t, k) or t[k + 1].text != "=":
            continue
        if (
            t[k - 1].text == ","
            or t[k + 2].text != "{"
            or not _ends_expression(t, flow.pair[k + 2] + 1)
        ):
            problems.append(f"line {t[k].line}: {CREW_DB} assigned other than from a constructor")
    # Fields. M20-11: the counters and `matches` are written now; what stays
    # is the limit on `matches`, `matched` before the counters
    # (`_crew_match_problems`), `warned` and `schema`.
    for k, x in enumerate(t):
        if x.kind == "string" and t[k - 1].text == "[" and t[k + 1].text == "]":
            if x.text in CREW_COUNTERS | {"matched", "matches", "warned", "schema"}:
                problems.append(f"line {x.line}: {x.text!r} as a bracket key")
            continue
        if x.kind != "name" or k + 4 >= len(t):
            continue  # the last tokens of a file are `... end`
        field = t[k - 1].text == "."
        key = t[k - 1].text in {"{", ","} and t[k + 1].text == "=" and t[k + 2].text != "="
        if key and flow.opener[k] is not None and t[flow.opener[k]].text != "{":
            key = False  # a multiple assignment's name, not a constructor key
        stored = field and t[k + 1].text in {"=", ","} and flow.statement_level(k)
        if x.text == "matches" and key:
            if _render(t[k + 2 : k + 4]) != "{ }" or t[k + 4].text not in {",", "}"}:
                problems.append(f"line {x.line}: matches created other than empty")
        elif x.text == "warned" and key:
            problems.append(f"line {x.line}: warned set in a constructor")
        elif x.text == "schema" and stored and not pinned_function(k, {"crewMigrate"}):
            problems.append(f"line {x.line}: schema stored outside the migration")
        elif (
            x.text == "schema"
            and key
            and (t[k + 2].text != CREW_SCHEMA_NAME or t[k + 3].text not in {",", "}"})
        ):
            problems.append(f"line {x.line}: schema set other than to {CREW_SCHEMA_NAME}")
    problems += _crew_match_problems(flow)
    problems += _crew_limit_problems(flow)
    return problems


# M20-11 (§21.2): the four per-player counters, raised once per match.
CREW_COUNTERS = frozenset({"allied", "faced", "allied_won", "faced_won"})
# The one way a store into `matches` keeps the limit: the oldest dropped first.
CREW_MATCH_DROP = "while # {list} >= MAX_MATCHES do table . remove ( {list} , 1 ) end"


def _crew_inner_block(flow: _Flow, fn: _Block, k: int) -> bool:
    """Is token k inside a block (an if, a loop, ...) within function fn?"""
    return any(
        b.kind != "function" and fn.start <= b.head and b.start <= k < b.stop for b in flow.blocks
    )


def _crew_match_problems(flow: _Flow) -> list[str]:
    """M20-11 (§21.2, "Once per match"; the limit of 500):
    - every function that raises a counter (CREW_COUNTERS, as a field store)
      first reads `<book>.matched` and then stores `<book>.matched = {`, as
      one of its own statements (not inside an `if` or a loop), before its
      first counter store; so a repeated event or a /reload counts a match
      once;
    - `matches` is stored into only through a local bound as `local L =
      <book>.matches`, in a function that holds CREW_MATCH_DROP for L before
      the store; `<book>.matches` is otherwise assigned only `{}`."""
    t = flow.tokens
    problems: list[str] = []
    by_fn: dict[int, list[int]] = {}
    for k in range(1, len(t) - 1):
        x = t[k]
        if x.kind != "name" or x.text not in CREW_COUNTERS or t[k - 1].text != ".":
            continue
        if t[k + 1].text != "=" or not flow.statement_level(k):
            continue
        fn = flow.function_at(k)
        if fn is None:
            problems.append(f"line {x.line}: {x.text} raised at file level")
            continue
        by_fn.setdefault(fn.head, []).append(k)
    for head, stores in by_fn.items():
        fn = flow.functions[head]
        first = min(stores)
        marks = [
            k
            for k in range(fn.start, first)
            if _render(t[k : k + 5]) == f"{CREW_DB} . matched = {{"
            and flow.function_at(k) is fn
            and not _crew_inner_block(flow, fn, k)
        ]
        reads = [
            k
            for k in range(fn.start, marks[0] if marks else first)
            if _render(t[k : k + 3]) == f"{CREW_DB} . matched"
            and t[k + 3].text != "="
            and _crew_marker_decides(flow, fn, k, marks[0] if marks else first)
        ]
        if not marks or not reads:
            problems.append(
                f"line {t[first].line}: a counter is raised without `matched` read and then "
                "written first, outside any if or loop"
            )
    lists: dict[int, str] = {}
    for p in range(len(t) - 6):
        if (
            t[p].text == "local"
            and t[p + 2].text == "="
            and _render(t[p + 3 : p + 6]) == f"{CREW_DB} . matches"
            and _ends_expression(t, p + 6)
        ):
            decl = flow.resolve(t[p + 1].text, p + 6)
            if decl is not None:
                lists[id(decl)] = t[p + 1].text
    for root, ts in flow.rebound:
        base = root.rstrip(".")
        if root == f"{CREW_DB}." and t[ts + 2].text == "matches" and t[ts + 3].text != "=":
            problems.append(f"line {t[ts].line}: a store into {CREW_DB}.matches not through a list")
        if (
            root == f"{CREW_DB}."
            and t[ts + 2].text == "matches"
            and t[ts + 3].text == "="
            and (_render(t[ts + 4 : ts + 6]) != "{ }" or not _ends_expression(t, ts + 6))
        ):
            problems.append(f"line {t[ts].line}: {CREW_DB}.matches assigned other than {{}}")
        if not root.endswith(".") or base == CREW_DB:
            continue
        decl = flow.resolve(base, ts)
        if decl is None or id(decl) not in lists:
            continue
        fn = flow.function_at(ts)
        drop = CREW_MATCH_DROP.format(list=base)
        n = len(drop.split(" "))
        held = fn is not None and any(_render(t[k : k + n]) == drop for k in range(fn.start, ts))
        if not held:
            problems.append(f"line {t[ts].line}: a match stored without the oldest dropped first")
    return problems


def _crew_marker_decides(flow: _Flow, fn: _Block, k: int, before: int) -> bool:
    """Fix round 1 (code review, #664): the `matched` read at token k decides
    whether the match is counted: it sits, directly or through a local bound
    once to it (`local m = <book>.matched`), in the condition of an `if`
    of fn, before `before`, whose whole body is `return`."""
    t = flow.tokens
    names: set[int] = set()
    if t[k - 1].text == "=" and t[k - 2].kind == "name" and t[k - 3].text == "local":
        decl = flow.resolve(t[k - 2].text, k + 3)
        if decl is not None and not any(
            r.rstrip(".") == t[k - 2].text and flow.resolve(t[k - 2].text, ts) is decl
            for r, ts in flow.rebound
        ):
            names.add(id(decl))
    for b in flow.blocks:
        if b.kind != "if" or b.cond is None or not fn.start <= b.head < before:
            continue
        if flow.function_at(b.head) is not fn or _render(t[b.start : b.stop]) != "return":
            continue
        cs, ce = b.cond
        if cs <= k < ce:
            return True
        if any(
            t[q].kind == "name" and id(flow.resolve(t[q].text, q)) in names for q in range(cs, ce)
        ):
            return True
    return False


def _crew_limit_problems(flow: _Flow) -> list[str]:
    """Fix round 1 (code review, #664), the 20,000 limit:
    - a new player entry (`<players>[K] = {`, the book's `players` written
      out or through a local bound to it) is stored only inside an `if`
      whose condition calls `makeRoom ( )`;
    - a player dropped (`<players>[X] = nil`) is named by a local X declared
      bare or as nil and assigned only nil or the key of a
      `for K ... in pairs(<players>)` loop around the assignment, so the drop
      takes a real entry."""
    t = flow.tokens
    problems: list[str] = []
    alias: set[int] = set()
    for q in range(len(t) - 5):
        if (
            t[q].text == "local"
            and t[q + 2].text == "="
            and _render(t[q + 3 : q + 6]) == f"{CREW_DB} . players"
            and _ends_expression(t, q + 6)
        ):
            decl = flow.resolve(t[q + 1].text, q + 6)
            if decl is not None:
                alias.add(id(decl))

    def players_at(k: int) -> int | None:
        """If the book's players table starts at token k, the index after it."""
        x = t[k]
        if x.kind != "name" or _is_field(t, k):
            return None
        if x.text == CREW_DB and _render(t[k + 1 : k + 3]) == ". players":
            return k + 3
        if id(flow.resolve(x.text, k)) in alias:
            return k + 1
        return None

    def walk_key(name: str, at: int) -> bool:
        for b in flow.blocks:
            if b.kind != "loop" or t[b.head].text != "for" or not b.head < at < b.stop:
                continue
            rest = next((q for q in range(b.head, b.start) if t[q].text == "in"), None)
            if rest is None or t[b.head + 1].text != name or t[rest + 1].text != "pairs":
                continue
            after = players_at(rest + 3)
            if after is not None and t[after].text == ")":
                return True
        return False

    for k in range(len(t) - 3):
        after = players_at(k)
        if after is None or t[after].text != "[":
            continue
        close = flow.pair[after]
        if t[close + 1].text != "=" or not flow.statement_level(k):
            continue
        value = t[close + 2]
        if value.text == "{":
            guarded = any(
                b.kind in {"if", "elseif"}
                and b.cond is not None
                and b.start <= k < b.stop
                and "makeRoom ( )" in _render(t[b.cond[0] : b.cond[1]])
                for b in flow.blocks
            )
            if not guarded:
                problems.append(f"line {t[k].line}: a new player added without makeRoom()")
        elif value.text == "nil":
            inner = t[after + 1 : close]
            name = inner[0].text if len(inner) == 1 and inner[0].kind == "name" else None
            decl = flow.resolve(name, after + 1) if name else None
            ok = decl is not None and name is not None
            if ok:
                for r, ts in flow.rebound:
                    if r != name or flow.resolve(name, ts) is not decl:
                        continue
                    v = t[ts + 2]
                    if not (
                        (v.text == "nil" or (v.kind == "name" and walk_key(v.text, ts)))
                        and _ends_expression(t, ts + 3)
                    ):
                        ok = False
                declared = [
                    q
                    for q in range(len(t) - 2)
                    if t[q].text == "local"
                    and t[q + 1].text == name
                    and any(d is decl for d in flow.decls.get(name, []) if q < d.start <= q + 4)
                ]
                if len(declared) != 1 or not (
                    t[declared[0] + 2].text != "="
                    or (t[declared[0] + 3].text == "nil" and _ends_expression(t, declared[0] + 4))
                ):
                    ok = False
            if not ok:
                problems.append(
                    f"line {t[k].line}: a player dropped who is not an entry of the book"
                )
    return problems


def _crew_score_problems(flow: _Flow, type_test: Callable[[int], bool]) -> list[str]:
    """M20-11 (§21.2; the client, not privacy): every scoreboard name
    (`_crew_scoreboard_name`) is a global, never a local or a string, used
    in a gated function either as a `type()` test or as the first argument
    of `pcall` (or inside a function literal handed to `pcall`). Each name
    bound from `local ok, v = pcall(<scoreboard call>, ...)`, and the key
    and value of a `pairs` walk over such a name, is read only as
    `secret(x)`, `type(x)`, or under an `if` whose condition has `not
    secret(x)` as a top-level conjunct."""
    t = flow.tokens
    problems: list[str] = []
    for name in sorted(n for n in flow.decls if _crew_scoreboard_name(n)):
        problems.append(f"{name} declared as a local")
    bound: list[tuple[str, int]] = []
    for k, x in enumerate(t):
        if x.kind == "string" and _crew_scoreboard_name(x.text):
            problems.append(f"line {x.line}: a scoreboard call named in a string")
        if x.kind != "name" or not _crew_scoreboard_name(x.text) or _is_field(t, k):
            continue
        if flow.resolve(x.text, k) is not None:
            continue
        fn = flow.function_at(k)
        if not _crew_gated(flow, fn.head if fn else None):
            problems.append(f"line {x.line}: {x.text} used outside the gate")
            continue
        first_arg = t[k - 1].text == "(" and t[k - 2].text == "pcall" and not _is_field(t, k - 2)
        if not (type_test(k) or first_arg or (fn is not None and _pcall_literal(t, fn.head))):
            problems.append(f"line {x.line}: {x.text} called other than through pcall")
            continue
        if first_arg and t[k - 3].text == "=":
            names: list[str] = []
            j = k - 4
            while j >= 0 and t[j].kind == "name" and t[j].text not in _KEYWORDS:
                names.insert(0, t[j].text)
                j -= 2 if t[j - 1].text == "," else 1
            # Only a `local` binds here (a stored pcall result is the stored-
            # value rule's); the first name is pcall's own true or false.
            if j >= 0 and t[j].text == "local":
                bound += [(n, flow.pair[k - 1] + 1) for n in names[1:]]
    walked: list[tuple[str, int]] = []
    for name, k in bound:
        decl = flow.resolve(name, k)
        for b in flow.blocks:
            if b.kind != "loop" or t[b.head].text != "for" or b.head < k:
                continue
            rest = next((q for q in range(b.head, b.start) if t[q].text == "in"), None)
            if rest is None or _render(t[rest + 1 : rest + 5]) != f"pairs ( {name} )":
                continue
            if flow.resolve(name, rest + 3) is not decl:
                continue
            for q in range(b.head + 1, rest):
                if t[q].kind == "name":
                    walked.append((t[q].text, b.start))
    for name, k in bound + walked:
        if name == "_":
            continue
        decl = flow.resolve(name, k)
        if decl is None:
            continue
        for u in range(k + 1, len(t)):
            y = t[u]
            if y.kind != "name" or y.text != name or _is_field(t, u):
                continue
            if flow.resolve(name, u) is not decl:
                continue
            if (
                t[u - 1].text == "("
                and t[u - 2].text in {"secret", "type"}
                and t[u + 1].text == ")"
            ):
                continue
            if not _crew_secret_guarded(flow, decl, name, u):
                problems.append(
                    f"line {y.line}: {name} (from the scoreboard) used before the secret test"
                )
    return problems


def _crew_parameter_problems(flow: _Flow, pins: _CrewPins) -> list[str]:
    """No function parameter in Crew.lua is indexed, called, assigned,
    stored in a table or a local, returned, or handed to `pcall`, `xpcall` or
    `ns.Call`: a table or function from another file (or the client) can
    carry nothing out. The pinned helpers are exempt (their text is fixed).
    No function uses `...` (fix round 2, item 1)."""
    t = flow.tokens
    problems: list[str] = []
    for head, fn in flow.functions.items():
        if flow.labels.get(head) in pins.pinned:
            continue
        opening = next(p for p in range(head, len(t)) if t[p].text == "(")
        params = [
            x.text
            for x in t[opening + 1 : flow.pair[opening]]
            if x.kind == "name" or x.text == "..."
        ]
        for name in params:
            if name == "...":
                problems.append(f"line {t[head].line}: a function takes `...`")
                continue
            decl = flow.resolve(name, fn.start)
            for root, ts in flow.rebound:
                if root.rstrip(".") == name and flow.resolve(name, ts) is decl:
                    problems.append(f"line {t[ts].line}: parameter {name} is assigned")
            for k in range(fn.start, fn.stop):
                x = t[k]
                if x.kind != "name" or x.text != name or _is_field(t, k):
                    continue
                if flow.resolve(name, k) is not decl:
                    continue
                problems += _crew_parameter_use(flow, k, name)
    return problems


def _crew_parameter_use(flow: _Flow, k: int, name: str) -> list[str]:
    t = flow.tokens
    where = f"line {t[k].line}: parameter {name}"
    nxt = t[k + 1].text if k + 1 < len(t) else ""
    prev = t[k - 1].text
    o = flow.opener[k]
    opener = t[o].text if o is not None else ""
    in_condition = any(
        b.cond is not None and b.cond[0] <= k < b.cond[1] for b in flow.blocks
    ) or _crew_in_loop_header(flow, k)
    if _crew_in_loop_header(flow, k) and prev == "in":
        return [f"{where} is iterated (called) by a for loop"]
    if (
        _crew_in_loop_header(flow, k)
        and opener not in {"(", "{"}
        and nxt not in {".", "[", ":", "("}
    ):
        return []  # a loop bound (`for i = 1, count do`, `while n > 0 do`)
    if nxt in {".", "[", ":", "("}:
        return [f"{where} is indexed or called"]
    if opener == "{":
        return [f"{where} is stored in a table"]
    if prev in {"=", "return", ","} and opener != "(" and _ends_expression(t, k + 1):
        return [f"{where} is assigned, returned or stored whole"]
    if (prev in {"and", "or"} or nxt in {"and", "or"}) and not in_condition:
        return [f"{where} may be the value of an and/or expression"]
    if opener == "(":
        callee = _render(t[max(0, o - 3) : o])
        if callee.split(" ")[-1] in {"pcall", "xpcall"} or callee.startswith("ns ."):
            # Fix round 4: every ns helper (ns.String, ns.Number, ...)
            # returns its argument or calls it.
            return [f"{where} is handed to {callee.split(' ')[-1]}"]
    return []


def _crew_in_loop_header(flow: _Flow, k: int) -> bool:
    """Is token k in a `for ... do` or `while ... do` header?"""
    return any(b.kind == "loop" and b.head < k < (b.start or k) for b in flow.blocks)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_crew_lua_keeps_its_own_rules(path: Path) -> None:
    bad = _crew_problems(path, path.read_text(encoding="utf-8"))
    assert not bad, bad


def _crew_rules(path: Path, text: str) -> dict[str, list[str]]:
    """Every rule CREW_RELAXED names, run on one source under one file name."""
    tokens = tokenize(text)
    return {
        "UNIT_ARGUMENT": _unit_argument_problems(path, tokens),
        "CONCAT_INDEX": _concat_index_problems(path, tokens) + _concat_value_problems(path, text),
        "SAVED_VARIABLES": _crew_book_problems(path, tokens),
        "SECTION_SCAN": _crew_section_failures(path, text),
        "PAIRS": _pairs_problems(path, tokens),
    }


def _crew_section_failures(path: Path, text: str) -> list[str]:
    """The M11-37 scan's "outside every section" failures for one source,
    written under `path.name` into a temporary folder of its own."""
    with tempfile.TemporaryDirectory(prefix="wowlab-m19-01-") as tmp:
        file = Path(tmp) / path.name
        file.write_text(text, encoding="utf-8")
        scan = _SectionScan([file])
    return [message for rule, message in scan.failures if rule == "outside"]


def test_m19_01_the_relaxation_is_one_table_keyed_on_crew_lua() -> None:
    """Each rule §20.1 lists is relaxed in CREW_RELAXED and nothing else is;
    nothing in NEVER_RELAXED (the sends and the readers no ticket builds) is
    in any entry. M20-13: the privacy relaxations left with their rules."""
    assert set(CREW_RELAXED) == {
        "UNIT_ARGUMENT",
        "CONCAT_INDEX",
        "SAVED_VARIABLES",
        "SECTION_SCAN",
        "PAIRS",
    }
    assert set(_crew_rules(_CREW_PATH, _CREW_SOURCE)) == set(CREW_RELAXED)
    assert set().union(*CREW_RELAXED.values()) & NEVER_RELAXED == set()
    assert set(FORBIDDEN_APIS) == NEVER_RELAXED
    assert not set(FORBIDDEN_APIS) & CREW_NAME_CALLS
    for rule in CREW_RELAXED:
        assert _relaxed(_CREW_PATH, rule) == CREW_RELAXED[rule]
        assert _relaxed(Path("Core.lua"), rule) == frozenset()


def test_m19_01_a_constructed_crew_lua_passes_every_rule() -> None:
    """Constructed Crew.lua (_CREW_SOURCE): each relaxation lets it through,
    and it keeps Crew.lua's own rules."""
    failing = {rule: bad for rule, bad in _crew_rules(_CREW_PATH, _CREW_SOURCE).items() if bad}
    assert not failing, failing
    assert _crew_problems(_CREW_PATH, _CREW_SOURCE) == []


# File names that are not exactly Crew.lua: the addon's own files and near misses.
_NOT_CREW = (
    "Core.lua",
    "Gear.lua",
    "Talents.lua",
    "Customization.lua",
    "Collections.lua",
    "Currencies.lua",
    "Professions.lua",
    "Actions.lua",
    "Identity.lua",
    "crew.lua",
    "CREW.lua",
    "Crew.LUA",
    "MyCrew.lua",
    "Crew2.lua",
    "Crew.lua.lua",
)


def test_m19_01_every_other_file_still_refuses_all_of_it() -> None:
    """The same constructed source under any other file name fails every
    relaxed rule, the TOC rule included."""
    passing = {
        name: [rule for rule, bad in _crew_rules(Path(name), _CREW_SOURCE).items() if not bad]
        for name in _NOT_CREW
    }
    passing = {name: rules for name, rules in passing.items() if rules}
    assert not passing, passing
    both = ("WowLabDB", CREW_DB)
    toc = [
        n
        for n in _NOT_CREW
        if not _toc_saved_variables_problems(("WowLabCharDB",), both, [Path(n)])
    ]
    assert not toc, toc
    assert {p.name for p in SOURCES} <= set(_NOT_CREW) | {CREW_FILE}, "a new file: add it here"


def test_m19_01_unit_guid_and_the_chat_sends_stay_refused_in_crew_lua() -> None:
    """Constructed: each never-relaxed name, used inside the gate in Crew.lua,
    is still refused, as a call and as a string. M20-13: `UnitGUID` and `os`
    are no longer among them (ADR-0026, note of 2026-11-20)."""
    missed = []
    for name in sorted(NEVER_RELAXED):
        call = _CREW_SOURCE.replace(_CREW_READ, f"{_CREW_READ}    local x = {name}(unit)\n")
        string = _CREW_SOURCE.replace(_CREW_READ, f'{_CREW_READ}    local x = "{name}"\n')
        for text in (call, string):
            assert text != _CREW_SOURCE
            if not _forbidden_api_problems(_CREW_PATH, tokenize(text)):
                missed.append((name, text is call))
    assert not missed, missed


def test_m19_01_the_chat_sends_are_refused_in_every_file() -> None:
    """Constructed: each chat send named in a source fails the forbidden-API
    rule under every file name."""
    sends = ("SendChatMessage", "BNSendWhisper", "ChatFrame_OpenChat", "ChatEdit_InsertLink")
    missed = [
        (name, send)
        for name in (*_NOT_CREW, CREW_FILE)
        for send in sends
        if not _forbidden_api_problems(Path(name), tokenize(f'{send}("constructed")\n'))
    ]
    assert not missed, missed


def _crew_edited(edits: list[tuple[str, str]]) -> str:
    text = _CREW_SOURCE
    for found, replacement in edits:
        assert found in text, found
        text = text.replace(found, replacement, 1)
    return text


def test_m19_01_constructed_gate_edits_fail() -> None:
    missed = [
        tag
        for tag, edits in sorted(_CREW_GATE_CASES.items())
        if not _crew_problems(_CREW_PATH, _crew_edited(edits))
    ]
    assert not missed, missed


# Each also fails the M11-37 scan under its SECTION_SCAN relaxation.
_CREW_SCAN_CASES = (
    "a call at file level",
    "a call in a function without the tests",
    "a closure handed to a timer",
    "a closure handed to coroutine.wrap",
    "the tests in the other order",
)


def test_m19_01_constructed_section_scan_edits_fail() -> None:
    missed = [
        tag
        for tag in _CREW_SCAN_CASES
        if not _crew_section_failures(_CREW_PATH, _crew_edited(_CREW_GATE_CASES[tag]))
    ]
    assert not missed, missed


_CORE_SLASH = "SlashCmdList.WOWLAB = function(message)\n"
_CORE_CREW_CALL = _CORE_SLASH + "    ns.CrewCommand(message)\n"


def _core_edited(replacement: str, found: str = _CORE_SLASH) -> str:
    text = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert found in text, found
    return text.replace(found, replacement, 1)


def _real_core_calls() -> int:
    """How many lines of the real Core.lua name CrewCommand."""
    core = ADDON / "Core.lua"
    tokens = _tokens(core)
    return len({x.line for x in tokens if "CrewCommand" in x.text})


def _core_with_call() -> str:
    """Core.lua holding the one pinned call: the real file once it has it
    (M19-10 adds it), otherwise the real file with it inserted. Fix round 3:
    correct before and after M19-10."""
    if _real_core_calls():
        return (ADDON / "Core.lua").read_text(encoding="utf-8")
    return _core_edited(_CORE_CREW_CALL)


def test_m19_01_the_real_core_lua_calls_the_crew_command_at_most_once() -> None:
    """Fix round 3: none before M19-10, then exactly the one pinned call."""
    core = ADDON / "Core.lua"
    calls = _real_core_calls()
    assert calls <= 1, f"Core.lua names CrewCommand on {calls} lines"
    if calls:
        assert len(_crew_command_call_lines(_Flow(core, _tokens(core)))) == 1


def test_m19_01_the_core_call_tests_hold_once_core_lua_has_the_call() -> None:
    """Fix round 3: the state after M19-10, on a constructed copy of the
    addon whose Core.lua already holds the pinned call. The tests that use
    Core.lua pass on it unchanged, and a second call fails."""
    global ADDON
    real = ADDON
    with tempfile.TemporaryDirectory(prefix="wowlab-m19-01-") as tmp:
        copy = Path(tmp) / "WowLab"
        copy.mkdir()
        for source in real.iterdir():
            if source.is_file():
                (copy / source.name).write_bytes(source.read_bytes())
        core = copy / "Core.lua"
        if not _real_core_calls():  # fix round 5: M19-10 may have added it
            core.write_text(
                core.read_text("utf-8").replace(_CORE_SLASH, _CORE_CREW_CALL, 1), encoding="utf-8"
            )
        ADDON = copy
        try:
            assert _real_core_calls() == 1
            test_m19_01_the_real_core_lua_calls_the_crew_command_at_most_once()
            test_m19_01_only_core_s_slash_handler_calls_the_crew_command()
            test_m19_01_the_whole_section_scan_takes_a_constructed_crew_lua()
            core.write_text(_core_edited(_CORE_CREW_CALL), encoding="utf-8")
            with pytest.raises(AssertionError):
                test_m19_01_the_real_core_lua_calls_the_crew_command_at_most_once()
        finally:
            ADDON = real


def test_m19_01_only_core_s_slash_handler_calls_the_crew_command() -> None:
    """Fix round 2, item 2. Constructed: Core.lua with the one pinned call
    passes; the call anywhere else, in another shape, or twice, fails (each
    refused case adds a call, so it is refused whether or not the real file
    already holds one)."""
    core = Path("Core.lua")
    assert _crew_command_call_problems(core, tokenize(_core_with_call())) == []
    refused = {
        "from Gear.lua": (Path("Gear.lua"), 'ns.CrewCommand("on")\n'),
        "from Identity.lua, as a string": (
            Path("Identity.lua"),
            'local f = ns.Fn(ns, "CrewCommand")\n',
        ),
        "with a literal": (core, _core_edited(_CORE_SLASH + '    ns.CrewCommand("on")\n')),
        "with another value": (core, _core_edited(_CORE_SLASH + "    ns.CrewCommand(ns)\n")),
        "twice": (core, _core_edited(_CORE_CREW_CALL + "    ns.CrewCommand(message)\n")),
        "sharing its line": (
            core,
            _core_edited(_CORE_SLASH + "    ns.CrewCommand(message) say(1)\n"),
        ),
        "outside the slash handler": (
            core,
            _core_edited(
                "local function early(message)\n    ns.CrewCommand(message)\nend\n\n" + _CORE_SLASH
            ),
        ),
        "aliased": (core, _core_edited(_CORE_SLASH + "    local f = ns.CrewCommand\n")),
    }
    missed = [
        tag
        for tag, (path, text) in refused.items()
        if not _crew_command_call_problems(path, tokenize(text))
    ]
    assert not missed, missed


def _crew_full_scan(crew: str, core: str | None = None) -> list[str]:
    """The whole M11-37 scan on the real addon plus a constructed Crew.lua
    (and, if given, a constructed Core.lua), in a temporary folder."""
    with tempfile.TemporaryDirectory(prefix="wowlab-m19-01-") as tmp:
        files = []
        for source in SOURCES:
            path = Path(tmp) / source.name
            text = (
                core
                if core is not None and source.name == "Core.lua"
                else source.read_text("utf-8")
            )
            path.write_text(text, encoding="utf-8")
            files.append(path)
        path = Path(tmp) / CREW_FILE
        path.write_text(crew, encoding="utf-8")
        scan = _SectionScan([*files, path])
    return [f"{rule}: {message}" for rule, message in scan.failures]


def test_m19_01_the_whole_section_scan_takes_a_constructed_crew_lua() -> None:
    """Control (fix round 2, items 2 and 4): the real addon, Core.lua with the
    pinned call, and the constructed Crew.lua with its three ns.On calls
    pass the M11-37 scan, pinned callers included, with no table edited."""
    assert _crew_full_scan(_CREW_SOURCE, _core_with_call()) == []


def test_m19_01_the_whole_section_scan_refuses_crew_lua_edits() -> None:
    """Constructed: an ungated registration, a roster event at load, and the
    typed text used twice in Core.lua each fail the scan."""
    core = _core_with_call()
    cases = {
        "a registration in an ungated function": (
            _crew_edited(_CREW_GATE_CASES["a registration in an ungated function"]),
            core,
        ),
        "a roster event registered at load": (
            _crew_edited(_CREW_GATE_CASES["a roster event registered at load"]),
            core,
        ),
        "the typed text used again in Core.lua": (
            _CREW_SOURCE,
            _core_edited(_CORE_CREW_CALL + "    ns.CrewCommand(message)\n"),
        ),
        "the typed text handed on with no Crew.lua rule": (
            _CREW_SOURCE,
            _core_edited(_CORE_SLASH + "    local spare = message\n    ns.CrewCommand(spare)\n"),
        ),
    }
    missed = [
        tag for tag, (crew, core_text) in cases.items() if not _crew_full_scan(crew, core_text)
    ]
    assert not missed, missed


def test_m19_01_constructed_calls_behind_the_gate_pass() -> None:
    """Controls: a raid member's level read inside the gate and tested, and
    the base source's pcall literal, slash handler and book stores, are
    accepted by Crew.lua's rules and by the M11-37 scan."""
    inside = _CREW_READ + (
        '    local more = "raid" .. 2\n    local lvl = UnitLevel(more)\n'
        f"    if not secret(lvl) and lvl > 0 then\n        {CREW_DB}.more = lvl\n    end\n"
    )
    text = _crew_edited([(_CREW_READ, inside)])
    assert _crew_problems(_CREW_PATH, text) == []
    assert _crew_section_failures(_CREW_PATH, text) == []
    assert _crew_rules(_CREW_PATH, text)["UNIT_ARGUMENT"] == []


def _std_without(text: str, names: set[str]) -> str:
    """wow_client.yml text with each `  NAME:` entry (and its indented
    lines) of `names` left out."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        match = re.match(r"^  ([A-Za-z_][\w.]*):\s*$", line)
        if match:
            skipping = match.group(1) in names
        elif not line.startswith("    "):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


def _readme_without(text: str, names: set[str]) -> str:
    return "".join(
        line
        for line in text.splitlines(keepends=True)
        if not any(line.startswith(f"| `{name}` |") for name in names)
    )


def _crew_installed_failures(crew: str, source: Path | None = None) -> list[str]:
    """Install `crew` as Crew.lua in a constructed copy of lab/addon (the
    TOC listing it and the book, Core.lua with the pinned call, the std file
    and README declaring the client names it uses, as M19-10 must), load a
    fresh copy of this grader pointed at it, and run every test in it (each
    source for a `path` test). The names of the tests that fail. `source` is
    the lab/addon folder copied (the tree's own by default).

    Follow-up (M19-10): when the copy already holds a real Crew.lua, the std
    file and README names only that file uses (declared, referenced by no
    other source) are taken out before `crew` replaces it, as the names
    `crew` uses are added; so the result does not depend on which client
    names the real file happens to call. A name nothing uses stays, and
    still fails."""
    import importlib.util
    import inspect
    import shutil
    import sys

    with tempfile.TemporaryDirectory(prefix="wowlab-m19-01-") as tmp:
        # The same layout under a temporary root, as the review probes use,
        # so paths reported relative to ROOT read the same.
        root = Path(tmp) / "lab" / "addon"
        shutil.copytree(source or ADDON_ROOT, root)
        addon = root / "WowLab"
        real_only: set[str] = set()
        if (addon / CREW_FILE).is_file():
            real = _tokens(addon / CREW_FILE)
            others: set[str] = set()
            for path in sorted(addon.glob("*.lua")):
                if path.name != CREW_FILE:
                    tokens = _tokens(path)
                    others |= _fn_lookups(tokens) | _dotted_references(tokens)
            real_refs = _fn_lookups(real) | _dotted_references(real)
            real_only = (_std_globals(root / "wow_client.yml") & real_refs) - others - OWN_GLOBALS
        std_text = _std_without((root / "wow_client.yml").read_text(encoding="utf-8"), real_only)
        (root / "wow_client.yml").write_text(std_text, encoding="utf-8")
        readme_text = _readme_without((root / "README.md").read_text(encoding="utf-8"), real_only)
        (root / "README.md").write_text(readme_text, encoding="utf-8")
        (addon / CREW_FILE).write_text(crew, encoding="utf-8")
        (addon / "Core.lua").write_text(_core_with_call(), encoding="utf-8")
        toc = (addon / "WowLab.toc").read_text(encoding="utf-8")
        if CREW_FILE not in toc:
            toc = toc.replace(
                "## SavedVariables: WowLabDB\n", f"## SavedVariables: WowLabDB, {CREW_DB}\n"
            )
            toc = toc.rstrip("\n") + f"\n{CREW_FILE}\n"
        (addon / "WowLab.toc").write_text(toc, encoding="utf-8")
        scan = _SectionScan(sorted(addon.glob("*.lua")))
        used = {re.split(r"[.:]", r.api)[0] for r in scan.refs if r.client and r.file == CREW_FILE}
        declared = _std_globals(root / "wow_client.yml")
        std = (root / "wow_client.yml").read_text(encoding="utf-8").rstrip("\n") + "\n"
        readme = (root / "README.md").read_text(encoding="utf-8").rstrip("\n") + "\n\n"
        if CREW_DB not in declared:
            std += f"  {CREW_DB}:\n    property: full-write\n"
        for name in sorted(used - declared):
            std += f"  {name}:\n    property: read-only\n"
            readme += f"| `{name}` | constructed for M19-01, {VERIFY_STATUS} |\n"
        (root / "wow_client.yml").write_text(std, encoding="utf-8")
        (root / "README.md").write_text(readme, encoding="utf-8")

        spec = importlib.util.spec_from_file_location("m19_01_installed_grader", __file__)
        assert spec is not None and spec.loader is not None
        grader = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = grader  # dataclasses resolve annotations through it
        try:
            spec.loader.exec_module(grader)
            grader.ROOT = Path(tmp)
            grader.ADDON_ROOT = root
            grader.ADDON = addon
            grader.TOC = addon / "WowLab.toc"
            grader.STD = root / "wow_client.yml"
            grader.README = root / "README.md"
            grader.SOURCES = sorted(addon.glob("*.lua"))
            grader.ACTIONS = addon / "Actions.lua"
            failed = []
            for name, fn in inspect.getmembers(grader, inspect.isfunction):
                if not name.startswith("test_") or name in _CREW_INSTALLED_SKIP:
                    continue
                params = inspect.signature(fn).parameters
                for args in [(p,) for p in grader.SOURCES] if "path" in params else [()]:
                    try:
                        fn(*args)
                    except AssertionError as error:
                        failed.append(f"{name}{[a.name for a in args]}: {str(error)[:300]}")
            return failed
        finally:
            del sys.modules[spec.name]


# Not run on the installed copy: this test itself, and the tests that build
# installed copies of their own.
_CREW_INSTALLED_SKIP = frozenset(
    {
        "test_m19_01_the_constructed_crew_lua_passes_the_whole_grader_when_installed",
        "test_m19_01_an_installed_crew_lua_that_breaks_a_whole_addon_rule_fails",
        "test_m19_01_the_installed_run_drops_names_only_a_real_crew_lua_uses",
        "test_m19_01_the_installed_run_keeps_a_name_nothing_uses",
        "test_m20_04_the_schema_1_source_passes_the_whole_grader_when_installed",
    }
)


def test_m19_01_the_constructed_crew_lua_passes_the_whole_grader_when_installed() -> None:
    """Fix round 5: the constructed Crew.lua, pinned helpers and all, passes
    every test in this file, the unrelaxed whole-addon checks (M11-13's
    stored-value check, the M11-37 scan, the M13-05 rules) included, when
    installed as M19-10 will install the real one. So no pin is unpassable."""
    failed = _crew_installed_failures(_CREW_SOURCE)
    assert not failed, failed


def test_m19_01_an_installed_crew_lua_that_breaks_a_whole_addon_rule_fails() -> None:
    """Control for the test above: a Crew.lua storing an unchecked client
    value fails the installed run."""
    # M20-04: the store sits in the gated readGroup (the schema 2 probe
    # handler no longer holds `<book>.probe = probe`), so only the
    # whole-addon check refuses it.
    bad = _crew_edited([(_CREW_READ, f"{_CREW_READ}    {CREW_DB}.raw = GetInstanceInfo()\n")])
    assert _crew_problems(_CREW_PATH, bad) == []
    failed = _crew_installed_failures(bad)
    assert any(f.startswith("test_every_stored_value_is_type_checked") for f in failed), failed


def _pretend_tree(tmp: str, crew_extra: str, name: str) -> Path:
    """A copy of lab/addon with a pretend real Crew.lua (the constructed
    source plus `crew_extra`) and `name` declared in the std file and README,
    as M19-10's tree declares the names its Crew.lua calls."""
    import shutil

    source = Path(tmp) / "source" / "addon"
    shutil.copytree(ADDON_ROOT, source)
    (source / "WowLab" / CREW_FILE).write_text(_CREW_SOURCE + crew_extra, encoding="utf-8")
    std = source / "wow_client.yml"
    std.write_text(
        std.read_text("utf-8").rstrip("\n") + f"\n  {name}:\n    property: read-only\n", "utf-8"
    )
    readme = source / "README.md"
    readme.write_text(
        readme.read_text("utf-8").rstrip("\n") + f"\n\n| `{name}` | {VERIFY_STATUS} |\n", "utf-8"
    )
    return source


def test_m19_01_the_installed_run_drops_names_only_a_real_crew_lua_uses() -> None:
    """Follow-up (M19-10): a name the real Crew.lua declares and calls, and
    no other file uses, is taken out when the constructed source replaces it,
    so the installed run passes whichever client names the real file uses."""
    with tempfile.TemporaryDirectory(prefix="wowlab-m19-01-") as tmp:
        source = _pretend_tree(tmp, "local spare = CrewOnlyThing\n", "CrewOnlyThing")
        assert _crew_installed_failures(_CREW_SOURCE, source) == []


def test_m19_01_the_installed_run_keeps_a_name_nothing_uses() -> None:
    """Control: a declared name no file uses, the real Crew.lua included,
    is not taken out, so the installed run still fails on it."""
    with tempfile.TemporaryDirectory(prefix="wowlab-m19-01-") as tmp:
        source = _pretend_tree(tmp, "", "NothingUsesThis")
        failed = _crew_installed_failures(_CREW_SOURCE, source)
        assert any(f.startswith("test_every_std_global_is_used") for f in failed), failed


def test_m19_01_a_fresh_table_may_be_written_outside_the_gate() -> None:
    """Control (follow-up, M19-10 security review): an ungated helper may
    fill a table it built itself; only stores into anything else need the
    gate or the slash handler."""
    extra = "local function fresh()\n    local t = {}\n    t.x = 1\n    return t\nend\n"
    text = _crew_edited([(_CREW_TAIL, _CREW_TAIL + extra)])
    assert _crew_problems(_CREW_PATH, text) == []
    rebound = extra.replace("    t.x = 1\n", "    t = ns\n    t.x = 1\n")
    assert _crew_problems(_CREW_PATH, _crew_edited([(_CREW_TAIL, _CREW_TAIL + rebound)]))


def test_m19_01_one_file_level_registration_per_core_event_passes() -> None:
    """Control (fix round 3, item 3): the base source registers the probe
    at ADDON_LOADED and the gated pickup at PLAYER_ENTERING_WORLD; a third,
    gated, at PLAYER_LOGOUT is accepted too, by Crew.lua's rules and the whole
    M11-37 scan."""
    text = _crew_edited(
        [
            (
                'ns.On("PLAYER_ENTERING_WORLD", onWorld)\n',
                'ns.On("PLAYER_ENTERING_WORLD", onWorld)\nns.On("PLAYER_LOGOUT", onRoster)\n',
            )
        ]
    )
    assert _crew_problems(_CREW_PATH, text) == []
    assert _crew_full_scan(text, _core_with_call()) == []


def test_m19_01_constructed_relaxations_stay_narrow_in_crew_lua() -> None:
    """Constructed: what each relaxation does not let through, in Crew.lua."""
    # M20-13: the FORBIDDEN_APIS, CLOCK and NAME_FIELDS cases left with
    # their rules (ADR-0026, note of 2026-11-20).
    cases = {
        "UNIT_ARGUMENT": [
            '    local x = UnitClass("party1")\n',
            '    local x = UnitClass("focus")\n',
            '    local x = UnitClass("party" .. 1)\n',
            "    local x = UnitClass(ns.unit)\n",
            "    local x = UnitClass(nil)\n",
            '    local x = "mouseover"\n',
            '    local x = ns.Fn(ns, "UnitClass")\n',
            # Fix round 3, item 2: "NONE" and "none" only as comparands.
            '    local x = UnitName("NONE")\n',
            '    local x = UnitLevel("none")\n',
            '    local x = "none"\n',
            f'    {CREW_DB}.role = "NONE"\n',
            '    local x = f("none", 1)\n',
            '    local x = { "NONE" }\n',
        ],
        "UNIT_ARGUMENT_CONTROL": [
            '    local x = role == "NONE"\n',
            '    local x = "none" ~= kind\n',
        ],
        "CONCAT_INDEX": [
            '    local x = book["Unit" .. "Name"]\n',
            '    local x = book[name .. "@" .. realm]\n',
            '    local k = "Unit" .. "Name"\n    local x = book[k]\n',
            "    local k = name .. realm\n    local x = book[k]\n",
            # Fix round 1 (code review, item 6): the "-" must be a plain
            # operand between two plain names, with nothing else in the index.
            '    local x = book[("-"):rep(0) .. name .. realm]\n',
            '    local k = ("-"):sub(2) .. name\n    local x = book[k]\n',
            '    local x = book[name .. "-" .. realm .. tail]\n',
            '    local x = book[pick(name) .. "-" .. realm]\n',
            '    local x = book[entry.name .. "-" .. realm]\n',
            '    local k = name .. "-" .. realm\n        .. tail\n    local x = book[k]\n',
            '    local k, j = name .. "-" .. realm\n    local x = book[j]\n',
        ],
    }
    rules = {
        "UNIT_ARGUMENT": lambda text: _unit_argument_problems(_CREW_PATH, tokenize(text)),
        "CONCAT_INDEX": lambda text: (
            _concat_index_problems(_CREW_PATH, tokenize(text))
            + _concat_value_problems(_CREW_PATH, text)
        ),
        "PAIRS": lambda text: _pairs_problems(_CREW_PATH, tokenize(text)),
    }
    # Fix round 2, item 3: only the book's two tables, and nothing around them.
    cases["PAIRS"] = [
        f"    for k in pairs({CREW_DB}.players.extra) do\n    end\n",
        f'    for k in pairs({CREW_DB}["players"]) do\n    end\n',
        f"    for k in next, {CREW_DB}.players do\n    end\n    local n = next({CREW_DB}.players)\n",
        f"    local walk = {CREW_DB}.players or {{}}\n    for k in pairs(walk) do\n    end\n",
        f"    local walk = {CREW_DB}.players\n    walk = ns\n    for k in pairs(walk) do\n    end\n",
        "    for k in pairs(ns) do\n    end\n",
    ]
    missed = []
    for rule, lines in cases.items():
        control = rule.endswith("_CONTROL")  # each of these must pass
        for line in lines:
            text = _CREW_SOURCE.replace(_CREW_READ, _CREW_READ + line)
            if bool(rules[rule.removesuffix("_CONTROL")](text)) == control:
                missed.append((rule, line.strip()))
    assert not missed, missed


# M20-04: schema 2 (docs/LAB_PLAN.md §21.1, §21.2) ------------------------------
#
# Source scans; nothing here runs Lua (L3). `_CREW_SOURCE` is now a constructed
# schema 2 Crew.lua (built from `_CREW_SOURCE_1`, M19-01's, by the edits in
# _crew_constructed.py), and every M19-01 test above grades it. The real
# Crew.lua is at schema 1 until M20-05: it declares `local CREW_SCHEMA = 1`, so
# `_crew_pins` grades it with CREW_PINS_1, M19's pins unchanged, and the tests
# below show those still refuse what they refused. TestM2005Activation holds
# the real file to schema 2 behind a strict xfail marker that M20-05 deletes.


def _crew_schema_2_edited(edits: list[tuple[str, str]], base: str = _CREW_SOURCE) -> str:
    text = base
    for found, replacement in edits:
        assert text.count(found) >= 1, found
        text = text.replace(found, replacement, 1)
    return text


def test_m20_04_the_constructed_source_is_schema_2_and_passes() -> None:
    """Control: the constructed schema 2 source is graded with CREW_PINS_2
    and passes Crew.lua's rules and every relaxed rule (the whole-grader run
    is test_m19_01_the_constructed_crew_lua_passes_the_whole_grader_when_installed)."""
    assert _crew_pins(tokenize(_CREW_SOURCE)) is CREW_PINS_2
    assert _crew_problems(_CREW_PATH, _CREW_SOURCE, CREW_PINS_2) == []
    failing = {rule: bad for rule, bad in _crew_rules(_CREW_PATH, _CREW_SOURCE).items() if bad}
    assert not failing, failing


def test_m20_04_each_schema_2_rule_refuses_its_edit_for_its_own_reason() -> None:
    """Constructed: each edit in _CREW_SCHEMA_2_CASES breaks one rule and is
    refused with that rule's problem."""
    missed = {}
    for tag, (edits, reason) in sorted(_CREW_SCHEMA_2_CASES.items()):
        problems = _crew_problems(_CREW_PATH, _crew_schema_2_edited(edits), CREW_PINS_2)
        if not any(reason in p for p in problems):
            missed[tag] = problems[:4]
    assert not missed, missed


def test_m20_04_schema_2_controls_pass() -> None:
    """Controls: what the rules leave alone (a type() test of `matches` or
    of the probe block, a later field read but not written, the winner store
    inside a pcall literal) passes Crew.lua's rules and the M11-37 scan."""
    failing = {}
    for tag, edits in sorted(_CREW_SCHEMA_2_CONTROLS.items()):
        text = _crew_schema_2_edited(edits)
        bad = _crew_problems(_CREW_PATH, text) + _crew_section_failures(_CREW_PATH, text)
        if bad:
            failing[tag] = bad
    assert not failing, failing


def test_m20_04_the_schema_1_source_keeps_m19_s_rules() -> None:
    """Control (the real file's state until M20-05): M19-01's constructed
    schema 1 source is graded with CREW_PINS_1 and passes, as before."""
    assert _crew_pins(tokenize(_CREW_SOURCE_1)) is CREW_PINS_1
    assert _crew_problems(_CREW_PATH, _CREW_SOURCE_1) == []
    failing = {rule: bad for rule, bad in _crew_rules(_CREW_PATH, _CREW_SOURCE_1).items() if bad}
    assert not failing, failing


def test_m20_04_m19_s_edits_still_fail_at_schema_1() -> None:
    """Every M19-01 edit case, in its schema 1 form where the text differs
    (_CREW_GATE_CASES_1), is still refused on the schema 1 source: M19's
    checks are unchanged for what they covered."""
    cases = {**_CREW_GATE_CASES, **_CREW_GATE_CASES_1}
    assert set(cases) == set(_CREW_GATE_CASES)
    missed = [
        tag
        for tag, edits in sorted(cases.items())
        if not _crew_problems(_CREW_PATH, _crew_schema_2_edited(edits, _CREW_SOURCE_1))
    ]
    assert not missed, missed


def test_m20_04_the_schema_1_pins_refuse_a_schema_2_book_s_forms() -> None:
    """Constructed: what schema 2 adds is refused while a source declares
    schema 1 (no migration call in the probe handler or the slash handler's
    head, no scoreboard event, no `arena` comparand)."""
    probe = _CONSTRUCTED._CREW_PROBE_SOURCE_1
    cases = {
        "the schema 2 probe handler": [(probe, _CONSTRUCTED._CREW_PROBE_SOURCE_2)],
        "the schema 2 head": [
            (
                _CONSTRUCTED._CREW_HEAD_STRING_TEST,
                _CONSTRUCTED._CREW_HEAD_STRING_TEST + _CONSTRUCTED._CREW_HEAD_MIGRATE,
            ),
            (
                "local function nameAbsent()\n",
                CREW_MIGRATE_SOURCE + "\nlocal function nameAbsent()\n",
            ),
        ],
        "a scoreboard event": [
            (
                '    ns.On("GROUP_ROSTER_UPDATE", onRoster)\nend\n',
                '    ns.On("PVP_MATCH_COMPLETE", onRoster)\nend\n',
            )
        ],
    }
    missed = [
        tag
        for tag, edits in cases.items()
        if not _crew_problems(_CREW_PATH, _crew_schema_2_edited(edits, _CREW_SOURCE_1))
    ]
    arena = _CREW_SOURCE_1.replace(_CREW_READ, _CREW_READ + '    local x = kind == "arena"\n')
    if not _unit_argument_problems(_CREW_PATH, tokenize(arena)):
        missed.append("an arena comparand")
    assert not missed, missed


CREW_MIGRATE_SOURCE: str = _CONSTRUCTED._CREW_MIGRATE_SOURCE


def test_m20_04_the_schema_is_read_from_the_declaration() -> None:
    """`_crew_pins`: schema 1 pins only for `local CREW_SCHEMA = 1` without
    `= 2`; every other source, one declaring both or neither included, gets
    the schema 2 pins."""
    one = tokenize("local CREW_SCHEMA = 1\n")
    both = tokenize("local CREW_SCHEMA = 1\nlocal CREW_SCHEMA = 2\n")
    assert _crew_pins(one) is CREW_PINS_1
    assert _crew_pins(both) is CREW_PINS_2
    assert _crew_pins(tokenize("local CREW_SCHEMA = 3\n")) is CREW_PINS_2
    assert _crew_pins([]) is CREW_PINS_2
    assert CREW_PINS_1.pinned == CREW_PINNED_1 and CREW_PINS_1.events == CREW_EVENTS_1
    # M20-11: the probe's three helpers are no longer pinned.
    assert set(CREW_PINS_2.pinned) == set(CREW_PINNED_1) | {"crewMigrate"}


def test_m20_04_arena_is_only_a_comparand_at_schema_2() -> None:
    """Constructed: in a schema 2 Crew.lua "arena" may be compared with == or
    ~=, nothing else; no unit call takes it, and no other file may use it."""
    lines = {
        '    local x = kind == "arena"\n': True,
        '    local x = "arena" ~= kind\n': True,
        '    local x = "arena"\n': False,
        '    local x = UnitLevel("arena")\n': False,
        '    local x = { "arena" }\n': False,
    }
    missed = []
    for line, passes in lines.items():
        text = _CREW_SOURCE.replace(_CREW_READ, _CREW_READ + line)
        if bool(_unit_argument_problems(_CREW_PATH, tokenize(text))) == passes:
            missed.append(line.strip())
        if not _unit_argument_problems(Path("Core.lua"), tokenize(line)):
            missed.append(f"Core.lua: {line.strip()}")
    assert not missed, missed


def test_m20_04_the_row_walk_is_only_the_pinned_one() -> None:
    """Constructed: `pairs(row)` passes only in a function of a schema 2
    Crew.lua that binds `row` from the newer score call (M20-11: no longer
    only in a pinned pvpSnapshot); in another function, at schema 1 or in
    another file it is refused by the PAIRS rule."""
    assert _pairs_problems(_CREW_PATH, tokenize(_CREW_SOURCE)) == []
    elsewhere = _CREW_SOURCE.replace(
        "local function pvpPass()\n",
        "local function peek(row)\n    for k in pairs(row) do\n    end\nend\n\n"
        "local function pvpPass()\n",
    )
    at_one = _CREW_SOURCE.replace("local CREW_SCHEMA = 2\n", "local CREW_SCHEMA = 1\n")
    refused = {
        "another function": (_CREW_PATH, elsewhere),
        "at schema 1": (_CREW_PATH, at_one),
        "another file": (Path("Core.lua"), _CREW_SOURCE),
    }
    missed = [
        tag for tag, (path, text) in refused.items() if not _pairs_problems(path, tokenize(text))
    ]
    assert not missed, missed


def test_m20_04_the_schema_1_source_passes_the_whole_grader_when_installed() -> None:
    """Control: M19-01's schema 1 source, installed as the real file is until
    M20-05, still passes every test in this file (the schema 1 pins are
    whole, not only Crew.lua's own rules)."""
    failed = _crew_installed_failures(_CREW_SOURCE_1)
    assert not failed, failed


class TestM2005Activation:
    """M20-05 activates this by deleting the marker line only (AGENTS.md):
    then the real Crew.lua must declare `local CREW_SCHEMA = 2` and keep every
    schema 2 rule, and the module-level tests grade it with CREW_PINS_2. It is
    a method, not a module-level test, because the review probes under
    lab/core/tests/review/ and the installed run call every module-level
    `test_` function bare, without its marker, and would read the expected
    failure as a real one."""

    def test_the_real_crew_lua_keeps_the_schema_2_rules(self) -> None:
        crew = ADDON / CREW_FILE
        text = crew.read_text(encoding="utf-8")
        assert _crew_pins(tokenize(text)) is CREW_PINS_2
        bad = _crew_problems(crew, text, CREW_PINS_2)
        assert not bad, bad


# M20-13: schema 5 (ADR-0026, note of 2026-11-20) ---------------------------------
#
# Source scans; nothing here runs Lua (L3). The addon-wide privacy rules are
# gone (the list of calls, the clock, the name fields, the std's identity-like
# names, the crafter blanking, the macro pin and the plain-command rule). What
# stays has a constructed edit below that fails for that rule's own reason.

IDENTITY = ADDON / "Identity.lua"
TALENTS = ADDON / "Talents.lua"
# Identity.lua's schema-5 values: each call is bound to a local that is only
# ever handed, first, to `ownText`, which tests it with `secret` before any
# compare or store (both pinned whole).
_M20_13_OWN_CALLS = (
    "UnitGUID",
    "UnitName",
    "GetRealmName",
    "GetGuildInfo",
    # M20-16: the realm's two fallbacks, under the same rule (and, through
    # _M20_13_OWN_HOME, only in Identity.lua outside Crew.lua).
    "GetNormalizedRealmName",
    "UnitFullName",
)
_M20_13_OWN_TEXT = (
    "local function ownText ( value , api ) "
    'if secret ( value ) then return nil , api .. " was a value the client calls secret" end '
    'if type ( value ) == "string" and value ~= "" then return value , nil end '
    'return nil , api .. " was not a non-empty string" end'
)


def _m20_13_identity_problems(path: Path) -> list[str]:
    """Why a schema-5 identity value in `path` (Identity.lua, or an edited
    copy) may be compared or stored before issecretvalue was asked."""
    t = _tokens(path)
    flow = _Flow(path, t)
    text = _render(t)
    problems: list[str] = []
    if text.count(CREW_SECRET) != 1:
        problems.append("secret is not the pinned one")
    if text.count(_M20_13_OWN_TEXT) != 1:
        problems.append("ownText is not the pinned one")
    for name in ("secret", "ownText"):
        if [n for n, _ in flow.assigned].count(name) != 1:
            problems.append(f"{name} is bound more than once")
    for k, x in enumerate(t):
        if x.kind != "name" or x.text not in _M20_13_OWN_CALLS or _is_field(t, k):
            continue
        if _render(t[k - 2 : k + 2]) == f"type ( {x.text} )":
            continue
        # M20-16: `local v = select ( N , Call ( ... ) )`, one return bound.
        if (
            t[k + 1].text == "("
            and [y.text for y in t[k - 4 : k - 2]] == ["select", "("]
            and t[k - 2].kind == "number"
            and t[k - 1].text == ","
            and t[k - 5].text == "="
            and t[k - 7].text == "local"
            and flow.pair[k + 1] + 1 == flow.pair[k - 3]
        ):
            v = t[k - 6].text
            uses = _m13_05_uses(flow, v, flow.pair[k - 3] + 1)
            if len(uses) != 1 or _render(t[uses[0] - 2 : uses[0] + 2]) != f"ownText ( {v} ,":
                problems.append(f"line {x.line}: {v} is used other than handed once to ownText")
            continue
        if t[k - 1].text != "=" or t[k - 3].text != "local" or t[k + 1].text != "(":
            problems.append(f"line {x.line}: {x.text} is not bound to a local of its own")
            continue
        v = t[k - 2].text
        close = flow.pair[k + 1]
        uses = _m13_05_uses(flow, v, close + 1)
        if len(uses) != 1 or _render(t[uses[0] - 2 : uses[0] + 2]) != f"ownText ( {v} ,":
            problems.append(f"line {x.line}: {v} is used other than handed once to ownText")
    if text.count("ownText (") != len(_M20_13_OWN_CALLS) + 1:
        problems.append("ownText is called other than once per value")
    return problems


def test_m20_13_identity_values_are_asked_about_before_use() -> None:
    assert _m20_13_identity_problems(IDENTITY) == []


# Fix round 1 (code review of #669): the owner's own values and macros are
# read only where a secret rule holds: the four own-value calls in
# Identity.lua (`_m20_13_identity_problems`), the macro calls in Actions.lua
# (the structural rule, `_m13_05_secret_problems`). Crew.lua keeps its own
# rules for its name calls (`_crew_problems`).
_M20_13_OWN_HOME = {
    **dict.fromkeys(_M20_13_OWN_CALLS, "Identity.lua"),
    **dict.fromkeys(("GetMacroInfo", "GetMacroBody", "GetNumMacros"), "Actions.lua"),
}


def _m20_13_own_value_problems(path: Path) -> list[str]:
    """A name of `_M20_13_OWN_HOME` in a file other than its home, as a name
    or a string; Crew.lua is graded by its own rules."""
    if path.name == CREW_FILE:
        return []
    return [
        f"{_where(path, x)} {x.text} outside {_M20_13_OWN_HOME[x.text]}"
        for x in _tokens(path)
        if x.kind in {"name", "string"}
        and x.text in _M20_13_OWN_HOME
        and _M20_13_OWN_HOME[x.text] != path.name
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_m20_13_own_values_are_read_only_where_a_secret_rule_holds(path: Path) -> None:
    bad = _m20_13_own_value_problems(path)
    assert not bad, bad


def _m20_13_gear_own_values(path: Path) -> list[str]:
    return _m20_13_own_value_problems(path)


def _m20_13_edited(
    path: Path, edits: list[tuple[str, str]], check: Callable[[Path], list[str]]
) -> list[str]:
    text = path.read_text(encoding="utf-8")
    for found, replacement in edits:
        assert found in text, found
        text = text.replace(found, replacement, 1)
    with tempfile.TemporaryDirectory(prefix="wowlab-m20-13-") as tmp:
        edited = Path(tmp) / path.name
        edited.write_text(text, encoding="utf-8")
        return check(edited)


def _m20_13_forbidden(path: Path) -> list[str]:
    return _forbidden_api_problems(path, _tokens(path))


def _m20_13_dynamic(path: Path) -> list[str]:
    return [t.text for t in _dynamic_lookups(_tokens(path))]


def _m20_13_core_combat(path: Path) -> list[str]:
    """Why Core.lua (or an edited copy) may gather in combat: the logout
    write gathers only out of combat, `/wowlab save` refuses in combat, a
    timer's pass and the first pass wait for the end of combat, and combat
    is tracked from the client's events and asked as the world is entered."""
    flow = _Flow(path, _tokens(path))
    rendered = _render(flow.tokens)
    problems = []
    pins = {
        'ns.On("PLAYER_LOGOUT")': 'if not inCombat then gatherDirty ( ) end ns . Write ( "logout" )',
        "ns.Refresh": "function ns . Refresh ( ) if inCombat then return false end",
        "schedule": (
            "timerAfter ( GATHER_INTERVAL , function ( ) "
            "if firstPassDone and inCombat then waitingForCombat = true return end"
        ),
        "startFirstPass": "if inCombat then firstPassWaiting = true else gatherDirty ( ) end",
    }
    for label, pin in pins.items():
        if pin not in _body(flow, label):
            problems.append(f"{label}: {pin!r} is missing")
    for pin in (
        'ns . On ( "PLAYER_REGEN_DISABLED" , function ( ) inCombat = true end )',
        'ns . On ( "PLAYER_REGEN_ENABLED" , function ( ) inCombat = false',
        "if ok and locked == true then inCombat = true elseif ok then inCombat = false end",
    ):
        if rendered.count(pin) != 1:
            problems.append(f"{pin!r} is not there once")
    if rendered.count("inCombat = false") != 3 or rendered.count("inCombat = true") != 2:
        problems.append("inCombat is set other than by the combat events and the world check")
    # Fix round 1 (code review of #669): every call of a gather, wherever it
    # is added, stands right after its own combat guard. Each place is pinned
    # by the function it is in and the tokens just before the call; a call
    # anywhere else (a `gatherDirty()` in the slash handler, say) fails.
    t = flow.tokens
    for k in range(1, len(t) - 1):
        x = t[k]
        if x.kind != "name" or x.text not in _M20_13_GATHERS or t[k + 1].text != "(":
            continue
        if _is_field(t, k) or t[k - 1].text == "function":
            continue
        where = _m20_13_named_function(flow, k)
        guard = next(
            (
                g
                for g in _M20_13_GATHER_GUARDS.get((where, x.text), ())
                if _render(t[k - len(g.split(" ")) : k]) == g
            ),
            None,
        )
        if guard is None:
            problems.append(f"line {x.line}: {x.text}() in {where} with no combat guard pinned")
    # `section.gather` is called only by `gather`, and `ns.Refresh` only by the
    # slash handler's `save` branch.
    # (`ns . Refresh ( )` twice: its definition and its one call.)
    if rendered.count("section . gather ,") != 1 or rendered.count("ns . Refresh ( )") != 2:
        problems.append("a section's gather or ns.Refresh is reached another way")
    return problems


_M20_13_GATHERS = frozenset({"gather", "gatherDirty"})


def _m20_13_named_function(flow: _Flow, k: int) -> str | None:
    """The innermost named function around token k: a function literal (a
    timer's callback) counts as the function it is written in."""
    named = [
        (head, label)
        for head, label in flow.labels.items()
        if not label.startswith("function@") and head < k <= flow.block_end(head)
    ]
    return max(named)[1] if named else None


# (function, callee) -> the tokens that must stand right before each call.
_M20_13_GATHER_GUARDS: dict[tuple[str | None, str], tuple[str, ...]] = {
    ('ns.On("PLAYER_LOGOUT")', "gatherDirty"): ("if not inCombat then",),
    ("schedule", "gatherDirty"): (
        "if inCombat then waitingForCombat = true else",
        "pending = false if firstPassDone then",
    ),
    ("startFirstPass", "gatherDirty"): ("if inCombat then firstPassWaiting = true else",),
    # Its handler starts with `inCombat = false` (pinned above).
    ('ns.On("PLAYER_REGEN_ENABLED")', "gatherDirty"): ("if run then",),
    # gatherDirty's own loop: reached only from the guarded calls above.
    ("gatherDirty", "gather"): ("if section . dirty then",),
    # ns.Refresh returns at once in combat (pinned above).
    ("ns.Refresh", "gather"): ("if section . on_world and not section . off then",),
    # An immediate section (customization, on BARBER_SHOP_OPEN and
    # BARBER_SHOP_APPLIED): the barber shop is not reachable in combat.
    ("onEvent", "gather"): ("if section . immediate then",),
}


def test_m20_13_core_gathers_nothing_in_combat() -> None:
    assert _m20_13_core_combat(ADDON / "Core.lua") == []


# Fix round 2 (code review of #669): `onEvent`'s exemption for an immediate
# gather holds only for the barber shop, which is not reachable in combat.
# `immediate` is a key of exactly one section spec: `customization`, in
# Customization.lua. Anywhere else (Gear.lua's section would gather on
# PLAYER_EQUIPMENT_CHANGED, which fires in combat on a weapon swap) fails.
_M20_13_IMMEDIATE = ("Customization.lua", "customization")


def _m20_13_immediate_keys(path: Path) -> list[str]:
    """The key of every immediate section spec in `path` (a literal, or a
    file-level `local KEY = "..."`)."""
    t = _tokens(path)
    flow = _Flow(path, t)
    constants = dict(
        re.findall(r'^local ([A-Z_]+) = "([^"]+)"$', path.read_text(encoding="utf-8"), re.M)
    )
    keys = []
    for k in range(len(t) - 4):
        if [x.text for x in t[k : k + 5]] != ["ns", ".", "Section", "(", "{"]:
            continue
        entries = {key: (a, b) for key, a, b in flow.entries(k + 4)}
        if "{immediate}" not in entries:
            continue
        at = t[entries["{key}"][0]] if "{key}" in entries else None
        if at is None:
            keys.append("?")
        elif at.kind == "string":
            keys.append(at.text)
        else:
            keys.append(constants.get(at.text, "?"))
    return keys


def _m20_13_immediate_problems(path: Path) -> list[str]:
    return [
        f"{path.name}: section {key} is immediate; only the barber-shop section may be"
        for key in _m20_13_immediate_keys(path)
        if (path.name, key) != _M20_13_IMMEDIATE
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_m20_13_only_the_barber_shop_gathers_on_its_event(path: Path) -> None:
    assert _m20_13_immediate_problems(path) == []


def test_m20_13_the_barber_shop_section_is_the_immediate_one() -> None:
    """Control: the exemption names a section that exists and is immediate."""
    assert _m20_13_immediate_keys(ADDON / "Customization.lua") == ["customization"]


_SEND = '    SendChatMessage("constructed", "SAY")\n'
# Each kept rule: (the file, its edits, the check, a word the check's report
# must hold, so the edit fails for that rule's own reason).
_M20_13_KEPT: dict[str, tuple[Path, list[tuple[str, str]], Callable[[Path], list[str]], str]] = {
    "no send: a chat message": (
        ADDON / "Core.lua",
        [("local function say(message)\n", "local function say(message)\n" + _SEND)],
        _m20_13_forbidden,
        "SendChatMessage",
    ),
    "no send: an addon message through C_ChatInfo": (
        ADDON / "Gear.lua",
        [("local _, ns = ...\n", 'local _, ns = ...\nC_ChatInfo.SendAddonMessage("x", "y")\n')],
        _m20_13_forbidden,
        "C_ChatInfo",
    ),
    "no send: mail named in a string": (
        ACTIONS,
        [("local IN_COMBAT", 'local verb = "SendMail"\nlocal IN_COMBAT')],
        _m20_13_forbidden,
        "SendMail",
    ),
    "no BattleTag": (
        IDENTITY,
        [("local _, ns = ...\n", "local _, ns = ...\nlocal tag = select(2, BNGetInfo())\n")],
        _m20_13_forbidden,
        "BNGetInfo",
    ),
    "no dynamic lookup": (
        IDENTITY,
        [("local _, ns = ...\n", 'local _, ns = ...\nlocal f = _G["UnitName"]\n')],
        _m20_13_dynamic,
        "_G",
    ),
    "no dynamic code": (
        ACTIONS,
        [("local IN_COMBAT", 'local run = loadstring("return 1")\nlocal IN_COMBAT')],
        _m20_13_dynamic,
        "loadstring",
    ),
    "nothing in combat: the macros gather reads before its check": (
        ACTIONS,
        [
            (
                '    events = { "UPDATE_MACROS" },\n    retry = { "PLAYER_REGEN_ENABLED" },\n'
                "    on_world = true,\n    gather = function()\n",
                '    events = { "UPDATE_MACROS" },\n    retry = { "PLAYER_REGEN_ENABLED" },\n'
                "    on_world = true,\n    gather = function()\n"
                "        local early = GetNumMacros()\n",
            )
        ],
        _m13_05_combat_problems,
        "macros: the gather does not start with the combat check",
    ),
    "nothing in combat: the logout gathers in combat": (
        ADDON / "Core.lua",
        [("    if not inCombat then\n        gatherDirty()\n    end\n", "    gatherDirty()\n")],
        _m20_13_core_combat,
        "PLAYER_LOGOUT",
    ),
    "nothing in combat: /wowlab save gathers in combat": (
        ADDON / "Core.lua",
        [("    if inCombat then\n        return false\n    end\n", "")],
        _m20_13_core_combat,
        "ns.Refresh",
    ),
    "nothing in combat: a timer's pass runs in combat": (
        ADDON / "Core.lua",
        [
            (
                "        if firstPassDone and inCombat then\n",
                "        if firstPassDone and false then\n",
            )
        ],
        _m20_13_core_combat,
        "schedule",
    ),
    "nothing in combat: the combat flag forced off": (
        ADDON / "Core.lua",
        [
            (
                "local function gather(section, event)\n",
                "local function gather(section, event)\n    inCombat = false\n",
            )
        ],
        _m20_13_core_combat,
        "inCombat is set",
    ),
    "secret test: a macro body stored raw": (
        ACTIONS,
        [("                    body = clean(isSecret, secret, info, body)\n", "")],
        _m13_05_secret_problems,
        "body",
    ),
    "secret test: a binding command stored raw": (
        ACTIONS,
        [("command = ns.String(found)", "command = found")],
        _m13_05_secret_problems,
        "found",
    ),
    "secret test: the GUID stored before the test": (
        IDENTITY,
        [
            (
                '            local keptGuid, whyGuid = ownText(guid, "the return of UnitGUID")\n',
                "            local keptGuid, whyGuid = guid, nil\n",
            )
        ],
        _m20_13_identity_problems,
        "guid",
    ),
    "secret test: ownText compares before the test": (
        IDENTITY,
        [("    if secret(value) then\n", '    if value == "" or secret(value) then\n')],
        _m20_13_identity_problems,
        "ownText",
    ),
    "secret test: the secret helper always says no": (
        IDENTITY,
        [("    return not ok or hidden == true\n", "    return false\n")],
        _m20_13_identity_problems,
        "secret",
    ),
    # Fix round 1 (code review of #669).
    "secret test: the own GUID stored untested in another file": (
        ADDON / "Gear.lua",
        [
            (
                "                local level, source = itemLevel(slot, raw)\n",
                "                local level, source = itemLevel(slot, raw)\n"
                '                record.guid = ns.String(UnitGUID("player"))\n',
            )
        ],
        _m20_13_gear_own_values,
        "UnitGUID outside Identity.lua",
    ),
    "secret test: a macro body read outside Actions.lua": (
        ADDON / "Talents.lua",
        [("local _, ns = ...\n", "local _, ns = ...\nlocal body = GetMacroBody(1)\n")],
        _m20_13_gear_own_values,
        "GetMacroBody outside Actions.lua",
    ),
    "nothing in combat: another section gathers on its event": (
        ADDON / "Gear.lua",
        [('    key = "gear",\n', '    key = "gear",\n    immediate = true,\n')],
        _m20_13_immediate_problems,
        "section gear is immediate",
    ),
    "nothing in combat: a gather added to the debug branch": (
        ADDON / "Core.lua",
        [("        debugCommand(key)\n", "        gatherDirty()\n        debugCommand(key)\n")],
        _m20_13_core_combat,
        "no combat guard pinned",
    ),
    # M20-16: the realm's two fallbacks are under the same rule.
    "secret test: the normalized realm stored before the test": (
        IDENTITY,
        [
            (
                "                local normalized = GetNormalizedRealmName()\n",
                "                record.realm = ns.String(GetNormalizedRealmName())\n"
                "                local normalized = nil\n",
            )
        ],
        _m20_13_identity_problems,
        "GetNormalizedRealmName is not bound to a local of its own",
    ),
    "secret test: UnitFullName's realm stored before the test": (
        IDENTITY,
        [
            (
                '                local keptFull, whyFull = ownText(fullRealm, "the second return '
                'of UnitFullName")\n',
                "                local keptFull, whyFull = fullRealm, nil\n",
            )
        ],
        _m20_13_identity_problems,
        "fullRealm is used other than handed once to ownText",
    ),
    "secret test: UnitFullName's realm compared before the test": (
        IDENTITY,
        [
            (
                '                local keptFull, whyFull = ownText(fullRealm, "the second return '
                'of UnitFullName")\n',
                '                local keptFull, whyFull = ownText(fullRealm, "the second return '
                'of UnitFullName")\n                if fullRealm == "" then return record end\n',
            )
        ],
        _m20_13_identity_problems,
        "fullRealm is used other than handed once to ownText",
    ),
    "secret test: the normalized realm read outside Identity.lua": (
        ADDON / "Gear.lua",
        [("local _, ns = ...\n", "local _, ns = ...\nlocal home = GetNormalizedRealmName()\n")],
        _m20_13_gear_own_values,
        "GetNormalizedRealmName outside Identity.lua",
    ),
    "secret test: UnitFullName read outside Identity.lua": (
        ADDON / "Talents.lua",
        [("local _, ns = ...\n", 'local _, ns = ...\nlocal _, home = UnitFullName("player")\n')],
        _m20_13_gear_own_values,
        "UnitFullName outside Identity.lua",
    ),
}


def test_m20_13_each_kept_rule_fails_its_own_edit() -> None:
    """Constructed edits of the real sources (boundary tests of the rules
    that stay): each is refused, by its own rule, with a report naming what
    the edit added."""
    missed = []
    for tag, (path, edits, check, word) in sorted(_M20_13_KEPT.items()):
        problems = _m20_13_edited(path, edits, check)
        if not any(word in problem for problem in problems):
            missed.append((tag, problems))
    assert not missed, missed


# M20-16 (the owner's first schema-5 run, 71037, 2026-11-20) ------------------


def test_m20_16_the_realm_comes_from_the_first_of_three_sources() -> None:
    """GetRealmName, then GetNormalizedRealmName, then UnitFullName's second
    return, each asked only while no realm was kept, each source named in
    `realm_from`; the last two mark the realm normalized. With none,
    `realm_absent` joins the three reasons."""
    text = _render(_tokens(IDENTITY))
    order = [
        text.index("local realm = GetRealmName ( )"),
        text.index("if not record . realm then if type ( GetNormalizedRealmName ) ~="),
        text.index("local normalized = GetNormalizedRealmName ( )"),
        text.index("if not record . realm then if type ( UnitFullName ) ~="),
        text.index('local fullRealm = select ( 2 , UnitFullName ( "player" ) )'),
        text.index("if not record . realm then record . realm_absent ="),
    ]
    assert order == sorted(order)
    for kept, source, normalized in (
        ("keptRealm", "realm_name", ""),
        ("keptNormalized", "normalized_realm_name", " record . realm_normalized = true"),
        ("keptFull", "full_name", " record . realm_normalized = true"),
    ):
        assert (
            f"if {kept} then record . realm = ns . String ( {kept} ) "
            f'record . realm_from = "{source}"{normalized} else'
        ) in text, source
    assert text.count("record . realm_from =") == 3
    assert text.count("record . realm =") == 3


def test_m20_16_a_record_without_a_realm_is_gathered_again() -> None:
    """`again_without` is a key of the identity spec only, naming `realm`:
    a record without one leaves the section dirty, so the next pass (at
    logout at the latest) gathers it again and replaces the record."""
    found = []
    for path in SOURCES:
        t = _tokens(path)
        found += [
            (path.name, t[k + 2].text)
            for k in range(len(t) - 2)
            if t[k].text == "again_without" and t[k + 1].text == "=" and not _is_field(t, k)
        ]
    assert found == [("Identity.lua", "realm")]


def test_m20_16_bindings_count_the_commands_that_are_not_plain() -> None:
    """Every listed command, keyed or not, that is not a plain upper-case
    name is counted in `commands_not_plain` (the stored, cleaned text is
    tested, never the raw return)."""
    text = _render(_tokens(ACTIONS))
    assert (
        "local named = ns . String ( found ) "
        'if named and not string . find ( named , "^[%u%d_]+$" ) then '
        "notPlain = notPlain + 1 end"
    ) in text
    assert "record . commands_not_plain = notPlain" in text


def test_m20_13_the_chat_line_prints_once_per_schema() -> None:
    """The first-pass line prints only when the file's `announced` is not
    this schema or the probe was lost; `announced` is cleared from the live
    table at ADDON_LOADED and put back only by the addon's own write in a
    session whose first pass ran."""
    flow = _core()
    first = _body(flow, "startFirstPass")
    assert (
        'if ns . announced ~= ns . SCHEMA or ns . probe . lost then say ( "recording in "' in first
    )
    assert first.count("say (") == 1
    write = _body(flow, "ns.Write")
    assert (
        "if firstPassDone then db . announced = ns . SCHEMA "
        "else db . announced = ns . Number ( ns . announced ) end"
    ) in write
    loaded = _body(flow, 'ns.On("ADDON_LOADED")')
    assert loaded.index("loadQuiet ( WowLabCharDB )") < loaded.index("loadSkip ( WowLabCharDB )")
    assert loaded.endswith("WowLabCharDB . announced = nil end"), loaded
    rendered = _render(flow.tokens)
    assert rendered.count("ns . announced =") == 2  # declared, and loaded


def test_m20_13_debug_is_off_by_default_and_gates_probes_and_count_only_events() -> None:
    """`/wowlab debug on|off` takes effect from the next load: ns.debug is
    read once at ADDON_LOADED (only `true` is on). Outside a debug session
    no `probes` and no count-only event: `quiet` drops the probes for a
    `dropped` count."""
    flow = _core()
    rendered = _render(flow.tokens)
    assert rendered.count("ns . debug =") == 2
    assert "ns . debug = false" in rendered
    assert "ns . debug = saved . debug == true debugNext = ns . debug" in _body(flow, "loadQuiet")
    command = _body(flow, "debugCommand")
    assert 'if word == "on" or word == "off" then debugNext = word == "on"' in command
    assert "ns . debug =" not in command
    assert "if counts then for _ , event in ipairs ( section . count_only or { } ) do" in _body(
        flow, "listen"
    )
    quiet = _body(flow, "quiet")
    assert quiet.startswith(
        'function quiet ( record ) if ns . debug or type ( record ) ~= "table" '
        'or type ( record . probes ) ~= "table" then return end'
    )
    assert "record . probes = nil record . dropped = dropped end" in quiet
    assert 'elseif command == "debug" then' in rendered and "debugCommand ( key )" in rendered


def test_m20_13_one_pass_at_most_every_30_to_60_s() -> None:
    flow = _core()
    t = flow.tokens
    k = next(
        i
        for i in range(len(t) - 3)
        if [x.text for x in t[i : i + 3]] == ["local", "GATHER_INTERVAL", "="]
    )
    assert 30 <= int(t[k + 3].text) <= 60
    assert "timerAfter ( 2 ," not in _render(t)
    assert "else section . dirty = true schedule ( ) end end" in _body(flow, "onEvent")


def test_m20_13_the_constants_sweep_runs_once_per_session() -> None:
    text = _render(_tokens(TALENTS))
    assert text.count("pairs ( Constants )") == 1
    assert (
        "if not systemsSwept then systemsSwept = true for group , fields in pairs ( Constants ) do"
    ) in text
    assert (
        "else for _ , known in ipairs ( answering ) do "
        "add ( ns . Call ( bySystem , known . system ) , known . how ) end end"
    ) in text
    assert text.count("systemsSwept = true") == 1


def test_m20_13_the_times_and_the_write_are_recorded() -> None:
    flow = _core()
    write = _body(flow, "ns.Write")
    assert (
        "db . gathered = gatheredList ( ) db . written_at = ns . Number ( time ( ) ) "
        "db . written_by = ns . String ( by )"
    ) in write
    assert "gatheredAt [ section . key ] = ns . Number ( time ( ) )" in _body(flow, "gather")


def test_m20_13_readme_and_plan_document_schema_5() -> None:
    readme = " ".join(README.read_text(encoding="utf-8").split())
    plan = " ".join(PLAN.read_text(encoding="utf-8").split("### 13.2")[0].split())
    for phrase in ("Schema 5 (M20-13", "### Debug", "/wowlab debug on", "`announced`", "`dropped`"):
        assert phrase in readme, phrase
    for text in (readme, plan):
        for word in ("`macros`", "`gathered`", "`written_at`", "`guid`", "`guild`"):
            assert word in text, word
    assert "*Schema 5, as built (2026-11-20, M20-13" in plan
