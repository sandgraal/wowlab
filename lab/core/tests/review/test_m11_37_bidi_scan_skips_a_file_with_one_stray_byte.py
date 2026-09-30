# Probe from review of m11/37-bidi-api-sections; reproduces the bidi scan skipping a whole tracked text file that also holds one non-UTF-8 byte or a NUL
"""tests/repo/test_bidi_controls.py reads each tracked file and returns no
finding for it when the bytes hold a NUL or do not decode as UTF-8
(`_bidi_in_file`). One stray byte anywhere in a file therefore hides every
bidirectional control in it. Lua 5.1 takes both in a comment, the pinned
selene reports 0 parse errors for Professions.lua with `-- <NUL>` and
`-- <U+202E>` lines prepended, and every test in tests/addon/test_lab_addon.py
still passes on that copy, so nothing else in `make ci` stops it. The same
holds for any tracked text file that is not Python (ruff's PLE2502 covers
Python).

Each case is a file with U+202E RIGHT-TO-LEFT OVERRIDE in a comment and one
stray byte on another line; the scan must report U+202E. Control: the same
file without the stray byte is reported. Constructed inputs (built with
chr() and byte literals; this file holds no raw control character).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
SCAN = REPO / "tests" / "repo" / "test_bidi_controls.py"
RLO = chr(0x202E).encode("utf-8")
BODY = b"local _, ns = ...\n-- " + RLO + b" reads the other way\n"

CASES: dict[str, bytes] = {
    "constructed-latin1-byte-in-a-comment": b"-- caf\xe9\n" + BODY,
    "constructed-nul-in-a-comment": b"-- \x00\n" + BODY,
}
CONTROLS: dict[str, bytes] = {
    "control-no-stray-byte": BODY,
}


def _scan() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m11_37_bidi_scan_probe", SCAN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _found(tmp_path: Path, data: bytes) -> list[str]:
    path = tmp_path / "Constructed.lua"
    path.write_bytes(data)
    found: list[str] = _scan()._bidi_in_file(path)
    return found


@pytest.mark.parametrize("tag", sorted(CONTROLS))
def test_positive_control_is_reported(tmp_path: Path, tag: str) -> None:
    assert _found(tmp_path, CONTROLS[tag]) == ["U+202E"]


@pytest.mark.parametrize("tag", sorted(CASES))
def test_bidi_control_beside_a_stray_byte_is_reported(tmp_path: Path, tag: str) -> None:
    assert _found(tmp_path, CASES[tag]) == ["U+202E"]
