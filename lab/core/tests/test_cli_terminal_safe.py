"""Terminal-safe CLI text (M11-30, from the M11-23 and M11-25 security reviews).

`cli._safe` shows every C0 control (tab and line feed included), DEL, every
C1 control, and every Unicode format and separator character (categories
Cf, Zl and Zp: U+202E reverses the text after it, U+2028 breaks a line) as
`\\xNN` escapes: C0 and DEL as their one byte, C1, format and separator
characters as their UTF-8 bytes, the way M11-25 prints key paths. `cli._note`
keeps the line breaks of its own message and escapes the ones in a value it
inserts, so a folder name can never start a line of its own on stderr. The
error notes a library exception carries, the library's log lines and
`db2 head`'s CSV text are escaped the same way. Letters, accented or not,
print as they are, and `--json` output is unchanged.

Every input here is `constructed` (L8, hostile-input and boundary cases):
no real capture holds a folder name with a line break or a bidirectional
override, and no recorded wago.tools table holds a control character. The
install is the captured tree (`fixtures/macos/`) copied into `tmp_path` with
extra character folders added, as `test_cli_char.py` does; `WOWLAB_WOW_ROOT`
points at the copy and the user data directory is redirected into
`tmp_path`. The game-data cache is planted under `tmp_path` and every
network request fails. Nothing reads or writes a real install.

Characters outside printable ASCII are spelled with `chr` so that none of
them sits raw in this file.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import shutil
import sys
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import platformdirs
import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import cli, gamedata, guard, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCOUNT_DIR = f"WTF/Account/{ACCOUNT}"
REALM = "1"  # the capture's realm folder
DONOR = f"{REALM}/Labchard-Labrealmg"  # a captured character with a WowLab.lua
BUILD = "9.9.9.99999"  # a constructed build: only the planted cache holds it

# Categories that must never reach a terminal raw; a line feed is allowed
# only between lines.
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
BAD_85 = chr(0xDC85)  # the invalid byte 0x85 (not U+0085)

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
    ("tab", "\t", "\\x09"),
    ("esc", "\x1b", "\\x1b"),
    ("bel", "\x07", "\\x07"),
    ("del", "\x7f", "\\x7f"),
    ("nel-c1", "\x85", "\\xc2\\x85"),
    ("csi-c1", "\x9b", "\\xc2\\x9b"),
    ("soft-hyphen-u00ad", SHY, "\\xc2\\xad"),
    ("zero-width-space-u200b", ZWSP, "\\xe2\\x80\\x8b"),
    ("lrm-u200e", LRM, "\\xe2\\x80\\x8e"),
    ("line-separator-u2028", LSEP, "\\xe2\\x80\\xa8"),
    ("paragraph-separator-u2029", PSEP, "\\xe2\\x80\\xa9"),
    ("rlo-u202e", RLO, "\\xe2\\x80\\xae"),
    ("rli-u2067", RLI, "\\xe2\\x81\\xa7"),
    ("bom-ufeff", BOM, "\\xef\\xbb\\xbf"),
    ("tag-ue0001", TAG, "\\xf3\\xa0\\x80\\x81"),
    ("invalid-byte-ff", BAD_BYTE, "\\xff"),
    ("invalid-byte-85", BAD_85, "\\x85"),
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
    "tab": "Evil\tTab",
    "accented": ACCENTED,
}
SHOWN = {
    "newline": "Evil\\x0awowlab: restored 1 file",
    "rlo": "Evil\\xe2\\x80\\xaeelif.gnp",
    "line-separator": "Evil\\xe2\\x80\\xa8wowlab: restored 1 file",
    "tab": "Evil\\x09Tab",
    "accented": ACCENTED,
}

runner = CliRunner()


def _unsafe(text: str) -> list[str]:
    """Every character of `text` a terminal must not get raw, as hex (line
    feeds included: callers pass one line)."""
    return [
        hex(ord(c))
        for c in text
        if unicodedata.category(c) in UNSAFE_CATEGORIES or 0xD800 <= ord(c) <= 0xDFFF
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


def test_safe_tells_a_c1_character_from_an_invalid_byte_constructed() -> None:
    """U+0085 is stored as the bytes C2 85; a lone invalid byte 0x85 is not."""
    assert cli._safe("\x85") != cli._safe(BAD_85)


@pytest.mark.parametrize("text", [pytest.param(t, id=f"constructed-{i}") for i, t in LETTERS])
def test_safe_keeps_letters_as_they_are_constructed(text: str) -> None:
    assert cli._safe(text) == text
    assert cli._safe(f"a b {text}") == f"a b {text}"


def test_safe_escapes_bytes_that_make_a_format_character_constructed() -> None:
    """Invalid bytes carried as lone surrogates that together are the UTF-8
    of U+202E are shown as those bytes, never as the character."""
    carried = "a" + "".join(chr(0xDC00 + b) for b in RLO.encode()) + "b"
    assert cli._safe(carried) == "a\\xe2\\x80\\xaeb"


def test_safe_escapes_every_control_format_and_separator_character_constructed() -> None:
    """Every code point this Python's Unicode database files under Cc, Cf,
    Zl or Zp is escaped (the table `_safe` uses is pinned to Unicode 15.0;
    a newer database that adds one fails here), and every letter prints as
    it is."""
    everything = [chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF]
    bad = [c for c in everything if unicodedata.category(c) in UNSAFE_CATEGORIES]
    assert bad, "the database has these categories"
    assert _unsafe(cli._safe("".join(bad))) == []
    for c in bad:
        assert cli._safe(c) == "".join(
            f"\\x{b:02x}" for b in (bytes([ord(c)]) if c < "\x80" else c.encode())
        ), hex(ord(c))
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


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("{}", id="constructed-empty-field"),
        pytest.param("{0}", id="constructed-index-field"),
        pytest.param("{:>999999999}", id="constructed-width-spec"),
        pytest.param("{{}}", id="constructed-doubled-braces"),
    ],
)
def test_note_prints_a_value_holding_braces_as_it_is_constructed(
    capsys: pytest.CaptureFixture[str], value: str
) -> None:
    cli._note("name {} (done)", value + RLO)
    assert capsys.readouterr().err == f"name {value}\\xe2\\x80\\xae (done)\n"


def test_note_prints_braces_of_its_own_message_as_they_are_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._note("a Lua table: { } and {x}: {}", "v")
    assert capsys.readouterr().err == "a Lua table: { } and {x}: v\n"


@pytest.mark.parametrize(
    ("message", "values"),
    [
        pytest.param("no field", ("v",), id="constructed-value-without-field"),
        pytest.param("one {}", (), id="constructed-field-without-value"),
        pytest.param("one {}", ("a", "b"), id="constructed-more-values"),
        pytest.param("{} and {}", ("a",), id="constructed-fewer-values"),
    ],
)
def test_note_refuses_a_field_count_that_does_not_match_constructed(
    capsys: pytest.CaptureFixture[str], message: str, values: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="_note"):
        cli._note(message, *values)
    assert capsys.readouterr().err == ""


def test_note_escapes_its_own_non_line_break_controls_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._note(f"a\x1bb{LSEP}c\td\ne")
    assert capsys.readouterr().err == "a\\x1bb\\xe2\\x80\\xa8c\\x09d\ne\n"


def _note_calls() -> list[ast.Call]:
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_note"
    ]


def _module_strings() -> dict[str, str]:
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def test_every_note_call_has_a_constant_message_and_matching_values() -> None:
    """Static check over `cli.py`: the first argument of every `_note` call is
    a string literal or a module-level string constant (never an f-string
    or an expression that could carry a value), and it has one `{}` per
    value passed."""
    constants = _module_strings()
    calls = _note_calls()
    assert len(calls) > 10, "the walk finds the call sites"
    for call in calls:
        where = f"cli.py line {call.lineno}"
        assert call.args and not call.keywords, where
        assert not any(isinstance(a, ast.Starred) for a in call.args), where
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            message = first.value
        else:
            assert isinstance(first, ast.Name) and first.id in constants, where
            message = constants[first.id]
        assert message.count("{}") == len(call.args) - 1, where


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


ROLLBACK_NOTE = (
    f"wowlab guard: the rollback did not finish: WTF/Account/{ACCOUNT}/{REALM}/"
    f"{NAMES['newline']}/SavedVariables/WowLab.lua: could not put back"
)
ROLLBACK_SHOWN = (
    f"wowlab guard: the rollback did not finish: WTF/Account/{ACCOUNT}/{REALM}/"
    f"{SHOWN['newline']}/SavedVariables/WowLab.lua: could not put back"
)


def test_handled_prints_the_notes_an_error_carries_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The write gate adds "the rollback did not finish" to the exception
    that ended a transaction as a note; the error line is followed by it."""

    @cli._handled
    def command() -> None:
        exc = guard.GuardError(f"{NAMES['rlo']} changed while the gate held it")
        exc.add_note(ROLLBACK_NOTE)
        raise exc

    with pytest.raises(typer.Exit) as exited:
        command()
    assert exited.value.exit_code == cli.EXIT_REFUSED
    assert capsys.readouterr().err.split("\n") == [
        f"wowlab: refused by the write gate: {SHOWN['rlo']} changed while the gate held it",
        ROLLBACK_SHOWN,
        "",
    ]


# ─── end to end: the library's log lines and error notes ────────────────────


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request is expected: {request.url}")


@pytest.fixture
def planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> gamedata.GameData:
    """A game-data client over a cache under `tmp_path`; any request fails."""
    monkeypatch.setenv(install.ENV_ROOT, str(tmp_path / "no install"))
    source = gamedata.WagoSource(transport=httpx.MockTransport(_no_network))
    data = gamedata.GameData(source, cache_dir=tmp_path / "gamedata")

    @contextlib.contextmanager
    def fake() -> Iterator[gamedata.GameData]:
        yield data

    monkeypatch.setattr(cli, "_open_gamedata", fake)
    return data


def test_a_library_log_line_reaches_stderr_escaped_constructed(
    planted: gamedata.GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write-gate warning naming a hostile path, logged while a command
    runs, is one escaped line on stderr (Python's fallback handler would
    print it raw), and the logger is as it was once the command ends."""
    rel = f"WTF/Account/{ACCOUNT}/{REALM}/{NAMES['newline']}{RLO}\x1b]0;x\x07/WowLab.lua"
    shown = (
        f"WTF/Account/{ACCOUNT}/{REALM}/{SHOWN['newline']}\\xe2\\x80\\xae\\x1b]0;x\\x07/WowLab.lua"
    )
    real = cli._open_gamedata

    @contextlib.contextmanager
    def logging_first() -> Iterator[gamedata.GameData]:
        guard._log.warning(
            "leftover temp file left in place: %s", guard.GuardError(f"{rel} changed")
        )
        with real() as data:
            yield data

    monkeypatch.setattr(cli, "_open_gamedata", logging_first)
    path = planted.table_path("ChrClasses", BUILD)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"ID,Name_lang\n1,Mage\n")
    library = logging.getLogger("wowlab_core")
    before = (list(library.handlers), library.propagate)
    result = runner.invoke(cli.app, ["db2", "head", "ChrClasses", "--build", BUILD])
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert result.stdout == "ID,Name_lang\n1,Mage\n"
    lines = result.stderr.split("\n")
    assert f"wowlab: leftover temp file left in place: {shown} changed" in lines
    assert _lines_are_safe(result.stderr), _unsafe(result.stderr)
    assert (list(library.handlers), library.propagate) == before


def test_an_error_note_reaches_stderr_escaped_constructed(
    planted: gamedata.GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextlib.contextmanager
    def failing() -> Iterator[gamedata.GameData]:
        exc = OSError(f"could not replace {NAMES['line-separator']}")
        exc.add_note(ROLLBACK_NOTE)
        raise exc
        yield  # pragma: no cover

    monkeypatch.setattr(cli, "_open_gamedata", failing)
    result = runner.invoke(cli.app, ["db2", "head", "ChrClasses", "--build", BUILD])
    assert result.exit_code == 1
    assert _lines_are_safe(result.stderr), _unsafe(result.stderr)
    assert result.stderr.split("\n") == [
        f"wowlab: could not replace {SHOWN['line-separator']}",
        ROLLBACK_SHOWN,
        "",
    ]


# ─── end to end: db2 head over a planted cache ──────────────────────────────


OSC52 = "\x1b]52;c;cm0gLXJmIH4=\x07"  # an OSC 52 clipboard write, ended by BEL


def test_db2_head_escapes_every_name_and_cell_constructed(planted: gamedata.GameData) -> None:
    path = planted.table_path("ChrClasses", BUILD)
    path.parent.mkdir(parents=True)
    header = f"ID,Name{RLO}_lang,Filename"
    row = f'1,"War{OSC52}{RLO}x{LSEP}y","a\tb\r\nc, d"'
    path.write_bytes(f"{header}\n{row}\n2,Mage,MAGE\n".encode())

    text = runner.invoke(cli.app, ["db2", "head", "ChrClasses", "--build", BUILD])
    assert text.exit_code == 0, (text.stdout, text.stderr)
    assert _lines_are_safe(text.stdout), _unsafe(text.stdout)
    assert text.stdout.split("\n") == [
        "ID,Name\\xe2\\x80\\xae_lang,Filename",
        '1,War\\x1b]52;c;cm0gLXJmIH4=\\x07\\xe2\\x80\\xaex\\xe2\\x80\\xa8y,"a\\x09b\\x0d\\x0ac, d"',
        "2,Mage,MAGE",
        "",
    ]

    as_json = runner.invoke(cli.app, ["db2", "head", "ChrClasses", "--build", BUILD, "--json"])
    assert as_json.exit_code == 0, (as_json.stdout, as_json.stderr)
    report = cli.HeadReport.model_validate_json(as_json.stdout)
    assert report.rows[0] == {
        "ID": "1",
        f"Name{RLO}_lang": f"War{OSC52}{RLO}x{LSEP}y",
        "Filename": "a\tb\r\nc, d",
    }


def test_db2_head_prints_the_recorded_table_as_it_was() -> None:
    """The positive control: a real recorded table (fixtures/wago) holds no
    character `_safe` changes, so its CSV text is unchanged byte for byte."""
    recorded = FIXTURES / "wago" / "ChrClasses.1.60.1.69876.csv"
    text = recorded.read_text(encoding="utf-8")
    assert all(cli._safe(line) == line for line in text.splitlines())


# ─── end to end: character folders with hostile names ───────────────────────


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
