"""`scripts/lab_capture.py`: fourth follow-up to M10-02.

`--pseudonymise-other-players` (combat logs only): every Player GUID that is
not the owner's becomes an invented GUID, and the character-name part of the
quoted unit name after it an invented pseudonym, consistently for the run. A
later part of the unit name that is one of the owner's own identity strings
(another player on the owner's realm) is scrubbed by the own-identity rules; a
realm the tool does not know gets a realm-style pseudonym; a region word or an
empty part (the trailing `-`) stays. The log is refused, with a count only, if
one of those real parts appears anywhere outside the rewritten unit fields, or
if another player's GUID never stands in a GUID+name unit pair. Without the
flag nothing changes: the log is refused with `unmapped player GUID`.

CONSTRUCTED INPUT. Every log line below is written for the test in the shape of
docs/LAB_FORMATS.md §8, with invented names only; trees are synthetic, under
`tmp_path`. No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import build_install, capture, lab_capture, outputs

Identity = lab_capture.Identity
OtherPlayers = lab_capture.OtherPlayers
GAME_TEXT = lab_capture.COMBAT_LOG_TEXT_LABEL
OTHER_NAME = lab_capture.OTHER_NAME_LABEL
OTHER_GUID = lab_capture.OTHER_GUID_LABEL

OWN = b"Player-1-0000ABCD"
TS = b"1/1/2026 00:00:00.000-4  "
HEADER = TS + b"COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
OWN_UNIT = b'Player-1-0000ABCD,"Orlavin-KestrelHollow-",0x511,0x0'
DUMMY = b'Creature-0-1-2-3-4-0000000000,"Training Dummy",0x10a48,0x0'
# Two other players, invented. Both on the owner's realm, as in the owner's log.
ZOR_GUID = b"Player-1-00C0FFEE"
QUE_GUID = b"Player-1-00BEEF01"
ZOR = b'Player-1-00C0FFEE,"Zorvinth-KestrelHollow-",0x512,0x0'
QUE = b'Player-1-00BEEF01,"Quellanor-KestrelHollow-",0x514,0x80'
# A third, on a realm the tool does not know.
FAR_GUID = b"Player-77-0DEADBEE"
FAR = b'Player-77-0DEADBEE,"Tamrisk-Gloamspire-US",0x548,0x0'
INVENTED = (
    b"Zorvinth",
    b"Quellanor",
    b"Tamrisk",
    b"Gloamspire",
    b"Orlavin",
    b"KestrelHollow",
    b"Kestrel Hollow",
    b"Qorv",
)
REAL_GUIDS = (ZOR_GUID, QUE_GUID, FAR_GUID, OWN)


def _identity(*guids: bytes) -> object:
    return Identity(
        characters=["Orlavin"], realms=["Kestrel Hollow"], extras=["Qorv"], guids=list(guids)
    )


def _process(
    log: bytes, tmp_path: Path, *, others: object | None = None, identity: object | None = None
) -> tuple[list[str], bytes, object]:
    source = tmp_path / "WoWCombatLog-010126_000000.txt"
    source.write_bytes(log)
    rel = PurePosixPath("_classic_beta_/Logs/WoWCombatLog-010126_000000.txt")
    item = lab_capture.Item(source, rel, "_classic_beta_", "1", "combatlog")
    identity = identity if identity is not None else _identity(OWN)
    outcome = lab_capture.process(item, identity, None, others)
    return list(outcome.problems), outcome.result.data, outcome


def _line(body: bytes) -> bytes:
    return TS + body + b"\n"


def _no_real(data: bytes) -> list[bytes]:
    lowered = data.lower()
    return [s for s in (*INVENTED, *REAL_GUIDS) if s.lower() in lowered]


TWO_PLAYERS = (
    HEADER
    + _line(b"SWING_DAMAGE," + ZOR + b"," + DUMMY + b",1234,-1,1,0,0,0,nil,nil,nil")
    + _line(b"SPELL_CAST_SUCCESS," + QUE + b",0000000000000000,nil,0x80000000,0x80000000,48438")
    + _line(b"SPELL_AURA_APPLIED," + QUE + b"," + ZOR + b',48438,"Wild Growth",0x8,BUFF')
    + _line(
        b"SPELL_PERIODIC_ENERGIZE," + ZOR + b"," + ZOR + b',12345,"Invented Surge",0x1,'
        b"Player-1-00C0FFEE,0000000000000000,100,100,0,0,0,-1,0,0,0,1.0,2.0,0,1.5,70,5,0,0,3,100"
    )
    + _line(b"SPELL_DAMAGE," + OWN_UNIT + b"," + DUMMY + b',585,"Smite",0x2,100')
    + _line(b"COMBATANT_INFO,Player-1-00BEEF01,1,2,3,[(1,2),(3,4)]")
)


# ─── rewritten consistently ──────────────────────────────────────────────────


def test_constructed_two_players_on_the_owners_realm_are_pseudonymised(tmp_path: Path) -> None:
    problems, data, outcome = _process(TWO_PLAYERS, tmp_path, others=OtherPlayers())
    assert problems == []
    # First appearance numbers them; the owner's realm goes through the own rules.
    assert b'Player-9998-00000001,"Labothera-LabrealmaPartb-",0x512,0x0' in data
    assert b'Player-9998-00000002,"Labotherb-LabrealmaPartb-",0x514,0x80' in data
    assert data.count(b"Player-9998-00000001") == 5  # 4 unit pairs + the advanced field
    assert data.count(b"Player-9998-00000002") == 3  # 2 unit pairs + COMBATANT_INFO
    assert b'Player-9999-00000001,"Labchara-LabrealmaPartb-",0x511,0x0' in data
    assert b'"Training Dummy"' in data and b'"Wild Growth"' in data
    assert _no_real(data) == []
    assert outcome.other_players == 2
    # Byte-level: only the edits changed anything.
    assert len(data) - len(TWO_PLAYERS) == sum(
        len(e.new) - len(e.old) for e in outcome.result.edits
    )


def test_constructed_player_on_a_foreign_realm_gets_a_realm_pseudonym(tmp_path: Path) -> None:
    log = (
        HEADER
        + _line(b"SPELL_HEAL," + FAR + b"," + OWN_UNIT + b',2061,"Flash Heal",0x2,500,0,0,nil')
        + _line(b"SWING_DAMAGE," + FAR + b"," + DUMMY + b",10,-1,1,0,0,0,nil,nil,nil")
    )
    problems, data, outcome = _process(log, tmp_path, others=OtherPlayers())
    assert problems == []
    assert data.count(b'Player-9998-00000001,"Labothera-Labotherrealma-US",0x548,0x0') == 2
    assert _no_real(data) == []
    assert outcome.other_players == 1


def test_constructed_pseudonyms_are_stable_across_logs_in_one_run(tmp_path: Path) -> None:
    others = OtherPlayers()
    first = HEADER + _line(b"SWING_DAMAGE," + QUE + b"," + DUMMY + b",1,-1,1,0,0,0,nil,nil,nil")
    _p, one, _o = _process(first, tmp_path, others=others)
    _p, two, _o = _process(TWO_PLAYERS, tmp_path, others=others)
    assert b'Player-9998-00000001,"Labothera-LabrealmaPartb-"' in one
    assert b'Player-9998-00000001,"Labothera-LabrealmaPartb-",0x514' in two  # same player
    assert b'Player-9998-00000002,"Labotherb-LabrealmaPartb-",0x512' in two


def test_constructed_invented_guids_are_not_derived_from_the_real_ones(tmp_path: Path) -> None:
    swapped = TWO_PLAYERS.replace(ZOR_GUID, b"Player-1-00FFFFFF")
    _p, one, _o = _process(TWO_PLAYERS, tmp_path, others=OtherPlayers())
    _p, two, _o = _process(swapped, tmp_path, others=OtherPlayers())
    assert one == two


# ─── refusals ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        # a chat or emote-like line in game text
        b'EMOTE,Creature-0-1-2-3-4-0000000000,"Grave Warden",0000000000000000,nil,'
        b'"The Grave Warden glares at Zorvinth!"',
        # a spell or aura text, in another casing
        b"SPELL_AURA_APPLIED," + DUMMY + b"," + OWN_UNIT + b',4242,"Mark of ZORVINTH",0x20,BUFF',
        # the other player's pet, named after them (rule 3 covers pets)
        b"SPELL_SUMMON," + ZOR + b',Pet-0-1-2-3-4-0000000001,"Zorvinth\'s Wolf",0xa28,0x0,883,'
        b'"Call Pet",0x1',
        # glued inside a longer word: a long name part counts anywhere
        b'SPELL_DAMAGE,Pet-0-1-2-3-4-0000000001,"Zorvinthpaw",0x1114,0x0,' + DUMMY + b",1,-1",
    ],
    ids=["emote", "aura-text-uppercase", "pet-named-after-player", "glued-long-name"],
)
def test_constructed_other_players_name_outside_a_unit_field_refuses(
    line: bytes, tmp_path: Path
) -> None:
    problems, _data, _o = _process(TWO_PLAYERS + _line(line), tmp_path, others=OtherPlayers())
    assert problems == [f"{OTHER_NAME} x1"]  # a count only: no offset, no line, no text


def test_constructed_foreign_realm_outside_a_unit_field_refuses(tmp_path: Path) -> None:
    log = (
        HEADER
        + _line(b"SWING_DAMAGE," + FAR + b"," + DUMMY + b",10,-1,1,0,0,0,nil,nil,nil")
        + _line(b'ENCOUNTER_START,1234,"Warden of Gloamspire",1,5,36')
    )
    problems, _data, _o = _process(log, tmp_path, others=OtherPlayers())
    assert problems == [f"{OTHER_NAME} x1"]


def test_constructed_own_realm_outside_other_units_is_left_to_the_own_rules(
    tmp_path: Path,
) -> None:
    """The owner's realm in other players' unit fields is not a rule-3 hit."""
    log = TWO_PLAYERS * 3
    problems, data, _o = _process(log, tmp_path, others=OtherPlayers())
    assert problems == []
    assert data.count(b"LabrealmaPartb") == 3 * 7  # 6 other-player fields + 1 own


def test_constructed_short_name_part_counts_only_as_a_whole_word(tmp_path: Path) -> None:
    short = b'Player-1-00000A5E,"Ash-KestrelHollow-",0x512,0x0'
    base = HEADER + _line(b"SWING_DAMAGE," + short + b"," + DUMMY + b",1,-1,1,0,0,0,nil,nil,nil")
    inside = base + _line(b"SPELL_DAMAGE," + OWN_UNIT + b"," + DUMMY + b',1,"Flash Heal",0x2')
    problems, data, _o = _process(inside, tmp_path, others=OtherPlayers())
    assert problems == [] and b'"Labothera-LabrealmaPartb-"' in data
    alone = base + _line(b"SPELL_DAMAGE," + OWN_UNIT + b"," + DUMMY + b',1,"Ash Nova",0x2')
    problems, _data, _o = _process(alone, tmp_path, others=OtherPlayers())
    assert problems == [f"{OTHER_NAME} x1"]


def test_constructed_other_players_guid_without_a_unit_name_refuses(tmp_path: Path) -> None:
    log = TWO_PLAYERS + _line(b"COMBATANT_INFO,Player-1-00FACADE,1,2,3,[(1,2)]")
    problems, _data, _o = _process(log, tmp_path, others=OtherPlayers())
    assert f"{OTHER_GUID} x1" in problems
    assert any(p.startswith("unmapped player GUID x1 ") for p in problems)
    assert not [p for p in problems if b"FACADE" in p.encode()]


def test_constructed_mapped_guid_away_from_its_pair_is_allowed(tmp_path: Path) -> None:
    """COMBATANT_INFO and advanced fields carry the GUID alone; a pair elsewhere maps it."""
    problems, data, _o = _process(TWO_PLAYERS, tmp_path, others=OtherPlayers())
    assert problems == []
    assert b"COMBATANT_INFO,Player-9998-00000002,1,2,3" in data


# ─── own-unit rules unchanged ────────────────────────────────────────────────


def test_constructed_own_name_in_game_text_still_refuses_with_the_flag(tmp_path: Path) -> None:
    log = TWO_PLAYERS + _line(
        b"SPELL_AURA_APPLIED," + DUMMY + b"," + OWN_UNIT + b',4242,"Qorv\'s Spirit",0x20,BUFF'
    )
    problems, _data, _o = _process(log, tmp_path, others=OtherPlayers())
    assert problems == [f"{GAME_TEXT} x1"]


def test_constructed_own_unit_without_own_guid_is_pseudonymised_not_leaked(
    tmp_path: Path,
) -> None:
    """With no --own-guid the owner's unit reads as another player's: still no leak."""
    problems, data, _o = _process(
        TWO_PLAYERS, tmp_path, others=OtherPlayers(), identity=_identity()
    )
    assert problems == []
    assert _no_real(data) == []
    assert b"Player-9999-" not in data


# ─── without the flag, and outside combat logs ───────────────────────────────


def test_constructed_without_the_flag_the_log_still_refuses(tmp_path: Path) -> None:
    problems, data, outcome = _process(TWO_PLAYERS, tmp_path)
    assert any(p.startswith("unmapped player GUID x8 ") for p in problems)
    assert not [p for p in problems if p.startswith((OTHER_NAME, OTHER_GUID))]
    assert outcome.other_players == 0
    assert ZOR_GUID in data and b"Zorvinth" in data  # nothing of theirs rewritten


@pytest.mark.parametrize(
    "log",
    [
        TWO_PLAYERS,
        TWO_PLAYERS + _line(b'EMOTE,Creature-0-1,"x",0000000000000000,nil,"Zorvinth waves"'),
        TWO_PLAYERS + _line(b"COMBATANT_INFO,Player-1-00FACADE,1,2,3,[(1,2)]"),
        HEADER + _line(b"SWING_DAMAGE," + FAR + b"," + DUMMY + b",10,-1,1,0,0,0,nil,nil,nil"),
    ],
    ids=["two-players", "name-in-emote", "unpaired-guid", "foreign-realm"],
)
def test_constructed_every_log_here_is_refused_without_the_flag(log: bytes, tmp_path: Path) -> None:
    """Differential: nothing that was refused before passes now unless the flag is given."""
    problems, _data, _o = _process(log, tmp_path)
    assert any(p.startswith("unmapped player GUID") for p in problems)


def test_constructed_flag_does_not_apply_to_other_file_kinds(tmp_path: Path) -> None:
    body = b'\nTrackerDB = {\n["friend"] = "Player-1-00C0FFEE",\n["name"] = "Zorvinth",\n}\n'
    source = tmp_path / "Tracker.lua"
    source.write_bytes(body)
    rel = PurePosixPath("_retail_/WTF/Account/A/SavedVariables/Tracker.lua")
    item = lab_capture.Item(source, rel, "_retail_", "1", "savedvariables")
    flagged = lab_capture.process(item, _identity(OWN), None, OtherPlayers())
    plain = lab_capture.process(item, _identity(OWN))
    assert flagged.problems == plain.problems
    assert any(p.startswith("unmapped player GUID") for p in flagged.problems)
    assert flagged.result.data == plain.result.data == body
    assert flagged.other_players == 0


# ─── through the command line ────────────────────────────────────────────────


# The synthetic install's own character is Thrallmar on Area 52 (test_lab_capture).
CLI_ZOR = b"Player-1234-00C0FFEE"
CLI_QUE = b"Player-1234-00BEEF01"
CLI_LINES = (
    _line(b'SWING_DAMAGE,Player-1234-00C0FFEE,"Zorvinth-Area52-",0x512,0x0,' + DUMMY + b",1,-1")
    + _line(
        b'SPELL_CAST_SUCCESS,Player-1234-00BEEF01,"Quellanor-Area52-US",0x514,0x0,'
        b"0000000000000000,nil,0x80000000,0x80000000,48438"
    )
    + _line(
        b'SPELL_HEAL,Player-1234-00BEEF01,"Quellanor-Area52-US",0x514,0x0,'
        b'Player-1234-0ABCDEF0,"Thrallmar-Area52-US",0x511,0x0,2061,"Flash Heal",0x2,10,0,0,nil'
    )
)
CLI_SECRETS = ("Zorvinth", "Quellanor", CLI_ZOR.decode(), CLI_QUE.decode())


def _install_with_other_players(tmp_path: Path, extra: bytes = b"") -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    for flavor in ("_retail_", "_classic_beta_"):
        log = root / flavor / "Logs" / "WoWCombatLog-092026_211403.txt"
        log.write_bytes(log.read_bytes() + CLI_LINES + extra)
    return root


def test_constructed_cli_flag_writes_the_log_and_records_the_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install_with_other_players(tmp_path)
    out = tmp_path / "incoming"
    assert capture(root, out, "--pseudonymise-other-players") == 0
    printed = capsys.readouterr()
    written = outputs(out)
    logs = [data for dest, data in written.items() if "WoWCombatLog" in dest]
    assert len(logs) == 2 and logs[0] == logs[1]  # one run, one pseudonym per player
    assert b'Player-9998-00000001,"Labothera-LabrealmaPartb-",0x512' in logs[0]
    assert logs[0].count(b'Player-9998-00000002,"Labotherb-LabrealmaPartb-US",0x514') == 2
    assert b'Player-9999-00000001,"Labchara-LabrealmaPartb-US",0x511' in logs[0]
    rows = [line for line in printed.out.splitlines() if "WoWCombatLog" in line and "| " in line]
    assert len(rows) == 2 and all("other-players-pseudonymised: 2" in row for row in rows)
    for data in written.values():
        assert not [s for s in CLI_SECRETS if s.encode().lower() in data.lower()]
    for text in (printed.out, printed.err):
        assert not [s for s in CLI_SECRETS if s.lower() in text.lower()]


def test_constructed_cli_without_the_flag_refuses_as_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install_with_other_players(tmp_path)
    out = tmp_path / "incoming"
    assert capture(root, out) == 1
    printed = capsys.readouterr().out
    refusals = [line for line in printed.splitlines() if line.startswith("REFUSED")]
    assert len(refusals) == 2 and all("unmapped player GUID x3 " in r for r in refusals)
    assert not [r for r in refusals if OTHER_NAME in r or OTHER_GUID in r]
    assert not [dest for dest in outputs(out) if "WoWCombatLog" in dest]


def test_constructed_cli_refusal_prints_no_name_and_no_guid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = _line(b'EMOTE,Creature-0-1,"x",0000000000000000,nil,"Quellanor bows"')
    extra += _line(b"COMBATANT_INFO,Player-1234-00FACADE,1,2,3,[(1,2)]")
    root = _install_with_other_players(tmp_path, extra)
    out = tmp_path / "incoming"
    assert capture(root, out, "--pseudonymise-other-players") == 1
    printed = capsys.readouterr()
    refusals = [line for line in printed.out.splitlines() if line.startswith("REFUSED")]
    assert len(refusals) == 2
    assert all(f"{OTHER_GUID} x1" in r and f"{OTHER_NAME} x1" in r for r in refusals)
    for text in (printed.out, printed.err):
        assert not [s for s in (*CLI_SECRETS, "00FACADE") if s.lower() in text.lower()]
    assert not [dest for dest in outputs(out) if "WoWCombatLog" in dest]


def test_constructed_cli_flag_leaves_every_other_file_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    plain, flagged = tmp_path / "plain", tmp_path / "flagged"
    assert capture(root, plain) == 0
    assert capture(root, flagged, "--pseudonymise-other-players") == 0
    assert outputs(plain) == outputs(flagged)


# ─── fix round 1 (review of #63) ─────────────────────────────────────────────

OTHER_UNCHECKABLE = lab_capture.OTHER_UNCHECKABLE_LABEL


def _one_player(unit_name: bytes, text: bytes = b"The Grave Warden wakes.") -> bytes:
    unit = b'Player-1-00C0FFEE,"' + unit_name + b'",0x512,0x0'
    return (
        HEADER
        + _line(b"SWING_DAMAGE," + unit + b"," + DUMMY + b",1,-1,1,0,0,0,nil,nil,nil")
        + _line(b"SPELL_DAMAGE," + OWN_UNIT + b"," + DUMMY + b',585,"Smite",0x2,100')
        + _line(
            b'EMOTE,Creature-0-1-2-3-4-0000000000,"Grave Warden",0000000000000000,nil,"'
            + text
            + b'"'
        )
    )


def test_constructed_names_inside_pseudonyms_are_masked_not_refused(tmp_path: Path) -> None:
    """`Other` sits inside `Labothera`, `Chara` inside the owner's `Labchara`."""
    log = (
        HEADER
        + _line(b'SWING_DAMAGE,Player-1-00C0FFEE,"Other-KestrelHollow-",0x512,0x0,' + DUMMY)
        + _line(b'SWING_DAMAGE,Player-1-00BEEF01,"Chara-KestrelHollow-",0x512,0x0,' + DUMMY)
        + _line(b"SPELL_DAMAGE," + OWN_UNIT + b"," + DUMMY + b',585,"Smite",0x2,100')
    )
    problems, data, _o = _process(log, tmp_path, others=OtherPlayers())
    assert problems == []
    assert b'"Labothera-LabrealmaPartb-"' in data and b'"Labotherb-LabrealmaPartb-"' in data


@pytest.mark.parametrize(
    ("unit_name", "text"),
    [
        ("Zoëlinde", "Zoëlinde"),  # NFC in the unit, NFD in the text
        ("Zoëlinde", "Zoëlinde"),  # and the other way round
        ("Weißbart", "WEISSBART"),  # full case folding, not simple
        ("WEISSBART", "Weißbart"),
    ],
    ids=["nfc-unit-nfd-text", "nfd-unit-nfc-text", "sharp-s-upper", "upper-sharp-s"],
)
def test_constructed_name_found_whatever_its_normal_form_or_casing(
    unit_name: str, text: str, tmp_path: Path
) -> None:
    log = _one_player(unit_name.encode() + b"-KestrelHollow-", f"{text} waves.".encode())
    problems, _data, _o = _process(log, tmp_path, others=OtherPlayers())
    assert problems == [f"{OTHER_NAME} x1"]


@pytest.mark.parametrize(
    "spelling", [b"Gloam Spire", b"Gloam_Spire", b"gloam-spire", b"Gloam'Spire"]
)
def test_constructed_unknown_realm_found_in_spaced_and_split_spellings(
    spelling: bytes, tmp_path: Path
) -> None:
    log = _one_player(b"Zorvinth-GloamSpire-US", b"The Warden of " + spelling + b" wakes.")
    problems, _data, _o = _process(log, tmp_path, others=OtherPlayers())
    assert problems == [f"{OTHER_NAME} x1"]


def test_constructed_own_realm_only_counts_as_a_whole_unit_name_part(tmp_path: Path) -> None:
    """`KestrelHollowmere` holds the owner's realm but is not it: pseudonymised whole."""
    problems, data, _o = _process(
        _one_player(b"Zorvinth-KestrelHollowmere-"), tmp_path, others=OtherPlayers()
    )
    assert problems == []
    assert b'"Labothera-Labotherrealma-"' in data
    assert b"Kestrel" not in data and b"mere" not in data


def test_constructed_other_pseudonyms_stop_counting_as_known_after_the_log(
    tmp_path: Path,
) -> None:
    identity = _identity(OWN)
    problems, _data, _o = _process(TWO_PLAYERS, tmp_path, others=OtherPlayers(), identity=identity)
    assert problems == []
    assert identity._scoped_words == frozenset()
    source = tmp_path / "Tracker.lua"
    source.write_bytes(b'\nTrackerDB = {\n["Labothera - Kestrel Hollow"] = 1,\n}\n')
    rel = PurePosixPath("_retail_/WTF/Account/A/SavedVariables/Tracker.lua")
    item = lab_capture.Item(source, rel, "_retail_", "1", "savedvariables")
    after = lab_capture.process(item, identity)
    assert any(p.startswith("someone else's name on an own realm") for p in after.problems)


@pytest.mark.parametrize(
    "name", [b"Neutral", b"Default", b"True", b"Nil", b"Player", b"Us", b"Bind"]
)
def test_constructed_vocabulary_name_refuses_as_uncheckable(name: bytes, tmp_path: Path) -> None:
    """Not searched (it would match the log's own words), so never written unchecked."""
    problems, _data, _o = _process(
        _one_player(name + b"-KestrelHollow-"), tmp_path, others=OtherPlayers()
    )
    assert problems == [f"{OTHER_UNCHECKABLE} x1"]


def test_constructed_region_empty_and_digit_parts_are_not_uncheckable(tmp_path: Path) -> None:
    for unit_name in (
        b"Zorvinth-KestrelHollow-US",
        b"Zorvinth-KestrelHollow-",
        b"Zorvinth-Gloam-52",
    ):
        problems, _data, _o = _process(_one_player(unit_name), tmp_path, others=OtherPlayers())
        assert problems == [], unit_name
