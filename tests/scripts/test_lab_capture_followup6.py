"""`scripts/lab_capture.py`: sixth follow-up to M10-02 (owner-approved 2026-09-28).

Every identity check reads five more characters as separators between two
letters: U+2212 (minus sign), U+2043 (hyphen bullet), U+30FC (katakana-
hiragana prolonged sound mark), U+2032 (prime) and the ASCII backtick. The
checks are the owner-side folded hunt, the hunt for other players' names
(`--pseudonymise-other-players`) and the loose second-name detector. The
client does not write these characters, so this is defence in depth. It is
detection and refusal only: no byte is rewritten that was not rewritten before.

CONSTRUCTED INPUT (L8: hostile-input and boundary tests). Every file body and
log line below is written for the test, with invented names, realms, GUIDs,
dates and times only, in the shape of docs/LAB_FORMATS.md sections 6 and 8.
No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import cpu_clock, lab_capture

Identity = lab_capture.Identity
OtherPlayers = lab_capture.OtherPlayers
FOLDED = lab_capture.FOLDED_SURVIVOR_LABEL
OTHER_NAME = lab_capture.OTHER_NAME_LABEL
LOOSE = "surviving identity string (spaced or apostrophe spelling)"

# The five new separators, and a run of all of them (a run counts as one).
SEPARATORS = [chr(0x2212), chr(0x2043), chr(0x30FC), chr(0x2032), chr(0x60)]
IDS = ["u2212-minus", "u2043-hyphen-bullet", "u30fc-prolonged-mark", "u2032-prime", "backtick"]
RUN = "".join(SEPARATORS) * 3

OWN = b"Player-1-0000ABCD"
OWN_UNIT = b'Player-1-0000ABCD,"Orlavin-KestrelHollow-",0x511,0x0'
BOAR = b"Creature-0-3771-0-58-2222-00004A2C11"
TS = b"1/1/2026 00:00:00.000-4  "
HEADER = TS + b"COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
LOG_NAME = "WoWCombatLog-010126_000000.txt"


def _owner() -> object:
    return Identity(characters=["Orlavin", "Ash"], realms=["Kestrel Hollow"], guids=[OWN])


def _saved(text: str) -> bytes:
    return ('Notes = "' + text + '"\n').encode("utf-8")


def _line(body: bytes) -> bytes:
    return TS + body + b"\n"


def _emote(text: str) -> bytes:
    return _line(b"EMOTE," + BOAR + b',"Boar",0000000000000000,nil,"' + text.encode() + b'"')


def _one_player(text: str, unit_name: str = "Zorvinth-KestrelHollow-") -> bytes:
    unit = b'Player-1-00C0FFEE,"' + unit_name.encode() + b'",0x512,0x0'
    return (
        HEADER
        + _line(b"SWING_DAMAGE," + unit + b"," + BOAR + b',"Boar",0xa48,0x0,1,-1')
        + _line(b"SPELL_DAMAGE," + OWN_UNIT + b"," + BOAR + b',"Boar",0xa48,0x0,585,"Smite"')
        + _emote(text)
    )


def _process(log: bytes, tmp_path: Path, identity: object | None = None) -> tuple[list[str], bytes]:
    source = tmp_path / LOG_NAME
    source.write_bytes(log)
    rel = PurePosixPath(f"_classic_beta_/Logs/{LOG_NAME}")
    item = lab_capture.Item(source, rel, "_classic_beta_", "1", lab_capture.kind_of(LOG_NAME))
    outcome = lab_capture.process(
        item, identity if identity is not None else _owner(), None, OtherPlayers(), None
    )
    return list(outcome.problems), outcome.result.data


# ─── owner-side: the folded hunt ─────────────────────────────────────────────


@pytest.mark.parametrize("sep", [*SEPARATORS, RUN], ids=[*IDS, "run-of-all"])
def test_constructed_owner_character_name_with_a_new_separator_refuses(sep: str) -> None:
    text = "Orla" + sep + "vin waves"
    result = _owner().scrub(_saved(text))
    assert f"{FOLDED} x1" in result.problems, result.problems
    assert result.data == _saved(text)  # detect only: nothing rewritten


@pytest.mark.parametrize("sep", [*SEPARATORS, RUN], ids=[*IDS, "run-of-all"])
def test_constructed_owner_realm_with_a_new_separator_refuses(sep: str) -> None:
    text = "Kes" + sep + "trel" + sep + "Hollow"
    result = _owner().scrub(_saved(text))
    assert f"{FOLDED} x1" in result.problems, result.problems
    assert result.data == _saved(text)


def test_constructed_owner_control_new_separators_beside_names_pass_untouched() -> None:
    """Positive control: the plain names are still rewritten, the separators
    around them and between unrelated words are left byte for byte, and
    nothing refuses."""
    text = "Orlavin" + RUN + " of Kestrel Hollow" + RUN + ". Orla" + RUN + " bows; the vin` waves"
    result = _owner().scrub(_saved(text))
    assert not result.problems, result.problems
    expected = text.replace("Orlavin", "Labcharb").replace("Kestrel Hollow", "Labrealma Partb")
    assert result.data == _saved(expected)


# ─── other players: --pseudonymise-other-players ─────────────────────────────


@pytest.mark.parametrize("sep", [*SEPARATORS, RUN], ids=[*IDS, "run-of-all"])
def test_constructed_other_player_name_with_a_new_separator_refuses(
    sep: str, tmp_path: Path
) -> None:
    text = "Zor" + sep + "vinth waves."
    problems, data = _process(_one_player(text), tmp_path)
    assert problems == [f"{OTHER_NAME} x1"]
    assert b'nil,"' + text.encode() + b'"\n' in data  # detect only: the text is unchanged


def test_constructed_other_player_control_new_separators_beside_names_pass(
    tmp_path: Path,
) -> None:
    text = "Zor" + RUN + " bows. The vinth" + RUN + " waves."
    problems, data = _process(_one_player(text), tmp_path)
    assert problems == []
    assert b'nil,"' + text.encode() + b'"\n' in data
    assert b"Zorvinth" not in data  # the unit field is still rewritten


# ─── loose second names (`<First>-<Second>` folders) ─────────────────────────


@pytest.mark.parametrize("sep", [*SEPARATORS, RUN], ids=[*IDS, "run-of-all"])
def test_constructed_loose_second_name_with_a_new_separator_refuses(sep: str) -> None:
    identity = Identity(characters=["Kael"], realms=["QuelThalas"], loose=["QuelThalas"])
    text = ('"Quel' + sep + 'Thalas"').encode()
    result = identity.scrub(text)
    assert any(p.startswith(LOOSE) for p in result.problems), result.problems
    assert result.data == text


@pytest.mark.parametrize("sep", [*SEPARATORS, RUN], ids=[*IDS, "run-of-all"])
def test_constructed_short_loose_second_name_with_a_new_separator_refuses(sep: str) -> None:
    """A short loose name in a pure-ASCII file (backtick) is seen only by the byte-level check."""
    identity = Identity(characters=["Moon"], realms=["Qorv"], loose=["Qorv"])
    text = ('"Qo' + sep + 'rv"').encode()
    result = identity.scrub(text)
    assert any(p.startswith(LOOSE) for p in result.problems), result.problems
    assert result.data == text


def test_constructed_loose_second_name_control_is_still_rewritten() -> None:
    identity = Identity(characters=["Kael"], realms=["QuelThalas"], loose=["QuelThalas"])
    text = '"QuelThalas" "Quel' + RUN + ' bows" "Qo`rvings"'
    result = identity.scrub(text.encode())
    assert not result.problems, result.problems
    assert result.data == text.replace("QuelThalas", "Labrealma").encode()


# ─── security review of #85: ASCII separators in a short loose name ──────────


@pytest.mark.parametrize(
    "sep",
    [b"_", b"\n", b"\r", b"\x0b", b"\x0c"],
    ids=["underscore", "lf", "cr", "vt", "ff"],
)
def test_constructed_short_loose_second_name_with_an_ascii_separator_refuses(sep: bytes) -> None:
    """A pure-ASCII file: only the byte-level loose check sees a short name."""
    identity = Identity(characters=["Moon"], realms=["Qorv"], loose=["Qorv"])
    text = b'"Qo' + sep + b'rv"'
    result = identity.scrub(text)
    assert any(p.startswith(LOOSE) for p in result.problems), result.problems
    assert result.data == text  # detect only


def test_constructed_short_loose_ascii_separator_control_passes() -> None:
    identity = Identity(characters=["Moon"], realms=["Qorv"], loose=["Qorv"])
    text = b'"Qo_bows" "rv\nx" "Qo\x0b\x0cx"'
    result = identity.scrub(text)
    assert not result.problems, result.problems
    assert result.data == text


# ─── security review of #85: U+30FC next to a short name ─────────────────────

PROLONGED = chr(0x30FC)


@pytest.mark.parametrize(
    "text",
    [PROLONGED + "ash " + chr(0xE9), "ash" + PROLONGED + " waves", "The " + PROLONGED + "ASH"],
    ids=["before", "after", "before-upper-case"],
)
def test_constructed_prolonged_mark_beside_a_short_owner_name_refuses(text: str) -> None:
    """The exact spelling `Ash` is rewritten wherever it occurs; a case variant
    is a whole word only, which U+30FC used to hide."""
    result = _owner().scrub(_saved(text))
    assert f"{FOLDED} x1" in result.problems, result.problems
    assert result.data == _saved(text)


def test_constructed_prolonged_mark_beside_a_longer_word_passes() -> None:
    """Control: `ash` inside a longer word is still no hit, with U+30FC beside it."""
    text = PROLONGED + "ashen " + chr(0xE9) + " lash" + PROLONGED
    result = _owner().scrub(_saved(text))
    assert not [p for p in result.problems if p.startswith(FOLDED)], result.problems
    assert result.data == _saved(text)


@pytest.mark.parametrize(
    "text",
    [PROLONGED + "Zor waves.", "Zor" + PROLONGED + " waves."],
    ids=["before", "after"],
)
def test_constructed_prolonged_mark_beside_a_short_other_player_name_refuses(
    text: str, tmp_path: Path
) -> None:
    problems, data = _process(_one_player(text, "Zor-KestrelHollow-"), tmp_path)
    assert problems == [f"{OTHER_NAME} x1"]
    assert b'nil,"' + text.encode() + b'"\n' in data


def test_constructed_prolonged_mark_beside_a_longer_word_other_player_control(
    tmp_path: Path,
) -> None:
    text = PROLONGED + "Zorro waves" + PROLONGED + " at Azor" + PROLONGED + "."
    problems, data = _process(_one_player(text, "Zor-KestrelHollow-"), tmp_path)
    assert problems == []
    assert b'nil,"' + text.encode() + b'"\n' in data


# ─── performance: long separator runs ────────────────────────────────────────


def test_constructed_long_separator_runs_scan_in_linear_time(tmp_path: Path) -> None:
    """2000 log lines, each with runs of 200 separators after the first letters
    of every hunted name (about 5.6 MB). Measured about 2 s, as on the code
    before this change; the time grows linearly with the run length. The
    budget is CPU time (M11-19, see `cpu_clock`). On the owner's M1: idle,
    1.8 s CPU and wall clock; under 7 spinning processes, 2.5 s CPU against
    3.2 to 3.9 s wall clock."""
    identity = Identity(
        characters=["Orlavin", "Ash"],
        realms=["Kestrel Hollow", "QuelThalas"],
        loose=["QuelThalas"],
        guids=[OWN],
    )
    run = "".join(SEPARATORS) * 40
    body = "Zor" + run + "x Orla" + run + "x Quel" + run + "x Q" + run + "x" + run
    log = _one_player("hello") + b"".join(_emote(body) for _ in range(2000))
    clock, name = cpu_clock()
    started = clock()
    problems, _data = _process(log, tmp_path, identity)
    elapsed = clock() - started
    assert problems == [], problems
    assert elapsed < 10, f"{elapsed:.1f} {name} s for {len(log)} bytes"
