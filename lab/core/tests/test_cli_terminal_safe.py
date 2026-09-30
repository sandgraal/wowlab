"""Terminal-safe CLI text (M11-30, from the M11-23 and M11-25 security reviews).

`cli._safe` shows every C0 control but tab (line feed included), DEL, every
C1 control, and every Unicode format and separator character (categories
Cf, Zl and Zp: U+202E reverses the text after it, U+2028 breaks a line) as
`\\xNN` escapes, the format and separator characters as their UTF-8 bytes,
the way M11-25 prints key paths. `cli._note` keeps the line breaks of its
own message and escapes the ones in a value it inserts, so a folder name
can never start a line of its own on stderr. Letters, accented or not,
print as they are.

Every input here is `constructed` (L8, hostile-input and boundary cases):
no real capture holds a folder name with a line break or a bidirectional
override. The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path` with extra character folders added, as `test_cli_char.py` does;
`WOWLAB_WOW_ROOT` points at the copy and the user data directory is
redirected into `tmp_path`. Nothing reads or writes a real install.

Characters outside printable ASCII are spelled with `chr` so that none of
them sits raw in this file.
"""

from __future__ import annotations

import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any

import platformdirs
import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCOUNT_DIR = f"WTF/Account/{ACCOUNT}"
REALM = "1"  # the capture's realm folder
DONOR = f"{REALM}/Labchard-Labrealmg"  # a captured character with a WowLab.lua

# Categories that must never reach a terminal raw. Tab (Cc) is the one
# exception `_safe` keeps; a line break is allowed only between lines.
UNSAFE_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")

SHY = chr(0x00AD)  # SOFT HYPHEN, Cf
ZWSP = chr(0x200B)  # ZERO WIDTH SPACE, Cf
LRM = chr(0x200E)  # LEFT-TO-RIGHT MARK, Cf
LSEP = chr(0x2028)  # LINE SEPARATOR, Zl
PSEP = chr(0x2029)  # PARAGRAPH SEPARATOR, Zp
RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE, Cf
RLI = chr(0x2067)  # RIGHT-TO-LEFT ISOLATE, Cf
BOM = chr(0xFEFF)  # ZERO WIDTH NO-BREAK SPACE, Cf
TAG = chr(0xE0001)  # LANGUAGE TAG, Cf
BAD_BYTE = chr(0xDCFF)  # the invalid byte 0xFF, carried by surrogateescape

E_DIAERESIS = chr(0x00EB)  # e with diaeresis
E_ACUTE = chr(0x00E9)  # e with acute
N_TILDE = chr(0x00D1)  # capital N with tilde
U_ACUTE = chr(0x00FA)  # u with acute
AE = chr(0x00C6)  # capital AE
COMBINING_DIAERESIS = chr(0x0308)  # Mn: a letter's accent, never escaped
CYRILLIC = "".join(map(chr, (0x0416, 0x0430, 0x043D, 0x043D, 0x0430)))
CJK = "".join(map(chr, (0x65E5, 0x672C, 0x8A9E)))
EMOJI = chr(0x1F600)

# (id, character, how `_safe` shows it)
HOSTILE = [
    ("newline", "\n", "\\x0a"),
    ("carriage-return", "\r", "\\x0d"),
    ("esc", "\x1b", "\\x1b"),
    ("del", "\x7f", "\\x7f"),
    ("nel-c1", "\x85", "\\x85"),
    ("csi-c1", "\x9b", "\\x9b"),
    ("soft-hyphen-u00ad", SHY, "\\xc2\\xad"),
    ("zero-width-space-u200b", ZWSP, "\\xe2\\x80\\x8b"),
    ("lrm-u200e", LRM, "\\xe2\\x80\\x8e"),
    ("line-separator-u2028", LSEP, "\\xe2\\x80\\xa8"),
    ("paragraph-separator-u2029", PSEP, "\\xe2\\x80\\xa9"),
    ("rlo-u202e", RLO, "\\xe2\\x80\\xae"),
    ("rli-u2067", RLI, "\\xe2\\x81\\xa7"),
    ("bom-ufeff", BOM, "\\xef\\xbb\\xbf"),
    ("tag-ue0001", TAG, "\\xf3\\xa0\\x80\\x81"),
    ("invalid-byte", BAD_BYTE, "\\xff"),
]

# (id, text): letters print as they are, accented (precomposed and with a
# combining mark), other scripts, and a symbol outside the BMP.
LETTERS = [
    ("e-diaeresis", f"Zo{E_DIAERESIS}"),
    ("e-acute", f"Caf{E_ACUTE}"),
    ("n-tilde", f"{N_TILDE}and{U_ACUTE}"),
    ("combining", f"Gru{COMBINING_DIAERESIS}n"),
    ("ae", f"{AE}rind{E_DIAERESIS}l"),
    ("cyrillic", CYRILLIC),
    ("cjk", CJK),
    ("emoji", EMOJI),
]

ACCENTED = f"Zo{E_DIAERESIS}lle"

# Character folder names for the end-to-end tests. Each hostile one ends in
# text that would pass for a line of the CLI's own output if it were split.
NAMES = {
    "newline": "Evil\nwowlab: restored 1 file",
    "rlo": f"Evil{RLO}elif.gnp",
    "line-separator": f"Evil{LSEP}wowlab: restored 1 file",
    "accented": ACCENTED,
}
SHOWN = {
    "newline": "Evil\\x0awowlab: restored 1 file",
    "rlo": "Evil\\xe2\\x80\\xaeelif.gnp",
    "line-separator": "Evil\\xe2\\x80\\xa8wowlab: restored 1 file",
    "accented": ACCENTED,
}

runner = CliRunner()


def _unsafe(text: str) -> list[str]:
    """Every character of `text` a terminal must not get raw, as hex (line
    feeds included: callers pass one line)."""
    return [
        hex(ord(c))
        for c in text
        if c != "\t"
        and (unicodedata.category(c) in UNSAFE_CATEGORIES or 0xD800 <= ord(c) <= 0xDFFF)
    ]


def _lines_are_safe(text: str) -> bool:
    return all(_unsafe(line) == [] for line in text.split("\n"))


# ─── _safe ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("char", "shown"), [pytest.param(c, s, id=f"constructed-{i}") for i, c, s in HOSTILE]
)
def test_safe_escapes_a_hostile_character_constructed(char: str, shown: str) -> None:
    got = cli._safe(f"a{char}b")
    assert got == f"a{shown}b"
    assert _unsafe(got) == []


@pytest.mark.parametrize("text", [pytest.param(t, id=f"constructed-{i}") for i, t in LETTERS])
def test_safe_keeps_letters_as_they_are_constructed(text: str) -> None:
    assert cli._safe(text) == text
    assert cli._safe(f"a\tb {text}") == f"a\tb {text}"


def test_safe_escapes_bytes_that_make_a_format_character_constructed() -> None:
    """Invalid bytes carried as lone surrogates that together are the UTF-8
    of U+202E are shown as those bytes, never as the character."""
    carried = "a" + "".join(chr(0xDC00 + b) for b in RLO.encode()) + "b"
    assert cli._safe(carried) == "a\\xe2\\x80\\xaeb"


def test_safe_escapes_every_control_format_and_separator_character_constructed() -> None:
    """Every code point this Python's Unicode database files under Cc, Cf,
    Zl or Zp (tab aside) is escaped (a newer database that adds one fails
    here), and every letter prints as it is."""
    everything = [chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF]
    bad = [c for c in everything if c != "\t" and unicodedata.category(c) in UNSAFE_CATEGORIES]
    assert bad, "the database has these categories"
    assert _unsafe(cli._safe("".join(bad))) == []
    for c in bad:
        assert cli._safe(c).startswith("\\x"), hex(ord(c))
    letters = "".join(c for c in everything if unicodedata.category(c).startswith("L"))
    assert cli._safe(letters) == letters


# ─── _note, _say_err and _fail ───────────────────────────────────────────────


def test_note_keeps_its_own_line_breaks_and_escapes_a_values_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._note("first line\nsecond: {} and {}", NAMES["newline"], NAMES["line-separator"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"first line\nsecond: {SHOWN['newline']} and {SHOWN['line-separator']}\n"
    )


def test_note_value_braces_are_text_constructed(capsys: pytest.CaptureFixture[str]) -> None:
    cli._note("name {} ({{literal}})", "{0} {}" + RLO)
    assert capsys.readouterr().err == "name {0} {}\\xe2\\x80\\xae ({literal})\n"


def test_note_escapes_its_own_non_line_break_controls_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._note(f"a\x1bb{LSEP}c\nd")
    assert capsys.readouterr().err == "a\\x1bb\\xe2\\x80\\xa8c\nd\n"


def test_say_err_is_one_line_on_stderr_constructed(capsys: pytest.CaptureFixture[str]) -> None:
    cli._say_err(f"note about {NAMES['newline']} and {NAMES['rlo']}")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"note about {SHOWN['newline']} and {SHOWN['rlo']}\n"


def test_fail_prints_an_error_on_one_line_constructed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as exited:
        cli._fail(f"no character folder in {NAMES['newline']}", cli.EXIT_USAGE)
    assert exited.value.exit_code == cli.EXIT_USAGE
    assert capsys.readouterr().err == f"wowlab: no character folder in {SHOWN['newline']}\n"


# ─── end to end: character folders with hostile names ───────────────────────


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The captured install with one character folder per `NAMES` entry,
    each holding the donor character's `WowLab.lua`."""
    if sys.platform == "win32":
        pytest.skip("Windows forbids control characters 0-31 in file names")
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    flavor = root / FLAVOR
    donor = flavor / ACCOUNT_DIR / DONOR / "SavedVariables" / "WowLab.lua"
    for name in NAMES.values():
        sv = flavor / ACCOUNT_DIR / REALM / name / "SavedVariables"
        sv.mkdir(parents=True)
        shutil.copy2(donor, sv / "WowLab.lua")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return flavor


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def _assert_terminal_safe(result: Any) -> None:
    assert _lines_are_safe(result.stdout), _unsafe(result.stdout)
    assert _lines_are_safe(result.stderr), _unsafe(result.stderr)
    for key, name in NAMES.items():
        if key != "accented":
            assert name not in result.stdout and name not in result.stderr


def test_sv_list_shows_hostile_folder_names_escaped_constructed(flavor: Path) -> None:
    result = run("sv", "list")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    _assert_terminal_safe(result)
    for key, shown in SHOWN.items():
        path = f"{ACCOUNT_DIR}/{REALM}/{shown}/SavedVariables/WowLab.lua"
        (line,) = [ln for ln in result.stdout.split("\n") if ln.endswith(path)]
        assert line.startswith("character"), (key, line)
    assert not any(ln.startswith("wowlab: restored") for ln in result.stdout.split("\n"))


def test_error_listing_hostile_folder_names_is_one_line_constructed(flavor: Path) -> None:
    result = run("sv", "list", "--character", "Nobody")
    assert result.exit_code == cli.EXIT_USAGE
    _assert_terminal_safe(result)
    assert result.stdout == ""
    (line,) = result.stderr.split("\n")[:-1]
    assert line.startswith("wowlab: no character folder 'Nobody' in account ")
    for shown in SHOWN.values():
        assert f"{REALM}/{shown}" in line
    assert f"{REALM}/{ACCENTED}" in line  # the accented name as it is


@pytest.mark.parametrize("key", [pytest.param(k, id=f"constructed-{k}") for k in NAMES])
def test_char_show_names_the_folder_escaped_constructed(flavor: Path, key: str) -> None:
    result = run("char", "show", "--character", f"{REALM}/{NAMES[key]}")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    _assert_terminal_safe(result)
    first = result.stdout.split("\n")[0]
    assert first == f"Character: {REALM}/{SHOWN[key]} in account {ACCOUNT}"


def test_char_show_json_keeps_the_folder_name_exact_constructed(flavor: Path) -> None:
    """JSON escapes are JSON's own: the name reads back exactly."""
    for name in NAMES.values():
        result = run("char", "show", "--character", f"{REALM}/{name}", "--json")
        assert result.exit_code == 0, (result.stdout, result.stderr)
        assert _lines_are_safe(result.stdout), _unsafe(result.stdout)
        report = cli.CharShowReport.model_validate_json(result.stdout)
        assert report.character == f"{REALM}/{name}"


def test_install_not_found_notes_escape_the_locations_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_note` lines that name a location: the location's line breaks are
    escaped, and each note stays one line."""
    if sys.platform == "win32":
        pytest.skip("Windows forbids control characters 0-31 in file names")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    absent = tmp_path / NAMES["newline"] / "World of Warcraft"
    a_file = tmp_path / NAMES["line-separator"]
    a_file.write_bytes(b"")  # something that is not a directory
    monkeypatch.setattr(install, "default_roots", lambda **k: (absent, a_file))
    result = run("install", "show")
    assert result.exit_code == 1
    _assert_terminal_safe(result)
    lines = result.stderr.split("\n")[:-1]
    assert len(lines) == 3, lines
    shown_absent = f"{tmp_path}/{SHOWN['newline']}/World of Warcraft"
    shown_file = f"{tmp_path}/{SHOWN['line-separator']}"
    assert lines[0] == f"searched, no install there: {shown_absent}"
    assert lines[1] == f"could not check: {shown_file} (not a directory)"
    assert lines[2].startswith("wowlab: ")
