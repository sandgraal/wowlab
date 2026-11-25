# Probe from review of m22/20-log-traps-practice; reproduces a buff on you for the whole segment (on before the log, removed after the segment) reported with no uptime, while the same buff removed inside the segment is reported.
"""CONSTRUCTED INPUT (L8, boundary case): a training-dummy session where a
long buff was put on before logging began. `summarize` reads only up to the
segment's last line, so a buff whose removal (or nothing) comes after the
segment is never seen, and the buff that was up for the whole segment
reports less than the same buff removed mid-segment.

Expected (practice.py's docstring, "Buff uptime"; ticket M22-20, "buff
uptime"): the buff that was up for the whole segment reports at least the
uptime of the one removed inside it. The positive control (removed inside
the segment) passes on the branch head.
"""

from __future__ import annotations

from wowlab_core import combatlog, practice

_ME = 'Player-1-0000000A,"Me-Realm-",0x511,0x80000000'
_DUMMY = 'Creature-0-1-1-1-31146-0000000001,"Training Dummy",0xa28,0x80000000'
_ADV = ",".join(["Player-1-0000000A", "0000000000000000", *["0"] * 17])
_HEADER = "COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,1.60.1,PROJECT_ID,18"


def _rec(seconds: int, body: str) -> str:
    return f"1/2/2026 10:00:{seconds:02d}.000-4  {body}\r\n"


def _cast(seconds: int, spell: int, name: str) -> str:
    return _rec(seconds, f'SPELL_CAST_SUCCESS,{_ME},{_DUMMY},{spell},"{name}",0x20,{_ADV}')


def _removed(seconds: int) -> str:
    return _rec(seconds, f'SPELL_AURA_REMOVED,{_ME},{_ME},1459,"Arcane Intellect",0x40,BUFF')


def _uptime(text: str) -> dict[str, float]:
    entries = list(combatlog.tokenize(text.encode()))
    (segment,) = practice.find_segments(entries).segments
    assert (segment.start, segment.end) == (
        "1/2/2026 10:00:10.000-4",
        "1/2/2026 10:00:20.000-4",
    )
    summary = practice.summarize(entries, segment, None)
    return {b.spell_name: b.seconds for b in summary.buffs}


def test_buff_removed_inside_the_segment_is_counted_positive_control_constructed() -> None:
    text = (
        _rec(0, _HEADER)
        + _cast(10, 686, "Shadow Bolt")
        + _cast(15, 172, "Corruption")
        + _removed(18)
        + _cast(20, 686, "Shadow Bolt")
    )
    assert _uptime(text) == {"Arcane Intellect": 8.0}


def test_buff_up_for_the_whole_segment_is_counted_constructed() -> None:
    text = (
        _rec(0, _HEADER)
        + _cast(10, 686, "Shadow Bolt")
        + _cast(15, 172, "Corruption")
        + _cast(20, 686, "Shadow Bolt")
        + _removed(50)
    )
    assert _uptime(text).get("Arcane Intellect", 0.0) >= 8.0
