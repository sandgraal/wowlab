# Probe from review of m13/08-binds-coach; reproduces a spell pressed often on one high-cost key missing "frequent" because its casts are split across rank ids.
"""Round 1 made `bindscoach.coach` judge each cast spell id on its own (exact
id first, a spellbook-name fallback for an id no slot holds). Casts at two
ranks of one spell that reach the same slot and key are then two rows, and
`frequent-on-high-cost` compares each row with `frequent_min_casts` and
`frequent_share` separately.

Here Shadow Bolt is cast 4 times at 705 (the id slot 5 holds) and 4 times at
695 (an older rank, matched to slot 5 by name), with 40 other casts: 8 presses
of key 5, 16.7% of the player's casts, over both default thresholds (5 casts,
5%). With `high_cost` set to 2, key 5 (cost 2) is a high-cost key. The
positive control casts all 8 at 705 and is reported; the split case is not,
though the player pressed the same key the same number of times. Expected:
the same report, since the report is about how often a key is pressed.

The record is the real schema-3 record (`fixtures/macos-70425/`, M13-05);
the casts are constructed lines in the real log's format-22 layout (boundary
inputs, L8). Nothing reads an install or the user config folder.
"""

from __future__ import annotations

from pathlib import Path

from wowlab_core import bindscoach, combatlog, labaddon

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SCHEMA_3 = (
    FIXTURES
    / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)
HEADER = (
    "4/1/2026 02:16:30.666-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,"
    "BUILD_VERSION,1.60.1,PROJECT_ID,18"
)


def _cast(spell_id: int, name: str) -> str:
    return (
        "4/1/2026 02:16:37.862-4  SPELL_CAST_SUCCESS,Player-9999-00000001,"
        '"Constructed-Unit-",0x511,0x80000000,Creature-0-1-0-2-1554-0000000000,'
        f'"Constructed Target",0x10a48,0x80000000,{spell_id},"{name}",0x20'
    )


def _frequent(*lines: str) -> list[str]:
    data = "".join(f"{line}\r\n" for line in (HEADER, *lines)).encode()
    counts = combatlog.cast_counts(combatlog.tokenize(data))
    record = labaddon.read_char(SCHEMA_3)
    shipped = Path(bindscoach.shipped_costs_path()).read_text(encoding="utf-8")
    table = bindscoach.parse_costs("[coach]\nhigh_cost = 2\n", bindscoach.parse_costs(shipped))
    got = bindscoach.coach(counts, record, table)
    return [f.spell_name for f in got.frequent_on_high_cost]


OTHERS = [_cast(172, "Corruption")] * 40


def test_positive_control_one_rank_is_frequent_constructed() -> None:
    assert _frequent(*[_cast(705, "Shadow Bolt")] * 8, *OTHERS) == ["Shadow Bolt"]


def test_two_ranks_on_the_same_key_are_frequent_constructed() -> None:
    lines = [_cast(705, "Shadow Bolt")] * 4 + [_cast(695, "Shadow Bolt")] * 4
    assert "Shadow Bolt" in _frequent(*lines, *OTHERS)
