# Probe from review of m16/45-addon-schema5; reproduces `binds coach` reporting a spell as cast with no bind, with no caveat, when a kept schema-5 CLICK bind presses its slot.
"""Before schema 5 a bar addon's `CLICK <frame>:<button>` bind was counted
in `commands_left_out`, and `bindscoach.coach` then hedged its
`cast-without-bind` report (a caveat, or "cannot judge" from 12 such
commands on): a key the record does not show may reach the spell. Schema 5
keeps that bind, with `action`, the slot it presses, and no longer counts it
as left out. The coach still maps keys to slots by `keymap.command_slot`
only, which knows no CLICK command, so the kept key reaches no slot and the
hedge is gone: the spell on that very slot is reported as cast with no bind,
with no caveat, though the record says which key presses it.

The record is the owner's real schema-4 capture (`macos-70450/`) moved to
schema 5 the way `Actions.lua` writes it, as the branch's own
`test_labaddon_schema_5_m16_45.py` does, with its one left-out command and
keys set to 0 so the bind under test is the only one; the casts are constructed lines in
the real log's format-22 layout (boundary inputs, L8). The positive control
is the same bind left out, as a schema-4 addon (or a schema-5 one whose
frame failed the rule) records it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from wowlab_core import bindscoach, combatlog, labaddon, luadata

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SCHEMA_4 = (
    FIXTURES
    / "macos-70450/forever/WTF/Account/90000001#6/1/Sangraal-Sett/SavedVariables/WowLab.lua"
)
HEADER = (
    "4/1/2026 02:16:30.666-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,"
    "BUILD_VERSION,1.60.1,PROJECT_ID,18"
)
# Slot 69 holds spell 697 in the capture, the only slot that holds it, and no
# plain binding reaches it.
SLOT, SPELL = 69, 697
CLICK = "CLICK BT4Button69:LeftButton"


def _cast(spell_id: int, name: str) -> str:
    return (
        "4/1/2026 02:16:37.862-4  SPELL_CAST_SUCCESS,Player-9999-00000001,"
        '"Constructed-Unit-",0x511,0x80000000,Creature-0-1-0-2-1554-0000000000,'
        f'"Constructed Target",0x10a48,0x80000000,{spell_id},"{name}",0x20'
    )


def _schema_5(*, kept: bool) -> dict[str, Any]:
    raw = luadata.parse(SCHEMA_4.read_bytes()).to_python()[labaddon.CHAR_VARIABLE]
    assert isinstance(raw, dict)
    raw = copy.deepcopy(raw)
    assert any(s["slot"] == SLOT and s.get("id") == SPELL for s in raw["actionbars"]["slots"])
    raw["schema"] = 5
    raw["announced"] = 5
    for key in ("spellbook", "actionbars", "bindings"):
        raw[key].pop("probes", None)
    binds = raw["bindings"]
    binds["commands"] += 1
    binds["commands_left_out"] = 0
    binds["keys_left_out"] = 0
    if kept:
        binds["list"].append({"command": CLICK, "action": SLOT, "keys": ["SHIFT-F9"]})
        binds["clicks_left_out"] = 0
    else:
        binds["commands_left_out"] = 1
        binds["clicks_left_out"] = 1
    return raw


def _coach(*, kept: bool) -> bindscoach.CoachResult:
    lines = [HEADER, *[_cast(SPELL, "Summon Voidwalker")] * 10]
    data = "".join(f"{line}\r\n" for line in lines).encode()
    counts = combatlog.cast_counts(combatlog.tokenize(data))
    record = labaddon.load_char(_schema_5(kept=kept))
    shipped = Path(bindscoach.shipped_costs_path()).read_text(encoding="utf-8")
    return bindscoach.coach(counts, record, bindscoach.parse_costs(shipped))


def _hedged(result: bindscoach.CoachResult) -> bool:
    unbound = [u.spell_id for u in result.cast_without_bind]
    cannot = [c.report for c in result.cannot_judge]
    if SPELL not in unbound:
        return True
    if "cast-without-bind" in cannot:
        return True
    # Since M16-36 (#369) the left-out sentence lives in `unseen`, counted in
    # `commands_unseen`, and the caveat says the coach "cannot see" those bindings.
    # A kept CLICK bind that nothing counts leaves commands_unseen at 0: unhedged.
    return result.commands_unseen > 0 and any("cannot see" in c for c in result.caveats)


def test_positive_control_a_left_out_click_hedges_the_report_constructed() -> None:
    result = _coach(kept=False)
    assert SPELL in [u.spell_id for u in result.cast_without_bind]
    assert _hedged(result), result.caveats


def test_a_kept_click_bind_is_not_reported_as_no_bind_unhedged_constructed() -> None:
    result = _coach(kept=True)
    assert _hedged(result), (
        "the spell on slot 69 is reported as cast with no bind and no caveat, though "
        f"the record keeps {CLICK} with action = 69"
    )
