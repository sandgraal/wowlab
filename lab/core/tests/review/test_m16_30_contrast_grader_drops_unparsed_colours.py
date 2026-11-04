# Probe from review of m16/30-ui-visual-a11y; reproduces the contrast grader silently dropping a colour it cannot read (8-digit hex, a named colour, hsl()), so the pair is never graded.
"""`test_ui_contrast._colours` reads `#rgb`, `#rrggbb`, `rgb()`/`rgba()` with
commas and `var()`. Any other colour syntax gives an empty list, and an empty
list means "no colour here": no pair is made and nothing fails. The tokens
M16-52 must declare (`docs/design/wellspring-tokens.json`) include 8-digit hex
(`wellspring-glow`: `#55d8b726`, `#0a73611f`), so that extension would skip
them without a sound. A colour the grader cannot read must fail the grade,
not pass it. Keywords that are not colours of the page's text or grounds
(`none`, `transparent`, `inherit`, `currentColor`, system colours under
forced colours) may still give nothing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_ui_contrast as grader


def test_positive_control_a_six_digit_hex_is_read() -> None:
    assert grader._colours("#e3bb55", {}) == [((227, 187, 85), 1.0)]


@pytest.mark.parametrize(
    "value",
    ["#55d8b726", "white", "hsl(40 50% 50%)"],
    ids=["eight-digit-hex-constructed", "named-colour-constructed", "hsl-constructed"],
)
def test_an_unread_colour_is_never_silently_ungraded(value: str) -> None:
    try:
        found = grader._colours(f"linear-gradient(180deg, {value}, #000000)", {})
    except (AssertionError, ValueError):
        return  # failing loudly is a correct answer
    assert len(found) == 2, f"{value} was dropped: {found}"
