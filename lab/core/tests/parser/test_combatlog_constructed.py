"""`combatlog` on constructed lines (L8: every input here is `constructed`).

The real Forever log (`test_combatlog_fixtures.py`) has no `COMBATANT_INFO`,
no `[...]`/`(...)` group and no quoted string holding a comma or a quote
(docs/LAB_FORMATS.md §8, amendment 2026-09-28). Until a boss-pull log is
captured, those are graded here on lines written from §8 and community
documentation. Each is **[verify]**: when a real `COMBATANT_INFO` line is
committed, a grader on it replaces the constructed one (M10-13 amendment of
2026-09-27). The rest are boundary and hostile inputs: timestamp shapes,
line endings, bytes that are not UTF-8, lines too long or nested too deep,
and the distinct shapes that are yielded as `Unparsed`.
"""

from __future__ import annotations

import time

import pytest

from wowlab_core import combatlog
from wowlab_core.combatlog import (
    MAX_DEPTH,
    MAX_LINE_BYTES,
    Group,
    Quoted,
    Record,
    Unparsed,
    tokenize,
    tokenize_line,
)

TS = "9/20/2026 21:14:05.871-4"


def _record(text: str) -> Record:
    entry = tokenize_line(text)
    assert isinstance(entry, Record), entry
    return entry


def _unparsed(text: str) -> Unparsed:
    entry = tokenize_line(text)
    assert isinstance(entry, Unparsed), entry
    return entry


# ─── COMBATANT_INFO and groups [verify] ──────────────────────────────────────

# Constructed [verify]: based on community documentation of the *retail*
# COMBATANT_INFO layout (reviewer memory, not a fixture). Forever's layout
# is unknown; it is reported to use the modern talent API (C_Traits),
# which is why the retail shape is the guess.
#   1 player GUID, 2 faction, 3-6 strength/agility/stamina/intellect,
#   7-9 dodge/parry/block, 10-12 crit melee/ranged/spell, 13 speed,
#   14 leech, 15-17 haste melee/ranged/spell, 18 avoidance, 19 mastery,
#   20-22 versatility (damage done, healing done, damage taken),
#   23 armor, 24 spec id                                        [verify]
#   25 [talents]: (node id, entry id, rank) per talent          [verify]
#   26 (PvP talents): four ids                                  [verify]
#   27 [items]: (item id, item level, (enchants), (bonus ids), (gems));
#      an empty slot is (0,0,(),(),()); the enchant and gem tuple
#      widths are not established                               [verify]
#   28 [auras]: flat pairs of source GUID, spell id              [verify]
#   29-32 honor level, season, rating, tier                      [verify]
# Values are invented.
COMBATANT_INFO = (
    f"{TS}  COMBATANT_INFO,Player-1234-0ABCDEF0,1,1023,432,17640,1412,0,0,0,"
    "1098,1098,1098,280,0,2400,2400,2400,1000,1250,1250,1250,1250,5021,62,"
    "[(62123,80154,1),(62124,80155,2)],"
    "(0,248,356,3589),"
    "[(212425,489,(),(7187,6652,10267,1498),()),(207161,489,(7534,0,0),(),(192985,0,0)),"
    "(0,0,(),(),())],"
    "[Player-1234-0ABCDEF0,1459,Player-1234-0ABCDEF0,21562],"
    "48,0,0,0"
)


@pytest.mark.parser
def test_combatant_info_groups_become_nested_lists_constructed() -> None:
    r = _record(COMBATANT_INFO)
    assert r.event == "COMBATANT_INFO"
    assert len(r.fields) == 32
    assert r.fields[0] == "Player-1234-0ABCDEF0"
    talents, pvp, items, auras = r.fields[24:28]
    assert talents == Group(
        bracket="[",
        items=(
            Group(bracket="(", items=("62123", "80154", "1")),
            Group(bracket="(", items=("62124", "80155", "2")),
        ),
    )
    assert pvp == Group(bracket="(", items=("0", "248", "356", "3589"))
    assert isinstance(items, Group) and items.bracket == "["
    assert len(items.items) == 3
    first_item = items.items[0]
    assert first_item == Group(
        bracket="(",
        items=(
            "212425",
            "489",
            Group(bracket="(", items=()),
            Group(bracket="(", items=("7187", "6652", "10267", "1498")),
            Group(bracket="(", items=()),
        ),
    )
    assert auras == Group(
        bracket="[", items=("Player-1234-0ABCDEF0", "1459", "Player-1234-0ABCDEF0", "21562")
    )
    assert r.fields[28:] == ("48", "0", "0", "0")


@pytest.mark.parser
def test_empty_groups_constructed() -> None:
    r = _record(f"{TS}  COMBATANT_INFO,[],(),[()],x")
    assert r.fields == (
        Group(bracket="[", items=()),
        Group(bracket="(", items=()),
        Group(bracket="[", items=(Group(bracket="(", items=()),)),
        "x",
    )


@pytest.mark.parser
def test_quoted_strings_inside_groups_constructed() -> None:
    r = _record(f'{TS}  EVENT,[("a, b",1),("c",(2,"d]"))],"e)"')
    assert r.fields == (
        Group(
            bracket="[",
            items=(
                Group(bracket="(", items=(Quoted(text="a, b"), "1")),
                Group(
                    bracket="(",
                    items=(Quoted(text="c"), Group(bracket="(", items=("2", Quoted(text="d]")))),
                ),
            ),
        ),
        Quoted(text="e)"),
    )


@pytest.mark.parser
def test_groups_nested_to_the_limit_constructed() -> None:
    deep = "[" * MAX_DEPTH + "1" + "]" * MAX_DEPTH
    r = _record(f"{TS}  EVENT,{deep}")
    value = r.fields[0]
    for _ in range(MAX_DEPTH):
        assert isinstance(value, Group)
        (value,) = value.items
    assert value == "1"
    too_deep = "[" * (MAX_DEPTH + 1) + "1" + "]" * (MAX_DEPTH + 1)
    u = _unparsed(f"{TS}  EVENT,{too_deep}")
    assert u.reason == f"groups nested deeper than {MAX_DEPTH}"
    assert u.column == len(f"{TS}  EVENT,") + MAX_DEPTH


@pytest.mark.parser
def test_hostile_nesting_is_bounded_constructed() -> None:
    u = _unparsed(f"{TS}  EVENT," + "(" * 200_000)
    assert "nested deeper" in u.reason


# ─── quoted strings: commas and quotes [verify] ──────────────────────────────


@pytest.mark.parser
@pytest.mark.parametrize(
    ("body", "fields"),
    [
        # A comma inside a quoted string (a localized failure reason, an item
        # or NPC name); the client is not known to escape it [verify].
        ('"Not yet recovered, try again",0', (Quoted(text="Not yet recovered, try again"), "0")),
        ('"a,b,c"', (Quoted(text="a,b,c"),)),
        ('","', (Quoted(text=","),)),
        # A quote inside: kept, unless a delimiter follows it [verify].
        ('"Summon "Mr. Pinchy"",1', (Quoted(text='Summon "Mr. Pinchy"'), "1")),
        ('"say \\"hi\\" now"', (Quoted(text='say \\"hi\\" now'),)),
        ('""', (Quoted(text=""),)),
        ('"",""', (Quoted(text=""), Quoted(text=""))),
        ('"Lil\' Ragnaros"', (Quoted(text="Lil' Ragnaros"),)),
        ('"nil",nil', (Quoted(text="nil"), "nil")),
        ('"Été",0x10a48', (Quoted(text="Été"), "0x10a48")),
    ],
    ids=[
        "comma",
        "commas",
        "only-a-comma",
        "inner-quotes",
        "backslash-not-an-escape",
        "empty",
        "two-empty",
        "apostrophe",
        "quoted-nil-is-not-nil",
        "non-ascii",
    ],
)
def test_quoted_strings_constructed(body: str, fields: tuple[object, ...]) -> None:
    r = _record(f"{TS}  EVENT,{body}")
    assert r.fields == fields
    # The quick path (no groups) and the general scanner agree.
    assert combatlog._quoted_fields(body) == list(fields)
    assert combatlog._scan(body, 0) == list(fields)


@pytest.mark.parser
@pytest.mark.parametrize(
    "body",
    ['"open', 'a,"open,b', '"a"b', 'ab"c', '"a" ,b', '"', 'x,"y'],
)
def test_the_quick_path_leaves_errors_to_the_scanner_constructed(body: str) -> None:
    assert combatlog._quoted_fields(body) is None
    assert isinstance(tokenize_line(f"{TS}  EVENT,{body}"), Unparsed)


# ─── bare tokens ─────────────────────────────────────────────────────────────


@pytest.mark.parser
def test_bare_tokens_are_kept_as_written_constructed() -> None:
    r = _record(f"{TS}  EVENT,nil,0x0,-1,0.5,1e3,0000000000000000,ST,BUFF, spaced ,")
    assert r.fields == (
        "nil",
        "0x0",
        "-1",
        "0.5",
        "1e3",
        "0000000000000000",
        "ST",
        "BUFF",
        " spaced ",
        "",
    )


@pytest.mark.parser
def test_empty_fields_constructed() -> None:
    assert _record(f"{TS}  EVENT,,a,,").fields == ("", "a", "", "")
    assert _record(f"{TS}  EVENT").fields == ()
    assert _record(f"{TS}  EVENT,[,]").fields == (Group(bracket="[", items=("", "")),)


# ─── timestamps ──────────────────────────────────────────────────────────────


@pytest.mark.parser
@pytest.mark.parametrize(
    "stamp",
    [
        "4/1/2026 02:16:30.666-4",  # the real fixture's shape (shifted)
        "4/01/2026 02:16:30.666-4",  # a padded day: unverified (§8: no real log had a day below 10)
        "04/1/2026 02:16:30.666-4",
        "12/25/2026 23:59:59.999-5",
        "9/20/2026 1:02:03.456-4",  # a one-digit hour
        "9/20/2026 21:14:03-4",  # no fraction
        "9/20/2026 21:14:03.1234567-4",
        "9/20/2026 21:14:03.123+5:30",  # positive and half-hour offsets [verify]
        "9/20/2026 21:14:03.123+0530",
        "9/20/2026 21:14:03.123-10",
        "9/20/2026 21:14:03.123",  # no offset
        "9/20 21:14:03.123",  # no year (older retail logs, community docs) [verify]
        "2/31/2026 25:61:61.000-4",  # shape only: never checked as a date or time
    ],
)
def test_timestamp_shapes_accepted_constructed(stamp: str) -> None:
    r = _record(f"{stamp}  SPELL_DAMAGE,a")
    assert r.timestamp == stamp


@pytest.mark.parser
@pytest.mark.parametrize(
    "stamp",
    [
        "2026-09-20 21:14:03.123",
        "9/20/26 21:14:03.123-4",
        "9/20/2026T21:14:03.123-4",
        "9/20/2026 21:14.123-4",
        "123/1/2026 02:16:30.666-4",
        "9/20/2026 21:14:03.123 -4",
        "",
    ],
)
def test_timestamp_shapes_refused_constructed(stamp: str) -> None:
    u = _unparsed(f"{stamp}  SPELL_DAMAGE,a")
    assert u.reason == "the text before the two spaces is not a timestamp"
    assert u.column == 0


# ─── the shapes yielded as Unparsed ──────────────────────────────────────────


@pytest.mark.parser
@pytest.mark.parametrize(
    ("text", "reason", "column"),
    [
        ("", "no two spaces after a timestamp", None),
        ("junk", "no two spaces after a timestamp", None),
        (f"{TS} SPELL_DAMAGE,a", "no two spaces after a timestamp", None),
        (f"{TS}  ", "the first field is not an event name", 26),
        (f"{TS}  ,a", "the first field is not an event name", 26),
        (f'{TS}  "EVENT",a', "the first field is not an event name", 26),
        (f"{TS}  [EVENT],a", "the first field is not an event name", 26),
        (f"{TS}  SPELL DAMAGE,a", "the first field is not an event name", 26),
        (f'{TS}  EVENT,"open', "a quoted string is not closed", 32),
        (f'{TS}  EVENT,"a"b', "a quoted string is not closed", 32),
        (f'{TS}  EVENT,ab"c', "'\"' inside a bare field", 34),
        (f"{TS}  EVENT,a[1]", "'[' inside a bare field", 33),
        (f"{TS}  EVENT,a]", "']' closes no group", 33),
        (f"{TS}  EVENT,[1)", "')' closes a group opened with '['", 34),
        (f"{TS}  EVENT,[1,(2]", "']' closes a group opened with '('", 37),
        (f"{TS}  EVENT,[1,2", "a group opened with '[' is not closed", 32),
        (f"{TS}  EVENT,[1]x", "'x' where a comma or the end of the line should be", 35),
        (f'{TS}  EVENT,"a" ,b', "a quoted string is not closed", 32),
    ],
)
def test_unparsed_shapes_constructed(text: str, reason: str, column: int | None) -> None:
    u = _unparsed(text)
    assert (u.reason, u.column) == (reason, column)
    assert u.raw == text


# ─── lines from bytes ────────────────────────────────────────────────────────


@pytest.mark.parser
def test_endings_are_per_line_and_a_lone_cr_stays_in_the_text_constructed() -> None:
    data = f"{TS}  A,1\r\n{TS}  B,2\n{TS}  C,3\r\r\n{TS}  D,4\rE\r\n{TS}  F,5".encode()
    entries = list(tokenize(data))
    assert [e.ending for e in entries] == ["\r\n", "\n", "\r\n", "\r\n", ""]
    assert [(e.event, e.fields) for e in entries if isinstance(e, Record)] == [
        ("A", ("1",)),
        ("B", ("2",)),
        ("C", ("3\r",)),
        ("D", ("4\rE",)),
        ("F", ("5",)),
    ]
    assert [e.offset for e in entries] == [0, 31, 61, 93, 126]
    assert b"".join(e.raw.encode() + e.ending.encode() for e in entries) == data


@pytest.mark.parser
def test_bytes_that_are_not_utf8_survive_constructed() -> None:
    data = f'{TS}  ZONE_CHANGE,0,"Caf'.encode() + b'\xe9",0\xff\r\n'
    (entry,) = tokenize(data)
    assert isinstance(entry, Record)
    assert entry.fields == ("0", Quoted(text="Caf\udce9"), "0\udcff")
    assert entry.raw.encode("utf-8", "surrogateescape") + b"\r\n" == data


@pytest.mark.parser
def test_an_empty_input_and_blank_lines_constructed() -> None:
    assert list(tokenize(b"")) == []
    entries = list(tokenize(b"\r\n\n"))
    assert [(type(e), e.raw, e.ending) for e in entries] == [
        (Unparsed, "", "\r\n"),
        (Unparsed, "", "\n"),
    ]


@pytest.mark.parser
def test_a_line_at_the_limit_tokenizes_and_one_over_it_is_cut_constructed() -> None:
    head = f"{TS}  EVENT,".encode()
    at_limit = head + b"x" * (MAX_LINE_BYTES - len(head))
    over = head + b"y" * (MAX_LINE_BYTES - len(head) + 1)
    after = f"{TS}  NEXT,1".encode()
    data = at_limit + b"\n" + over + b"\r\n" + after + b"\n"
    first, second, third = tokenize(data)
    assert isinstance(first, Record) and len(first.raw) == MAX_LINE_BYTES
    assert isinstance(second, Unparsed)
    assert second.reason.startswith(f"a line longer than {MAX_LINE_BYTES} bytes")
    assert second.truncated and second.length == len(over) and second.ending == "\r\n"
    assert len(second.raw) == MAX_LINE_BYTES and second.offset == len(at_limit) + 1
    assert isinstance(third, Record) and third.event == "NEXT"
    assert third.offset == len(at_limit) + 1 + len(over) + 2


@pytest.mark.parser
def test_unparsed_lines_carry_their_byte_length_constructed() -> None:
    (entry,) = tokenize("é junk\r\n".encode())
    assert isinstance(entry, Unparsed)
    assert (entry.length, entry.truncated, entry.ending) == (7, False, "\r\n")


def _long_line_in_pieces(pieces: list[bytes]) -> list[Record | Unparsed]:
    splitter = combatlog._Splitter()
    out: list[Record | Unparsed] = []
    for piece in pieces:
        out.extend(splitter.feed(piece))
        assert len(splitter.pending) <= MAX_LINE_BYTES
    return out + splitter.end()


@pytest.mark.parser
@pytest.mark.parametrize(
    "cut",
    [65536, MAX_LINE_BYTES + 7, 3 * MAX_LINE_BYTES],
    ids=["constructed-64KiB", "constructed-just-over", "constructed-at-the-CR"],
)
def test_an_overlong_line_is_the_same_however_it_is_chunked_constructed(cut: int) -> None:
    # A 3 MiB line ending in CRLF, then a normal line. The CR may arrive in
    # one piece and its LF in the next ("at-the-CR").
    line = b"z" * (3 * MAX_LINE_BYTES)
    data = line + b"\r\n" + f"{TS}  NEXT,1\n".encode()
    whole = list(tokenize(data))
    pieces = [data[i : i + cut] for i in range(0, len(data), cut)]
    if cut == 3 * MAX_LINE_BYTES:
        pieces = [data[: cut + 1], data[cut + 1 :]]  # ... b"\r" | b"\n..."
    streamed = _long_line_in_pieces(pieces)
    assert streamed == whole
    first, second = whole
    assert isinstance(first, Unparsed) and first.truncated
    assert (first.offset, first.length, first.ending) == (0, len(line), "\r\n")
    assert isinstance(second, Record) and second.offset == len(line) + 2


@pytest.mark.parser
def test_an_overlong_line_with_no_break_at_the_end_constructed() -> None:
    step = b"z" * 65536
    n = MAX_LINE_BYTES // len(step) + 2
    out = _long_line_in_pieces([step] * n)
    (only,) = out
    assert isinstance(only, Unparsed) and only.truncated
    assert (only.offset, only.length, only.ending) == (0, n * len(step), "")


@pytest.mark.parser
def test_an_unclosed_quote_over_a_megabyte_is_refused_in_linear_time_constructed() -> None:
    head = f'{TS}  EVENT,"'
    line = head + ",a" * ((MAX_LINE_BYTES - len(head)) // 2)
    started = time.perf_counter()
    entry = tokenize_line(line)
    elapsed = time.perf_counter() - started
    assert isinstance(entry, Unparsed) and entry.reason == "a quoted string is not closed"
    assert elapsed < 1.0, f"{elapsed:.2f}s for a {len(line)}-byte line"
    closed = tokenize_line(head + 'x"' + ",a" * ((MAX_LINE_BYTES - len(head)) // 2))
    assert isinstance(closed, Record)
