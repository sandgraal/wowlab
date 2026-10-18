"""`combatlog` with `events=`: filter lines by event before tokenizing (M13-41).

The grade is the tokenizer's own output. For every line, a filtered read
gives exactly what an unfiltered read gives, except that a line whose first
field after the timestamp is a bare event name not asked for comes back as
a `Skipped` holding the same offset, text and ending. On the real Forever
log (every `combatlog` row of the fixture index) that is checked line by
line for several event sets, the lines of a filtered read rebuild the file,
and `cast_counts` gives the counts M13-08 pinned, filtered or not.

Inputs labelled `constructed` are boundary and hostile cases (L8) the real
log lacks: an event field that is quoted, holds a comma, carries a quote or
bracket, a byte that is not UTF-8, a timestamp in non-ASCII digits, an
over-long line, and a malformed line of an event not asked for (the one case
whose `unparsed` count differs, by design: the line is not tokenized).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from wowlab_core import combatlog
from wowlab_core.combatlog import (
    CAST_COUNT_EVENTS,
    MAX_LINE_BYTES,
    Entry,
    Record,
    Skipped,
    Unparsed,
    read_log,
    tokenize,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"
FOREVER_LOG = FIXTURES / "macos" / "forever" / "Logs" / "WoWCombatLog-040126_021630.txt"

TS = "4/1/2026 02:16:37.862-4"
HEADER = f"{TS}  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,1.60.1,PROJECT_ID,18"
CAST_TAIL = (
    'Player-9999-00000001,"Constructed-Unit-",0x511,0x80000000,'
    'Creature-0-1-0-2-1554-0000000000,"Constructed Target",0x10a48,0x80000000,'
    '172,"Constructed Bolt",0x20'
)


def _indexed(kind: str) -> list[str]:
    found = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) > 1 and cells[1] == kind:
            found.append(cells[0].strip("`"))
    return found


LOGS = _indexed("combatlog")


def _rebuilt(entries: Iterable[Entry | Skipped]) -> bytes:
    return b"".join(e.raw.encode("utf-8", "surrogateescape") + e.ending.encode() for e in entries)


def _same_but_skipped(
    unfiltered: list[Entry], filtered: list[Entry | Skipped], events: frozenset[str]
) -> None:
    """Every filtered line is the unfiltered one, or a Skipped of an event not
    asked for, standing for that line (a record of that event, or a line the
    tokenizer would stop on after its event name)."""
    assert len(filtered) == len(unfiltered)
    for u, f in zip(unfiltered, filtered, strict=True):
        if isinstance(f, Skipped):
            assert f.event not in events
            assert (f.offset, f.raw, f.ending) == (u.offset, u.raw, u.ending)
            if isinstance(u, Record):
                assert u.event == f.event
            else:
                assert not u.truncated
                assert u.column is not None and u.column > len(TS) + 2 + len(f.event)
        else:
            assert f == u


def _lines(*lines: str) -> bytes:
    return "".join(f"{line}\r\n" for line in lines).encode("utf-8")


# ─── the real log ────────────────────────────────────────────────────────────


@pytest.mark.parser
def test_the_index_has_the_forever_log() -> None:
    assert FOREVER_LOG.relative_to(FIXTURES).as_posix() in LOGS


@pytest.mark.parser
@pytest.mark.parametrize("name", LOGS, ids=LOGS)
@pytest.mark.parametrize(
    "events",
    [
        CAST_COUNT_EVENTS,
        frozenset(),
        frozenset({"SPELL_DAMAGE"}),
        frozenset({"SWING_DAMAGE", "UNIT_DIED", "NOT_AN_EVENT_IN_THE_LOG"}),
    ],
    ids=["cast-counts", "none", "one", "several"],
)
def test_real_log_filtered_is_the_tokenizer_with_the_rest_skipped(
    name: str, events: frozenset[str]
) -> None:
    path = FIXTURES / name
    data = path.read_bytes()
    unfiltered = list(read_log(path))
    filtered = list(read_log(path, events=events))
    _same_but_skipped(unfiltered, filtered, events)
    # The filter filters: every record of an event not asked for is skipped,
    # and nothing else is.
    for u, f in zip(unfiltered, filtered, strict=True):
        skip = isinstance(u, Record) and u.event not in events
        assert isinstance(f, Skipped) is skip, u.raw[:80]
    assert _rebuilt(filtered) == data
    assert list(tokenize(data, events=events)) == filtered


@pytest.mark.parser
def test_real_log_cast_counts_are_the_same_filtered() -> None:
    """The counts M13-08 pinned on the real log, with and without the filter."""
    filtered = combatlog.cast_counts(read_log(FOREVER_LOG, events=CAST_COUNT_EVENTS))
    assert filtered == combatlog.cast_counts(read_log(FOREVER_LOG))
    assert (filtered.lines, filtered.unparsed, filtered.versions) == (79, 0, ("22",))
    assert (filtered.cast_events, filtered.mine, filtered.others) == (7, 6, 1)
    assert (filtered.other_version, filtered.not_read, filtered.sources) == (0, 0, 1)
    assert (filtered.first_timestamp, filtered.last_timestamp) == (
        "4/1/2026 02:16:37.862-4",
        "4/1/2026 02:17:00.351-4",
    )
    assert [(s.spell_id, s.spell_name, s.casts) for s in filtered.spells] == [
        (172, "Corruption", 2),
        (980, "Bane of Agony", 2),
        (348, "Immolate", 1),
        (695, "Shadow Bolt", 1),
    ]


@pytest.mark.parser
def test_real_log_tokenizes_only_the_lines_asked_for() -> None:
    """Of the 79 lines, the version line and the seven casts are tokenized."""
    filtered = list(read_log(FOREVER_LOG, events=CAST_COUNT_EVENTS))
    read = [e for e in filtered if not isinstance(e, Skipped)]
    assert [e.event for e in read if isinstance(e, Record)] == [
        "COMBAT_LOG_VERSION",
        *["SPELL_CAST_SUCCESS"] * 7,
    ]
    assert len(read) == 8


# ─── constructed: the event field ────────────────────────────────────────────


@pytest.mark.parser
@pytest.mark.parametrize(
    "line",
    [
        f'{TS}  "SPELL_CAST_SUCCESS",{CAST_TAIL}',  # constructed: the asked-for event, quoted
        f'{TS}  "SPELL_DAMAGE",{CAST_TAIL}',  # constructed: another event, quoted
        f'{TS}  "SPELL_CAST,SUCCESS",{CAST_TAIL}',  # constructed: a quoted event holding a comma
        f'{TS}  "SPELL_DAMAGE,X",{CAST_TAIL}',  # constructed: the same, another event
        f'{TS}  "SPELL_DAMAGE",SPELL_CAST_SUCCESS,{CAST_TAIL}',  # constructed: quoted, then asked-for
        f'{TS}  SPELL_DAMAGE"x",{CAST_TAIL}',  # constructed: a quote in the name
        f"{TS}  SPELL_DAMAGE[1],{CAST_TAIL}",  # constructed: a group after the name
        f"{TS}  (SPELL_DAMAGE),{CAST_TAIL}",  # constructed: the name in a group
        f"{TS}  ,SPELL_DAMAGE,{CAST_TAIL}",  # constructed: an empty first field
        f"{TS}  SPELL DAMAGE,{CAST_TAIL}",  # constructed: a space in the name
        f"{TS} SPELL_DAMAGE,{CAST_TAIL}",  # constructed: one space, no separator
        f"not a stamp  SPELL_DAMAGE,{CAST_TAIL}",  # constructed: no timestamp
        "not a combat log line",  # constructed: neither
    ],
    ids=[
        "quoted-asked",
        "quoted-other",
        "quoted-comma",
        "quoted-comma-other",
        "quoted-then-asked",
        "quote-in-name",
        "group-after-name",
        "name-in-group",
        "empty-first",
        "space-in-name",
        "one-space",
        "no-timestamp",
        "garbage",
    ],
)
def test_constructed_a_line_without_a_bare_event_name_is_tokenized(line: str) -> None:
    """The tokenizer reads none of these as a record, filtered or not, and
    `cast_counts` counts each as one line it could not read."""
    data = _lines(HEADER, line)
    unfiltered = list(tokenize(data))
    filtered = list(tokenize(data, events=CAST_COUNT_EVENTS))
    assert filtered == unfiltered
    assert isinstance(filtered[1], Unparsed)
    got = combatlog.cast_counts(iter(filtered))
    assert got == combatlog.cast_counts(iter(unfiltered))
    assert (got.lines, got.unparsed, got.cast_events, got.mine) == (2, 1, 0, 0)


@pytest.mark.parser
def test_constructed_a_comma_in_a_later_quoted_field_does_not_move_the_event() -> None:
    """The event field ends at the first comma; a quoted spell name holding a
    comma comes later and is the tokenizer's to read."""
    cast = f"{TS}  SPELL_CAST_SUCCESS,{CAST_TAIL.replace('Constructed Bolt', 'Bolt, Constructed')}"
    other = f"{TS}  SPELL_DAMAGE,{CAST_TAIL.replace('Constructed Bolt', 'Bolt, Constructed')}"
    data = _lines(HEADER, cast, other)
    filtered = list(tokenize(data, events=CAST_COUNT_EVENTS))
    unfiltered = list(tokenize(data))
    assert filtered[:2] == unfiltered[:2]
    assert isinstance(filtered[2], Skipped) and filtered[2].event == "SPELL_DAMAGE"
    got = combatlog.cast_counts(iter(filtered))
    assert got == combatlog.cast_counts(iter(unfiltered))
    assert [(s.spell_id, s.spell_name, s.casts) for s in got.spells] == [
        (172, "Bolt, Constructed", 1)
    ]


@pytest.mark.parser
def test_constructed_a_name_alone_on_its_line() -> None:
    """No comma after the name: the whole rest of the line is the field."""
    data = _lines(HEADER, f"{TS}  SPELL_DAMAGE", f"{TS}  SPELL_CAST_SUCCESS")
    filtered = list(tokenize(data, events=CAST_COUNT_EVENTS))
    assert isinstance(filtered[1], Skipped) and filtered[1].event == "SPELL_DAMAGE"
    assert filtered[2] == list(tokenize(data))[2]
    got = combatlog.cast_counts(iter(filtered))
    assert (got.cast_events, got.not_read) == (1, 1)


@pytest.mark.parser
def test_constructed_a_byte_that_is_not_utf8_in_the_name_is_tokenized() -> None:
    data = _lines(HEADER) + f"{TS}  SPELL_".encode() + b"\xff" + f"DAMAGE,{CAST_TAIL}\r\n".encode()
    filtered = list(tokenize(data, events=CAST_COUNT_EVENTS))
    assert filtered == list(tokenize(data))
    assert isinstance(filtered[1], Unparsed)


@pytest.mark.parser
def test_constructed_a_timestamp_in_other_digits_is_tokenized_not_skipped() -> None:
    """The tokenizer's timestamp check accepts any Unicode digit; the byte
    selector accepts only ASCII ones, so such a line is tokenized, and its
    record is the one the tokenizer gives without the filter."""
    stamp = f"{chr(0x664)}/{chr(0x661)}/2026 02:16:37.862-4"  # constructed: Arabic-Indic digits
    data = _lines(HEADER, f"{stamp}  SPELL_DAMAGE,{CAST_TAIL}")
    unfiltered = list(tokenize(data))
    assert isinstance(unfiltered[1], Record)
    assert list(tokenize(data, events=CAST_COUNT_EVENTS)) == unfiltered


# ─── constructed: what the filter does not change ────────────────────────────


@pytest.mark.parser
def test_constructed_a_malformed_line_of_an_asked_for_event_is_unparsed_as_before() -> None:
    line = f"{TS}  SPELL_CAST_SUCCESS,{CAST_TAIL},[1,2"
    data = _lines(HEADER, line)
    filtered = list(tokenize(data, events=CAST_COUNT_EVENTS))
    assert filtered == list(tokenize(data))
    assert isinstance(filtered[1], Unparsed)
    assert combatlog.cast_counts(iter(filtered)).unparsed == 1


@pytest.mark.parser
def test_constructed_a_malformed_line_of_another_event_is_skipped_unread() -> None:
    """The one difference the filter makes: a line of an event not asked for
    is not tokenized, so a malformed field after its name is not found and
    the line is not among `unparsed`."""
    line = f"{TS}  SPELL_DAMAGE,{CAST_TAIL},[1,2"
    data = _lines(HEADER, line)
    unfiltered = list(tokenize(data))
    filtered = list(tokenize(data, events=CAST_COUNT_EVENTS))
    _same_but_skipped(unfiltered, filtered, CAST_COUNT_EVENTS)
    assert isinstance(unfiltered[1], Unparsed) and isinstance(filtered[1], Skipped)
    assert combatlog.cast_counts(iter(unfiltered)).unparsed == 1
    assert combatlog.cast_counts(iter(filtered)).unparsed == 0
    assert combatlog.cast_counts(iter(filtered)).lines == 2


@pytest.mark.parser
@pytest.mark.parametrize("ending", [b"\r\n", b"\n", b""], ids=["crlf", "lf", "none"])
def test_constructed_an_over_long_line_of_another_event_is_truncated_as_before(
    ending: bytes, tmp_path: Path
) -> None:
    """The 1 MiB rule comes first: an over-long line is the truncated
    Unparsed it always was, whatever its event, read whole or in chunks."""
    long = f"{TS}  SPELL_DAMAGE,".encode() + b"x" * (MAX_LINE_BYTES + 10)
    data = _lines(HEADER) + long + ending
    unfiltered = list(tokenize(data))
    filtered = list(tokenize(data, events=CAST_COUNT_EVENTS))
    assert filtered == unfiltered
    assert isinstance(filtered[1], Unparsed) and filtered[1].truncated
    path = tmp_path / "WoWCombatLog-050126_000000.txt"
    path.write_bytes(data)
    assert list(read_log(path, events=CAST_COUNT_EVENTS)) == unfiltered


@pytest.mark.parser
def test_constructed_a_line_split_across_chunks_is_selected_the_same(tmp_path: Path) -> None:
    """A line straddling a read boundary is selected on the whole line."""
    first = f"{TS}  SPELL_DAMAGE,{'y' * 1000}"
    pad = "z" * (combatlog._CHUNK - len(HEADER) - 2 - len(first) - 2 - 20)
    lines = [HEADER, first, f"{TS}  SPELL_HEAL,{pad}", f"{TS}  SPELL_CAST_SUCCESS,{CAST_TAIL}"]
    data = _lines(*lines)
    path = tmp_path / "WoWCombatLog-050126_000000.txt"
    path.write_bytes(data)
    filtered = list(read_log(path, events=CAST_COUNT_EVENTS))
    assert filtered == list(tokenize(data, events=CAST_COUNT_EVENTS))
    assert [type(e).__name__ for e in filtered] == ["Record", "Skipped", "Skipped", "Record"]
    _same_but_skipped(list(read_log(path)), filtered, CAST_COUNT_EVENTS)
    assert _rebuilt(filtered) == data


@pytest.mark.parser
def test_constructed_events_that_are_not_ascii_are_never_matched() -> None:
    """A name that is not ASCII is never a bare event name: asking for it
    skips nothing extra and reads nothing as a record."""
    data = _lines(HEADER, f"{TS}  SPELL_DAMAGE,{CAST_TAIL}")
    filtered = list(tokenize(data, events={"SPELL_DAMAGÉ", "COMBAT_LOG_VERSION"}))
    assert isinstance(filtered[0], Record) and isinstance(filtered[1], Skipped)


@pytest.mark.parser
def test_constructed_one_name_as_a_str_is_refused() -> None:
    """A str would be read as one-letter event names and skip every line."""
    with pytest.raises(TypeError):
        list(tokenize(_lines(HEADER), events="SPELL_CAST_SUCCESS"))
