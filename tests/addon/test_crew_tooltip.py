"""Static checks on the crew book's tooltip line (M20-14), `lab/addon/WowLab/Crew.lua`.

Owner decision, 2026-11-20 (ADR-0036's second note of that date; §20.8
decisions 2 and 8 overturned): out of combat, a player's tooltip gains one
line when the crew book holds them, and a note may be 2,000 bytes.

Source scans, labelled as such: nothing here runs Lua (L3, ADR-0026). The
rules that bind the hook, each with a constructed edit of the real source
that must fail for that rule's own reason (`_KEPT`):

- off means off: the hook is installed only by a gated function, once per
  session, and every function of the hook starts with the on and combat
  tests, so with the book off or in combat it does nothing;
- every client value is secret-tested before any compare, concatenation or
  store (Crew.lua's own rule, `_crew_problems`), and a secret name shows
  nothing;
- no send of any kind (the whole-addon rule);
- the default tooltip is not broken or tainted: the hook call exists before
  it is made and is made through `pcall`, the hook's work runs through
  `pcall`, only the game's own tooltip gets the line, and nothing is done to
  it but `AddLine`;
- cheap: the hook reaches only its own helpers, walks nothing, and looks the
  player up once;
- the key is built exactly as the roster pass builds a member's;
- the note: 2,000 bytes, and the tooltip shows only its start, cut before a
  character's first byte.

Whether the Forever client has `TooltipDataProcessor`, whether the tooltip's
unit is `mouseover`, whether adding a line taints anything and whether a
name is a secret value in an instance are [verify] in game.

The book's saved variable is named through `test_lab_addon.BOOK`, so this
file never spells it (tests/repo/test_crew_book_out_of_repo.py).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import test_crew_book
import test_lab_addon as g

CREW = g.ADDON / g.CREW_FILE
BOOK = g.BOOK

# The hook's functions: the handler the client calls and what it reaches.
HOOK = ("onTooltip", "tipShow", "hoverKey", "tipText")
# What the hook's functions may call: the pinned helpers, each other, the
# note's start and the print flattening; nothing that walks the book.
HOOK_CALLS = frozenset({"crewOn", "crewBlocked", "secret", "noteStart", "shown", *HOOK})
# Lua's own functions the hook may call (no `pairs`, `ipairs` or `next`).
HOOK_BUILTINS = frozenset(
    {"type", "pcall", "string", "UnitIsPlayer", "UnitName", "GetNormalizedRealmName"}
)
HOOK_HANDLER = (
    "function onTooltip ( tooltip ) " + g.CREW_GATE + " "
    "if tooltip ~= GameTooltip then return end pcall ( tipShow ) end"
)
HOOK_INSTALL = "pcall ( TooltipDataProcessor . AddTooltipPostCall , unitType , onTooltip )"
# Fix round 1 (code review of #673): the hook is for unit tooltips. Its type
# is the one local, bound once in hookTooltip to exactly this, never assigned
# again, and `Enum.TooltipDataType` is read nowhere else but its type() test.
HOOK_TYPE = "local unitType = Enum . TooltipDataType . Unit"
HOOK_TESTS = (
    'type ( TooltipDataProcessor ) ~= "table" or '
    'type ( TooltipDataProcessor . AddTooltipPostCall ) ~= "function"',
    'type ( Enum ) ~= "table" or type ( Enum . TooltipDataType ) ~= "table"',
)
TIP_ADD = "GameTooltip : AddLine ( text , 1 , 0.82 , 0 , true )"
NOTE_START = (
    "function noteStart ( note ) "
    'if type ( note ) ~= "string" then return "" end '
    "local size = # note local cut = size local chars = 0 "
    "for i = 1 , size do local byte = string . byte ( note , i ) "
    "if byte < 128 or byte >= 192 then chars = chars + 1 "
    "if chars > TIP_NOTE_CHARS then cut = i - 1 break end end end "
    'if cut < size then return string . sub ( note , 1 , cut ) .. "..." end '
    "return string . sub ( note , 1 , size ) end"
)
NOTE_LIMITS = ("local NOTE_MAX_BYTES = 2000", "local TIP_NOTE_CHARS = 60")


def _flow(text: str) -> g._Flow:
    return g._Flow(CREW, g.tokenize(text))


def _functions(flow: g._Flow, label: str) -> list[int]:
    return [h for h, lab in flow.labels.items() if lab == label]


def _text(flow: g._Flow, head: int) -> str:
    return g._render(flow.tokens[head : flow.block_end(head) + 1])


def _key_statements(flow: g._Flow, head: int) -> list[str]:
    """Each `local key = ...` statement of one function, rendered."""
    t = flow.tokens
    fn = flow.functions[head]
    out = []
    for k in range(fn.start, fn.stop - 3):
        if g._render(t[k : k + 3]) == "local key =" and flow.function_at(k) is fn:
            # `local key = <a> .. "-" .. <b>` is eight tokens; anything
            # longer renders differently.
            end = k + 8
            while end < fn.stop and t[end].text in {"..", "(", ".", ":", "["}:
                end += 2
            out.append(g._render(t[k:end]))
    return out


def tooltip_problems(text: str) -> list[str]:
    """M20-14's rules for the tooltip line, on one Crew.lua text."""
    flow = _flow(text)
    t = flow.tokens
    rendered = g._render(t)
    problems: list[str] = []
    heads: dict[str, int] = {}
    for label in (*HOOK, "hookTooltip", "noteStart", "readMember"):
        found = _functions(flow, label)
        if len(found) != 1:
            problems.append(f"{label} is defined {len(found)} times, not once")
        else:
            heads[label] = found[0]
    if problems:
        return problems
    # Off means off, and nothing in combat: every function of the hook, and
    # the installer, starts with the on and combat tests.
    for label in (*HOOK, "hookTooltip"):
        if not g._crew_gated(flow, heads[label]):
            problems.append(f"{label} is not behind the on and combat tests")
    # The handler, pinned whole: only the game's tooltip, the work in pcall.
    if _text(flow, heads["onTooltip"]) != HOOK_HANDLER:
        problems.append("onTooltip is not the pinned handler (gate, the game's tooltip, pcall)")
    # Installed once, the modern way, tested for first, through pcall, by
    # the gated installer only; a session flag set before the call.
    install = _text(flow, heads["hookTooltip"])
    if rendered.count("AddTooltipPostCall") != 3 or HOOK_INSTALL not in install:
        problems.append("the hook is installed other than once, through pcall, in hookTooltip")
    elif any(test not in install for test in HOOK_TESTS) or not all(
        install.index(test) < install.index(HOOK_INSTALL) for test in HOOK_TESTS
    ):
        problems.append("the hook is installed before the client is tested for it")
    if "if tipHooked then return end tipHooked = true" not in install:
        problems.append("the hook is not installed once per session")
    type_reads = [
        k
        for k in range(len(t) - 2)
        if g._render(t[k : k + 3]) == "Enum . TooltipDataType" and not g._is_field(t, k)
    ]
    bound = [k for k in type_reads if g._render(t[k - 3 : k + 5]) == HOOK_TYPE]
    tested = [
        k for k in type_reads if g._render(t[k - 2 : k + 4]) == "type ( Enum . TooltipDataType )"
    ]
    if (
        HOOK_TYPE not in install
        or len(bound) != 1
        or len(type_reads) != len(bound) + len(tested)
        or len(flow.decls.get("unitType", [])) != 1
        or any(r.rstrip(".") == "unitType" for r, _ in flow.rebound)
    ):
        problems.append("the hook is for a tooltip type other than Unit")
    if rendered.count("hookTooltip (") < 2 or any(
        not g._crew_gated(flow, fn.head)
        for k in range(len(t) - 1)
        if t[k].text == "hookTooltip"
        and t[k + 1].text == "("
        and t[k - 1].text != "function"
        and (fn := flow.function_at(k)) is not None
    ):
        problems.append("hookTooltip is called outside a gated function")
    if any(
        t[k].text == "hookTooltip" and t[k - 1].text != "function" and flow.function_at(k) is None
        for k in range(1, len(t))
    ):
        problems.append("hookTooltip is called at file level")
    # The default tooltip: nothing but AddLine, once, in tipShow.
    uses = [k for k, x in enumerate(t) if x.kind == "name" and x.text == "GameTooltip"]
    adds = [k for k in uses if g._render(t[k : k + len(TIP_ADD.split(" "))]) == TIP_ADD]
    compared = [k for k in uses if t[k - 1].text == "~=" and t[k - 2].text == "tooltip"]
    if len(adds) != 1 or flow.function_at(adds[0]) is not flow.functions[heads["tipShow"]]:
        problems.append("the line is not added once, by tipShow")
    if len(uses) != len(adds) + len(compared):
        problems.append("the game's tooltip is used other than for AddLine")
    # Cheap: the hook reaches only its own helpers and walks nothing.
    for label in HOOK:
        fn = flow.functions[heads[label]]
        for k in range(fn.start, fn.stop):
            x = t[k]
            if x.kind == "name" and x.text in {"pairs", "ipairs", "next", "while", "for"}:
                problems.append(f"line {x.line}: {label} walks ({x.text})")
            called = x.kind == "name" and t[k + 1].text == "(" and not g._is_field(t, k)
            if called and x.text not in HOOK_CALLS | HOOK_BUILTINS:
                problems.append(f"line {x.line}: {label} calls {x.text}")
    lookups = [k for k in range(len(t) - 3) if g._render(t[k : k + 4]) == f"{BOOK} . players ["]
    in_tip = [k for k in lookups if flow.function_at(k) is flow.functions[heads["tipText"]]]
    if len(in_tip) != 1:
        problems.append(f"tipText looks the player up {len(in_tip)} times, not once")
    for label in ("onTooltip", "tipShow", "hoverKey"):
        fn = flow.functions[heads[label]]
        if f"{BOOK} ." in g._render(t[fn.start : fn.stop]):
            problems.append(f"{label} reads the book")
    # The key, exactly as the roster pass builds a member's.
    roster = set(_key_statements(flow, heads["readMember"]))
    keys = _key_statements(flow, heads["hoverKey"])
    if len(keys) != 2 or not set(keys) <= roster:
        problems.append("hoverKey builds a key other than as the roster pass does")
    # The note: 2,000 bytes; the tooltip shows its start, cut on a character.
    for decl in NOTE_LIMITS:
        if decl not in rendered:
            problems.append(f"no `{decl}`")
    if _text(flow, heads["noteStart"]) != NOTE_START:
        problems.append("noteStart is not the pinned cut before a character's first byte")
    return problems


def test_m20_14_the_tooltip_line_keeps_its_rules() -> None:
    bad = tooltip_problems(CREW.read_text(encoding="utf-8"))
    assert not bad, bad


def test_m20_14_the_real_source_keeps_crew_lua_s_own_rules() -> None:
    """Control: the real file with the hook passes Crew.lua's rules."""
    text = CREW.read_text(encoding="utf-8")
    assert g._crew_problems(CREW, text) == []
    assert g._forbidden_api_problems(CREW, g.tokenize(text)) == []


def _crew(text: str) -> list[str]:
    return g._crew_problems(CREW, text)


def _forbidden(text: str) -> list[str]:
    return g._forbidden_api_problems(CREW, g.tokenize(text))


def _cuts(text: str) -> list[str]:
    return test_crew_book._cuts_outside_note_start(_flow(text))


def _unit(text: str) -> list[str]:
    return g._unit_argument_problems(CREW, g.tokenize(text))


_GATE_TIP = (
    "local function tipShow()\n    if not crewOn() then\n        return\n    end\n"
    "    if crewBlocked() then\n        return\n    end\n"
)
_HOVER_READ = '    local isPlayer = UnitIsPlayer("mouseover")\n'
_TIP_LOOKUP = f"    local entry = {BOOK}.players[key]\n"
_ADD = "    GameTooltip:AddLine(text, 1, 0.82, 0, true)\n"

# Each kept rule: (its edits of the real Crew.lua, the check, a word the
# check's report must hold, so the edit fails for that rule's own reason).
_KEPT: dict[str, tuple[list[tuple[str, str]], Callable[[str], list[str]], str]] = {
    "off: the tooltip read with the book off": (
        [
            (
                _GATE_TIP,
                "local function tipShow()\n    if crewBlocked() then\n        return\n    end\n",
            )
        ],
        tooltip_problems,
        "tipShow is not behind the on and combat tests",
    ),
    "off: a name read with the book off": (
        [
            (
                "local function hoverKey()\n    if not crewOn() then\n        return\n    end\n",
                "local function hoverKey()\n",
            )
        ],
        _crew,
        "UnitName(...) is not behind the on and combat tests",
    ),
    "off: the hook installed at load, whatever the book": (
        [
            (
                'ns.On("ADDON_LOADED", crewLoaded)\n',
                'ns.On("ADDON_LOADED", crewLoaded)\nhookTooltip()\n',
            )
        ],
        tooltip_problems,
        "hookTooltip is called at file level",
    ),
    "off: the handler without the on test": (
        [
            (
                "local function onTooltip(tooltip)\n    if not crewOn() then\n        return\n    end\n",
                "local function onTooltip(tooltip)\n",
            )
        ],
        tooltip_problems,
        "onTooltip is not behind the on and combat tests",
    ),
    "combat: the tooltip read in combat": (
        [
            (
                _GATE_TIP,
                "local function tipShow()\n    if not crewOn() then\n        return\n    end\n",
            )
        ],
        tooltip_problems,
        "tipShow is not behind the on and combat tests",
    ),
    "combat: the name read in combat": (
        [
            (
                "    if crewBlocked() then\n        return\n    end\n"
                '    if type(UnitIsPlayer) ~= "function" or type(UnitName) ~= "function" then\n'
                "        return nil\n",
                '    if type(UnitIsPlayer) ~= "function" or type(UnitName) ~= "function" then\n'
                "        return nil\n",
            )
        ],
        _crew,
        "is not behind the on and combat tests",
    ),
    "secret: the name compared before the secret test": (
        [
            (
                _HOVER_READ,
                _HOVER_READ + '    local name0, realm0 = UnitName("mouseover")\n'
                '    if name0 == "" then\n        return nil\n    end\n',
            )
        ],
        _crew,
        "name0 (from UnitName) is used before the secret test",
    ),
    "secret: the name concatenated before the secret test": (
        [
            (
                "    if secret(isPlayer) or secret(name) or secret(realm) then\n",
                '    local early = name .. "-" .. realm\n'
                "    if secret(isPlayer) or secret(name) or secret(realm) then\n",
            )
        ],
        _crew,
        "name (from UnitName) is used before the secret test",
    ),
    "secret: the player test used before the secret test": (
        [
            (
                "    if secret(isPlayer) or secret(name) or secret(realm) then\n",
                "    if not isPlayer then\n        return nil\n    end\n"
                "    if secret(isPlayer) or secret(name) or secret(realm) then\n",
            )
        ],
        _crew,
        "isPlayer (from UnitIsPlayer) is used before the secret test",
    ),
    "cheap: a walk of the whole book in the hook": (
        [(_TIP_LOOKUP, _TIP_LOOKUP + f"    for _ in pairs({BOOK}.players) do\n    end\n")],
        tooltip_problems,
        "tipText walks (pairs)",
    ),
    "cheap: the book counted in the hook": (
        [(_TIP_LOOKUP, _TIP_LOOKUP + "    local held = countPlayers()\n")],
        tooltip_problems,
        "tipText calls countPlayers",
    ),
    "cheap: a second lookup": (
        [(_TIP_LOOKUP, _TIP_LOOKUP + f"    local again = {BOOK}.players[key]\n")],
        tooltip_problems,
        "tipText looks the player up 2 times",
    ),
    "no send: the line said in chat": (
        [(_ADD, _ADD + '    SendChatMessage(text, "SAY")\n')],
        _forbidden,
        "SendChatMessage",
    ),
    "no send: an addon message from the hook": (
        [(_ADD, _ADD + '    C_ChatInfo.SendAddonMessage("x", text)\n')],
        _forbidden,
        "C_ChatInfo",
    ),
    "the tooltip: the hook installed without pcall": (
        [
            (
                "    if not pcall(TooltipDataProcessor.AddTooltipPostCall, unitType, onTooltip) then\n",
                "    TooltipDataProcessor.AddTooltipPostCall(unitType, onTooltip)\n    if false then\n",
            )
        ],
        tooltip_problems,
        "the hook is installed other than once, through pcall",
    ),
    "the tooltip: the hook's work outside pcall": (
        [("    pcall(tipShow)\n", "    tipShow()\n")],
        tooltip_problems,
        "onTooltip is not the pinned handler",
    ),
    "the tooltip: a line added to any tooltip": (
        [
            (
                "    if tooltip ~= GameTooltip then\n        return\n    end\n    pcall(tipShow)\n",
                "    pcall(tipShow)\n",
            )
        ],
        tooltip_problems,
        "onTooltip is not the pinned handler",
    ),
    "the tooltip: the default tooltip shown again": (
        [(_ADD, _ADD + "    GameTooltip:Show()\n")],
        tooltip_problems,
        "the game's tooltip is used other than for AddLine",
    ),
    "the tooltip: the hook installed before the client is tested": (
        [
            (
                '    if type(Enum) ~= "table" or type(Enum.TooltipDataType) ~= "table" then\n',
                "    pcall(TooltipDataProcessor.AddTooltipPostCall, unitType, onTooltip)\n"
                '    if type(Enum) ~= "table" or type(Enum.TooltipDataType) ~= "table" then\n',
            )
        ],
        tooltip_problems,
        "the hook is installed other than once",
    ),
    "the tooltip: installed at every loading screen": (
        [("    if tipHooked then\n        return\n    end\n", "")],
        tooltip_problems,
        "the hook is not installed once per session",
    ),
    "the tooltip: the hook on item tooltips": (
        [
            (
                "    local unitType = Enum.TooltipDataType.Unit\n",
                "    local unitType = Enum.TooltipDataType.Item\n",
            )
        ],
        tooltip_problems,
        "the hook is for a tooltip type other than Unit",
    ),
    "the tooltip: the type rebound after its test": (
        [
            (
                "    if not pcall(TooltipDataProcessor.AddTooltipPostCall, unitType, onTooltip) then\n",
                "    unitType = Enum.TooltipDataType.Item\n"
                "    if not pcall(TooltipDataProcessor.AddTooltipPostCall, unitType, onTooltip) then\n",
            )
        ],
        tooltip_problems,
        "the hook is for a tooltip type other than Unit",
    ),
    "the note: cut by a method call before the refusal": (
        [
            (
                "    local tidy = cleanNote(text)\n",
                "    local tidy = cleanNote(text)\n    tidy = tidy:sub(1, NOTE_MAX_BYTES)\n",
            )
        ],
        _cuts,
        "'sub' outside noteStart",
    ),
    "the note: cut by a format precision": (
        [
            (
                "    local tidy = cleanNote(text)\n",
                '    local tidy = cleanNote(text)\n    tidy = string.format("%.2000s", tidy)\n',
            )
        ],
        _cuts,
        "outside noteStart",
    ),
    "the key: built other than as the roster's": (
        [
            (
                '            local key = name .. "-" .. realm\n            return key\n',
                '            local key = realm .. "-" .. name\n            return key\n',
            )
        ],
        tooltip_problems,
        "hoverKey builds a key other than as the roster pass does",
    ),
    "the unit: mouseover handed on as a value": (
        [(_HOVER_READ, '    local unit = "mouseover"\n' + _HOVER_READ)],
        _unit,
        "unit-token literal 'mouseover'",
    ),
    "the note: the old 200-byte limit": (
        [("local NOTE_MAX_BYTES = 2000\n", "local NOTE_MAX_BYTES = 200\n")],
        tooltip_problems,
        "no `local NOTE_MAX_BYTES = 2000`",
    ),
    "the note: the tooltip cuts by bytes, inside a character": (
        [
            (
                "        if byte < 128 or byte >= 192 then\n",
                "        if true then\n",
            )
        ],
        tooltip_problems,
        "noteStart is not the pinned cut",
    ),
}


def test_m20_14_each_kept_rule_fails_its_own_edit() -> None:
    """Constructed edits of the real Crew.lua (boundary tests of the rules):
    each is refused, by its own rule, with a report naming what the edit
    did."""
    source = CREW.read_text(encoding="utf-8")
    missed = []
    for tag, (edits, check, word) in sorted(_KEPT.items()):
        text = source
        for found, replacement in edits:
            assert text.count(found) == 1, (tag, found)
            text = text.replace(found, replacement)
        problems = check(text)
        if not any(word in problem for problem in problems):
            missed.append((tag, problems[:5]))
    assert not missed, missed


def test_m20_14_the_tooltip_line_uses_the_warning_s_words() -> None:
    """The line says what the warning says, in its words: the rating as
    "you rated", the note as "Your note:", flattened as every printed field
    is (`shown`), and the matches as the desk words them."""
    flow = _flow(CREW.read_text(encoding="utf-8"))
    (head,) = _functions(flow, "tipText")
    tip = _text(flow, head)
    (warn,) = _functions(flow, "lineFor")
    warning = _text(flow, warn)
    for words in ("you rated", "rated good", "rated bad", '" Your note: "'):
        assert words.lower() in tip.lower() and words.lower() in warning.lower(), words
    assert '" In matches with you " .. allied .. ", against you " .. faced' in tip
    assert (
        "ns . Number ( entry . allied ) or 0" in tip and "ns . Number ( entry . faced ) or 0" in tip
    )


def test_m20_14_the_readme_documents_the_line_and_the_limit() -> None:
    readme = " ".join(g.README.read_text(encoding="utf-8").split())
    for phrase in ("The tooltip line", "2,000 bytes", "mouseover", "[verify]"):
        assert phrase in readme, phrase
    assert "200 bytes" not in readme.replace("2,000 bytes", "")


def test_m20_14_the_unit_relaxation_is_call_only() -> None:
    """`mouseover` is relaxed for Crew.lua only, as a unit call's whole first
    argument, never anywhere else (the narrow cases are in
    test_m19_01_constructed_relaxations_stay_narrow_in_crew_lua)."""
    assert "mouseover" in g._relaxed(Path(g.CREW_FILE), "UNIT_ARGUMENT")
    assert g._relaxed(Path("Core.lua"), "UNIT_ARGUMENT") == frozenset()
    line = 'local x = UnitClass("mouseover")\n'
    assert g._unit_argument_problems(Path("Core.lua"), g.tokenize(line))
    assert not g._unit_argument_problems(Path(g.CREW_FILE), g.tokenize(line))
    assert re.search(r"mouseover", CREW.read_text(encoding="utf-8"))
