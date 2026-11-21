# Probe from review of m20/18-world-pvp; reproduces `read_world`'s byte skip dropping your own damage lines on NPCs, so `logging` is never set and your pet's spell on a hostile player is not counted, while a full read counts it
"""CONSTRUCTED INPUT. The real fixture's lines (header, zone, your
SPELL_DAMAGE on an NPC), plus one invented line: your pet's SPELL_DAMAGE on
an invented hostile player. On a spell line the advanced block describes
the target (the real log shows the NPC there), so the pet is known only by
its flags "mine" and pet while your character is the one logging, which
the reader learns from a damage line of yours. `_skipper` leaves those
lines out before they are tokenized, so the fast read finds no fight.

`docs/LAB_PLAN.md` §21.3 as built: "a damage line naming no other
player's GUID is skipped ... (`skip=`)" and `_skipper`'s docstring: the
lines skipped "can be no fight and no ally's damage". The positive control
is the same log read without the skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import combatlog, worldpvp

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "macos"
    / "forever"
    / "Logs"
    / "WoWCombatLog-040126_021630.txt"
)
OWNER = "Player-9999-00000001"
OWNERS: dict[str, str | None] = {OWNER: None}
PET = "Pet-0-1-0-2-416-0100000001"
FOE = ("Player-9998-0000000B", "Inventedb-Fakerealm-")
NPC_GUID = "Creature-0-1-0-2-1554-0000000000"


def _log(tmp_path: Path) -> Path:
    real = FIXTURE.read_bytes().decode("utf-8").split("\r\n")
    header, zone, you_on_npc = real[0], real[1], real[6]
    assert "  SPELL_DAMAGE," in you_on_npc and OWNER in you_on_npc
    stamp = you_on_npc[: you_on_npc.index("  ")]
    # Your pet's spell on the foe: the real spell line, source your pet,
    # target the foe; the advanced block is the target's (owner 0).
    pet_on_foe = (
        you_on_npc.replace(
            f'{OWNER},"Labchard-LabrealmbPartbPartcPartd-",0x511', f'{PET},"Invented Imp",0x1111'
        )
        .replace(f'{NPC_GUID},"Vampiric Duskbat",0x10a48', f'{FOE[0]},"{FOE[1]}",0x548')
        .replace(NPC_GUID, FOE[0])
    )
    assert pet_on_foe.startswith(stamp) and OWNER not in pet_on_foe
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(("\r\n".join([header, zone, you_on_npc, pet_on_foe]) + "\r\n").encode())
    return path


@pytest.mark.parser
def test_positive_control_a_full_read_counts_the_pet_fight(tmp_path: Path) -> None:
    full = worldpvp.scan_entries(combatlog.read_log(_log(tmp_path)), OWNERS)
    assert full.fights == 1
    assert [p.key for e in full.encounters for p in e.players] == ["Inventedb-Fakerealm"]


@pytest.mark.parser
def test_the_skipping_read_finds_what_a_full_read_finds(tmp_path: Path) -> None:
    path = _log(tmp_path)
    fast = worldpvp.read_world(path, OWNERS)
    full = worldpvp.scan_entries(combatlog.read_log(path), OWNERS)
    assert fast.fights == full.fights
