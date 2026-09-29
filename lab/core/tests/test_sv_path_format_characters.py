"""Printed key paths escape Unicode format and separator characters (M11-25,
security review): a key holding U+202E (RIGHT-TO-LEFT OVERRIDE), U+2028
(LINE SEPARATOR) or another character of category Cf, Zl or Zp prints with
that character as `\\ddd` UTF-8 byte escapes, never raw, and the printed path
still reads back to the same key through `sv dump --path` and `--key`.

Constructed (L8, boundary): such characters are valid UTF-8 a client could
write into a string key; no real fixture holds one. The user data directory
is redirected to `tmp_path`; no install is read or written.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, luadata, svmerge

# (id, character). U+2065 is unassigned but sits in the U+2060 block.
CHARACTERS = [
    ("rlo-u202e", chr(0x202E)),
    ("line-separator-u2028", chr(0x2028)),
    ("paragraph-separator-u2029", chr(0x2029)),
    ("zero-width-space-u200b", chr(0x200B)),
    ("bom-ufeff", chr(0xFEFF)),
    ("soft-hyphen-u00ad", chr(0x00AD)),
    ("arabic-letter-mark-u061c", chr(0x061C)),
    ("unassigned-u2065", chr(0x2065)),
    ("isolate-u2066", chr(0x2066)),
    ("tag-ue0001", chr(0xE0001)),
]


@pytest.fixture(autouse=True)
def _user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)


def _document(values: int) -> bytes:
    lines = [b"", b"FmtDB = {"]
    for n, (_id, char) in enumerate(CHARACTERS, start=1):
        raw = char.encode()
        lines.append(b'["raw' + raw + b'x"] = %d,' % (values + n))
        escaped = b"".join(b"\\%d" % b for b in raw)
        lines.append(b'["esc' + escaped + b'x"] = %d,' % (values + 100 + n))
    lines += [b"}", b""]
    return b"\n".join(lines)


OURS = _document(1000)
THEIRS = _document(2000)


def _printed(file: Path, value: int) -> str:
    dump = CliRunner().invoke(cli.app, ["sv", "dump", str(file)])
    assert dump.exit_code == 0, dump.stderr
    (line,) = [ln for ln in dump.stdout.split("\n") if ln.endswith(f" = {value}")]
    return line[: -len(f" = {value}")]


@pytest.mark.parametrize("offset", [0, 100], ids=["raw", "escaped"])
@pytest.mark.parametrize(
    ("n", "char"),
    [pytest.param(n, c, id=f"constructed-{i}") for n, (i, c) in enumerate(CHARACTERS, start=1)],
)
def test_format_character_key_prints_escaped_and_reads_back_constructed(
    tmp_path: Path, n: int, char: str, offset: int
) -> None:
    file = tmp_path / "Fmt.lua"
    file.write_bytes(OURS)
    path = _printed(file, 1000 + offset + n)
    assert char not in path, path
    assert all(unicodedata.category(c) not in ("Cf", "Zl", "Zp") for c in path), path
    assert "".join(f"\\{b:03d}" for b in char.encode()) in path, path
    again = CliRunner().invoke(cli.app, ["sv", "dump", str(file), "--path", path])
    assert (again.exit_code, again.stdout) == (0, f"{path} = {1000 + offset + n}\n"), again.stderr
    ours, theirs = luadata.parse(OURS), luadata.parse(THEIRS)
    limited = svmerge.merge(ours, theirs, keys=[path])
    assert [(t.path, t.value) for t in limited.taken] == [(path, str(2000 + offset + n))]


def test_sv_dump_text_holds_no_raw_format_character_constructed(tmp_path: Path) -> None:
    file = tmp_path / "Fmt.lua"
    file.write_bytes(OURS)
    dump = CliRunner().invoke(cli.app, ["sv", "dump", str(file)])
    assert dump.exit_code == 0, dump.stderr
    raw = [hex(ord(c)) for c in dump.stdout if unicodedata.category(c) in ("Cf", "Zl", "Zp")]
    assert raw == []
    assert dump.stdout.count("\n") == 2 * len(CHARACTERS)


def test_every_format_and_separator_character_is_escaped_constructed() -> None:
    """Every Cf, Zl and Zp code point this Python's Unicode database knows is
    printed as escapes (a newer database that adds one fails here)."""
    codes = [c for c in range(0x110000) if unicodedata.category(chr(c)) in ("Cf", "Zl", "Zp")]
    body = b"".join(b'["k%d' % c + chr(c).encode() + b'"] = 1,\n' for c in codes)
    doc = luadata.parse(b"T = {\n" + body + b"}\n")
    table = doc.assignments[0].value
    assert isinstance(table, luadata.LuaTable) and len(table.entries) == len(codes)
    for code, entry in zip(codes, table.entries, strict=True):
        step = svmerge.path_step(entry, None)
        assert chr(code) not in step, hex(code)
        read = svmerge.parse_path("T" + step)
        assert [s.key_id for s in read.steps] == [("s", b"k%d" % code + chr(code).encode())]
