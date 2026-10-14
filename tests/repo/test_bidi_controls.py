"""No tracked file holds a raw bidirectional control character (M11-37).

ruff's PLE2502 covers Python source, less U+200E LEFT-TO-RIGHT MARK, which
ruff leaves out on purpose. This covers every tracked file, the lab-addon Lua
and YAML, the docs and the harness included, for the 12 code points below:
the embeddings and overrides U+202A to U+202E, the isolates U+2066 to U+2069,
the marks U+200E and U+200F, and U+061C ARABIC LETTER MARK. The code points
are built with chr(), so this file holds none.

Each scanned file must be strict UTF-8 (round 3, security review): a byte
search for the UTF-8 forms misses a control written in another encoding, and
Python honours a coding declaration, so `# coding: utf-7` with `"+IC4-"`
decodes to U+202E and `# coding: cp1255` turns \\xfd and \\xfe into U+200E
and U+200F (ruff then skips the file and exits 0). So every scanned file
decodes as UTF-8 with no error, every Python source (a .py, .pyi or .pyw
suffix, or a python shebang) declares UTF-8 or nothing, and the raw bytes
and the decoded text are both searched: a NUL or a stray byte elsewhere in a
file cannot hide a control, it fails the file. Every tracked file name is
checked too.

Nothing is skipped for its content or its size: pre-commit keeps every
tracked file outside the fixtures under 512 KB, so each is read whole. Under
lab/core/tests/fixtures/ only the real captures are left out, by type
(`CAPTURE_SUFFIXES`): they are the client's bytes, and a capture may hold a
control, or a byte that is not UTF-8, legitimately. Anything else there is
scanned, the fixture index README.md included, and a Python source or a
Markdown file other than README.md there fails outright: pytest would
collect the one, and the other is prose written here.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIDI = tuple(
    chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C)
)
FIXTURES = "lab/core/tests/fixtures/"
FIXTURE_INDEX = FIXTURES + "README.md"
# The capture types under the fixtures (lab/core/tests/fixtures/README.md).
CAPTURE_SUFFIXES = frozenset(
    {".lua", ".wtf", ".txt", ".csv", ".json", ".gz", ".info", ".html", ".toc", ".bak"}
)
PYTHON_SUFFIXES = frozenset({".py", ".pyi", ".pyw"})
PYTHON_ENCODINGS = frozenset({"utf-8", "utf-8-sig"})


def _bidi_in_bytes(data: bytes) -> list[str]:
    """The code points whose UTF-8 encoding appears anywhere in `data`."""
    return sorted(f"U+{ord(ch):04X}" for ch in BIDI if ch.encode("utf-8") in data)


def _bidi_in_text(text: str) -> list[str]:
    return sorted(f"U+{ord(ch):04X}" for ch in BIDI if ch in text)


def _bidi_in_file(path: Path) -> list[str]:
    """The code points in a file's raw bytes or, where it decodes, its UTF-8
    text (a decoding error is `_problems`'s to report)."""
    return [hit for hit in _problems(path.name, path.read_bytes()) if hit.startswith("U+")]


def _python(name: str, data: bytes) -> bool:
    """Is this a Python source: by suffix, or by a python shebang?"""
    if Path(name).suffix.lower() in PYTHON_SUFFIXES:
        return True
    first = data.split(b"\n", 1)[0]
    return first.startswith(b"#!") and b"python" in first


def _problems(name: str, data: bytes) -> list[str]:
    """Why a scanned file fails: the controls found in its raw bytes or its
    decoded text, a decoding error, or a Python coding declaration other
    than UTF-8."""
    found = set(_bidi_in_bytes(data))
    out = []
    try:
        found.update(_bidi_in_text(data.decode("utf-8")))
    except UnicodeDecodeError as err:
        out.append(f"not UTF-8 (byte {err.start})")
    if _python(name, data):
        try:
            encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
        except SyntaxError as err:
            out.append(f"a coding declaration Python refuses ({err.msg})")
        else:
            if encoding not in PYTHON_ENCODINGS:
                out.append(f"declares the encoding {encoding}")
    return sorted(found) + out


def _tracked() -> list[bytes]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    done = subprocess.run([git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    return [name for name in done.stdout.split(b"\0") if name]


def _scanned(name: str) -> bool:
    """Is the file scanned (everything but a capture under the fixtures)?"""
    return not name.startswith(FIXTURES) or Path(name).suffix.lower() not in CAPTURE_SUFFIXES


def _misplaced(name: str) -> bool:
    """Is it a Python source, or Markdown other than the index, under the fixtures?"""
    if not name.startswith(FIXTURES):
        return False
    suffix = Path(name).suffix.lower()
    return suffix in PYTHON_SUFFIXES or (suffix == ".md" and name != FIXTURE_INDEX)


def test_no_tracked_file_holds_a_bidi_control() -> None:
    found: dict[str, list[str]] = {}
    names = _tracked()
    for raw in names:
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError:
            found[f"file name {raw!r}"] = ["not UTF-8"]
            name = raw.decode("utf-8", "surrogateescape")
        if hits := _bidi_in_bytes(raw):
            found[f"file name {raw!r}"] = hits
        if _misplaced(name):
            found[repr(name)] = ["a Python or Markdown file among the captures"]
            continue
        path = ROOT / name
        if _scanned(name) and path.is_file() and (problems := _problems(name, path.read_bytes())):
            found[repr(name)] = problems
    assert names, "git listed no tracked files"
    assert not found, found


def test_bidi_check_constructed_controls(tmp_path: Path) -> None:
    """Constructed inputs: every code point is found, beside a NUL and a byte
    that is not UTF-8 too (which now fails the file on its own); a clean file
    and the fixture rules behave."""
    assert len(set(BIDI)) == 12
    lines = b"".join(f"-- {ch} UnitName\n".encode() for ch in BIDI)
    problems = _problems("Constructed.lua", b"-- \x00\n-- caf\xe9\n" + lines)
    assert problems[:12] == sorted(f"U+{ord(ch):04X}" for ch in BIDI)
    assert problems[12:] == ["not UTF-8 (byte 11)"]
    assert _problems("Constructed.lua", lines)[:12] == problems[:12]
    assert _problems("Clean.lua", b"-- plain text \x00 caf\xc3\xa9\n") == []
    assert _problems("Clean.lua", b"-- plain text \xff\x00\n") == ["not UTF-8 (byte 14)"]
    assert _bidi_in_bytes(f"docs/a{BIDI[4]}b.md".encode()) == ["U+202E"]
    assert _scanned(FIXTURE_INDEX)
    assert _scanned("docs/LAB_PLAN.md")
    for capture in ("wago/x.csv", "wago/x.json.gz", "SavedVariables/WowLab.lua.bak", "a.WTF"):
        assert not _scanned(FIXTURES + capture), capture
    assert _scanned(FIXTURES + "notes.md")
    assert _scanned(FIXTURES + "conftest.py")
    assert _misplaced(FIXTURES + "notes.md")
    assert _misplaced(FIXTURES + "wago/test_x.py")
    assert not _misplaced(FIXTURE_INDEX)
    assert not _misplaced("lab/core/tests/test_x.py")


def test_constructed_utf7_python_source_fails() -> None:
    """Constructed input (security review, round 3): Python decodes "+IC4-"
    under `# coding: utf-7` as U+202E, though every byte is ASCII."""
    data = b'# coding: utf-7\nx = "+IC4-"\n'
    assert data.decode("utf-7").count(BIDI[4]) == 1  # the premise
    assert _bidi_in_bytes(data) == []  # what the byte search alone saw
    assert _problems("constructed_utf7.py", data) == ["declares the encoding utf-7"]


def test_constructed_cp1255_python_source_fails() -> None:
    """Constructed input (security review, round 3): under `# coding: cp1255`
    the bytes \\xfd and \\xfe are U+200E and U+200F."""
    data = b'# -*- coding: cp1255 -*-\nx = "\xfd\xfe"\n'
    assert data.decode("cp1255").count(chr(0x200E)) == 1  # the premise
    assert _bidi_in_bytes(data) == []
    assert _problems("constructed_cp1255.py", data) == [
        "not UTF-8 (byte 30)",
        "declares the encoding cp1255",
    ]


def test_constructed_utf16_markdown_fails() -> None:
    """Constructed input (security review, round 3): a UTF-16 Markdown file
    with a byte-order mark holds U+202E as the bytes 2E 20, never E2 80 AE."""
    data = f"# notes {BIDI[4]}\n".encode("utf-16")
    assert data[:2] in {b"\xff\xfe", b"\xfe\xff"}  # the premise: a BOM
    assert _bidi_in_bytes(data) == []
    assert _problems("docs/constructed.md", data) == ["not UTF-8 (byte 0)"]


def test_constructed_python_shebang_without_suffix_is_python() -> None:
    """Constructed input: a script run by python without a .py suffix is
    held to the same coding rule."""
    data = b"#!/usr/bin/env python3\n# coding: utf-7\n"
    assert _problems("scripts/constructed", data) == ["declares the encoding utf-7"]
    assert _problems("scripts/constructed.sh", b"#!/bin/sh\n# coding: utf-7\n") == []
