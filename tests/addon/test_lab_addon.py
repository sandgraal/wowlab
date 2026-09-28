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


def test_pet_rows_bind_only_species_and_owned() -> None:
    """GetPetInfoByIndex's first return is a battle-pet GUID: it is only ever
    reached as `local a, b = select(2, ns.Call(byIndex, ...))`."""
    problems = []
    lookups = 0
    for path in SOURCES:
        tokens = _tokens(path)
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
