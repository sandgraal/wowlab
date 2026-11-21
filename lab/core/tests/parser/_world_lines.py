"""Constructed combat-log lines for the world PvP tests (M20-18).
CONSTRUCTED INPUT, built at run time from the real fixture's line shapes.

The one real combat log in the repository
(`fixtures/macos/forever/Logs/WoWCombatLog-040126_021630.txt`, one solo
fight against one NPC) holds no other player. Every PvP line here is one of
that log's real lines with its NPC (GUID, quoted name, flags) or its owner
swapped for an invented player, and its timestamp replaced: the field
layout, the advanced block, the quoting and the CRLF are the real log's.
The flags of a hostile player (`0x548`: outsider, hostile, controlled by a
player, a player) and of a friendly one (`0x512`: party, friendly, player,
player) are the client's bit values by community documentation, not shown
by any fixture (not yet confirmed).

Keys are the four invented keys of the repository's fixed list; GUIDs are
invented (`Player-9998-…`, the scrubbed shape of another player's id).
"""

from __future__ import annotations

from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "macos"
    / "forever"
    / "Logs"
    / "WoWCombatLog-040126_021630.txt"
)
REAL = FIXTURE.read_bytes().decode("utf-8").split("\r\n")

OWNER_GUID = "Player-9999-00000001"
OWNER_NAME = "Labchard-LabrealmbPartbPartcPartd-"
OWNER_FLAGS = "0x511"
NPC_GUID = "Creature-0-1-0-2-1554-0000000000"
NPC_NAME = "Vampiric Duskbat"
NPC_FLAGS = "0x10a48"

HOSTILE = "0x548"
FRIENDLY = "0x512"

# Invented players: (GUID, unit name as the log writes it).
FOE_A = ("Player-9998-0000000A", "Inventeda-Fakerealm-")
FOE_B = ("Player-9998-0000000B", "Inventedb-Fakerealm-")
ALLY_C = ("Player-9998-0000000C", "Inventedc-Otherfakerealm-")
FOE_D = ("Player-9998-0000000D", "Inventedd-Otherfakerealm-")


def _line(n: int) -> str:
    """The real log's line `n` (1-based), without its CRLF."""
    return REAL[n - 1]


HEADER = _line(1)
ZONE = _line(2)  # ZONE_CHANGE,0,"Tirisfal Glades",0
SPELL_DAMAGE_YOU_TO_NPC = _line(7)
SWING_NPC_TO_YOU = _line(16)
PARTY_KILL_YOU = _line(77)
UNIT_DIED_NPC = _line(79)


def stamp(seconds: int) -> str:
    """A timestamp in the real log's shape (`M/D/YYYY HH:MM:SS.mmm-4`),
    `seconds` after 4/1/2026 02:16:30 at UTC-4 (the fixture's first line),
    kept within one day."""
    total = 2 * 3600 + 16 * 60 + 30 + seconds
    hh, rest = divmod(total, 3600)
    mm, ss = divmod(rest, 60)
    return f"4/1/2026 {hh:02d}:{mm:02d}:{ss:02d}.000-4"


def _at(line: str, seconds: int) -> str:
    return stamp(seconds) + line[line.index("  ") :]


def _as_player(line: str, who: tuple[str, str], flags: str) -> str:
    guid, name = who
    return (
        line.replace(NPC_GUID, guid).replace(f'"{NPC_NAME}"', f'"{name}"').replace(NPC_FLAGS, flags)
    )


def _owner_as(line: str, who: tuple[str, str], flags: str) -> str:
    guid, name = who
    return (
        line.replace(OWNER_GUID, guid)
        .replace(f'"{OWNER_NAME}"', f'"{name}"')
        .replace(OWNER_FLAGS, flags)
    )


def zone(
    seconds: int, instance: int = 0, name: str = "Tirisfal Glades", difficulty: int = 0
) -> str:
    """The real `ZONE_CHANGE` line, its fields changed (a battleground's
    instance id and difficulty are invented)."""
    return f'{stamp(seconds)}  ZONE_CHANGE,{instance},"{name}",{difficulty}'


def you_hit(foe: tuple[str, str], seconds: int, flags: str = HOSTILE) -> str:
    """Your spell damages `foe` (the real SPELL_DAMAGE, NPC swapped)."""
    return _at(_as_player(SPELL_DAMAGE_YOU_TO_NPC, foe, flags), seconds)


def hits_you(foe: tuple[str, str], seconds: int, flags: str = HOSTILE) -> str:
    """`foe` swings at you (the real SWING_DAMAGE, NPC swapped)."""
    return _at(_as_player(SWING_NPC_TO_YOU, foe, flags), seconds)


def ally_hits(ally: tuple[str, str], foe: tuple[str, str], seconds: int) -> str:
    """`ally` damages `foe` (the real SPELL_DAMAGE, owner and NPC swapped)."""
    line = _owner_as(SPELL_DAMAGE_YOU_TO_NPC, ally, FRIENDLY)
    return _at(_as_player(line, foe, HOSTILE), seconds)


def pet_hits(foe: tuple[str, str], seconds: int, pet_flags: str = "0x1111") -> str:
    """Your pet swings at `foe`: the real SWING_DAMAGE with the NPC as the
    target, an invented pet as the source (flags mine, friendly, player
    control, pet; its advanced block names you as its owner)."""
    pet = "Pet-0-1-0-2-416-0100000001"
    fields = SWING_NPC_TO_YOU.split(",", 1)[1].split(",")
    # source: the pet; destination: the foe; advanced: the pet, owned by you.
    fields[0:4] = [pet, '"Invented Imp"', pet_flags, "0x80000000"]
    fields[4:8] = [foe[0], f'"{foe[1]}"', HOSTILE, "0x80000000"]
    fields[8:10] = [pet, OWNER_GUID]
    return f"{stamp(seconds)}  SWING_DAMAGE," + ",".join(fields)


def you_kill(foe: tuple[str, str], seconds: int) -> str:
    return _at(_as_player(PARTY_KILL_YOU, foe, HOSTILE), seconds)


def you_die(seconds: int) -> str:
    """The real UNIT_DIED with you as the unit that died."""
    line = UNIT_DIED_NPC.replace(NPC_GUID, OWNER_GUID).replace(f'"{NPC_NAME}"', f'"{OWNER_NAME}"')
    return _at(line.replace(NPC_FLAGS, OWNER_FLAGS), seconds)


def log(*lines: str) -> bytes:
    """A log: the real header and zone lines, then `lines`, CRLF each."""
    return ("\r\n".join([HEADER, ZONE, *lines]) + "\r\n").encode("utf-8")
