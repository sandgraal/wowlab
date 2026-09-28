"""Static checks on the lab-addon sources (M11-01, docs/LAB_PLAN.md §13.1, ADR-0026).

Nothing here runs Lua (L3): the sources are tokenized by a small lexer in
this file and the checks read the tokens. The TOC template is read with
`wowlab_core.toc`. The selene lint (`make lint-lua`) is the other half: it
fails on any global the addon uses that `lab/addon/wow_client.yml` does not
declare.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from wowlab_core.toc import parse_toc

ROOT = Path(__file__).resolve().parents[2]
ADDON_ROOT = ROOT / "lab" / "addon"
ADDON = ADDON_ROOT / "WowLab"
TOC = ADDON / "WowLab.toc"
STD = ADDON_ROOT / "wow_client.yml"
README = ADDON_ROOT / "README.md"
SOURCES = sorted(ADDON.glob("*.lua"))

# The ticket's list: identity, realm, GUID, guild, Battle.net and chat APIs.
FORBIDDEN_APIS = (
    "UnitName",
    "UnitFullName",
    "GetUnitName",
    "GetRealmName",
    "GetNormalizedRealmName",
    "UnitGUID",
    "GetGuildInfo",
    "BNGetInfo",
    "C_BattleNet",
    "C_ChatInfo",
    "GetPlayerInfoByGUID",
    # Added in review (M11-01 fix round 1): more identity, roster and typed text.
    "UnitNameUnmodified",
    "UnitPVPName",
    "GetGuildRosterInfo",
    "BNGetFriendInfo",
    "GetMacroBody",
    "C_Club",
    "C_FriendList",
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
)
# §13.1: the capture records no wall-clock time.
# `os` covers os.time() and os.date(); `C_Calendar` covers the in-game calendar.
CLOCK = (
    "time",
    "date",
    "os",
    "GetTime",
    "GetServerTime",
    "GetGameTime",
    "C_DateAndTime",
    "C_Calendar",
)
# No std-declared client global may look like an identity, chat, roster,
# CVar, macro or equipment-set API (security review, M11-01).
STD_PRIVACY = re.compile(
    r"Name|Realm|GUID|Guild|^BN|C_BattleNet|C_Club|C_FriendList|C_ChatInfo|CVar|Macro|C_EquipmentSet"
)
# Names that match STD_PRIVACY but are not identity APIs, each with its reason.
STD_PRIVACY_ALLOW: dict[str, str] = {}
# Events that would deliver chat, guild, community, Battle.net or friends data.
PRIVATE_EVENT = re.compile(r"^(CHAT_MSG_|GUILD_|CLUB_|BN_|FRIENDLIST_)")
# The one place a stored item link may come from (Gear.lua).
CRAFTER_BLANKING = 'local clean , removed = string . gsub ( raw , "Player%-%d+%-%x+" , "" )'
TOC_INTERFACE_LINE = "## Interface: @WOWLAB_INTERFACE@"
# ADR-0026 / §13.1: no names of anyone, no text the owner typed.
NAME_FIELDS = (
    "name",
    "fullName",
    "realm",
    "realmName",
    "guid",
    "GUID",
    "battleTag",
    "accountName",
    "customName",
    "petID",
)
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
OWN_GLOBALS = frozenset({"WowLabCharDB", "WowLabDB", "SLASH_WOWLAB1"})


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
    return f"{path.relative_to(ROOT)}:{token.line}"


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


def test_toc_saved_variables() -> None:
    doc = parse_toc(TOC.read_bytes())
    assert doc.saved_variables_per_character == ("WowLabCharDB",)
    assert doc.saved_variables in {(), ("WowLabDB",)}


def test_toc_loads_every_source_and_nothing_else() -> None:
    doc = parse_toc(TOC.read_bytes())
    listed = [line.path for line in doc.files]
    assert all(not line.conditions and not line.variables for line in doc.files)
    assert sorted(listed) == sorted(p.name for p in SOURCES)
    assert listed[0] == "Core.lua", "Core.lua defines the section registry"


# --- Privacy and flavor rules --------------------------------------------------


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_call_no_forbidden_api(path: Path) -> None:
    bad = [
        f"{_where(path, t)} {t.text}"
        for t in _tokens(path)
        if (t.kind == "name" and t.text in FORBIDDEN_APIS)
        or (t.kind == "string" and any(api in t.text for api in FORBIDDEN_APIS))
    ]
    assert not bad, bad


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_do_no_dynamic_global_lookup(path: Path) -> None:
    tokens = _tokens(path)
    bad = [
        f"{_where(path, t)} {t.text}"
        for i, t in enumerate(tokens)
        if t.kind == "name" and t.text in DYNAMIC_LOOKUP and not _is_field(tokens, i)
    ]
    assert not bad, bad


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_read_no_clock(path: Path) -> None:
    tokens = _tokens(path)
    bad = [
        f"{_where(path, t)} {t.text}"
        for i, t in enumerate(tokens)
        if t.kind == "name" and t.text in CLOCK and not _is_field(tokens, i)
    ]
    assert not bad, bad


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_read_no_name_fields(path: Path) -> None:
    tokens = _tokens(path)
    bad = []
    for i, t in enumerate(tokens):
        if t.kind == "name" and t.text in NAME_FIELDS and _is_field(tokens, i):
            bad.append(f"{_where(path, t)} .{t.text}")
        if (
            t.kind == "string"
            and t.text in NAME_FIELDS
            and i > 0
            and tokens[i - 1].text == "["
            and tokens[i + 1].text == "]"
        ):
            bad.append(f"{_where(path, t)} [{t.text!r}]")
    assert not bad, bad


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


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_every_unit_token_argument_is_player(path: Path) -> None:
    tokens = _tokens(path)
    bad = []
    for i, t in enumerate(tokens):
        if t.kind == "string" and t.text != "player" and UNIT_TOKEN.match(t.text):
            bad.append(f"{_where(path, t)} unit-token literal {t.text!r}")
        if t.kind == "string" and _is_unit_api(t.text):
            # ns.Fn(tbl, "UnitX") would hand a unit API around as a value.
            bad.append(f"{_where(path, t)} unit API named in a string {t.text!r}")
        if t.kind != "name" or not _is_unit_api(t.text):
            continue
        callee = _callee(tokens, i)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None and nxt.text == "(":
            args = tokens[i + 2 : i + 4]
            ok = (
                len(args) == 2
                and args[0].kind == "string"
                and args[0].text == "player"
                and args[1].text in {",", ")"}
            )
            if not ok:
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
    assert not bad, bad


def _render(tokens: list[Token]) -> str:
    return " ".join(f'"{t.text}"' if t.kind == "string" else t.text for t in tokens)


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
    tokens = _tokens(path)
    bad = []
    depth: list[int] = []
    for i, t in enumerate(tokens):
        if t.kind != "op":
            continue
        if t.text == "[":
            depth.append(i)
        elif t.text == "]" and depth:
            depth.pop()
        elif t.text == ".." and depth:
            bad.append(f"{_where(path, t)} '..' inside [...]")
    assert not bad, bad


def test_stored_item_links_only_come_from_crafter_blanking() -> None:
    """ADR-0026: a crafted item's link can hold the crafter's player GUID.

    Every `link =` field in the sources is `link = clean`, and `clean` is bound
    only by the one gsub that blanks `Player-<id>-<hex>` runs, in Gear.lua.
    """
    problems = []
    blanking_sites = []
    for path in SOURCES:
        tokens = _tokens(path)
        for i, t in enumerate(tokens):
            if t.kind != "name":
                continue
            if (
                t.text == "link"
                and tokens[i + 1].text == "="
                and tokens[i - 1].text in {"{", ",", "."}
            ):
                value = tokens[i + 2]
                end = tokens[i + 3].text
                if not (value.kind == "name" and value.text == "clean" and end in {",", "}"}):
                    problems.append(
                        f"{_where(path, t)} link = {value.text} (not the blanked value)"
                    )
            if t.text == "clean" and not _is_field(tokens, i):
                if i > 0 and tokens[i - 1].text == "local":
                    if (
                        _statement_at(tokens, i - 1, len(CRAFTER_BLANKING.split(" ")))
                        == CRAFTER_BLANKING
                    ):
                        blanking_sites.append(_where(path, t))
                    else:
                        problems.append(f"{_where(path, t)} `clean` bound by something else")
                elif tokens[i + 1].text == "=" and tokens[i + 2].text != "=":
                    problems.append(f"{_where(path, t)} `clean` reassigned")
    assert not problems, problems
    assert len(blanking_sites) == 1, blanking_sites
    assert blanking_sites[0].startswith(str(Path("lab/addon/WowLab/Gear.lua")))


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


def test_std_declares_no_identity_like_api() -> None:
    hits = sorted(
        n
        for n in _std_globals() - OWN_GLOBALS
        if STD_PRIVACY.search(n) and n not in STD_PRIVACY_ALLOW
    )
    assert not hits, hits


def test_raw_item_link_only_reaches_item_level_and_blanking() -> None:
    """The raw link (which may hold a crafter GUID) is bound once, in Gear.lua,
    and only type-checked, passed to the item-level lookup, and blanked.
    This covers what the literal `link =` check cannot see."""
    allowed_uses = {
        "type ( raw )",
        "itemLevel ( slot , raw )",
        'string . gsub ( raw , "Player%-%d+%-%x+"',
    }
    binding = 'local raw = GetInventoryItemLink ( "player" , slot )'
    problems = []
    bindings = 0
    for path in SOURCES:
        tokens = _tokens(path)
        for i, t in enumerate(tokens):
            if t.kind == "name" and t.text == "GetInventoryItemLink" and not _is_field(tokens, i):
                if tokens[i - 1].text == "(" and tokens[i - 2].text == "type":
                    continue  # existence test
                if _render(tokens[i - 3 : i + 6]) != binding:
                    problems.append(f"{_where(path, t)} GetInventoryItemLink not bound to `raw`")
            if t.kind != "name" or t.text != "raw" or _is_field(tokens, i):
                continue
            if path.name != "Gear.lua":
                problems.append(f"{_where(path, t)} `raw` outside Gear.lua")
            elif tokens[i - 1].text == "local":
                bindings += 1
                if _render(tokens[i - 1 : i + 8]) != binding:
                    problems.append(f"{_where(path, t)} `raw` bound by something else")
            elif not any(
                use in {_render(tokens[i - k : i - k + len(use.split(" "))]) for k in range(6)}
                for use in allowed_uses
            ):
                problems.append(
                    f"{_where(path, t)} `raw` used as {_render(tokens[i - 3 : i + 3])!r}"
                )
    assert bindings == 1
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
    parameters, multiple assignments and anything else fail (closed)."""
    tokens = _tokens(path)
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
        arg, close = tokens[i + 2], tokens[i + 3]
        if tokens[i + 1].text != "(" or arg.kind != "name" or close.text != ")":
            problems.append(f"{_where(path, t)} {t.text} over {_render(tokens[i + 1 : i + 6])!r}")
        elif not allowed(arg.text):
            problems.append(
                f"{_where(path, t)} {t.text}({arg.text}): a binding is not a built or client table"
            )
    assert not problems, problems


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
    through function calls or table fields."""
    tokens = _tokens(path)
    # Line-based: `local a, b = <expr>` / `a = <expr>`, plus continuation lines
    # that start with `..` (they extend the previous assignment).
    source = [re.sub(r"--.*$", "", line) for line in path.read_text(encoding="utf-8").splitlines()]
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
                assigned.update(last)
        elif _CONCAT.match(line.strip()):
            assigned.update(last)
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
    assert not bad, bad


def _carry_lines() -> list[str]:
    """The `carry = function(saved) ... end,` lines of Customization.lua, comments removed."""
    lines = (ADDON / "Customization.lua").read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines) if re.match(r"^\s*carry = function\(saved\)\s*$", line)
    )
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == " " * indent + "end,")
    return [re.sub(r"--.*$", "", line).rstrip() for line in lines[start + 1 : end]]


_ASSIGN = re.compile(r"^(\s*)(local\s+)?([\w.\[\]#+ ]+?)\s*=(?!=)\s*(.*?),?$")
_SAVED_ACCESS = re.compile(r"\b(?:r|c)(?:\.\w+|\[\w+\])")


def test_carried_customization_is_rebuilt_from_checked_values() -> None:
    """Security review, M11-01: `carry` returns a NEW table (`out`), never its
    input or any table from the saved file, and copies only values that are
    type-checked as numbers, or checked against the two `recorded_at` values,
    at the store or in the enclosing `if`."""
    lines = _carry_lines()
    body = "\n".join(lines)
    problems = []
    returns = re.findall(r"\breturn\s+(\w+)", body)
    if not returns or any(value not in {"nil", "out"} for value in returns):
        problems.append(f"carry returns {returns}, not only nil / out")
    out_bindings = [line for line in lines if re.match(r"^\s*local\s+out\s*=", line)]
    if len(out_bindings) != 1 or not re.match(r"^\s*local\s+out\s*=\s*\{", out_bindings[0]):
        problems.append(f"`out` must be bound once, to a new table: {out_bindings}")
    if re.search(r"^\s*out\s*=", body, re.MULTILINE):
        problems.append("`out` reassigned")
    for n, line in enumerate(lines):
        match = _ASSIGN.match(line)
        if not match or line.lstrip().startswith(("if ", "for ", "elseif ")):
            continue
        indent, is_local, target, value = match.groups()
        if is_local and target == "r":
            continue  # the guarded read of saved.customization
        constants_blanked = re.sub(r'"[^"]*"', '""', value)  # string literals are constants
        if re.search(r"\b(saved|r|c)\b(?![.\[])", constants_blanked):
            problems.append(f"line {n}: stores a saved table itself: {line.strip()!r}")
        guard = ""
        for back in range(n - 1, -1, -1):
            prev = lines[back]
            if prev.strip().startswith("if ") and len(prev) - len(prev.lstrip()) < len(indent):
                guard = prev
                break
        for access in _SAVED_ACCESS.findall(value):
            inline = re.search(
                rf"type\({re.escape(access)}\) == \"number\" and {re.escape(access)} or nil", value
            )
            numeric = f'type({access}) == "number"' in guard
            enum = re.search(
                rf'{re.escape(access)} == "open" or {re.escape(access)} == "applied"', guard
            )
            if not (inline or numeric or enum):
                problems.append(f"line {n}: unchecked copy of {access}: {line.strip()!r}")
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


def _std_globals() -> set[str]:
    names = set()
    in_globals = False
    for line in STD.read_text(encoding="utf-8").splitlines():
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
