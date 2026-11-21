# Probe from review of m20/18-world-pvp; reproduces a read in parts counting the fight on the line that starts the next part twice (`fights` incremented before `_Full` is raised, then the line is read again)
"""CONSTRUCTED INPUT. The real fixture's header, zone line and your
SPELL_DAMAGE, with the NPC swapped for invented hostile players, one fight
per encounter. Read in parts as `crew scan-log` reads them (`more`, `end`,
`state`), with `MAX_READ_PLAYERS` lowered so the read stops twice.

`docs/LAB_PLAN.md` §21.3 as built and the ticket: a log is read once and
nothing is counted twice; `WorldOutcome.fights` is "fights counted". The
positive control is the same log read in one part.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import worldpvp

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "macos"
    / "forever"
    / "Logs"
    / "WoWCombatLog-040126_021630.txt"
)
OWNERS: dict[str, str | None] = {"Player-9999-00000001": None}
NPC = 'Creature-0-1-0-2-1554-0000000000,"Vampiric Duskbat",0x10a48'
NPC_GUID = "Creature-0-1-0-2-1554-0000000000"


def _log(tmp_path: Path, fights: int) -> Path:
    real = FIXTURE.read_bytes().decode("utf-8").split("\r\n")
    header, zone, you_on_npc = real[0], real[1], real[6]
    body = you_on_npc[you_on_npc.index("  ") :]
    gap = worldpvp.ENCOUNTER_GAP_SECONDS + 1
    lines = [header, zone]
    for n in range(fights):
        guid = f"Player-9998-{n:08X}"
        total = 2 * 3600 + 16 * 60 + 40 + n * gap
        hh, rest = divmod(total, 3600)
        mm, ss = divmod(rest, 60)
        line = f"4/1/2026 {hh:02d}:{mm:02d}:{ss:02d}.000-4" + body
        line = line.replace(NPC, f'{guid},"Inventeda-Fakerealm-",0x548').replace(NPC_GUID, guid)
        lines.append(line)
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode())
    return path


def _in_parts(path: Path) -> tuple[int, int, int]:
    fights = encounters = parts = 0
    start, state = 0, None
    while True:
        scan = worldpvp.read_world(path, OWNERS, start=start, state=state)
        fights += scan.fights
        encounters += len(scan.encounters)
        parts += 1
        if not scan.more:
            return fights, encounters, parts
        start, state = scan.end, scan.state


@pytest.mark.parser
def test_positive_control_one_part_counts_each_fight_once(tmp_path: Path) -> None:
    assert _in_parts(_log(tmp_path, 7)) == (7, 7, 1)


@pytest.mark.parser
def test_a_read_in_parts_counts_each_fight_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(worldpvp, "MAX_READ_PLAYERS", 3)
    fights, encounters, parts = _in_parts(_log(tmp_path, 7))
    assert parts > 1 and encounters == 7
    assert fights == 7
