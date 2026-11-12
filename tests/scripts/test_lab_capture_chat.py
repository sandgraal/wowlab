"""`scripts/lab_capture.py`: other players' chat, and the packed-bitfield email (M16-126).

Two findings from a capture of the launch client:

- A UI addon's SavedVariables held guild chat history (`|Hchannel:GUILD|h[Guild]|h
  |Hplayer:<Name-Realm>...|h[<Name>]|h: <message>`), and the automatic pick took
  it as "colour and link escapes". A file with a `|Hplayer:` link to anyone but
  the owner's characters, any `|HBNplayer:` link, or a chat-channel line with a
  plain-text speaker who is someone else, is now refused, and the automatic
  pick passes it over with that reason.
- The `closedInfoFrames` CVar is a packed bitfield (`@` plus six bits per byte)
  whose `x@y` matched the email pattern. Inside such a value in a config file,
  an `x@y` glued to the `@` runs is not an address; a real one still refuses.

CONSTRUCTED INPUT (L8, hostile-input and boundary cases). Every tree and byte
string here is synthetic, built in `tmp_path` or inline. The names, realms and
messages are invented; no real chat text is copied in. No test needs or
touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_lab_capture import ACCOUNT, MAIN, build_install, capture, lab_capture, outputs

OWN = "Thrallmar"  # MAIN in the synthetic install
STRANGER = "Quillmoss"
STRANGER_WORDS = "bring the purple flask to the bridge"


def _problems(data: bytes, *, own: tuple[str, ...] = (OWN,), config: bool = False) -> list[str]:
    identity = lab_capture.Identity(own_characters=own)
    return list(identity.scrub(data, blank_cvars=config).problems)


def _chat(problems: list[str]) -> list[str]:
    return [
        p
        for p in problems
        if p.startswith((lab_capture.OTHER_CHAT_LABEL, lab_capture.BNET_LINK_LABEL))
    ]


# ─── other players' chat: the detector ───────────────────────────────────────

GUILD = b"|Hchannel:GUILD|h[Guild]|h "


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # Another player's guild line: the speaker is a player link.
        (
            b'x = "' + GUILD + b"|Hplayer:Quillmoss-Silvermoon:12:GUILD|h[Quillmoss]|h: "
            b'bring the purple flask"\n',
            [f"{lab_capture.OTHER_CHAT_LABEL} x1"],
        ),
        # Two strangers, two lines.
        (
            b'x = { "|Hplayer:Quillmoss-Ash:1|h[Quillmoss]|h: hi", '
            b'"|Hplayer:Brindlefen|h[Brindlefen]|h: yo" }\n',
            [f"{lab_capture.OTHER_CHAT_LABEL} x2"],
        ),
        # The owner's own line: name before the realm, any casing.
        (b'x = "' + GUILD + b'|Hplayer:THRALLMAR-Area 52:3|h[Thrallmar]|h: ok"\n', []),
        (b'x = "|Hplayer:thrallmar|h[thrallmar]|h"\n', []),
        # A Battle.net friend link is never the character captured.
        (b'x = "|HBNplayer:|Kq12|k:5:9:BN_WHISPER|h[|Kq12|k]|h: hi"\n', None),
        # A plain-text speaker after a channel link.
        (
            b'x = "|Hchannel:CHANNEL:2|h[2. Trade]|h Quillmoss: selling flasks"\n',
            [f"{lab_capture.OTHER_CHAT_LABEL} x1"],
        ),
        (b'x = "|Hchannel:GUILD|h[Guild]|h [Quillmoss-Ash]: hi"\n', None),
        (b'x = "|Hchannel:GUILD|h[Guild]|h Thrallmar: back in five"\n', []),
        # A channel link with no speaker (a channel list), a format string, an empty name.
        (b'x = "|Hchannel:GUILD|h[Guild]|h"\n', []),
        (b'x = "|Hplayer:%s|h[%s]|h"\n', []),
        (b'x = "|Hplayer:|h[]|h"\n', []),
        # Escaped pipes, as some addons store them.
        (b'x = "||Hplayer:Quillmoss||h[Quillmoss]||h: hi"\n', None),
        # Colour codes alone are not chat.
        (b'x = "|cffff8000Purple flask|r"\n', []),
    ],
    ids=[
        "constructed-guild-line-other-speaker",
        "constructed-two-other-speakers",
        "constructed-own-line-with-realm",
        "constructed-own-link-lower-case",
        "constructed-bnet-link",
        "constructed-channel-plain-other-speaker",
        "constructed-channel-bracketed-other-speaker",
        "constructed-channel-plain-own-speaker",
        "constructed-channel-link-alone",
        "constructed-format-string",
        "constructed-empty-name",
        "constructed-doubled-pipes",
        "constructed-colour-code-only",
    ],
)
def test_constructed_other_players_chat_refuses(data: bytes, expected: list[str] | None) -> None:
    found = _chat(_problems(data))
    if expected is None:
        assert found, "another player's chat was not refused"
    else:
        assert found == expected


def test_constructed_without_known_characters_every_named_link_refuses() -> None:
    data = b'x = "|Hplayer:Thrallmar|h[Thrallmar]|h: hi"\n'
    assert _chat(_problems(data, own=()))


def test_constructed_a_pseudonymised_character_is_still_the_owner() -> None:
    """The engine's `characters` input (tests only) rewrites the name; the
    link then holds the pseudonym, and that is still the owner."""
    identity = lab_capture.Identity(characters=["Brindlefen"])
    result = identity.scrub(b'x = "|Hplayer:Brindlefen|h[Brindlefen]|h: hi"\n')
    assert not _chat(list(result.problems))


# ─── other players' chat: a whole run ────────────────────────────────────────

CHAT_SKIN = (
    "\nPaintBoxDB = {\n"
    '\t["history"] = {\n'
    f'\t\t"|Hchannel:GUILD|h[Guild]|h |Hplayer:{STRANGER}-Silvermoon:7:GUILD|h'
    f'|cff40c040[{STRANGER}]|r|h: {STRANGER_WORDS}",\n'
    "\t},\n"
    '\t["colour"] = "|cffff8000|cff00ff00|cff0000ff",\n'
    "}\n"
)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    saved = root / "_retail_" / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    (saved / "PaintBox.lua").write_bytes(CHAT_SKIN.encode())
    return root


def test_constructed_automatic_pick_passes_over_a_file_with_other_players_chat(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert OWN == MAIN
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_") == 0
    assert not [p for p in outputs(out) if p.endswith("/PaintBox.lua")]
    printed = capsys.readouterr().out
    passed_over = printed.split("candidates passed over by the automatic selection")[1]
    assert f"PaintBox.lua: {lab_capture.OTHER_CHAT_LABEL} x1" in passed_over
    # The reason never repeats the name or the words.
    assert STRANGER not in printed and STRANGER_WORDS not in printed


@pytest.mark.parametrize("flag", [(), ("--pseudonymise-other-players",)])
def test_constructed_a_requested_file_with_other_players_chat_is_refused(
    flag: tuple[str, ...], install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--sv` names it: still refused, with or without the combat-log-only
    pseudonymise flag, which does not apply to chat."""
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_", "--sv", "PaintBox.lua", *flag) == 1
    assert not [p for p in outputs(out) if p.endswith("/PaintBox.lua")]
    text = capsys.readouterr()
    refused = [line for line in text.out.splitlines() if line.startswith("REFUSED")]
    assert len(refused) == 1
    assert "PaintBox.lua" in refused[0] and lab_capture.OTHER_CHAT_LABEL in refused[0]
    assert STRANGER not in text.out + text.err and STRANGER_WORDS not in text.out + text.err


def test_constructed_the_owners_own_chat_is_captured(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = install / "_retail_" / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    own = CHAT_SKIN.replace(f"{STRANGER}-Silvermoon", f"{MAIN}-Area 52").replace(STRANGER, MAIN)
    (saved / "PaintBox.lua").write_bytes(own.encode())
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_", "--sv", "PaintBox.lua") == 0
    assert [p for p in outputs(out) if p.endswith("/PaintBox.lua")]


# ─── the packed bitfield and the email check ─────────────────────────────────

# The shape of a launch-client value that was refused: `@` runs around `x@y`.
PACKED = b'SET closedInfoFrames "@@pD@@@@D@A@@@@@@APH"\n'


@pytest.mark.parametrize(
    "line",
    [
        PACKED,
        b'SET closedInfoFrames "@@R@@@@@P@@B@@@@@@APH@@@@@B"\r\n',  # an earlier client's value
        b'SET closedInfoFramesAccountWide "@@B@C@@"\n',
        b'SET someBits "D@A@@"\n',  # at the start of the value
    ],
    ids=[
        "constructed-launch-shape",
        "constructed-older",
        "constructed-other-cvar",
        "constructed-at-start",
    ],
)
def test_constructed_a_packed_bitfield_is_not_an_email(line: bytes) -> None:
    problems = _problems(b'SET portal "US"\n' + line, config=True)
    assert not [p for p in problems if p.startswith("email address")]


@pytest.mark.parametrize(
    "line",
    [
        b'SET lastAddress "someone.real@example.com"\n',
        b'SET closedInfoFrames "@@someone@example.com@@"\n',  # a dot: not packed bytes
        b'SET closedInfoFrames "@@D@A@@ x@y.z"\n',  # a blank: not packed bytes
        b'SET contact "someone@example"\n',  # packed bytes, but no `@@`
        b'SET contact "@@ someone@example"\n',  # not packed: a blank
        b'SET contact "someone@example@@"\n'.replace(b"someone", b"some-one"),
    ],
    ids=[
        "constructed-plain",
        "constructed-dotted-in-at-runs",
        "constructed-blank-after-bits",
        "constructed-no-double-at",
        "constructed-blank",
        "constructed-hyphen",
    ],
)
def test_constructed_a_real_email_in_a_cvar_value_still_refuses(line: bytes) -> None:
    problems = _problems(b'SET portal "US"\n' + line, config=True)
    assert [p for p in problems if p.startswith("email address")]


def test_constructed_the_exemption_is_for_config_files_only() -> None:
    assert [p for p in _problems(PACKED) if p.startswith("email address")]


def test_constructed_a_packed_cvar_in_config_cache_is_captured(
    install: Path, tmp_path: Path
) -> None:
    character = install / "_retail_" / "WTF" / "Account" / ACCOUNT / "Area 52" / MAIN
    path = character / "config-cache.wtf"
    original = path.read_bytes() + PACKED
    path.write_bytes(original)
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_") == 0
    written = outputs(out)
    (dest,) = [p for p in written if p.endswith(f"/{MAIN}/config-cache.wtf")]
    assert written[dest] == original  # byte for byte: the value is not blanked
