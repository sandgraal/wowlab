"""No tracked file holds a raw bidirectional control character (M11-37).

ruff's PLE2502 covers Python source, less U+200E LEFT-TO-RIGHT MARK, which
ruff leaves out on purpose. This covers every tracked file, the lab-addon Lua
and YAML, the docs and the harness included, for the 12 code points below:
the embeddings and overrides U+202A to U+202E, the isolates U+2066 to U+2069,
the marks U+200E and U+200F, and U+061C ARABIC LETTER MARK. It searches each
file's raw bytes for their UTF-8 encodings, so a NUL or a byte that is not
UTF-8 elsewhere in the file does not hide one, and it checks every tracked
file name too. The code points are built with chr(), so this file holds none.

Nothing is skipped for its content or its size: pre-commit keeps every
tracked file outside the fixtures under 512 KB, so each is read whole. The
real captures under lab/core/tests/fixtures/ are left out, since they are the
client's bytes and a capture may hold one legitimately; the fixture index,
lab/core/tests/fixtures/README.md, is prose written here, and it is scanned.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIDI = tuple(
    chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C)
)
FIXTURES = "lab/core/tests/fixtures/"
FIXTURE_INDEX = FIXTURES + "README.md"


def _bidi_in_bytes(data: bytes) -> list[str]:
    """The code points whose UTF-8 encoding appears anywhere in `data`."""
    return sorted(f"U+{ord(ch):04X}" for ch in BIDI if ch.encode("utf-8") in data)


def _bidi_in_file(path: Path) -> list[str]:
    return _bidi_in_bytes(path.read_bytes())


def _tracked() -> list[bytes]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    done = subprocess.run([git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    return [name for name in done.stdout.split(b"\0") if name]


def _scanned(name: str) -> bool:
    return not name.startswith(FIXTURES) or name == FIXTURE_INDEX


def test_no_tracked_file_holds_a_bidi_control() -> None:
    found = {}
    names = _tracked()
    for raw in names:
        if hits := _bidi_in_bytes(raw):
            found[f"file name {raw!r}"] = hits
        name = raw.decode("utf-8", "surrogateescape")
        path = ROOT / name
        if _scanned(name) and path.is_file() and (hits := _bidi_in_file(path)):
            found[repr(name)] = hits
    assert names, "git listed no tracked files"
    assert not found, found


def test_bidi_check_constructed_controls(tmp_path: Path) -> None:
    """Constructed inputs: every code point is found, beside a NUL and a byte
    that is not UTF-8 too; a clean file and the fixture rule behave."""
    assert len(set(BIDI)) == 12
    path = tmp_path / "Constructed.lua"
    lines = [f"-- {ch} UnitName\n".encode() for ch in BIDI]
    path.write_bytes(b"-- \x00\n-- caf\xe9\n" + b"".join(lines))
    assert len(_bidi_in_file(path)) == 12
    clean = tmp_path / "Clean.lua"
    clean.write_bytes(b"-- plain text \xff\x00\n")
    assert _bidi_in_file(clean) == []
    assert _bidi_in_bytes(f"docs/a{BIDI[4]}b.md".encode()) == ["U+202E"]
    assert _scanned(FIXTURE_INDEX)
    assert not _scanned(FIXTURES + "wago/x.csv")
    assert _scanned("docs/LAB_PLAN.md")
