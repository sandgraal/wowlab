"""`scripts/lab_capture.py`: the detection gaps left by the final security review (M10-02).

Every input here is constructed: hostile or boundary shapes written for the
test, with invented identity strings. No test needs or touches a real
install (ADR-0012).

Amended 2026-10-17 (M13-49, ADR-0019 amendment): the own-realm partner checks
(R1, R2, R3 and the joint, window and line-break cases of the later rounds)
were retired with the rule they enforced, and their tests with them.
"""

from __future__ import annotations

import pytest
from test_lab_capture import MAIN, REALM, cpu_clock, lab_capture

Identity = lab_capture.Identity

MACRO = "whisper/invite/target macro line or @Name"


def _labels(lines: tuple[str, ...]) -> set[str]:
    return {line.rsplit(" x", 1)[0] for line in lines}


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
    """Finding 1: reading a realm's line must not scan back to byte 0 (LF-only or CR-only files).
    The budget is CPU time (M11-19, see `cpu_clock`)."""
    line = b"Area52" + newline
    data = line * (400_000 // len(line))
    clock, name = cpu_clock()
    started = clock()
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(data)
    elapsed = clock() - started
    assert not result.problems and not result.notes
    # measured 0.6 s wall clock; was 13.6 s
    assert elapsed < 5, f"{elapsed:.1f} {name} s for {len(data)} bytes"


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


def test_constructed_character_help_names_both_shapes(capsys: pytest.CaptureFixture[str]) -> None:
    """Code review 6."""
    with pytest.raises(SystemExit):
        lab_capture.parse_args(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "REALM/NAME, or a <First>-<Second> folder name" in help_text


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
