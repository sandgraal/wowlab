"""`combatlog` against the real combat log captures (L4, L8; LAB_FORMATS §8).

Every `combatlog` row in the fixture index is tokenized: each line becomes a
`Record` or an `Unparsed`, and the lines rebuild the file byte for byte, so a
future capture is graded the day its row lands. The named tests pin what the
Forever log of 2026-09-28 shows (the §8 amendment of that date): 79 lines, all
tokenized, none `Unparsed`. Its file name and timestamps were shifted by the
capture tool, so nothing here treats either as a real time.

What the corpus does not contain (`COMBATANT_INFO`, `[...]`/`(...)` groups,
a quoted comma or quote) is graded on constructed lines in
`test_combatlog_constructed.py` until a boss-pull log is captured.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from wowlab_core.combatlog import Quoted, Record, Unparsed, read_log, tail, tokenize

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"
FOREVER_LOG = FIXTURES / "macos" / "forever" / "Logs" / "WoWCombatLog-040126_021630.txt"


def _indexed(kind: str) -> list[str]:
    """Fixture paths whose index row has this `kind`."""
    found = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) > 1 and cells[1] == kind:
            found.append(cells[0].strip("`"))
    return found


LOGS = _indexed("combatlog")


def _rebuilt(entries: list[Record | Unparsed]) -> bytes:
    return b"".join(e.raw.encode("utf-8", "surrogateescape") + e.ending.encode() for e in entries)


@pytest.mark.parser
def test_the_index_has_the_forever_log() -> None:
    assert FOREVER_LOG.relative_to(FIXTURES).as_posix() in LOGS


@pytest.mark.parser
@pytest.mark.parametrize("name", LOGS, ids=LOGS)
def test_every_line_is_a_record_or_unparsed_and_the_lines_rebuild_the_file(name: str) -> None:
    data = (FIXTURES / name).read_bytes()
    entries = list(read_log(FIXTURES / name))
    assert all(isinstance(e, Record | Unparsed) for e in entries)
    assert len(entries) == data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
    assert _rebuilt(entries) == data
    offsets = [e.offset for e in entries]
    assert offsets == sorted(offsets)
    for e in entries:  # each offset is where that line's bytes start
        raw = e.raw.encode("utf-8", "surrogateescape")
        assert data[e.offset : e.offset + len(raw)] == raw
    assert entries == list(tokenize(data)), "reading in chunks and all at once agree"


@pytest.mark.parser
def test_the_forever_log_tokenizes_whole() -> None:
    entries = list(read_log(FOREVER_LOG))
    unparsed = [e for e in entries if isinstance(e, Unparsed)]
    assert len(entries) == 79
    assert unparsed == [], {e.reason for e in unparsed}
    assert {e.ending for e in entries} == {"\r\n"}, "CRLF on every line, the last included"


@pytest.mark.parser
def test_the_forever_header() -> None:
    first = next(iter(read_log(FOREVER_LOG)))
    assert isinstance(first, Record)
    assert first.event == "COMBAT_LOG_VERSION"
    assert first.fields == (
        "22",
        "ADVANCED_LOG_ENABLED",
        "1",
        "BUILD_VERSION",
        "1.60.1",
        "PROJECT_ID",
        "18",
    )
    assert first.offset == 0


@pytest.mark.parser
def test_the_forever_timestamps_are_kept_as_written() -> None:
    # Shifted by the capture tool: the tool writes the day and month unpadded
    # (`4/1/2026`), whatever the client wrote. Kept as text, never as a time.
    records = [e for e in read_log(FOREVER_LOG) if isinstance(e, Record)]
    assert records[0].timestamp == "4/1/2026 02:16:30.666-4"
    assert all(r.timestamp.startswith("4/1/2026 ") for r in records)
    assert all(r.timestamp.endswith("-4") for r in records)
    assert all(r.raw.startswith(r.timestamp + "  " + r.event) for r in records)


@pytest.mark.parser
def test_the_forever_events_are_those_the_amendment_lists() -> None:
    counts = collections.Counter(e.event for e in read_log(FOREVER_LOG) if isinstance(e, Record))
    assert set(counts) == {
        "COMBAT_LOG_VERSION",
        "SPELL_CAST_START",
        "SPELL_CAST_SUCCESS",
        "SPELL_CAST_FAILED",
        "SPELL_AURA_APPLIED",
        "SPELL_AURA_REFRESH",
        "SPELL_AURA_REMOVED",
        "SPELL_DAMAGE",
        "SPELL_PERIODIC_DAMAGE",
        "SPELL_HEAL",
        "SWING_DAMAGE",
        "SWING_DAMAGE_LANDED",
        "SWING_MISSED",
        "UNIT_DIED",
        "PARTY_KILL",
        "ZONE_CHANGE",
        "MAP_CHANGE",
    }
    assert counts["SWING_DAMAGE"] == counts["SWING_DAMAGE_LANDED"] == 11


@pytest.mark.parser
def test_the_forever_fields_are_tokens_and_quoted_strings_only() -> None:
    records = [e for e in read_log(FOREVER_LOG) if isinstance(e, Record)]
    fields = [f for r in records for f in r.fields]
    assert all(isinstance(f, str | Quoted) for f in fields), "no groups in this log"
    quoted = [f.text for f in fields if isinstance(f, Quoted)]
    assert len(quoted) == 199
    # All 77 player unit names are "<Name>-<Realm>-", with an empty third part.
    names = [t for t in quoted if t.endswith("-")]
    assert len(names) == 77
    assert all(t.count("-") == 2 for t in names)
    assert not any("," in t or '"' in t for t in quoted), "no quoted comma or quote here"


@pytest.mark.parser
def test_nil_hex_and_numbers_stay_tokens() -> None:
    records = [e for e in read_log(FOREVER_LOG) if isinstance(e, Record)]
    zone = next(r for r in records if r.event == "ZONE_CHANGE")
    assert zone.fields == ("0", Quoted(text="Tirisfal Glades"), "0")
    cast = next(r for r in records if r.event == "SPELL_CAST_START")
    # An absent unit: 0000000000000000,nil,0x80000000,0x80000000.
    assert cast.fields[4:8] == ("0000000000000000", "nil", "0x80000000", "0x80000000")
    failed = next(r for r in records if r.event == "SPELL_CAST_FAILED")
    assert failed.fields[-1] == Quoted(text="Interrupted")
    died = next(r for r in records if r.event == "UNIT_DIED")
    assert died.fields[-1] == "0"
    mapping = next(r for r in records if r.event == "MAP_CHANGE")
    assert mapping.fields[0] == "1420"
    assert "-1485.416626" in mapping.fields


@pytest.mark.parser
@pytest.mark.parametrize("n", [0, 1, 5, 79, 200])
def test_tail_is_the_last_lines_of_the_real_log(n: int) -> None:
    whole = list(read_log(FOREVER_LOG))
    lines, end = tail(FOREVER_LOG, n)
    assert lines == (whole[-n:] if n else [])
    assert end == FOREVER_LOG.stat().st_size
