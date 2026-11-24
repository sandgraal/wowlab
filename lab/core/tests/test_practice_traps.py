"""The combat-log traps of Sparxx947/gse-makrowerkstatt (MIT), as graders of
wowlab's log reader (M22-20).

Source: the headers of `werkzeug/puppe-messen.py`, `log-casts.py` and
`log-sequenz.py` at commit c60d296 (read 2026-11-23; cited, not vendored).
Each trap produced a wrong number there; each is proven here on a real
Forever log slice:

- `LAUNCH`: the first 200 lines of the owner's launch-client log of
  2026-11-23 20:04 (build 1.60.1.71037, captured for M22-20 with
  `--pseudonymise-other-players`): an open-world fight at the Pyrewood
  camp, a warrior (`Player-9998-00000004`) and a hunter
  (`Player-9998-00000001`) with its pet Imaru hitting the same Pyrewood
  Watcher, and a player summoning a Campfire Marker.
- `BETA`: the owner's beta log (build 1.60.1.70009; M10-03), one solo fight
  of a warlock against a Vampiric Duskbat.

What no committed slice shows is constructed, labelled `constructed`, and
marked **[verify]** where the doubt is about the client.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import combatlog, practice
from wowlab_core.combatlog import Record

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAUNCH = FIXTURES / "macos-71037" / "forever" / "Logs" / "WoWCombatLog-053126_104937.txt"
BETA = FIXTURES / "macos" / "forever" / "Logs" / "WoWCombatLog-040126_021630.txt"

WARRIOR = "Player-9998-00000004"
HUNTER = "Player-9998-00000001"
CAMPER = "Player-9998-00000003"
IMARU = "Pet-0-4-2-9-165189-0000000006"
WATCHER = "Creature-0-7-0-8-1891-0000000002"
CAMPFIRE = "Creature-0-7-0-8-260300-0000000007"


def _lines(path: Path) -> list[Record]:
    out = []
    for entry in combatlog.read_log(path):
        assert isinstance(entry, Record), entry
        out.append(entry)
    return out


def _line(path: Path, number: int) -> Record:
    return _lines(path)[number - 1]


def _of(path: Path, event: str) -> list[Record]:
    return [r for r in _lines(path) if r.event == event]


# ─── the advanced block, found by its shape ──────────────────────────────────


def test_launch_header_says_advanced_off_and_every_damage_line_has_the_block() -> None:
    """A format finding: the launch client writes `ADVANCED_LOG_ENABLED,0`
    and still writes the 19-field block, so the block is found by shape."""
    headers = _of(LAUNCH, "COMBAT_LOG_VERSION")
    assert [h.fields[:4] for h in headers] == [
        ("22", "ADVANCED_LOG_ENABLED", "0", "BUILD_VERSION")
    ] * 3
    damage = [r for r in _lines(LAUNCH) if r.event in practice.DAMAGE_EVENTS]
    assert len(damage) == 28
    assert all(practice.advanced_at(r) is not None for r in damage)


# ─── trap 1: a swing's amount is three fields earlier ────────────────────────


def test_trap1_swing_amount_is_field_27_on_the_launch_slice() -> None:
    swing = _line(LAUNCH, 28)
    assert swing.event == "SWING_DAMAGE"
    assert swing.fields[0] == WARRIOR
    assert practice.advanced_at(swing) == 8
    assert practice.damage_amount(swing) == 84
    # The spell index read on a swing is the school: "1 damage per swing".
    assert swing.fields[30] == "1"


def test_trap1_spell_amount_is_field_30_on_the_launch_slice() -> None:
    hit = _line(LAUNCH, 65)
    assert hit.event == "SPELL_DAMAGE"
    assert practice.advanced_at(hit) == 11
    assert hit.fields[8:10] == ("6660", combatlog.Quoted(text="Shoot"))
    assert practice.damage_amount(hit) == 20
    # The swing index read on a spell is inside the advanced block.
    assert hit.fields[27] != "20"


def test_trap1_beta_fight_adds_up_to_the_duskbats_health() -> None:
    """Every hit read at the right index: the warlock's damage on the
    Duskbat sums to its maximum health, which the advanced block of each
    line describing it gives (180), and it died."""
    lines = _lines(BETA)
    bat = next(r.fields[4] for r in lines if r.event == "SPELL_DAMAGE")
    max_health = {r.fields[14] for r in lines if r.event == "SPELL_DAMAGE" and r.fields[11] == bat}
    assert max_health == {"180"}
    done = practice.damage_done(lines, "Player-9999-00000001")
    assert done.total == 180
    assert any(r.event == "UNIT_DIED" and r.fields[4] == bat for r in lines)
    swings = [r for r in lines if r.event == "SWING_DAMAGE"]
    assert swings and all(practice.damage_amount(r) == int(str(r.fields[27])) for r in swings)


# ─── trap 2: a companion is found two ways ───────────────────────────────────


def test_trap2_pet_out_before_the_log_is_found_only_by_the_owner_slot() -> None:
    lines = _lines(LAUNCH)
    assert not any(r.event == "SPELL_SUMMON" and r.fields[4] == IMARU for r in lines)
    companions = practice.Companions()
    for r in lines:
        companions.see(r)
    assert companions.by_owner_slot.get(IMARU.upper()) == HUNTER.upper()
    assert IMARU.upper() not in companions.by_summon
    # A summon-only reader would give the hunter none of the pet's damage.
    assert practice.damage_done(lines, HUNTER).total == 335


def test_trap2_unit_that_never_acts_is_found_only_by_its_summon() -> None:
    lines = _lines(LAUNCH)
    summon = _line(LAUNCH, 115)
    assert summon.event == "SPELL_SUMMON"
    assert (summon.fields[0], summon.fields[4]) == (CAMPER, CAMPFIRE)
    assert [r.event for r in lines if CAMPFIRE in r.fields] == ["SPELL_SUMMON"]
    companions = practice.Companions()
    for r in lines:
        companions.see(r)
    assert companions.by_summon.get(CAMPFIRE.upper()) == CAMPER.upper()
    assert CAMPFIRE.upper() not in companions.by_owner_slot
    assert companions.owner_of(CAMPFIRE) == CAMPER.upper()


def test_trap2_a_summoned_unit_that_deals_damage_and_never_names_its_owner_constructed() -> None:
    """[verify] No committed slice holds a summoned unit that does damage
    and never writes a line whose block names its owner (the source's Dire
    Beast); the owner's 2026-11-23 13:30 log shows a Searing Totem naming
    its owner on its casts, and that slice cannot be captured (a player's
    name inside an NPC's). Constructed on the launch slice's shapes."""
    adv = ",".join(["Creature-0-1-1-1-1-0000000001", "0000000000000000"] + ["0"] * 17)
    text = (
        '5/31/2026 10:00:00.000-6  SPELL_SUMMON,Player-1-0000000A,"Me",0x511,0x80000000,'
        'Creature-0-1-1-1-2-0000000002,"Beast",0xa28,0x80000000,1,"Call",0x1\r\n'
        '5/31/2026 10:00:01.000-6  SPELL_DAMAGE,Creature-0-1-1-1-2-0000000002,"Beast",0x2111,'
        '0x80000000,Creature-0-1-1-1-1-0000000001,"Dummy",0xa28,0x80000000,2,"Bite",0x1,'
        f"{adv},55,55,-1,1,0,0,0,nil,nil,nil,ST\r\n"
    )
    lines = list(combatlog.tokenize(text.encode()))
    assert practice.damage_done(lines, "Player-1-0000000A").total == 55


# ─── trap 3: on spell damage the block describes the target ──────────────────


@pytest.mark.parametrize("path", [LAUNCH, BETA], ids=["launch", "beta"])
def test_trap3_block_describes_target_on_spell_damage_and_source_on_swings(path: Path) -> None:
    lines = _lines(path)
    spell = [r for r in lines if r.event in ("SPELL_DAMAGE", "SPELL_PERIODIC_DAMAGE")]
    landed = [r for r in lines if r.event == "SWING_DAMAGE_LANDED"]
    swings = [r for r in lines if r.event == "SWING_DAMAGE"]
    casts = [r for r in lines if r.event == "SPELL_CAST_SUCCESS"]
    assert spell and landed and swings and casts
    assert all(r.fields[11] == r.fields[4] != r.fields[0] for r in spell)
    assert all(r.fields[8] == r.fields[4] for r in landed)
    assert all(r.fields[8] == r.fields[0] for r in swings)
    assert all(r.fields[11] == r.fields[0] for r in casts)
    assert all(practice.owner_named(r) is None for r in spell + landed)


def test_trap3_the_creature_hitting_the_hunter_is_no_ones_companion() -> None:
    hit = _line(LAUNCH, 65)
    assert (hit.fields[0], hit.fields[4], hit.fields[11]) == (WATCHER, HUNTER, HUNTER)
    assert practice.owner_named(hit) is None
    companions = practice.Companions()
    for r in _lines(LAUNCH):
        companions.see(r)
    assert companions.owner_of(WATCHER) is None


def test_trap3_a_creature_hitting_your_pet_does_not_become_yours_constructed() -> None:
    """The source's case: a spell on your pet writes your GUID in the owner
    slot, the block describing the pet. Seen on the owner's 2026-11-23 13:30
    log (a Dalaran Apprentice's Frostbolt on the voidwalker), which cannot
    be captured; constructed on that line's shape."""
    adv = ",".join(["Pet-0-1-1-1-1860-0000000001", "Player-1-0000000A"] + ["0"] * 17)
    text = (
        "5/31/2026 10:00:00.000-6  SPELL_DAMAGE,Creature-0-1-1-1-1867-0000000002,"
        '"Dalaran Apprentice",0xa48,0x80000000,Pet-0-1-1-1-1860-0000000001,"Hukrath",0x1111,'
        f'0x80000000,13322,"Frostbolt",0x10,{adv},33,33,-1,16,0,0,0,nil,nil,nil,ST\r\n'
    )
    (rec,) = list(combatlog.tokenize(text.encode()))
    assert isinstance(rec, Record)
    assert practice.owner_named(rec) is None
    companions = practice.Companions()
    companions.see(rec)
    assert companions.owner_of("Creature-0-1-1-1-1867-0000000002") is None
    assert practice.damage_done([rec], "Player-1-0000000A").total == 0


# ─── trap 4: other players' damage on the same target ────────────────────────


def test_trap4_two_players_on_one_watcher_each_keep_only_their_own() -> None:
    lines = _lines(LAUNCH)
    hits = [r for r in lines if r.event in practice.DAMAGE_EVENTS and r.fields[4] == WATCHER]
    sources = {r.fields[0] for r in hits}
    assert {WARRIOR, IMARU} <= sources
    warrior = practice.damage_done(lines, WARRIOR)
    hunter = practice.damage_done(lines, HUNTER)
    assert warrior.total == 681 and [s.guid for s in warrior.by_source] == [WARRIOR]
    assert hunter.total == 335 and [s.guid for s in hunter.by_source] == [IMARU]
    assert warrior.others_on_targets == hunter.total
    assert hunter.others_on_targets == warrior.total
    every = sum(
        practice.damage_amount(r) or 0
        for r in lines
        if r.event in practice.DAMAGE_EVENTS
        and r.fields[0] in (WARRIOR, IMARU)
        and str(r.fields[4]).startswith("Creature-")
    )
    assert every == warrior.total + hunter.total
