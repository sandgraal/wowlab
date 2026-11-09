"""`scripts/lab_capture.py` and free text (M16-44, security:F8).

The scrub finds identifiers by pattern; a chat log, a mailbox or a note a
player typed about someone else holds none of those patterns and would pass.
So the automatic SavedVariables selection never picks a chat-log, mail or
notes addon's file: one is captured only when `--sv` names it and
`--confirm-free-text` names it again. And any SavedVariables string value of
`FREE_TEXT_MIN_WORDS` words or more is flagged "free text, review by hand"
in the run's output (a note; nothing is refused for it).

CONSTRUCTED INPUT. Every tree and every byte string here is synthetic, built
in `tmp_path` or inline, with invented names and invented sentences. No test
needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_lab_capture import build_install, capture, lab_capture, outputs, tree_state

ACCOUNT = "123456789#1"
CHAT_LOG = (
    '\nElephantDB = {\n\t["log"] = {\n'
    '\t\t"Sorry I cannot make the raid tonight, my mum is ill",\n'
    '\t\t"ok see you tomorrow then",\n\t},\n}\n'
)
# Small, clean and harmless: what the automatic selection would take first if
# the deny-list were not there (the smallest, and a single scalar).
CHAT_LOG_SMALL = '\nWIMDB = "x"\n'
MAIL = '\nPostalDB = { ["n"] = 1, }\n'
NOTES = '\nMyFriendNotesDB = { ["n"] = 2, }\n'
DENIED = ("Elephant.lua", "WIM.lua", "Postal.lua", "MyFriendNotes.lua", "Prat-3.0.lua.bak")


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    for flavor in ("_retail_", "_classic_beta_"):
        saved = root / flavor / "WTF" / "Account" / ACCOUNT / "SavedVariables"
        (saved / "Elephant.lua").write_bytes(CHAT_LOG.encode())
        (saved / "WIM.lua").write_bytes(CHAT_LOG_SMALL.encode())
        (saved / "Postal.lua").write_bytes(MAIL.encode())
        (saved / "MyFriendNotes.lua").write_bytes(NOTES.encode())
        (saved / "Prat-3.0.lua.bak").write_bytes(b"\nPrat3DB = 1\n")
    return root


# ─── the deny-list ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "Elephant.lua",
        "elephant.LUA",
        "WIM.lua",
        "Prat-3.0.lua",
        "Chattynator.lua",
        "Postal.lua",
        "DataStore_Mail.lua",
        "MRT.lua",
        "Prat-3.0.lua.bak",
        "MyGuildNotes.lua",  # by word: a note addon the list does not name
        "WhisperPop.lua",
        "Blizzard_ChatFrameLog.lua",
    ],
)
def test_constructed_chat_mail_and_note_addons_are_denied(name: str) -> None:
    assert lab_capture.is_free_text_sv(name)


@pytest.mark.parametrize("name", ["Details.lua", "Bartender4.lua", "Deep.lua", "WIMBLE.lua"])
def test_constructed_other_addons_are_not_denied(name: str) -> None:
    assert not lab_capture.is_free_text_sv(name)


def test_constructed_automatic_selection_never_picks_a_denied_file(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out) == 0
    written = outputs(out)
    for name in DENIED:
        assert not [p for p in written if p.endswith(f"/SavedVariables/{name}")], name
    # The automatic picks still run on everything else.
    assert [p for p in written if p.endswith("/SavedVariables/Tiny.lua")]
    printed = capsys.readouterr().out
    assert f"5 {lab_capture.FREE_TEXT_SV_LABEL} SavedVariables never picked" in printed
    # The note counts; it names no file.
    assert "Elephant" not in printed


def test_constructed_sv_alone_does_not_capture_a_denied_file(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    before = tree_state(install)
    assert capture(install, out, "--sv", "Elephant.lua") == 2
    assert not out.exists() or outputs(out) == {}
    err = capsys.readouterr().err
    assert "--confirm-free-text Elephant.lua" in err
    assert "Nothing is captured" in err
    assert tree_state(install) == before


def test_constructed_sv_with_the_confirmation_captures_it(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out, "--sv", "Elephant.lua", "--confirm-free-text", "elephant.lua") == 0
    written = outputs(out)
    captured = [p for p in written if p.endswith("/SavedVariables/Elephant.lua")]
    assert len(captured) == 2  # both flavors
    # Byte for byte: a free-text note never edits the file.
    assert written[captured[0]] == CHAT_LOG.encode()
    printed = capsys.readouterr().out
    assert "confirmed with --confirm-free-text" in printed  # the provenance row says so
    # The other denied files stay out.
    assert not [p for p in written if p.endswith("/SavedVariables/WIM.lua")]


def test_constructed_a_confirmation_without_its_sv_stops_the_run(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    code = capture(install, out, "--sv", "Deep.lua", "--confirm-free-text", "Elephant.lua")
    assert code == 2
    assert "no --sv names this file" in capsys.readouterr().err
    assert not out.exists() or outputs(out) == {}


def test_constructed_an_ordinary_sv_needs_no_confirmation(install: Path, tmp_path: Path) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out, "--sv", "Deep.lua") == 0


# ─── free text in string values ──────────────────────────────────────────────


def test_constructed_long_string_values_are_flagged_with_a_place_not_the_text(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = install / "_retail_" / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    (saved / "Tracker.lua").write_bytes(
        b'\nJournalDB = {\n\t["entry"] = "met a lovely priest called Bob in Goldshire",\n}\n'
    )
    out = tmp_path / "incoming"
    assert capture(install, out, "--sv", "Tracker.lua") == 0
    printed = capsys.readouterr().out
    assert f"look   {lab_capture.FREE_TEXT_LABEL} x1 (first at byte 28, line 3)" in printed
    assert "Goldshire" not in printed
    written = outputs(out)
    (dest,) = [p for p in written if p.endswith("/Tracker.lua")]
    assert written[dest] == (saved / "Tracker.lua").read_bytes()


@pytest.mark.parametrize(
    ("data", "count"),
    [
        (b'x = "one two three four five"\n', 1),
        (b'x = "one two three four"\n', 0),
        (b"x = 'one two three four five'\n", 1),
        (b"x = [[one two three\nfour five]]\n", 1),
        (b"x = [==[one two three four five]==]\n", 1),
        # A key, not a value.
        (b'x = { ["one two three four five"] = 1 }\n', 0),
        (b'x = { [ "one two three four five" ]\n= 1 }\n', 0),
        # Comments are not values.
        (b"-- one two three four five\nx = 1\n", 0),
        (b"--[[ one two three four five ]]\nx = 1\n", 0),
        # Escapes and the client's |n split words; lone punctuation is no word.
        (b'x = "one\\ntwo\\tthree|nfour five"\n', 1),
        (b'x = "one - two - three - four"\n', 0),
        # A quote inside a string, and an escaped one.
        (b'x = "it\'s one two three"\n', 0),
        (b'x = "say \\"one two\\" three four five"\n', 1),
        # Non-ASCII words count.
        ('x = "élan über naïve café déjà"\n'.encode(), 1),
        # Two values; an unterminated string stops at its line break.
        (b'a = "one two three four five"\nb = "six seven eight nine ten\n', 2),
        (b'a = "one two three four five', 1),
        (b"a = [[one two three four five", 1),
    ],
)
def test_constructed_free_text_offsets(data: bytes, count: int) -> None:
    assert len(lab_capture.free_text_offsets(data)) == count


def test_constructed_free_text_scan_is_linear_on_hostile_input() -> None:
    # Many quotes, many unclosed long brackets and comments: each is skipped once.
    for data in (b'"' * 400_000, b"[[" * 200_000, b"--" * 200_000, b'["a"]' * 100_000):
        lab_capture.free_text_offsets(data)


def test_constructed_free_text_is_only_looked_for_in_saved_variables(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    text = b'SET someText "one two three four five six"\n'
    path = root / "config-cache.wtf"
    path.write_bytes(text)
    item = lab_capture.Item(
        path, lab_capture.PurePosixPath("config-cache.wtf"), "", "", "config-wtf"
    )
    identity = lab_capture.Identity()
    outcome = lab_capture.process(item, identity)
    assert not [n for n in outcome.result.notes if lab_capture.FREE_TEXT_LABEL in n]
