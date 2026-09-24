"""`scripts/lab_capture.py`: fifth follow-up to M10-02.

1. In every combat log, whatever the flags, each non-player unit GUID
   (`<Type>-0-<serverID>-<instanceID>-<zoneUID>-<ID>-<spawnUID>`) keeps its
   type, the leading `0` and the NPC or object id, and gets invented server,
   instance, zone and spawn parts: the same real value maps to the same
   invented one for the whole run, two different GUIDs never collide, a field
   of zeros stays, the spawn UID keeps its width in upper-case hex. A
   GUID-like token of any other shape refuses the log, with a count only. The
   provenance row says `unit-guids-rewritten: N`.
2. The hunt for other players' names (`--pseudonymise-other-players`) folds
   full-width letters (NFKC) and reads past tabs, NBSPs, zero-width spaces and
   doubled separators; the loose second-name detector reads past the same
   separators. Detection only: no new rewrite.
3. `--combat-log NAME` (repeatable) captures each named log from a flavor's
   `Logs/` instead of only the newest.

CONSTRUCTED INPUT. Every log line and file body below is written for the test
in the shape of docs/LAB_FORMATS.md section 8, with invented names, GUIDs, server
numbers and spawn UIDs only; trees are synthetic, under `tmp_path`. No test
needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

import pytest
from conftest import DEFAULT_OFFSET
from test_lab_capture import build_install, capture, lab_capture, outputs

Identity = lab_capture.Identity
OtherPlayers = lab_capture.OtherPlayers
UnitGuids = lab_capture.UnitGuids


def _shifted(name: str) -> str:
    """A log file name as a run under test writes it (conftest.DEFAULT_OFFSET)."""
    moved = lab_capture.TimeShift(DEFAULT_OFFSET).file_name(name)
    assert moved is not None
    return moved


UNCLASSIFIED = lab_capture.UNIT_GUID_UNCLASSIFIED_LABEL
OTHER_NAME = lab_capture.OTHER_NAME_LABEL
LOOSE = "surviving identity string (spaced or apostrophe spelling)"

OWN = b"Player-1-0000ABCD"
TS = b"1/1/2026 00:00:00.000-4  "
HEADER = TS + b"COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
OWN_UNIT = b'Player-1-0000ABCD,"Orlavin-KestrelHollow-",0x511,0x0'
# Invented unit GUIDs: server 3771, zone 58, spawn UIDs made up for the test.
BOAR = b"Creature-0-3771-0-58-2222-00004A2C11"
WOLF = b"Creature-0-3771-0-58-2223-00004A2C99"
BOAR_TWO = b"Creature-0-3771-0-58-2222-00004A2D00"  # same NPC id, another spawn
PET = b"Pet-0-3771-0-58-4545-0200A1B2C3"
VEHICLE = b"Vehicle-0-3771-7-58-6060-00004A2E01"
DOOR = b"GameObject-0-3771-0-58-7070-00004A2F02"


def _identity(*guids: bytes) -> object:
    return Identity(characters=["Orlavin"], realms=["Kestrel Hollow"], guids=list(guids))


def _line(body: bytes) -> bytes:
    return TS + body + b"\n"


def _process(
    log: bytes,
    tmp_path: Path,
    *,
    others: object | None = None,
    locations: object | None = None,
    identity: object | None = None,
    name: str = "WoWCombatLog-010126_000000.txt",
) -> tuple[list[str], bytes, object]:
    source = tmp_path / name
    source.write_bytes(log)
    rel = PurePosixPath(f"_classic_beta_/Logs/{name}")
    kind = lab_capture.kind_of(name)
    item = lab_capture.Item(source, rel, "_classic_beta_", "1", kind)
    identity = identity if identity is not None else _identity(OWN)
    outcome = lab_capture.process(item, identity, None, others, locations)
    return list(outcome.problems), outcome.result.data, outcome


def _hit(attacker: bytes, target: bytes, name: bytes = b"Boar") -> bytes:
    return _line(
        b"SWING_DAMAGE," + attacker + b',"' + name + b'",0xa48,0x0,' + target + b',"Target",'
        b"0x10a48,0x0,1,-1"
    )


_UNIT = re.compile(rb"([A-Za-z]+)-0-([0-9]+)-([0-9]+)-([0-9]+)-([0-9]+)-([0-9A-F]+)")


# ─── 1. unit GUIDs ───────────────────────────────────────────────────────────


def test_constructed_unit_guid_gets_invented_location_parts(tmp_path: Path) -> None:
    log = HEADER + _hit(BOAR, OWN_UNIT[: len(OWN)])
    problems, data, outcome = _process(log, tmp_path)
    assert problems == []
    # server 3771 -> 1, instance 0 stays, zone 58 -> 2, first spawn UID -> 0 (width kept).
    assert b"Creature-0-1-0-2-2222-0000000000," in data
    assert BOAR not in data and b"3771" not in data and b"4A2C11" not in data
    assert outcome.result.count("unit-guid") == 1


def test_constructed_same_guid_maps_the_same_on_every_line(tmp_path: Path) -> None:
    log = HEADER + _hit(BOAR, OWN) + _hit(WOLF, OWN) + _hit(OWN, BOAR) + _hit(BOAR, WOLF)
    problems, data, outcome = _process(log, tmp_path)
    assert problems == []
    boar, wolf = b"Creature-0-1-0-2-2222-0000000000", b"Creature-0-1-0-2-2223-0000000001"
    assert data.count(boar) == 3 and data.count(wolf) == 2
    assert outcome.result.count("unit-guid") == 5
    # Byte level: nothing but the GUIDs changed (every one keeps its offset order).
    own = data.replace(b"Player-9999-00000001", OWN)  # the owner's GUID, rewritten as before
    assert _UNIT.sub(b"G", own) == _UNIT.sub(b"G", log)


def test_constructed_different_guids_never_collide() -> None:
    """Differ in one field at a time, then in bulk: the invented GUIDs stay distinct."""
    base = (b"Creature", b"3771", b"12", b"58", b"2222", b"00004A2C11")
    variants = {base}
    for index, other in enumerate((b"3772", b"13", b"59", b"2223", b"00004A2C12")):
        changed = list(base)
        changed[index + 1] = other
        variants.add(tuple(changed))
    variants.update(
        (b"Creature", b"%d" % s, b"%d" % i, b"%d" % z, b"2222", b"%010X" % (s * i * z))
        for s in (3771, 12, 1)
        for i in (0, 1, 12, 3771)
        for z in (0, 1, 58, 3771)
    )
    real = [b"-".join((t, b"0", s, i, z, n, u)) for t, s, i, z, n, u in sorted(variants)]
    log = HEADER + b"".join(_hit(guid, OWN) for guid in real)
    scan = UnitGuids().scan(log)
    assert scan.unclassified == 0 and len(scan.edits) == len(real)
    invented = [new for _s, _e, new in scan.edits]
    assert len(set(invented)) == len(set(real))


def test_constructed_npc_id_type_and_shape_are_kept() -> None:
    log = HEADER + b"".join(_hit(guid, OWN) for guid in (BOAR, WOLF, BOAR_TWO, PET, VEHICLE, DOOR))
    edits = UnitGuids().scan(log).edits
    assert len(edits) == 6
    for start, end, new in edits:
        real = _UNIT.fullmatch(log[start:end])
        fake = _UNIT.fullmatch(new)
        assert real is not None and fake is not None
        assert fake.group(1) == real.group(1)  # type
        assert fake.group(5) == real.group(5)  # NPC or object id
        assert len(fake.group(6)) == len(real.group(6))  # spawn UID width, upper-case hex
        for index in (2, 3, 4):
            assert fake.group(index).isdigit()  # decimal stays decimal


def test_constructed_pet_vehicle_and_game_object_shapes(tmp_path: Path) -> None:
    log = (
        HEADER
        + _line(b"SPELL_SUMMON," + OWN_UNIT + b"," + PET + b',"Grizzle",0x1111,0x0,883,"Call"')
        + _hit(VEHICLE, OWN)
        + _hit(DOOR, OWN)
        + _hit(PET, VEHICLE)
    )
    problems, data, outcome = _process(log, tmp_path)
    assert problems == []
    # One shared decimal sequence: server 3771 -> 1, zone 58 -> 2, instance 7 -> 3;
    # spawn UIDs numbered in order of first appearance, whatever the type.
    assert data.count(b"Pet-0-1-0-2-4545-0000000000") == 2
    assert data.count(b"Vehicle-0-1-3-2-6060-0000000001") == 2
    assert data.count(b"GameObject-0-1-0-2-7070-0000000002") == 1
    assert not [g for g in (PET, VEHICLE, DOOR) if g in data]
    assert outcome.result.count("unit-guid") == 5


@pytest.mark.parametrize(
    ("guid", "expected"),
    [
        (b"Creature-0-3771-0-0-2222-00004A2C11", b"Creature-0-1-0-0-2222-0000000000"),
        (b"Creature-0-0-0-0-2222-00004A2C11", b"Creature-0-0-0-0-2222-0000000000"),
        (b"Creature-0-3771-00-58-2222-00004A2C11", b"Creature-0-1-00-2-2222-0000000000"),
    ],
    ids=["instance-and-zone-zero", "server-zero-too", "zero-keeps-its-spelling"],
)
def test_constructed_zero_fields_stay(guid: bytes, expected: bytes, tmp_path: Path) -> None:
    problems, data, _o = _process(HEADER + _hit(guid, OWN), tmp_path)
    assert problems == [] and expected in data


def test_constructed_empty_guid_and_nil_stay(tmp_path: Path) -> None:
    log = HEADER + _line(
        b"SPELL_CAST_SUCCESS," + BOAR + b',"Boar",0xa48,0x0,0000000000000000,nil,0x80000000,'
        b'0x80000000,585,"Smite",0x2'
    )
    problems, data, _o = _process(log, tmp_path)
    assert problems == []
    assert b",0000000000000000,nil,0x80000000," in data


def test_constructed_registry_is_shared_across_logs_of_one_run(tmp_path: Path) -> None:
    locations = UnitGuids()
    _p, first, _o = _process(HEADER + _hit(BOAR, OWN), tmp_path, locations=locations)
    _p, second, _o = _process(
        HEADER + _hit(WOLF, OWN) + _hit(BOAR, OWN),
        tmp_path,
        locations=locations,
        name="WoWCombatLog-010226_000000.txt",
    )
    assert b"Creature-0-1-0-2-2222-0000000000" in first
    assert b"Creature-0-1-0-2-2222-0000000000" in second  # the boar again: same GUID
    assert b"Creature-0-1-0-2-2223-0000000001" in second  # the wolf: the next spawn UID


def test_constructed_spawn_width_that_cannot_hold_another_value_refuses() -> None:
    """Sixteen one-digit spawn UIDs fill the width; a seventeenth is never a reuse."""
    guids = [b"Creature-0-3771-0-58-2222-%X" % n for n in range(16)]
    scan = UnitGuids().scan(b"".join(_hit(g, OWN) for g in guids))
    assert scan.unclassified == 0 and len({new for *_, new in scan.edits}) == 16
    scan = UnitGuids().scan(
        b"".join(_hit(g, OWN) for g in [*guids, b"Creature-0-3771-0-58-2222-G"])
    )
    assert scan.unclassified == 1  # `G` is no hex digit: unclassifiable anyway
    wide = UnitGuids()
    wide.scan(b"".join(_hit(g, OWN) for g in guids))
    assert wide.scan(_hit(b"Creature-0-3771-0-59-2222-0", OWN)).unclassified == 0  # seen before
    assert wide.scan(_hit(b"Creature-0-3771-0-58-2222-00", OWN)).unclassified == 0  # new width


@pytest.mark.parametrize(
    "token",
    [
        b"Cast-3-3771-0-58-585-00004A2C11",  # a type number where the `0` stands
        b"Creature-0-3771-0-58-2222",  # a field short
        b"Creature-0-3771-0-58-2222-00004A2C11-7",  # a field long
        b"Creature-0-3771-0-58-2222-00004a2c11",  # lower-case spawn UID
        b"Creature-0-3771-0-58-2222-00004A2C11_x",  # glued suffix
        b"Creature-0-37x1-0-58-2222-00004A2C11",  # a letter in a decimal field
        b"BattlePet-0-00000ABC1234",
        b"Player-0-3771-0-58-2222-00004A2C11",  # a Player type in the unit shape
        b"Creature-0-1",
    ],
)
def test_constructed_unclassifiable_guid_refuses_with_a_count_only(
    token: bytes, tmp_path: Path
) -> None:
    log = HEADER + _hit(BOAR, OWN) + _line(b"SPELL_DAMAGE," + token + b',"x",0xa48,0x0')
    problems, _data, _o = _process(log, tmp_path)
    assert f"{UNCLASSIFIED} x1" in problems
    for problem in problems:
        assert "3771" not in problem and "4A2C11" not in problem.upper()
    assert f"{UNCLASSIFIED} x1" == next(p for p in problems if p.startswith(UNCLASSIFIED))


def test_constructed_identity_guid_families_keep_their_own_refusal(tmp_path: Path) -> None:
    """Player, BNetAccount, Guild, ClubFinder: not double-counted as unclassifiable."""
    log = HEADER + _line(
        b"X,Player-1-0000ABCD,Guild-1-00AB,BNetAccount-0-0000CD,ClubFinder-1-2-3-4,"
        + BOAR
        + b',"Boar"'
    )
    problems, _data, _o = _process(log, tmp_path)
    assert not [p for p in problems if p.startswith(UNCLASSIFIED)]
    assert any(p.startswith("guild GUID") for p in problems)


@pytest.mark.parametrize(
    "log",
    [
        HEADER + _line(b"SPELL_DAMAGE," + OWN_UNIT + b",0000000000000000,nil,0x80000000,0x0,1"),
        HEADER + _line(b'ENCOUNTER_START,1234,"Warden of the Deep",1,5,36'),
        HEADER + _line(b'ZONE_CHANGE,2222,"Deep-Run 3 - East",0'),
        HEADER + _line(b'SPELL_DAMAGE,Player-1-0000ABCD,"Orlavin-KestrelHollow",0x511,0x0'),
    ],
    ids=["nil-target", "encounter", "hyphenated-text", "own-unit"],
)
def test_constructed_log_without_unit_guids_is_byte_identical_to_the_plain_scrub(
    log: bytes, tmp_path: Path
) -> None:
    """Differential: with no unit GUID in it, a log comes out exactly as the scrub
    without the unit-GUID step makes it (itself byte-identical where nothing of the
    owner's is in it)."""
    problems, data, outcome = _process(log, tmp_path)
    plain = _identity(OWN).scrub(log)
    assert data == plain.data and problems == list(plain.problems) == []
    assert outcome.result.count("unit-guid") == 0
    if b"Orlavin" not in log:
        assert data == log


def test_constructed_unit_guids_outside_a_combat_log_are_not_touched(tmp_path: Path) -> None:
    body = b'\nTrackerDB = {\n["last"] = "' + BOAR + b'",\n["odd"] = "Cast-3-1-2",\n}\n'
    source = tmp_path / "Tracker.lua"
    source.write_bytes(body)
    rel = PurePosixPath("_retail_/WTF/Account/A/SavedVariables/Tracker.lua")
    item = lab_capture.Item(source, rel, "_retail_", "1", "savedvariables")
    outcome = lab_capture.process(item, _identity(OWN), None, None, UnitGuids())
    assert outcome.problems == [] and outcome.result.data == body


def test_constructed_unit_guids_apply_with_the_other_players_flag(tmp_path: Path) -> None:
    zor = b'Player-1-00C0FFEE,"Zorvinth-KestrelHollow-",0x512,0x0'
    log = HEADER + _line(b"SWING_DAMAGE," + zor + b"," + BOAR + b',"Boar",0xa48,0x0,1,-1')
    problems, data, outcome = _process(log, tmp_path, others=OtherPlayers())
    assert problems == []
    assert b'Player-9998-00000001,"Labothera-LabrealmaPartb-"' in data
    assert b"Creature-0-1-0-2-2222-0000000000" in data and BOAR not in data
    assert outcome.result.count("unit-guid") == 1 and outcome.other_players == 1


# ─── provenance row and summary line ─────────────────────────────────────────


def test_constructed_provenance_row_counts_unit_guids(tmp_path: Path) -> None:
    log = HEADER + _hit(BOAR, OWN) + _hit(WOLF, OWN) + _hit(BOAR, OWN)
    _p, _d, outcome = _process(log, tmp_path)
    row = lab_capture.provenance_row(outcome, "macos", "owner", "owner")
    scrub = row.split(" | ")[7]
    assert "unit-guids-rewritten: 3" in scrub
    assert "3771" not in row


def _add_log(root: Path, flavor: str, name: str, body: bytes, mtime: int) -> Path:
    path = root / flavor / "Logs" / name
    path.write_bytes(body)
    os.utime(path, (mtime, mtime))
    return path


def test_constructed_cli_row_and_summary_line_carry_the_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    body = (
        b"9/20/2026 21:14:03.123-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
        b'9/20/2026 21:14:05.871-4  SWING_DAMAGE,Player-1234-0ABCDEF0,"Thrallmar-Area52-US",'
        b"0x511,0x0," + BOAR + b',"Boar",0xa48,0x0,1,-1\n'
        b"9/20/2026 21:14:05.990-4  SWING_DAMAGE," + BOAR + b',"Boar",0xa48,0x0,'
        b'Player-1234-0ABCDEF0,"Thrallmar-Area52-US",0x511,0x0,1,-1\n'
    )
    _add_log(root, "_retail_", "WoWCombatLog-092126_190000.txt", body, 2_000_000_000)
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_") == 0
    printed = capsys.readouterr().out
    wrote = [line for line in printed.splitlines() if line.startswith("wrote") and "Combat" in line]
    assert len(wrote) == 1 and wrote[0].endswith(", 2 unit GUIDs rewritten)")
    rows = [line for line in printed.splitlines() if line.startswith("| ") and "Combat" in line]
    assert len(rows) == 1 and "unit-guids-rewritten: 2" in rows[0]
    log = outputs(out)[f"macos/_retail_/Logs/{_shifted('WoWCombatLog-092126_190000.txt')}"]
    assert log.count(b"Creature-0-1-0-2-2222-0000000000") == 2
    assert b"3771" not in log and b"3771" not in printed.encode()


# ─── 2. fold gap: other players' names ───────────────────────────────────────


def _one_player(unit_name: str, text: str) -> bytes:
    unit = b'Player-1-00C0FFEE,"' + unit_name.encode() + b'",0x512,0x0'
    return (
        HEADER
        + _line(b"SWING_DAMAGE," + unit + b"," + BOAR + b',"Boar",0xa48,0x0,1,-1')
        + _line(b"SPELL_DAMAGE," + OWN_UNIT + b"," + BOAR + b',"Boar",0xa48,0x0,585,"Smite"')
        + _line(b"EMOTE," + BOAR + b',"Boar",0000000000000000,nil,"' + text.encode() + b'"')
    )


@pytest.mark.parametrize(
    ("unit_name", "text"),
    [
        ("Zorvinth-KestrelHollow-", "\uff3a\uff4f\uff52\uff56\uff49\uff4e\uff54\uff48 waves."),
        ("Zorvinth-KestrelHollow-", "\uff3aorvinth waves."),  # one full-width letter
        ("Ash-KestrelHollow-", "\uff21\uff53\uff48 waves."),  # a short name, full-width
        ("Zorvinth-KestrelHollow-", "Zor\tvinth waves."),
        ("Zorvinth-KestrelHollow-", "Zor\u00a0vinth waves."),
        ("Zorvinth-KestrelHollow-", "Zor\u200bvinth waves."),
        ("Zorvinth-KestrelHollow-", "Zor  vinth waves."),
        ("Zorvinth-KestrelHollow-", "Zor -\t' vinth waves."),
        ("Zorvinth-Gloamspire-US", "The Warden of Gloam\u00a0\u00a0spire wakes."),
        ("Zorvinth-Gloamspire-US", "The Warden of Gloam\u200bspire wakes."),
        # Two words in the unit name: each is its own hunted part, so two hits.
        ("Zorvinth-Gloam\u00a0Spire-US", "The Warden of GloamSpire wakes."),
    ],
    ids=[
        "full-width",
        "one-full-width-letter",
        "short-full-width",
        "tab",
        "nbsp",
        "zero-width-space",
        "doubled-space",
        "mixed-run",
        "realm-doubled-nbsp",
        "realm-zero-width",
        "nbsp-in-the-unit-name",
    ],
)
def test_constructed_other_players_name_in_a_new_fold_case_refuses(
    unit_name: str, text: str, tmp_path: Path
) -> None:
    problems, _data, _o = _process(_one_player(unit_name, text), tmp_path, others=OtherPlayers())
    hits = 2 if "\u00a0Spire" in unit_name else 1
    assert problems == [f"{OTHER_NAME} x{hits}"]


def test_constructed_new_fold_cases_have_a_passing_control(tmp_path: Path) -> None:
    log = _one_player("Zorvinth-KestrelHollow-", "Zor\tbows. The vinth\u200b waves.")
    problems, data, _o = _process(log, tmp_path, others=OtherPlayers())
    assert problems == []
    # Detection only: the text the hunt folded is written back byte for byte.
    assert b'"Zor\tbows. The vinth\xe2\x80\x8b waves."' in data


# ─── 2. fold gap: the loose second-name detector ─────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        b'"Quel\tThalas"',
        b'"Quel\xc2\xa0Thalas"',
        b'"Quel\xe2\x80\x8bThalas"',
        b'"Quel  Thalas"',
        b"\"Quel'' Thalas\"",
        b'"Qu el\tTha las"',
    ],
    ids=["tab", "nbsp", "zero-width-space", "doubled-space", "doubled-apostrophe", "several"],
)
def test_constructed_loose_second_name_in_a_new_separator_refuses(text: bytes) -> None:
    identity = Identity(characters=["Kael"], realms=["QuelThalas"], loose=["QuelThalas"])
    result = identity.scrub(text)
    assert any(p.startswith(LOOSE) for p in result.problems), result.problems
    assert result.data == text  # detection only: nothing was rewritten


def test_constructed_loose_second_name_control_is_still_rewritten() -> None:
    identity = Identity(characters=["Kael"], realms=["QuelThalas"], loose=["QuelThalas"])
    result = identity.scrub(b'"QuelThalas" "Quel\tbows"')
    assert not result.problems and result.data == b'"Labrealma" "Quel\tbows"'


# ─── 3. --combat-log ─────────────────────────────────────────────────────────

OLDER = "WoWCombatLog-091926_100000.txt"
NEWER = "WoWCombatLog-092126_190000.txt"
DEFAULT = "WoWCombatLog-092026_211403.txt"  # build_install's log, in both flavors
CLI_OWN = b'Player-1234-0ABCDEF0,"Thrallmar-Area52-US",0x511,0x0'
CLI_TS = b"9/20/2026 21:14:05.871-4  "
CLI_HEADER = CLI_TS + b"COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"


def _cli_line(body: bytes) -> bytes:
    return CLI_TS + body + b"\n"


def _two_logs(root: Path, older: bytes, newer: bytes) -> None:
    _add_log(root, "_retail_", OLDER, older, 1_800_000_000)
    _add_log(root, "_retail_", NEWER, newer, 2_000_000_000)


def _written_logs(out: Path) -> dict[str, bytes]:
    """Written logs by the REAL name each came from (outputs carry shifted names)."""
    real = {_shifted(name): name for name in (OLDER, NEWER, DEFAULT)}
    return {
        real[PurePosixPath(dest).name]: data
        for dest, data in outputs(out).items()
        if "WoWCombatLog" in dest
    }


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    return root


def test_constructed_without_the_option_only_the_newest_log_is_captured(
    root: Path, tmp_path: Path
) -> None:
    body = CLI_HEADER + _cli_line(b"SWING_DAMAGE," + CLI_OWN + b"," + BOAR + b',"Boar",0xa48,0x0')
    _two_logs(root, body, body)
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_") == 0
    assert set(_written_logs(out)) == {NEWER}


def test_constructed_each_named_log_is_captured_with_its_own_row(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    older = CLI_HEADER + _cli_line(b"SWING_DAMAGE," + CLI_OWN + b"," + BOAR + b',"Boar",0xa48,0x0')
    newer = CLI_HEADER + _cli_line(b"SWING_DAMAGE," + CLI_OWN + b"," + WOLF + b',"Wolf",0xa48,0x0')
    _two_logs(root, older, newer)
    out = tmp_path / "incoming"
    code = capture(root, out, "--flavor", "_retail_", "--combat-log", OLDER, "--combat-log", NEWER)
    assert code == 0
    logs = _written_logs(out)
    assert set(logs) == {OLDER, NEWER}
    # One registry for the run: the boar's server and zone numbers carry over to the wolf.
    assert b"Creature-0-1-0-2-2222-0000000000" in logs[OLDER]
    assert b"Creature-0-1-0-2-2223-0000000001" in logs[NEWER]
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("| `macos/")]
    for name in (OLDER, NEWER):
        mine = [row for row in rows if _shifted(name) in row]
        assert len(mine) == 1 and "requested with --combat-log" in mine[0]
        assert "unit-guids-rewritten: 1" in mine[0]


def test_constructed_named_log_is_captured_from_every_flavor_that_has_it(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _add_log(root, "_retail_", OLDER, CLI_HEADER, 1_800_000_000)
    out = tmp_path / "incoming"
    assert capture(root, out, "--combat-log", DEFAULT) == 0
    written = [dest for dest in outputs(out) if "WoWCombatLog" in dest]
    assert sorted(written) == [
        f"macos/_classic_beta_/Logs/{_shifted(DEFAULT)}",
        f"macos/_retail_/Logs/{_shifted(DEFAULT)}",
    ]
    assert capture(root, tmp_path / "second", "--combat-log", OLDER) == 0
    assert [d for d in outputs(tmp_path / "second") if "WoWCombatLog" in d] == [
        f"macos/_retail_/Logs/{_shifted(OLDER)}"
    ]
    printed = capsys.readouterr().out
    assert "_classic_beta_: --combat-log named no log here; none captured" in printed


@pytest.mark.parametrize(
    "name",
    [
        "WoWCombatLog-000000_000000.txt",  # does not exist
        "wowcombatlog-092026_211403.txt",  # exists only in another casing
        "../Logs/WoWCombatLog-092026_211403.txt",
        "WoWCombatLog-092026_211403.txt/..",
        "sub/WoWCombatLog-092026_211403.txt",
        "WoWCombatLog\\..\\x.txt",
        "WoWCombatLog..txt",
        "Config.wtf",
        "combat-WoWCombatLog-092026_211403.txt",
        "",
    ],
    ids=[
        "missing",
        "other-casing",
        "parent",
        "trailing-parent",
        "slash",
        "backslash",
        "dot-dot",
        "not-a-log",
        "prefix-not-first",
        "empty",
    ],
)
def test_constructed_bad_combat_log_name_stops_the_run_before_anything_is_written(
    name: str, root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(root, out, "--combat-log", DEFAULT, "--combat-log", name) == 2
    assert outputs(out) == {}
    err = capsys.readouterr().err
    # Named by position only: a log's file name encodes the session's real date.
    assert "the 2nd --combat-log" in err
    assert not name or name not in err
    assert "092026_211403" not in err


def test_constructed_symlinked_log_is_not_a_log(root: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.txt"
    target.write_bytes(CLI_HEADER)
    link = root / "_retail_" / "Logs" / OLDER
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("no symlinks on this file system")
    out = tmp_path / "incoming"
    assert capture(root, out, "--combat-log", OLDER) == 2
    assert outputs(out) == {}


def test_constructed_each_named_log_is_checked_on_its_own(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stranger = b'Player-1234-00C0FFEE,"Zorvinth-Area52-US",0x512,0x0'
    older = CLI_HEADER + _cli_line(b"SWING_DAMAGE," + stranger + b"," + BOAR + b',"Boar"')
    newer = CLI_HEADER + _cli_line(b"SWING_DAMAGE," + CLI_OWN + b"," + BOAR + b',"Boar"')
    _two_logs(root, older, newer)
    out = tmp_path / "incoming"
    args = ("--flavor", "_retail_", "--combat-log", OLDER, "--combat-log", NEWER)
    assert capture(root, out, *args) == 1  # the older log names another player
    assert set(_written_logs(out)) == {NEWER}
    refused = [line for line in capsys.readouterr().out.splitlines() if "REFUSED" in line]
    assert len(refused) == 1 and _shifted(OLDER) in refused[0]
    assert "unmapped player GUID" in refused[0] and OLDER not in refused[0]


def test_constructed_log_lines_and_own_guid_apply_to_each_named_log(
    root: Path, tmp_path: Path
) -> None:
    alone = b'Player-5-0000BEEF,"Thrallmar",0x511,0x0'  # only --own-guid makes it the owner's
    body = CLI_HEADER + b"".join(
        _cli_line(b"SWING_DAMAGE," + alone + b"," + BOAR + b',"Boar",0xa48,0x0,%d' % i)
        for i in range(5)
    )
    _two_logs(root, body, body)
    args = ("--flavor", "_retail_", "--combat-log", OLDER, "--combat-log", NEWER)
    out = tmp_path / "incoming"
    assert capture(root, out, *args, "--log-lines", "3") == 1  # no --own-guid: both refused
    assert _written_logs(out) == {}
    out = tmp_path / "allowed"
    code = capture(root, out, *args, "--log-lines", "3", "--own-guid", "Player-5-0000BEEF")
    assert code == 0
    logs = _written_logs(out)
    assert set(logs) == {OLDER, NEWER}
    for data in logs.values():
        assert data.count(b"\n") == 3
        assert b',"Labchara",0x511' in data and b"0000BEEF" not in data


def test_constructed_other_player_pseudonyms_stay_distinct_across_logs(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One registry per run: a player keeps one pseudonym in every log, and two
    different players never share one, whichever log each first appears in."""
    zor = b'Player-1234-00C0FFEE,"Zorvinth-Area52-US",0x512,0x0'
    que = b'Player-1234-00BEEF01,"Quellanor-Area52-US",0x514,0x0'
    older = CLI_HEADER + _cli_line(b"SWING_DAMAGE," + zor + b"," + BOAR + b',"Boar",0xa48,0x0')
    newer = (
        CLI_HEADER
        + _cli_line(b"SWING_DAMAGE," + que + b"," + BOAR + b',"Boar",0xa48,0x0')
        + _cli_line(b"SWING_DAMAGE," + zor + b"," + WOLF + b',"Wolf",0xa48,0x0')
    )
    _two_logs(root, older, newer)
    out = tmp_path / "incoming"
    args = ("--flavor", "_retail_", "--combat-log", OLDER, "--combat-log", NEWER)
    assert capture(root, out, *args, "--pseudonymise-other-players") == 0
    logs = _written_logs(out)
    assert b'Player-9998-00000001,"Labothera-LabrealmaPartb-US"' in logs[OLDER]
    assert b'Player-9998-00000001,"Labothera-LabrealmaPartb-US"' in logs[NEWER]
    assert b'Player-9998-00000002,"Labotherb-LabrealmaPartb-US"' in logs[NEWER]
    for data in logs.values():
        assert not [s for s in (b"Zorvinth", b"Quellanor", b"00C0FFEE", b"00BEEF01") if s in data]
    printed = capsys.readouterr().out
    rows = [line for line in printed.splitlines() if line.startswith("| ") and "Combat" in line]
    counts = sorted(re.findall(r"other-players-pseudonymised: (\d)", " ".join(rows)))
    assert counts == ["1", "2"]
