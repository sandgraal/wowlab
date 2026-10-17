# Probe from review of m13/15-windows-long-paths; reproduces longpath keeping
# the plain spelling of a path past MAX_PATH when it holds non-BMP characters,
# because PLAIN_LIMIT is compared with code points, not UTF-16 units.
"""`MAX_PATH` (260) and `CreateDirectoryW`'s 248 are counted in UTF-16 code
units, as the `longpath` module docstring itself says ("260 UTF-16 units").
`extended_text` compares `len(ntpath.abspath(text))`, which counts Python
code points, with `PLAIN_LIMIT`. A character outside the Basic Multilingual
Plane (an emoji, a CJK Extension B ideograph in a Windows user name or a
folder the player made) is one code point but two UTF-16 units, so a path of
139 code points and 265 UTF-16 units is left plain, and on Windows with
`LongPathsEnabled` off every call through `longpath` (and the gate's temp
file) fails for it. The snapshot then raises rather than skipping
(`_confirm_gone` uses `always=True`), so the effect is a refusal, not a
partial snapshot; the gate and the readers fail the same way.

Constructed boundary input (L8 allows these); pure text, so it runs on every
platform. The positive control is an ASCII path of the same UTF-16 length,
which is extended. Fix: measure `len(full.encode("utf-16-le")) // 2`.
"""

from __future__ import annotations

from wowlab_core import longpath

_NON_BMP = "\U0001f600"  # one code point, a surrogate pair in UTF-16


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_control_an_ascii_path_past_the_limit_is_extended() -> None:
    path = "C:\\Games\\" + "a" * 252 + "\\WTF"
    assert _utf16_units(path) == 265
    assert longpath.extended_text(path).startswith("\\\\?\\C:\\")


def test_a_path_past_the_limit_in_utf16_units_is_extended() -> None:
    path = "C:\\Games\\" + _NON_BMP * 126 + "\\WTF"
    assert len(path) == 139  # code points: under PLAIN_LIMIT
    assert _utf16_units(path) == 265  # what Windows counts: past MAX_PATH
    assert longpath.extended_text(path).startswith("\\\\?\\C:\\"), (
        "a path of 265 UTF-16 units is past MAX_PATH and must be extended"
    )
