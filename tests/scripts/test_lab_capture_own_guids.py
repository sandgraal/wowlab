"""`scripts/lab_capture.py`: own GUIDs in every file, and the chat, bare-GUID
and HonorBound gaps left after M16-126 (M16-128).

On 2026-11-12 an own character GUID without its `Player-` prefix reached a
pushed branch. The capture tool now:

- finds each of the owner's own GUIDs in every captured file as exact text,
  with or without `Player-`, in any case, and rewrites it to the pseudonym;
- refuses a second `Name:` on the line of the owner's own speaker link;
- takes a channel link's `[label]` whole and its closers in either case;
- refuses, in `character-list-order.txt`, any `<n>-<hex{6,}>` token left
  unmapped, whatever its boundaries;
- matches HonorBound lines as `^\\s*#HB<n>\\s` in any case after a BOM, accepts
  `[ \\t;,]` before `h` and spaces around `=`, and refuses an HonorBound line
  whose `h` was not replaced.

CONSTRUCTED INPUT (L8: hostile-input and boundary tests). Every byte string
and tree here is synthetic, built inline or in `tmp_path`. The GUIDs, names,
realms, hashes and messages are invented. No test needs or touches a real
install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import ACCOUNT, OWN_GUID, build_install, capture, lab_capture, outputs

Identity = lab_capture.Identity

GUID = b"Player-4321-00C0FFEE"  # invented
BODY = b"4321-00C0FFEE"
PSEUDO = b"Player-9999-00000001"
PSEUDO_BODY = b"9999-00000001"


def _own_guid_problems(problems: tuple[str, ...]) -> list[str]:
    return [p for p in problems if p.startswith(lab_capture.OWN_GUID_LABEL)]


# ─── (1) own GUIDs in every file ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b'x = "' + BODY + b'"\n', b'x = "' + PSEUDO_BODY + b'"\n'),
        (b'x = "4321-00c0ffee"\n', b'x = "' + PSEUDO_BODY + b'"\n'),
        (b'x = "player-4321-00c0ffee"\n', b'x = "' + PSEUDO + b'"\n'),
        (b'x = "PLAYER-4321-00C0FFEE"\n', b'x = "' + PSEUDO + b'"\n'),
        (b"guid=4321-00C0FFEE;\n", b"guid=" + PSEUDO_BODY + b";\n"),
        # Glued on both sides: exact text, whatever stands around it.
        (b"ab4321-00C0FFEEcd\n", b"ab" + PSEUDO_BODY + b"cd\n"),
        (b"x_Player-4321-00C0FFEE_y\n", b"x_" + PSEUDO + b"_y\n"),
        (b"1 4321-00C0FFEE 0\r\n", b"1 " + PSEUDO_BODY + b" 0\r\n"),
    ],
    ids=[
        "constructed-bare",
        "constructed-bare-lower-case",
        "constructed-prefixed-lower-case",
        "constructed-prefixed-upper-case",
        "constructed-bare-in-a-field",
        "constructed-bare-glued",
        "constructed-prefixed-glued",
        "constructed-bare-in-a-list-line",
    ],
)
def test_constructed_own_guid_rewritten_in_any_file(data: bytes, expected: bytes) -> None:
    result = Identity(guids=[GUID]).scrub(data)
    assert result.data == expected
    assert not _own_guid_problems(result.problems)
    assert BODY.lower() not in result.data.lower()


def test_constructed_every_own_guid_found() -> None:
    other = b"Player-77-0BADF00D"
    identity = Identity(guids=[GUID, other])
    result = identity.scrub(b"a 77-0badf00d b 4321-00C0FFEE c Player-77-0BADF00D\n")
    assert b"0BADF00D" not in result.data.upper()
    assert b"00C0FFEE" not in result.data.upper()
    assert result.data.count(b"9999-") == 3


def test_constructed_an_own_guid_left_in_the_output_refuses() -> None:
    """The survivor check behind the rewrite. Boundary case: an own GUID whose
    pseudonym is its own text gets no edit (an edit must change bytes), so it
    is still in the output, and the check refuses it, by count and offset only."""
    self_mapped = b"Player-9999-00000002"  # sorts second, so it maps to itself
    identity = Identity(guids=[b"Player-1-0000000A", self_mapped])
    assert identity.guids[self_mapped.upper()] == self_mapped
    result = identity.scrub(b'x = "9999-00000002"\n')
    (problem,) = _own_guid_problems(result.problems)
    assert problem.startswith(f"{lab_capture.OWN_GUID_LABEL} x1 (first at byte 5")
    assert "00000002" not in problem


def test_constructed_a_lookalike_guid_is_not_rewritten() -> None:
    result = Identity(guids=[GUID]).scrub(b'x = "4321-00C0FFED"\n')
    assert result.data == b'x = "4321-00C0FFED"\n'
    assert not result.edits


def test_constructed_a_run_rewrites_a_bare_own_guid_in_saved_variables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    bare = OWN_GUID.removeprefix("Player-")
    saved = root / "_retail_" / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    (saved / "Ledger.lua").write_bytes(
        f'\nLedgerDB = {{\n\t["me"] = "{bare.lower()}",\n}}\n'.encode()
    )
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--sv", "Ledger.lua") == 0
    written = outputs(out)
    (dest,) = [p for p in written if p.endswith("/SavedVariables/Ledger.lua")]
    assert bare.lower().encode() not in written[dest].lower()
    assert b'"9999-' in written[dest]
    printed = capsys.readouterr()
    assert bare.lower() not in (printed.out + printed.err).lower()


# ─── (2) a second speaker after the owner's own link ─────────────────────────

GUILD = b"|Hchannel:GUILD|h[Guild]|h "
OWN_LINK = b"|Hplayer:Thrallmar-Area52:3:GUILD|h[Thrallmar]|h"


def _chat(data: bytes) -> list[str]:
    identity = Identity(own_characters=[("Thrallmar", "Area 52")])
    return [
        p
        for p in identity.scrub(b'x = "' + data + b'"\n').problems
        if p.startswith(lab_capture.OTHER_CHAT_LABEL)
    ]


@pytest.mark.parametrize(
    "line",
    [
        GUILD + OWN_LINK + b" Quillmoss: selling flasks",
        GUILD + OWN_LINK + b": hi Quillmoss: bring the flask",
        GUILD + OWN_LINK + b"|r [Quillmoss]: hi",
        GUILD + OWN_LINK + b" |cff40ff40Quillmoss|r : hi",
        GUILD + OWN_LINK + b" Quillmoss\xef\xbc\x9a hi",  # U+FF1A, full-width colon
        OWN_LINK + b": ok Quillmoss: secret",
        GUILD + OWN_LINK + b": ok\\nQuillmoss: yo",
    ],
    ids=[
        "constructed-plain-speaker-after-own-link",
        "constructed-second-speaker-in-message",
        "constructed-bracketed-second-speaker",
        "constructed-coloured-second-speaker",
        "constructed-fullwidth-colon",
        "constructed-own-speaker-without-channel",
        "constructed-escaped-newline-then-speaker",
    ],
)
def test_constructed_a_second_speaker_after_the_own_link_refuses(line: bytes) -> None:
    assert _chat(line), "a second speaker after the owner's link was not refused"


@pytest.mark.parametrize(
    "line",
    [
        GUILD + OWN_LINK + b": ok",
        GUILD + OWN_LINK + b": back at 12:30",
        GUILD + b"|cff40ff40" + OWN_LINK + b"|r: ok 12:30 :)",
        OWN_LINK + b" has come online.",
        b'x = "' + OWN_LINK + b' joined" -- a: b',  # not a speaker: no colon after it
    ],
    ids=[
        "constructed-own-line",
        "constructed-digit-colon",
        "constructed-coloured-own-link-smiley",
        "constructed-own-link-not-speaker",
        "constructed-own-link-mid-text",
    ],
)
def test_constructed_the_owners_own_line_still_passes(line: bytes) -> None:
    assert not _chat(line)


# ─── (3) channel label and closer ────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        b"|Hchannel:CHANNEL:2|h[2. Tr|hade]|h Quillmoss: hi",
        b"|Hchannel:GUILD|H[Guild]|H Quillmoss: hi",
        b"|Hchannel:GUILD|h[Guild]|H Quillmoss: hi",
        b"|Hchannel:GUILD|h|cff40ff40[Guild]|r|H Quillmoss: hi",
        b"|Hchannel:GUILD|h[Guild]|h |CFF40FF40Quillmoss|r: hi",
    ],
    ids=[
        "constructed-pipe-h-inside-label",
        "constructed-upper-case-closers",
        "constructed-upper-case-last-closer",
        "constructed-coloured-label-upper-closer",
        "constructed-upper-case-colour-code",
    ],
)
def test_constructed_channel_link_forms_still_find_the_speaker(line: bytes) -> None:
    assert _chat(line), "the plain-text speaker after the channel link was not refused"


def test_constructed_own_link_after_a_label_with_pipe_h_passes() -> None:
    assert not _chat(b"|Hchannel:CHANNEL:2|h[2. Tr|hade]|H " + OWN_LINK + b": ok")


# ─── (4) character-list-order.txt: any unmapped bare token ───────────────────


@pytest.mark.parametrize(
    "listing",
    [
        b"1 4321-00C0FFEE 0\n2 77-0BADF0 1\n",  # six hex digits
        b"1 4321-00C0FFEE 0\n2 x77-0BADF00Dy 1\n",  # glued
        b"1 4321-00C0FFEE 0\n2 77-0BADF00D-1 1\n",  # a dash after it
        b"1 4321-00C0FFEE 0,77-0BADF00D\n",  # comma-joined
    ],
    ids=[
        "constructed-six-hex",
        "constructed-glued-letters",
        "constructed-trailing-dash",
        "constructed-comma-joined",
    ],
)
def test_constructed_an_unmapped_bare_token_refuses_whatever_its_boundaries(
    listing: bytes,
) -> None:
    result = Identity(guids=[GUID]).scrub(listing, bare_guids=True)
    problems = [p for p in result.problems if p.startswith(lab_capture.BARE_GUID_LABEL)]
    assert problems
    assert b"0BADF0" not in "".join(problems).encode()


def test_constructed_a_mapped_listing_still_passes() -> None:
    result = Identity(guids=[GUID]).scrub(b"1 4321-00C0FFEE 0\r\n", bare_guids=True)
    assert result.data == b"1 " + PSEUDO_BODY + b" 0\r\n"
    assert not result.problems


# ─── (5) HonorBound ──────────────────────────────────────────────────────────

HASH = b"a1b2c3d4e5f60718"
ZEROS = b"0" * len(HASH)


def _macros(hb_line: bytes, *, bom: bool = False) -> bytes:
    lines = [
        b'VER 3 0000000000000001 "pvp" "134400"',
        b"#showtooltip",
        hb_line,
        b"/cast Frost Nova",
        b"END",
    ]
    body = b"\r\n".join(lines) + b"\r\n"
    return b"\xef\xbb\xbf" + body if bom else body


def _process(data: bytes, tmp_path: Path, name: str = "macros-cache.txt") -> lab_capture.Outcome:
    folder = tmp_path / "in"
    folder.mkdir(exist_ok=True)
    source = folder / name
    source.write_bytes(data)
    item = lab_capture.Item(source, PurePosixPath(name), "", "", lab_capture.kind_of(name))
    return lab_capture.process(item, Identity(characters=["Orlavin"], realms=["Kestrel"]))


def _unreplaced(outcome: lab_capture.Outcome) -> list[str]:
    return [p for p in outcome.problems if p.startswith(lab_capture.HONORBOUND_UNREPLACED_LABEL)]


@pytest.mark.parametrize(
    "line",
    [
        b"#HB3 l=1,\th=" + HASH,
        b"#HB3 l=1,h=" + HASH,
        b"#HB3 l=1;h = " + HASH,
        b"#HB3 l=1;h\t=\t" + HASH,
        b"#HB3 l=1;H=" + HASH,
        b"  #HB3 l=1;h=" + HASH,
        b"\t#HB3 l=1;h=" + HASH,
        b"#HB3\tl=1;h=" + HASH,
        b"#hb3 l=1;h=" + HASH,
        b"#HB4 l=1;h=" + HASH,
        b"#HB2 l=1;h=" + HASH,
        b"#HB3 h=" + HASH,
    ],
    ids=[
        "constructed-comma-tab-before-key",
        "constructed-comma-before-key",
        "constructed-spaces-around-equals",
        "constructed-tabs-around-equals",
        "constructed-upper-case-key",
        "constructed-leading-spaces",
        "constructed-leading-tab",
        "constructed-tab-after-tag",
        "constructed-lower-case-tag",
        "constructed-hb4",
        "constructed-hb2",
        "constructed-first-field",
    ],
)
def test_constructed_honorbound_hash_replaced_in_every_spelling(
    tmp_path: Path, line: bytes
) -> None:
    original = _macros(line)
    outcome = _process(original, tmp_path)
    assert outcome.result.data == original.replace(HASH, ZEROS)
    assert not _unreplaced(outcome)
    assert outcome.result.count(lab_capture.HONORBOUND_REASON) == 1


def test_constructed_honorbound_line_after_a_bom(tmp_path: Path) -> None:
    lines = b"#HB3 l=1;h=" + HASH + b"\r\n/cast Frost Nova\r\n"
    original = b"\xef\xbb\xbf" + lines
    outcome = _process(original, tmp_path)
    assert outcome.result.data == original.replace(HASH, ZEROS)
    assert not _unreplaced(outcome)


@pytest.mark.parametrize(
    "line",
    [
        b"#HB3 l=1;xh=" + HASH,  # no `h` field at all
        b"#HB3 l=1;hash=" + HASH,
        b"#HB3 l=1|h=" + HASH,  # a separator the rule does not know
        b"#HB3 l=1;h:" + HASH,
    ],
    ids=[
        "constructed-xh",
        "constructed-hash-key",
        "constructed-pipe-separator",
        "constructed-colon-not-equals",
    ],
)
def test_constructed_honorbound_line_without_a_replaced_h_refuses(
    tmp_path: Path, line: bytes
) -> None:
    outcome = _process(_macros(line), tmp_path)
    (problem,) = _unreplaced(outcome)
    assert problem == f"{lab_capture.HONORBOUND_UNREPLACED_LABEL} x1"  # count only


def test_constructed_honorbound_zero_hash_passes(tmp_path: Path) -> None:
    outcome = _process(_macros(b"#HB3 l=1;h=" + ZEROS), tmp_path)
    assert not _unreplaced(outcome)
    assert not outcome.result.edits


def test_constructed_honorbound_refusal_counts_lines(tmp_path: Path) -> None:
    data = _macros(b"#HB3 l=1;xh=" + HASH) + _macros(b"#hb9 q=" + HASH)
    outcome = _process(data, tmp_path)
    assert _unreplaced(outcome) == [f"{lab_capture.HONORBOUND_UNREPLACED_LABEL} x2"]


def test_constructed_honorbound_lines_outside_macros_are_not_checked(tmp_path: Path) -> None:
    outcome = _process(b"#HB3 l=1;xh=" + HASH + b"\n", tmp_path, name="bindings-cache.wtf")
    assert not _unreplaced(outcome)
