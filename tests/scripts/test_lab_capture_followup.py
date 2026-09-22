"""`scripts/lab_capture.py`: the detection gaps left by the final security review (M10-02).

Every input here is constructed: hostile or boundary shapes written for the
test, with invented identity strings. No test needs or touches a real
install (ADR-0012).
"""

from __future__ import annotations

import pytest
from test_lab_capture import MAIN, REALM, lab_capture

Identity = lab_capture.Identity

FOREIGN = "someone else's name on an own realm"
TOLERATED = "own realm next to a faction, region or 'Default' word"
UNEXPLAINED = "own realm near an unexplained capitalised word"
MACRO = "whisper/invite/target macro line or @Name"


def _labels(lines: tuple[str, ...]) -> set[str]:
    return {line.rsplit(" x", 1)[0] for line in lines}


# ─── R1. the look-back walks past every vocabulary word ─────────────────────


@pytest.mark.parametrize(
    "text",
    [
        '"Jaina-US-Area52"',
        '"Jaina-Default-Area52"',
        '"Jaina.Default.Area 52"',
        '"Area52-US-Jaina"',
        '"Area 52 - US - Jaina"',
        '"Jaina - Horde - US - Area 52"',  # two vocabulary words deep
        '"Jaina  - Horde - Area 52"',  # a double space does not cut the chain
        '"Jaina - Horde  - Area 52"',
        '"Jaina -Area52" "Jaina- Area52"',  # blanks on one side of the joint only
    ],
)
def test_constructed_name_beyond_a_region_or_default_word_refuses(text: str) -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert any(p.startswith(FOREIGN) for p in result.problems), result.problems


@pytest.mark.parametrize(
    "text",
    [
        '"Horde - Area 52 - US"',
        '"Horde  - Area 52"',
        '"US-Area52"',
        f'"Default.Area 52.{MAIN}"',
        f'"{MAIN}-US-Area52" "Area52-US-{MAIN}"',
        f'"{MAIN} - Horde - Area 52"',
    ],
)
def test_constructed_vocabulary_chains_ending_on_nobody_or_the_owner_still_pass(text: str) -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert not result.problems, result.problems
    assert _labels(result.notes) == {TOLERATED}


# ─── R2. separators outside the joint set, and digits in a name ──────────────


def test_constructed_digit_ends_no_word_a_name_with_a_digit_refuses() -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(b'"Jaina9-Area52" "Area52-J4ina"')
    assert any(p.startswith(FOREIGN) and " x2 " in p for p in result.problems), result.problems


def test_constructed_a_plain_number_next_to_an_own_realm_is_nobody() -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(b'"Area52-2" "2 - Area 52 - 3"')
    assert not result.problems and not result.notes
    # ...but a name beyond the number is still a name.
    beyond = Identity(characters=[MAIN], realms=[REALM]).scrub(b'"Jaina-2-Area52"')
    assert any(p.startswith(FOREIGN) for p in beyond.problems)


@pytest.mark.parametrize(
    "text",
    [
        '"Jaina Area52"',
        '"Jaina\tArea52"',
        '"Jaina,Area 52"',
        '"Jaina~Area52"',
        '"Jaina of Area52"',
        '"Jaina @ Area52"',
        '"Jaina--Area52"',
        '["Area 52"] = "Jaina",',  # the same line, however far
        '"Zoë Area52"',  # a non-ASCII initial may be a capital
    ],
)
def test_constructed_own_realm_near_an_unexplained_capitalised_word_is_noted(text: str) -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert not result.problems, result.problems
    notes = [n for n in result.notes if n.startswith(UNEXPLAINED)]
    assert notes and " x1 " in notes[0], result.notes
    assert "Jaina" not in " ".join(result.notes) and "Zo" not in " ".join(result.notes)


@pytest.mark.parametrize(
    "text",
    [
        f'["Area 52"] = {{\n["{MAIN}"] = 1,\n',  # the name is on another line
        f'"Horde - Area 52" "US-Area52" "Default.Area 52.{MAIN}"',
        '"Area 52" = true, SET x "Area52"',  # format keywords are vocabulary
        '"Area 52 jaina" "lowercase area52 words"',  # not capitalised
        'Player-9999-00000001,"Thrallmar-Area 52"',  # the pseudonym GUID's own word
    ],
)
def test_constructed_own_realm_beside_accounted_for_words_is_not_noted(text: str) -> None:
    identity = Identity(characters=[MAIN], realms=[REALM], guids=[b"Player-1234-0ABCDEF0"])
    result = identity.scrub(text.encode())
    assert not result.problems, result.problems
    assert not [n for n in result.notes if n.startswith(UNEXPLAINED)], result.notes


def test_constructed_unexplained_word_note_is_counted_per_pseudonym_and_read_once_per_line() -> (
    None
):
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(
        b'["Area52"] = "Jaina", ["Area 52"] = 2,\n["Area52"] = 3,\n["Area 52"] = "Sylvanas",\n'
    )
    assert not result.problems
    notes = [n for n in result.notes if n.startswith(UNEXPLAINED)]
    assert notes == [f"{UNEXPLAINED} x3 (first at byte 2, line 1)"], result.notes


def test_constructed_one_line_of_many_own_realms_stays_fast() -> None:
    """A serialized blob puts thousands of realm keys on one line: the line is read once."""
    import time

    identity = Identity(characters=[MAIN], realms=[REALM])
    data = b'blob = "' + b'["Area52"]=1,' * 20_000 + b'"\n'
    started = time.perf_counter()
    result = identity.scrub(data)
    elapsed = time.perf_counter() - started
    assert not result.problems and not [n for n in result.notes if n.startswith(UNEXPLAINED)]
    assert elapsed < 5, f"{elapsed:.1f} s"


# ─── R4. more social slash commands ──────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        b"/pr Privatefriend\n",
        b"/cw Privatefriend\n",
        b"/who Privatefriend\n",
        b"/targetexact Privatefriend\n",
        b'/run SendChatMessage("hi", "WHISPER", nil, "Privatefriend")\n',
        b'  /script local n = "Privatefriend"; SendChatMessage ("hi", "WHISPER", nil, n)\n',
    ],
)
def test_constructed_more_social_macro_lines_are_noted(line: bytes) -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(b"#showtooltip\n" + line)
    assert not result.problems
    notes = [n for n in result.notes if n.startswith(MACRO)]
    assert notes == [f"{MACRO} x1 (first at byte 13, line 2)"], result.notes
    assert "Privatefriend" not in " ".join(result.notes)


def test_constructed_unit_tokens_and_lookalike_commands_still_produce_no_note() -> None:
    clean = (
        b"/cast [@player][@mouseover,help][target=raid12pet][@arena3target][@cursor] Heal\n"
        b"/run print(GetTime())\n/script SetCVar('x', 1)\n"
        b"/targetenemy\n/whoami\n/prayer\n/cwhat\n"
    )
    assert not Identity(characters=[MAIN], realms=[REALM]).scrub(clean).notes
