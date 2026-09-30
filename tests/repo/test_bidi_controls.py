"""No tracked text file holds a raw bidirectional control character (M11-37).

ruff's PLE2502 covers Python source, less U+200E LEFT-TO-RIGHT MARK, which
ruff leaves out on purpose. This covers every tracked text file, the lab-addon
Lua and YAML included, for the 12 code points below: the embeddings and
overrides U+202A to U+202E, the isolates U+2066 to U+2069, the marks U+200E
and U+200F, and U+061C ARABIC LETTER MARK. They are built with chr(), so this
file holds none. Real captures under lab/core/tests/fixtures/ are left out:
they are the client's bytes, and a capture may hold one legitimately.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIDI = frozenset(
    chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C)
)
EXCLUDED = ("lab/core/tests/fixtures/",)


def _bidi_in(text: str) -> list[str]:
    return sorted({f"U+{ord(ch):04X}" for ch in text if ch in BIDI})


def _tracked() -> list[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    done = subprocess.run([git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    return [name for name in done.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def _bidi_in_file(path: Path) -> list[str]:
    """The bidi controls a text file holds; a binary or non-UTF-8 file holds none."""
    data = path.read_bytes()
    if b"\0" in data:
        return []
    try:
        return _bidi_in(data.decode("utf-8"))
    except UnicodeDecodeError:
        return []


def test_no_tracked_text_file_holds_a_bidi_control() -> None:
    found = {}
    for name in _tracked():
        path = ROOT / name
        if name.startswith(EXCLUDED) or not path.is_file():
            continue
        if hits := _bidi_in_file(path):
            found[name] = hits
    assert not found, found


def test_bidi_check_constructed_control(tmp_path: Path) -> None:
    """Constructed input: a Lua file with each code point in a comment."""
    assert len(BIDI) == 12
    path = tmp_path / "Constructed.lua"
    path.write_text("".join(f"-- {ch} UnitName\n" for ch in sorted(BIDI)), encoding="utf-8")
    assert len(_bidi_in_file(path)) == 12
    clean = tmp_path / "Clean.lua"
    clean.write_text("-- plain text\n", encoding="utf-8")
    assert _bidi_in_file(clean) == []
