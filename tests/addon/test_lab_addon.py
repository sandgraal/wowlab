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
  a call that writes to it.

The stored-value check (M11-13) also does not check table keys, and it trusts
the Core.lua plumbing listed in `PLUMBING_STORES`.
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
    (
        "Core.lua",
        "ns.Section",
        "section . events_unregistered = section . events_unregistered or { }",
    ): "the section's list of events the client refused",
    (
        "Core.lua",
        "ns.Section",
        "missing [ # missing + 1 ] = event",
    ): "an event name from the section's own `events` literal",
    (
        "Core.lua",
        "gather",
        "record . events_unregistered = section . events_unregistered",
    ): "the list of refused event names built in ns.Section",
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
                    if after < e and self.call_start(after):
                        return True  # a call through an index or parentheses
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
_TRUSTED_NAMES = frozenset(
    {
        "type",
        "pcall",
        "ipairs",
        "pairs",
        "select",
        "tostring",
        "tonumber",
        "string",
        "math",
        "table",
        "ns",
    }
)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_sources_store_nothing_through_table_library(path: Path) -> None:
    """Review of M11-13: `table.insert(out, api_table)` stores through a call,
    which the stored-value check does not read. The addon appends with
    `t[#t + 1] = v`; `table` is used only as `table.sort`, and `tinsert` (and a
    string naming either) never appears."""
    tokens = _tokens(path)
    bad = []
    for i, t in enumerate(tokens):
        if t.kind == "string" and t.text in {"insert", "tinsert"}:
            bad.append(f"{_where(path, t)} string {t.text!r}")
        if t.kind != "name" or _is_field(tokens, i):
            continue
        if t.text == "tinsert":
            bad.append(f"{_where(path, t)} tinsert")
        if t.text == "table" and [x.text for x in tokens[i + 1 : i + 3]] != [".", "sort"]:
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
    variable, local or global function, or assignment binds `type`, `pcall`,
    `ipairs`, `pairs`, `select`, `tostring`, `tonumber`, `string`, `math`,
    `table` or `ns`, and no field of `string`, `math` or `table` is assigned;
    the one `local _, ns = ...` at the top of each file is the exception."""
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
        if label in _TRUSTED_NAMES and t[head + 1].text == label:
            bad.append(f"{_where(path, t[head])} function {label}")
    assert not bad, bad


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
