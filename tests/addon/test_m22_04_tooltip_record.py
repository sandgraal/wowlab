"""M22-04: the record on the in-game tooltip (`lab/addon/WowLab/Crew.lua`).

Hovering a player adds one line: how often you fought them, the killing
blows each way, the matches your side won, and the Nemesis, Rival team and
kill-on-sight marks. Two sources: the crew book's own match counters, live,
and the table wowlab writes into `WowLabDeskDB` (`wowlab crew send`,
`wowlab_core.desktable`), which the game loads at login or `/reload`.

Nothing here runs Lua (L3). Three parts, as in test_m21_12_full_names.py:

1. Source pins. `recordText`, its helpers and `tipShow` are pinned token for
   token, with the constants they use; `WowLabDeskDB` is named only in
   Crew.lua, declared on the TOC's account line, and never written by the
   addon (the game saves it back as it loaded it); nothing the record line
   does asks the client anything (every value comes from a saved table), so
   no secret value is touched, in combat or out.
2. Stub clients. Each pinned function has a line-for-line Python model, and
   a stub stands in for the game: whether the book is on, whether the player
   is in combat, and the two saved tables as the game loaded them. The line
   is played through the model with the book alone, the desk's table alone,
   and both; in combat and with the book off it shows nothing.
3. The two halves together: a table `desktable` builds, loaded back as the
   game would load it (`luadata`), read by the model.

The model proves the code as pinned. Whether the Forever client shows `·`
and `×` in the tooltip's font, and that a second added line needs no
redraw, are [verify] in game (lab/addon/README.md, the owner's batched
check).

CONSTRUCTED INPUT: every key, count and day below is invented.
"""

# ruff: noqa: RUF001, RUF002 - the line's own multiplication sign (U+00D7), as the game shows it

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import test_lab_addon as g

from wowlab_core import desktable, luadata

CREW = g.ADDON / "Crew.lua"
DESK = g.DESK_DB
BOOK = g.BOOK
KEY = "Inventeda-Fakerealm"
OTHER = "Inventedb-Fakerealm"


def _flow(path: Path = CREW) -> g._Flow:
    return g._Flow(path, g._tokens(path))


def _text(flow: g._Flow, label: str) -> str:
    heads = [h for h, lab in flow.labels.items() if lab == label]
    assert len(heads) == 1, (label, len(heads))
    return g._render(flow.tokens[heads[0] : flow.block_end(heads[0]) + 1])


# ─── 1. pins ────────────────────────────────────────────────────────────────

GATE = "if not crewOn ( ) then return end if crewBlocked ( ) then return end"
# The constants as the tokenizer renders them (a decimal escape is one byte:
# `\194\183` is U+00B7 and `\195\151` U+00D7 in UTF-8), and as the source
# spells them, in ASCII.
CONSTANTS = (
    "local DESK_SCHEMA = 1",
    'local TIP_SEP = " \xc2\xb7 "',
    'local TIP_TIMES = "\xc3\x97"',
    'local DESK_DAY = "^%d%d%d%d%-%d%d%-%d%d$"',
)
SPELLED = ('local TIP_SEP = " \\194\\183 "', 'local TIP_TIMES = "\\195\\151"')
WHOLE_COUNT = (
    "function wholeCount ( value ) "
    'if type ( value ) ~= "number" or value ~= value or value < 0 or value >= 1000000000 then '
    "return 0 end return value - value % 1 end"
)
DESK_PLAYERS = (
    f"function deskPlayers ( ) {GATE} "
    f'if type ( {DESK} ) ~= "table" or {DESK} . schema ~= DESK_SCHEMA then return nil end '
    f'if type ( {DESK} . players ) ~= "table" then return nil end '
    f"return {DESK} . players end"
)
DESK_DAY = (
    f"function deskDay ( ) {GATE} "
    f'if deskPlayers ( ) == nil or type ( {DESK} . day ) ~= "string" then return nil end '
    f"if string . find ( {DESK} . day , DESK_DAY ) == nil then return nil end "
    f"return {DESK} . day end"
)
RECORD_TEXT = (
    f"function recordText ( key ) {GATE} "
    f"local kept = {BOOK} . players [ key ] local row = nil local desk = deskPlayers ( ) "
    'if desk ~= nil and type ( desk [ key ] ) == "table" then row = desk [ key ] end '
    "local faced = 0 local won = 0 "
    'if type ( kept ) == "table" then faced = wholeCount ( kept . faced ) '
    "won = wholeCount ( kept . faced_won ) end "
    "local fights = 0 local kills = 0 local deaths = 0 local matches = 0 local wins = 0 "
    "if row ~= nil then local sentFaced = wholeCount ( row . book_faced ) "
    "local sentWon = wholeCount ( row . book_won ) "
    "if faced >= sentFaced then faced = faced - sentFaced end "
    "if won >= sentWon then won = won - sentWon end "
    "fights = wholeCount ( row . fights ) kills = wholeCount ( row . kills ) "
    "deaths = wholeCount ( row . deaths ) matches = wholeCount ( row . matches ) "
    "wins = wholeCount ( row . won ) end "
    "if won > faced then won = faced end "
    "fights = fights + faced matches = matches + faced wins = wins + won "
    'local text = "" local sep = "" '
    'if fights > 0 then text = text .. "Fought " .. fights .. TIP_TIMES sep = TIP_SEP end '
    "if kills > 0 or deaths > 0 then "
    'text = text .. sep .. "your kills " .. kills .. ", theirs " .. deaths sep = TIP_SEP end '
    "if matches > 0 then "
    'text = text .. sep .. "matches won " .. wins .. " of " .. matches sep = TIP_SEP end '
    'if row ~= nil and row . nemesis == true then text = text .. sep .. "Nemesis" '
    "sep = TIP_SEP end "
    'if row ~= nil and row . rival == true then text = text .. sep .. "Rival team" '
    "sep = TIP_SEP end "
    'if row ~= nil and row . kos == true then text = text .. sep .. "on a Spy KOS list" end '
    'if text == "" then return nil end '
    "local day = deskDay ( ) "
    'if row ~= nil and day ~= nil then text = text .. " (wowlab, " .. shown ( day ) .. ")" end '
    "return text end"
)
TIP_SHOW = (
    f"function tipShow ( ) {GATE} "
    'local key = hoverKey ( ) if type ( key ) ~= "string" then return end '
    'local text = tipText ( key ) if type ( text ) == "string" then '
    "GameTooltip : AddLine ( text , 1 , 0.82 , 0 , true ) end "
    'local record = recordText ( key ) if type ( record ) == "string" then '
    "GameTooltip : AddLine ( record , 1 , 0.82 , 0 , true ) end end"
)
PINS = {
    "wholeCount": WHOLE_COUNT,
    "deskPlayers": DESK_PLAYERS,
    "deskDay": DESK_DAY,
    "recordText": RECORD_TEXT,
    "tipShow": TIP_SHOW,
}
# What the record line's functions may call: one another, the gate, the
# printed-field flattening, and Lua's own `type` and `string.find`. No
# client function: every value is read from a saved table.
RECORD_CALLS = frozenset(
    {"crewOn", "crewBlocked", "deskPlayers", "deskDay", "wholeCount", "shown", "type", "find"}
)


def test_m22_04_the_record_line_is_the_pinned_code() -> None:
    flow = _flow()
    for label, pinned in PINS.items():
        assert _text(flow, label) == pinned, label
    rendered = g._render(flow.tokens)
    for decl in CONSTANTS:
        assert rendered.count(decl) == 1, decl
    source = CREW.read_text(encoding="utf-8")
    for decl in SPELLED:
        assert source.count(decl) == 1, decl
    assert TIP_SEP.encode("utf-8") == b" \xc2\xb7 " and TIP_TIMES.encode("utf-8") == b"\xc3\x97"


def test_m22_04_the_record_line_asks_the_client_nothing() -> None:
    """Labelled source scan: the record line and its helpers call only each
    other, the gate and Lua's own functions, so no value the client could
    make secret is read, compared or stored (ADR-0026); and every one of
    them that reads a table starts with the on and combat tests."""
    flow = _flow()
    t = flow.tokens
    for label in ("recordText", "deskPlayers", "deskDay", "wholeCount"):
        (head,) = [h for h, lab in flow.labels.items() if lab == label]
        fn = flow.functions[head]
        for k in range(fn.start, fn.stop):
            x = t[k]
            if x.kind == "name" and t[k + 1].text == "(" and t[k - 1].text != "function":
                assert x.text in RECORD_CALLS, (label, x.text, x.line)
        if label != "wholeCount":
            assert g._crew_gated(flow, head), label
    client = {name.split(".")[0] for name in g._std_globals()} - g.OWN_GLOBALS
    record = _text(flow, "recordText") + _text(flow, "deskPlayers") + _text(flow, "deskDay")
    used = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", record))
    assert not used & client, used & client


def test_m22_04_the_desk_table_is_read_only_and_only_crew_lua_names_it() -> None:
    """Labelled source scan: no Lua file assigns the table or a field of it
    (the game saves back what it loaded, unchanged), and only Crew.lua names
    it; the TOC declares it account-wide beside the crew book."""
    for path in g.SOURCES:
        flow = _flow(path)
        t = flow.tokens
        named = [k for k, x in enumerate(t) if x.kind == "name" and x.text == DESK]
        in_string = [x for x in t if x.kind == "string" and DESK in x.text]
        if path.name != g.CREW_FILE:
            assert not named and not in_string, path.name
            continue
        assert named and not in_string
        for root, ts in flow.rebound:
            assert root.rstrip(".") != DESK, (path.name, t[ts].line)
        for k in named:
            assert t[k + 1].text in {".", ")"} or t[k - 1].text == "(", t[k].line
            assert not (t[k + 1].text == "=" and t[k - 1].text != "="), t[k].line
    toc = g.parse_toc(g.TOC.read_bytes())
    assert DESK in toc.saved_variables and DESK not in toc.saved_variables_per_character


def test_m22_04_the_status_line_is_gated_and_flattened() -> None:
    flow = _flow()
    (head,) = [h for h, lab in flow.labels.items() if lab == "deskSaid"]
    assert g._crew_gated(flow, head)
    text = _text(flow, "deskSaid")
    assert "shown ( day )" in text
    command = _text(flow, g.CREW_COMMAND)
    assert "reasons ( ) deskSaid ( )" in command


def test_m22_04_the_readme_says_what_the_line_shows() -> None:
    readme = " ".join(g.README.read_text(encoding="utf-8").split())
    for phrase in (
        "The record line",
        "WowLabDeskDB",
        "wowlab crew send",
        "Fought 7× · your kills 1, theirs 3 · matches won 1 of 4 · Nemesis · Rival team",
        "on a Spy KOS list (wowlab, 2026-11-24)",
        "wowlab addon install lab",
    ):
        assert phrase in readme, phrase


# ─── 2. stub clients and the models ─────────────────────────────────────────

TIP_SEP = " · "
TIP_TIMES = "×"
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


@dataclass
class Client:
    """What the record line reads: whether the book is on, whether the
    player is in combat, and the two saved tables as the game loaded them
    (None: the game loaded no such variable)."""

    on: bool = True
    combat: bool = False
    book: dict[str, Any] | None = field(default_factory=lambda: {"players": {}})
    desk: Any = None


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def whole_count(value: object) -> float:
    """`wholeCount`, line for line."""
    if not _number(value) or value != value or value < 0 or value >= 1_000_000_000:  # type: ignore[operator]
        return 0
    return value - value % 1  # type: ignore[operator]


def _gate(c: Client) -> bool:
    return c.on and not c.combat


def desk_players(c: Client) -> Any:
    """`deskPlayers`, line for line."""
    if not _gate(c):
        return None
    if not isinstance(c.desk, dict) or c.desk.get("schema") != 1:
        return None
    if not isinstance(c.desk.get("players"), dict):
        return None
    return c.desk["players"]


def desk_day(c: Client) -> str | None:
    """`deskDay`, line for line (Lua's `^%d%d%d%d%-%d%d%-%d%d$`: ASCII digits)."""
    if not _gate(c):
        return None
    if desk_players(c) is None or not isinstance(c.desk.get("day"), str):
        return None
    if not _DAY.match(c.desk["day"]):
        return None
    return str(c.desk["day"])


def _n(x: float) -> str:
    """A whole number as Lua 5.1's `..` writes it (`%.14g`)."""
    return f"{x:.14g}"


def record_text(c: Client, key: str) -> str | None:
    """`recordText`, line for line."""
    if not _gate(c):
        return None
    assert c.book is not None
    kept = c.book["players"].get(key)
    row = None
    desk = desk_players(c)
    if desk is not None and isinstance(desk.get(key), dict):
        row = desk[key]
    faced: float = 0
    won: float = 0
    if isinstance(kept, dict):
        faced = whole_count(kept.get("faced"))
        won = whole_count(kept.get("faced_won"))
    fights: float = 0
    kills: float = 0
    deaths: float = 0
    matches: float = 0
    wins: float = 0
    if row is not None:
        sent_faced = whole_count(row.get("book_faced"))
        sent_won = whole_count(row.get("book_won"))
        if faced >= sent_faced:
            faced = faced - sent_faced
        if won >= sent_won:
            won = won - sent_won
        fights = whole_count(row.get("fights"))
        kills = whole_count(row.get("kills"))
        deaths = whole_count(row.get("deaths"))
        matches = whole_count(row.get("matches"))
        wins = whole_count(row.get("won"))
    if won > faced:
        won = faced
    fights = fights + faced
    matches = matches + faced
    wins = wins + won
    text = ""
    sep = ""
    if fights > 0:
        text = text + "Fought " + _n(fights) + TIP_TIMES
        sep = TIP_SEP
    if kills > 0 or deaths > 0:
        text = text + sep + "your kills " + _n(kills) + ", theirs " + _n(deaths)
        sep = TIP_SEP
    if matches > 0:
        text = text + sep + "matches won " + _n(wins) + " of " + _n(matches)
        sep = TIP_SEP
    if row is not None and row.get("nemesis") is True:
        text = text + sep + "Nemesis"
        sep = TIP_SEP
    if row is not None and row.get("rival") is True:
        text = text + sep + "Rival team"
        sep = TIP_SEP
    if row is not None and row.get("kos") is True:
        text = text + sep + "on a Spy KOS list"
    if text == "":
        return None
    day = desk_day(c)
    if row is not None and day is not None:
        text = text + " (wowlab, " + day + ")"
    return text


def tip_lines(c: Client, key: str, book_line: str | None = None) -> list[str]:
    """`tipShow`'s lines for a hovered key (`hoverKey` and `tipText` are
    M20-14's, pinned in test_crew_tooltip.py; `book_line` stands in for
    tipText's answer)."""
    if not _gate(c):
        return []
    return [line for line in (book_line, record_text(c, key)) if isinstance(line, str)]


def _desk(players: dict[str, Any], day: Any = "2026-11-24", schema: Any = 1) -> dict[str, Any]:
    return {"schema": schema, "day": day, "count": len(players), "players": players}


def test_m22_04_the_book_alone() -> None:
    """No desk table: the line counts the book's own matches against the
    player, all of them live; no marks, no date."""
    c = Client(book={"players": {KEY: {"faced": 4, "faced_won": 1, "allied": 9}}})
    assert record_text(c, KEY) == "Fought 4× · matches won 1 of 4"
    # Only grouped with (no match against you): no line.
    c.book = {"players": {KEY: {"met": 3, "allied": 2}}}
    assert record_text(c, KEY) is None
    assert record_text(c, OTHER) is None


def test_m22_04_the_desk_table_alone() -> None:
    """A player the book does not hold (an open-world foe from a combat
    log, a Spy-only mark): the desk's row, its marks and its day."""
    row = {"fights": 7, "kills": 1, "deaths": 3, "matches": 4, "won": 1}
    row |= {"nemesis": True, "rival": True, "kos": True}
    c = Client(desk=_desk({KEY: row, OTHER: {"kos": True}}))
    assert record_text(c, KEY) == (
        "Fought 7× · your kills 1, theirs 3 · matches won 1 of 4 · Nemesis · Rival team · on a Spy KOS list "
        "(wowlab, 2026-11-24)"
    )
    assert record_text(c, OTHER) == "on a Spy KOS list (wowlab, 2026-11-24)"


def test_m22_04_both_add_only_the_matches_recorded_since_the_write() -> None:
    """The book's counters as wowlab wrote them are in the row: only the
    matches recorded since are added, so nothing is counted twice."""
    row = {"fights": 7, "kills": 1, "deaths": 3, "matches": 4, "won": 1, "nemesis": True}
    row |= {"book_faced": 4, "book_won": 1}
    c = Client(
        book={"players": {KEY: {"faced": 6, "faced_won": 2}}},
        desk=_desk({KEY: row}),
    )
    assert record_text(c, KEY) == (
        "Fought 9× · your kills 1, theirs 3 · matches won 2 of 6 · Nemesis (wowlab, 2026-11-24)"
    )
    # Nothing new since the write: the desk's numbers as they are.
    c.book = {"players": {KEY: {"faced": 4, "faced_won": 1}}}
    assert record_text(c, KEY) == (
        "Fought 7× · your kills 1, theirs 3 · matches won 1 of 4 · Nemesis (wowlab, 2026-11-24)"
    )
    # The book dropped the player and met them again: the whole new counter.
    c.book = {"players": {KEY: {"faced": 1}}}
    assert record_text(c, KEY) == (
        "Fought 8× · your kills 1, theirs 3 · matches won 1 of 5 · Nemesis (wowlab, 2026-11-24)"
    )


def test_m22_04_both_lines_show_in_order() -> None:
    c = Client(book={"players": {KEY: {"faced": 1}}})
    assert tip_lines(c, KEY, "Crew book: You rated bad.") == [
        "Crew book: You rated bad.",
        "Fought 1× · matches won 0 of 1",
    ]
    assert tip_lines(c, KEY, None) == ["Fought 1× · matches won 0 of 1"]


@pytest.mark.parametrize(("on", "combat"), [(False, False), (True, True), (False, True)])
def test_m22_04_nothing_with_the_book_off_or_in_combat(on: bool, combat: bool) -> None:
    c = Client(
        on=on,
        combat=combat,
        book={"players": {KEY: {"faced": 3}}},
        desk=_desk({KEY: {"fights": 2, "kos": True}}),
    )
    assert record_text(c, KEY) is None and tip_lines(c, KEY, "x") == []


@pytest.mark.parametrize(
    "desk",
    [
        _desk({KEY: {"fights": 2}}, schema=2),
        _desk({KEY: {"fights": 2}}, schema="1"),
        {"schema": 1, "players": "x"},
        "not a table",
        None,
    ],
)
def test_m22_04_a_desk_table_this_version_cannot_read_is_left_out(desk: Any) -> None:
    c = Client(book={"players": {KEY: {"faced": 2, "faced_won": 2}}}, desk=desk)
    assert record_text(c, KEY) == "Fought 2× · matches won 2 of 2"


@pytest.mark.parametrize(
    "day", ["24 Nov", "2026-11-24|r", "2026-11-24\n", "|cffff0000x", 20261124, None]
)
def test_m22_04_a_day_not_in_its_form_is_not_shown(day: Any) -> None:
    c = Client(desk=_desk({KEY: {"fights": 2}}, day=day))
    assert record_text(c, KEY) == "Fought 2×"


@pytest.mark.parametrize(
    "value", [-3, 1.5, float("nan"), float("inf"), 1e12, "7", True, None, {"x": 1}]
)
def test_m22_04_a_count_that_is_not_a_whole_number_counts_its_floor_or_nothing(
    value: Any,
) -> None:
    got = whole_count(value)
    assert got == (1 if value == 1.5 else 0)
    c = Client(desk=_desk({KEY: {"fights": value, "kos": True}}))
    assert record_text(c, KEY) in (
        "Fought 1× · on a Spy KOS list (wowlab, 2026-11-24)",
        "on a Spy KOS list (wowlab, 2026-11-24)",
    )


def test_m22_04_won_never_passes_the_matches() -> None:
    c = Client(book={"players": {KEY: {"faced": 1, "faced_won": 5}}})
    assert record_text(c, KEY) == "Fought 1× · matches won 1 of 1"


# ─── 3. the two halves together ─────────────────────────────────────────────


def test_m22_04_a_table_desktable_writes_reads_back_as_the_line() -> None:
    """A table built by `desktable`, put into a file and loaded back as the
    game loads it (as data), gives the line the model of the addon shows."""
    rows = [
        desktable.DeskRow(
            key=KEY,
            fights=7,
            kills=1,
            deaths=3,
            matches=4,
            won=1,
            nemesis=True,
            rival=True,
            kos=True,
            book_faced=4,
            book_won=1,
        ),
        desktable.DeskRow(key=OTHER, kos=True),
    ]
    data = desktable.with_table(b"\r\nWowLabDB = {\r\n}\r\n", rows, "2026-11-24")
    loaded = luadata.parse(data).to_python()
    c = Client(book={"players": {KEY: {"faced": 4, "faced_won": 1}}}, desk=loaded[DESK])
    assert record_text(c, KEY) == (
        "Fought 7× · your kills 1, theirs 3 · matches won 1 of 4 · Nemesis · Rival team · on a Spy KOS list "
        "(wowlab, 2026-11-24)"
    )
    assert record_text(c, OTHER) == "on a Spy KOS list (wowlab, 2026-11-24)"
    assert desktable.DESK_VARIABLE == DESK and desktable.DESK_SCHEMA == 1
