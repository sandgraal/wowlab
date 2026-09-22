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
LOWERCASE = "own realm next to an unexplained lowercase word"
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
        '"Area 52" = true, "Area52" = nil',  # format keywords are vocabulary
        'Player-9999-00000001,"Thrallmar-Area 52",0x511',  # the GUID's own word; hex
        '["Area52"] = true,\n"Area52", -- [1]\n["Thrallmar - Area 52"] = {',  # Lua shapes
    ],
)
def test_constructed_own_realm_beside_accounted_for_words_is_not_noted(text: str) -> None:
    identity = Identity(characters=[MAIN], realms=[REALM], guids=[b"Player-1234-0ABCDEF0"])
    result = identity.scrub(text.encode())
    assert not result.problems, result.problems
    assert not [n for n in result.notes if n.startswith((UNEXPLAINED, LOWERCASE))], result.notes


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
        b"/cast [target = focus][@ player][target= raid3] Heal\n"
        b"/run print(GetTime())\n/script SetCVar('x', 1)\n"
        b"/targetenemy\n/whoami\n/prayer\n/cwhat\n/duels\n/kickstart\n/inspection\n"
        b'Macro = "#showtooltip\\n/cast [@mouseover] Heal\\n/kickoff"\n'
    )
    assert not Identity(characters=[MAIN], realms=[REALM]).scrub(clean).notes


# ─── second security review of 7f8c4ef ───────────────────────────────────────


@pytest.mark.parametrize("newline", [b"\n", b"\r", b"\r\n"])
def test_constructed_many_short_lines_scrub_in_linear_time(newline: bytes) -> None:
    """Finding 1: reading a realm's line must not scan back to byte 0 (LF-only or CR-only files)."""
    import time

    line = b"Area52" + newline
    data = line * (400_000 // len(line))
    started = time.perf_counter()
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(data)
    elapsed = time.perf_counter() - started
    assert not result.problems and not result.notes
    assert elapsed < 5, f"{elapsed:.1f} s for {len(data)} bytes"  # measured 0.6 s; was 13.6 s


@pytest.mark.parametrize(
    "text",
    ['"Jaina -\nArea52"', '"Jaina-\r\nArea52"', '"Jaina\r-Area52"', "Area52 -\r\nJaina"],
)
def test_constructed_name_joined_across_a_line_break_refuses(text: str) -> None:
    """Finding 2: the blank run around a joint character may hold line breaks."""
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert any(p.startswith(FOREIGN) for p in result.problems), result.problems


def test_constructed_capitalised_word_on_the_neighbouring_line_is_noted() -> None:
    """Finding 2: only a line break between them, so the neighbouring line is read."""
    identity = Identity(characters=[MAIN], realms=[REALM])
    for text in (b"Jaina\nArea52", b"Area52\r\nJaina", b"Jaina\n\t Area52 "):
        result = identity.scrub(text)
        assert [n for n in result.notes if n.startswith(UNEXPLAINED)], (text, result.notes)
    # Something other than blanks and joints between the realm and the line
    # break: the neighbouring line is not read.
    quiet = identity.scrub(b'Jaina\n["Area52"] = 1,\n')
    assert not [n for n in quiet.notes if n.startswith(UNEXPLAINED)], quiet.notes


def test_constructed_slash_command_after_an_own_pair_is_no_joint() -> None:
    """A macro line that starts with `/` is a command, not a joint to the line before."""
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(
        f"/tar {MAIN}-Area52\n/cast Heal\n".encode()
    )
    assert not result.problems, result.problems


@pytest.mark.parametrize(
    "text",
    [
        '"jaina area52"',
        '"jaina, area52"',
        '"jaina~area52"',
        '"jaina of area52"',
        '"area52 (jaina)"',
        '"jaina\'s area52"',
        '"jaina+area52"',
        "jaina=area52",
        '"e\u0301ric area52"',  # NFD
        f'"{MAIN.lower()}-area52 jaina"',  # the owner's own pair, then a stranger
        '"Area 52 jaina" "lowercase area52 words"',
    ],
)
def test_constructed_lowercase_word_next_to_an_own_realm_is_noted(text: str) -> None:
    """Finding 3: addons lowercase keys; the nearest word is read in any casing."""
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert not result.problems, result.problems
    assert [n for n in result.notes if n.startswith(LOWERCASE)], result.notes
    assert "jaina" not in " ".join(result.notes).lower()


@pytest.mark.parametrize("dash", ["\u2013", "\u2014"])
def test_constructed_en_and_em_dash_are_joints(dash: str) -> None:
    identity = Identity(characters=[MAIN], realms=[REALM])
    result = identity.scrub(f'"jaina{dash}area52" "Area52{dash}Jaina"'.encode())
    assert any(p.startswith(FOREIGN) and " x2 " in p for p in result.problems), result.problems
    own = identity.scrub(f'"{MAIN}{dash}Area52" "Horde {dash} Area 52"'.encode())
    assert not own.problems, own.problems


@pytest.mark.parametrize(
    "text",
    [
        '"jaina-' + "1" * 170 + '-area52"',  # a cut number run
        '"jaina' + " " * 170 + '-area52"',  # a cut blank run
        '"jaina-us' + " " * 170 + '-area52"',
        '"' + "j" * 200 + '-area52"',  # a cut word
    ],
)
def test_constructed_window_cut_is_read_as_a_person(text: str) -> None:
    """Finding 4: what the backward window cuts never ends the walk on "own"."""
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert any(p.startswith(FOREIGN) for p in result.problems), result.problems


def test_constructed_long_blank_run_without_a_joint_is_not_a_cut() -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(b"{" + b" " * 170 + b'"Area52"')
    assert not result.problems and not result.notes


@pytest.mark.parametrize(
    "line",
    [
        *(
            b"/%s Privatefriend\n" % command
            for command in (
                b"guildinvite",
                b"promote",
                b"gpromote",
                b"gdemote",
                b"gkick",
                b"guildremove",
                b"uninvite",
                b"kick",
                b"unignore",
                b"removefriend",
                b"inspect",
                b"duel",
                b"fol",
            )
        ),
        b'/run InviteUnit"Privatefriend"\n',
        b"/run TargetUnit 'Privatefriend'\n",
        b'/script GuildInvite("Privatefriend")\n',
        b'/run AddFriend("Privatefriend")\n',
        b'/script BNSendWhisper(1, "hi")\n',
        b"/cast [target = Privatefriend] Heal\n",
        b"/cast [@ Privatefriend] Heal\n",
    ],
)
def test_constructed_second_round_social_macro_lines_are_noted(line: bytes) -> None:
    """Finding 5."""
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(b"#showtooltip\n" + line)
    notes = [n for n in result.notes if n.startswith(MACRO)]
    assert len(notes) == 1 and " x1 " in notes[0] and notes[0].endswith(", line 2)"), notes


def test_constructed_macro_body_inside_a_lua_string_is_seen() -> None:
    """Finding 5: a literal `\\n` escape starts a macro line too."""
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(
        b'\tMacroText = "#showtooltip\\n/w Privatefriend hi\\n/run AddFriend(n)",\n'
    )
    notes = [n for n in result.notes if n.startswith(MACRO)]
    assert notes and " x2 " in notes[0], result.notes


# ─── round 2 of #33: code review and security re-review of ebba464 ───────────


def test_constructed_folder_only_second_part_is_replaced_in_its_spaced_spelling() -> None:
    """Code review 1: `StormRage` -> `Storm Rage`; `PvE2` -> both `Pv E 2` and `PvE 2`."""
    identity = Identity(characters=["Vex"], realms=["StormRage", "PvE2"])
    result = identity.scrub(b'"Vex - Storm Rage" "storm rage" "PvE 2" "Pv E 2" "Area 52"')
    assert b"Storm" not in result.data and b"storm" not in result.data
    assert b"PvE" not in result.data and b"Pv E" not in result.data
    assert b'"Area 52"' in result.data  # not a name here: untouched
    assert not result.problems, result.problems
    # The pseudonyms are the ones the folder spelling always had.
    assert identity.names == {"PvE2": "Labrealma", "StormRage": "Labrealmb", "Vex": "Labchara"}


@pytest.mark.parametrize(
    "text",
    [
        '"Jaina  (Area 52)"',
        '"Jaina\\t(Area52)"',
        '"Jaina ( Area52)"',
        '"jaina' + " " * 170 + '(area52)"',  # a blank run longer than the window
    ],
)
def test_constructed_blank_run_before_a_paren_refuses(text: str) -> None:
    """Code review 2."""
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert any(p.startswith(FOREIGN) for p in result.problems), result.problems


def test_constructed_own_name_in_parens_after_a_blank_run_still_passes() -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(f'"{MAIN} \t (Area 52)"'.encode())
    assert not result.problems, result.problems


def test_constructed_indented_slash_command_after_a_line_break_is_no_joint() -> None:
    """Code review 4."""
    identity = Identity(characters=[MAIN], realms=[REALM])
    for text in (f"/tar {MAIN}-Area52 \n /cast X\n", f"/tar {MAIN}-Area52\r\n\t/cast X\n"):
        result = identity.scrub(text.encode())
        assert not result.problems, (text, result.problems)
    # On the same line a slash is still a joint.
    assert any(p.startswith(FOREIGN) for p in identity.scrub(b'"Jaina/Area52"').problems)


def test_constructed_offsets_after_a_dash_are_offsets_of_the_original_bytes() -> None:
    """Code review 5: each dash is replaced by a copy of the same length."""
    identity = Identity(characters=[MAIN], realms=[REALM])
    prefix = "\u2014\u2013 \u2013\u2014\u2014 ".encode()
    result = identity.scrub(prefix + '"jaina\u2013Area52"'.encode())
    problem = next(p for p in result.problems if p.startswith(FOREIGN))
    assert f"first at byte {result.data.index(b'LabrealmaPartb')}, line 1)" in problem
    assert result.data.startswith(prefix)  # the dashes themselves are never rewritten


def test_constructed_character_help_names_both_shapes(capsys: pytest.CaptureFixture[str]) -> None:
    """Code review 6."""
    with pytest.raises(SystemExit):
        lab_capture.parse_args(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "REALM/NAME, or a <Name>-<Suffix> folder name" in help_text


@pytest.mark.parametrize(
    "text",
    [b'"Kael - Quel\'Thalas"', b'"quel thalas"', b'"Quel-Thalas"', b'"QUEL\\\'THALAS"'],
)
def test_constructed_other_spelling_of_a_folder_only_second_part_refuses(text: bytes) -> None:
    """Security S1: a space, apostrophe or hyphen between two letters; any casing."""
    identity = Identity(characters=["Kael"], realms=["QuelThalas"], loose=["QuelThalas"])
    result = identity.scrub(text)
    spelled = "surviving identity string (spaced or apostrophe spelling)"
    if text == b'"quel thalas"':
        # The CamelCase split form covers this spelling: replaced, not refused.
        assert result.data == b'"labrealma"' and not result.problems
    else:
        assert any(p.startswith(spelled) for p in result.problems), result.problems


def test_constructed_loose_spelling_of_a_short_part_needs_a_whole_word() -> None:
    identity = Identity(characters=["Moon"], realms=["Qorv"], loose=["Qorv"])
    assert not identity.scrub(b'"qorvings" "qo rvle" "Qorv"').problems
    assert identity.scrub(b'"qo\'rv"').problems


def test_constructed_character_list_refuses_a_character_with_no_folder() -> None:
    """Security S2."""
    identity = Identity(
        characters=["Alyra", "Moon"], realms=["Bloodfist", "Glade"], guids=[b"Player-1-0000ABCD"]
    )
    good = b"\xef\xbb\xbfAlyra-Bloodfist\r\n\r\nMoon-Glade\r\nPlayer-1-0000ABCD\r\n42\r\n"
    assert not identity.scrub(good, character_list=True).problems
    bad = identity.scrub(b"Alyra-Bloodfist\nZedalt-Faerlina\nzedalt\n", character_list=True)
    label = "character list names a character this install has no folder for"
    assert [p for p in bad.problems if p.startswith(f"{label} x2 (first at byte 19, line 2)")]
    assert "Zedalt" not in " ".join(bad.problems)
    # Any other file is not held to that rule.
    assert not identity.scrub(b"Zedalt-Faerlina\n").problems


def test_constructed_group_folders_get_a_path_only_numeric_pseudonym() -> None:
    """Security S3: `<flavor>/WTF/Account/<account>/<digits>` only; never a content token."""
    identity = Identity(characters=["Alyra"], realms=["Bloodfist"], groups=["70", "5"])
    assert identity.groups == {"5": "1", "70": "2"}
    path, problems = identity.scrub_path(
        lab_capture.PurePosixPath("_x_/wtf/account/A1/70/Alyra-Bloodfist/chat-cache.txt")
    )
    assert path.parts[4:6] == ("2", "Labchara-Labrealma") and not problems
    elsewhere, _ = identity.scrub_path(lab_capture.PurePosixPath("_x_/Interface/AddOns/70/a.toc"))
    assert elsewhere.parts[3] == "70"
    assert identity.scrub(b"70 170 -270.0 1703 5").data == b"70 170 -270.0 1703 5"
    _unmapped, problems = Identity().scrub_path(lab_capture.PurePosixPath("f/WTF/Account/A/9/x"))
    assert problems == ["unsafe path component after scrubbing"]


@pytest.mark.parametrize(
    "text",
    [
        '"jaina-horde area52"',
        '"jaina us area52"',
        '"jaina, 2 area52"',
        "jaina\narea52",
        "area52\njaina",
        '"jaina,     area52"',  # a separator of up to 8 bytes
        f'"jaina-{MAIN.lower()}-area52"',  # one segment past an own name
    ],
)
def test_constructed_lowercase_stranger_past_vocabulary_numbers_and_own_names_is_noted(
    text: str,
) -> None:
    """Security S4: still a note, never a refusal."""
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert not result.problems, result.problems
    assert [n for n in result.notes if n.startswith(LOWERCASE)], result.notes


def test_constructed_byte_order_mark_is_never_part_of_a_partner_word() -> None:
    """Found while writing the S2 test: a BOM is made of word bytes (\\x80-\\xff)."""
    identity = Identity(characters=[MAIN], realms=[REALM])
    for text in (f"\ufeff{MAIN}-Area52\n", "\ufeff Area52\n", "\ufeffArea52-US\n"):
        result = identity.scrub(text.encode())
        assert not result.problems, (text, result.problems)
        assert not [n for n in result.notes if n.startswith((UNEXPLAINED, LOWERCASE))], text
    jaina = identity.scrub("\ufeffJaina-Area52".encode())
    assert any(p.startswith(FOREIGN) for p in jaina.problems)
